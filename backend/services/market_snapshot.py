"""Costruzione dell'istantanea di mercato normalizzata.

Un `MarketSnapshot` è tutto ciò che il sistema osserva, letto **nello stesso
giro**: conto, esposizione, contesti degli strumenti, fase di mercato, ora del
broker. Comporre un'analisi da letture fatte in momenti diversi produce numeri che
non sono mai esistiti insieme — un'equity di adesso con posizioni di dieci secondi
fa — e quell'incoerenza non si vede guardando i singoli valori.

Due comportamenti deliberati.

**Uno strumento che fallisce non ferma gli altri.** Se GOLD non risponde, lo
snapshot contiene gli altri nove e dichiara il problema. Interrompere tutto
renderebbe il sistema ostaggio del simbolo meno affidabile.

**La whitelist viene verificata contro il catalogo del broker.** Una voce
sbagliata in `.env` non produce errori: produce uno strumento che il sistema non
guarda mai. Qui quell'assenza diventa un messaggio con i candidati probabili, e
nessuna sostituzione automatica (vedi `market_data/registry.py`).
"""

from __future__ import annotations

from backend.brokers.base import ReadOnlyBroker
from backend.config.risk_limits import RiskLimits
from backend.core.clock import Clock, SystemClock
from backend.core.enums import Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.global_market.backdrop import sfondo
from backend.market_data.ingestion import TIMEFRAMES_ANALISI
from backend.market_data.registry import SymbolRegistry
from backend.market_data.sessions import (
    BrokerClock,
    QuoteAssessment,
    QuoteStateDetector,
    fase_di_mercato,
)
from backend.portfolio.exposure import ExposureCalculator
from backend.schemas.context import AssetMarketContext, MarketSnapshot, WhitelistProblem
from backend.schemas.global_market import GlobalMarketContext
from backend.services.asset_analysis import AssetAnalyst
from backend.services.global_intelligence import GlobalIntelligenceService

logger = get_logger(__name__)


class MarketSnapshotService:
    """Assembla l'istantanea di mercato dai componenti di sola lettura."""

    def __init__(
        self,
        *,
        broker: ReadOnlyBroker,
        registry: SymbolRegistry,
        analyst: AssetAnalyst,
        exposure: ExposureCalculator,
        detector: QuoteStateDetector,
        broker_clock: BrokerClock,
        limits: RiskLimits,
        clock: Clock | None = None,
        global_intelligence: GlobalIntelligenceService | None = None,
    ) -> None:
        self._globale = global_intelligence
        self._broker = broker
        self._registry = registry
        self._analyst = analyst
        self._exposure = exposure
        self._detector = detector
        self._broker_clock = broker_clock
        self._limits = limits
        self._clock = clock or SystemClock()

    async def build(
        self,
        symbols: tuple[str, ...] | None = None,
        timeframes: tuple[Timeframe, ...] = TIMEFRAMES_ANALISI,
    ) -> MarketSnapshot:
        """Istantanea completa. Senza `symbols` usa la whitelist dell'utente."""
        adesso = self._clock.now()
        avvisi: list[str] = []

        await self._registry.ensure_loaded()
        problemi = self._registry.check_whitelist(self._limits.allowed_symbols)
        for problema in problemi:
            # ERROR per le voci inesistenti: il sintomo altrimenti è un'assenza,
            # la cosa più difficile da notare in un sistema che osserva mercati.
            livello = logger.error if problema.severity == "errore" else logger.warning
            livello(
                "whitelist.problema",
                simbolo=problema.requested,
                messaggio=problema.message,
                candidati=list(problema.suggestions),
            )

        da_analizzare = symbols or tuple(
            voce for voce in self._limits.allowed_symbols if self._registry.resolve(voce).usable
        )
        esclusi = set(self._limits.allowed_symbols) - set(da_analizzare)
        if esclusi and symbols is None:
            avvisi.append(
                "Strumenti esclusi perché il nome non corrisponde a nessun simbolo "
                "del broker: " + ", ".join(sorted(esclusi))
            )

        # Pipeline della fase 4: prima "cosa succede sui mercati", poi il singolo
        # strumento. Un guasto del globale non ferma l'istantanea, ma si dichiara.
        globale: GlobalMarketContext | None = None
        if self._globale is not None:
            try:
                _, globale = await self._globale.build()
            except TradingAgentError as errore:
                logger.warning("snapshot.globale_non_disponibile", motivo=str(errore))
                avvisi.append(f"Contesto globale non disponibile ({errore}).")

        portafoglio = await self._broker.get_portfolio()
        esposizione = await self._exposure.build(portafoglio)
        avvisi.extend(esposizione.warnings)

        contesti: list[AssetMarketContext] = []
        valutazioni: list[QuoteAssessment] = []
        for simbolo in da_analizzare:
            canonico = self._registry.resolve(simbolo).canonical or simbolo
            try:
                contesto = await self._analyst.analyse(canonico, timeframes)
            except TradingAgentError as errore:
                logger.warning(
                    "snapshot.asset_non_analizzabile", symbol=canonico, motivo=str(errore)
                )
                avvisi.append(f"{canonico}: analisi non disponibile ({errore})")
                continue
            if globale is not None and contesto.instrument is not None:
                contesto = contesto.model_copy(
                    update={"global_backdrop": sfondo(contesto.instrument, globale)}
                )
            contesti.append(contesto)
            valutazioni.append(
                QuoteAssessment(
                    symbol=contesto.symbol,
                    state=contesto.quote.state,
                    reason=contesto.quote.reason,
                    is_inference=contesto.quote.is_inference,
                    tick_age_seconds=contesto.quote.tick_age_seconds,
                    spread_points=contesto.quote.spread_points,
                    last_bar_age_seconds=contesto.quote.last_bar_age_seconds,
                    evidence={"spread_su_atr": contesto.quote.spread_atr_ratio},
                    quality=contesto.quote.quality,
                )
            )

        fase, motivo_fase = fase_di_mercato(valutazioni)
        stima = self._broker_clock.estimate(now=adesso)
        if not stima.reliable:
            avvisi.append(
                "Ora del server del broker non misurabile: senza una quotazione "
                "fresca lo scarto da UTC non è noto, e la giornata di trading non è "
                "allineabile. A mercato aperto la misura avviene da sola."
            )

        simulato = any(c.simulated for c in contesti)
        if simulato:
            avvisi.append(
                "Prezzi simulati: nessun valore di questa istantanea ha significato di mercato."
            )

        return MarketSnapshot(
            as_of=adesso,
            broker=self._broker.name,
            simulated=simulato or self._broker.is_simulated,
            broker_time=stima.server_time,
            broker_time_source=stima.source,
            broker_time_offset_seconds=stima.offset_seconds,
            market_phase=fase,
            market_phase_reason=motivo_fase,
            account=portafoglio.account,
            exposure=esposizione,
            global_context=globale,
            assets=tuple(contesti),
            whitelist_problems=tuple(
                WhitelistProblem(
                    requested=p.requested,
                    severity=p.severity,
                    message=p.message,
                    suggestions=p.suggestions,
                )
                for p in problemi
            ),
            warnings=tuple(dict.fromkeys(avvisi)),
        )
