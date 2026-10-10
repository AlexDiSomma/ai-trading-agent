"""Scrittura su database di specifiche e contesti di mercato.

Sta in `services/` e non nei moduli di dominio per una ragione precisa: `quant/`,
`structure/` e `market_data/` non devono conoscere lo schema del database. Le
specifiche vengono salvate tramite una funzione iniettata nel repository, i
contesti tramite una chiamata esplicita del worker. Così quei moduli si testano
senza PostgreSQL e restano riusabili dal backtest, che non scrive negli stessi
posti.

Una scrittura fallita non ferma l'analisi: viene registrata come errore e la
pagina System Health la mostra. Perdere una riga di storico è grave, ma smettere
di osservare il mercato perché il database non risponde è peggio — e il Risk
Engine blocca comunque l'operatività quando la tracciabilità è compromessa.
"""

from __future__ import annotations

from datetime import datetime

from backend.ai.contracts import AnalysisRecord
from backend.core.clock import Clock, SystemClock
from backend.core.enums import AssetClass, PersistenceRequirement
from backend.core.errors import PersistenceUnavailableError
from backend.core.logging import get_logger
from backend.database.session import Database
from backend.models.ai import AiAnalysisRow
from backend.models.context import AssetContextRow
from backend.models.global_market import GlobalContextRow
from backend.models.market import AccountSnapshotRow, SymbolRow
from backend.schemas.account import PortfolioSnapshot
from backend.schemas.context import AssetMarketContext
from backend.schemas.global_market import GlobalMarketContext, GlobalMarketSnapshot
from backend.schemas.market import SymbolInfo

logger = get_logger(__name__)


class MarketPersistence:
    """Scritture di sola aggiunta: niente aggiornamenti sui dati storici.

    Le righe non vengono mai modificate. Una specifica cambiata genera una riga
    nuova, non un `UPDATE`: altrimenti, rileggendo una size contestata, si
    troverebbe la specifica di oggi al posto di quella con cui era stata
    calcolata.
    """

    #: Dopo una scrittura fallita, per questi secondi le successive non vengono
    #: nemmeno tentate (e contano come saltate). Misurato il 2026-10-04 con
    #: PostgreSQL spento: ogni tentativo attendeva ~4 secondi il rifiuto della
    #: connessione, e le 37 specifiche dell'universo globale bloccavano la prima
    #: analisi per 146 secondi. L'analisi non deve aspettare un database assente.
    PAUSA_DOPO_GUASTO_SECONDI = 30.0

    def __init__(self, database: Database, *, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or SystemClock()
        #: Scritture fallite dall'avvio: le legge System Health.
        self.write_failures = 0
        #: Scritture non tentate perché il database era appena risultato giù.
        self.writes_skipped = 0
        self._ultimo_guasto: datetime | None = None
        self._ultimo_successo: datetime | None = None

    async def save_symbol(
        self,
        info: SymbolInfo,
        asset_class: AssetClass,
        *,
        in_whitelist: bool = False,
        value_per_price_unit: float | None = None,
    ) -> None:
        """Registra una specifica di contratto."""
        riga = SymbolRow(
            name=info.name,
            description=info.description[:160],
            path=info.path[:200],
            asset_class=str(asset_class),
            digits=info.digits,
            point=info.point,
            contract_size=info.contract_size,
            tick_size=info.tick_size,
            tick_value=info.tick_value,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            spread_points=info.spread_points,
            stops_level_points=info.stops_level_points,
            currency_base=info.currency_base[:8],
            currency_profit=info.currency_profit[:8],
            currency_margin=info.currency_margin[:8],
            trade_mode=info.trade_mode,
            trade_allowed=info.trade_allowed,
            value_per_price_unit=value_per_price_unit,
            in_whitelist=in_whitelist,
            as_of=info.as_of,
        )
        await self._scrivi(riga, "specifica", symbol=info.name)

    async def save_account(self, portafoglio: PortfolioSnapshot, *, simulated: bool) -> None:
        """Registra uno snapshot del conto per la serie storica dell'equity."""
        conto = portafoglio.account
        riga = AccountSnapshotRow(
            login=conto.login,
            server=conto.server[:80],
            currency=conto.currency[:3],
            leverage=conto.leverage,
            balance=conto.balance,
            equity=conto.equity,
            margin=conto.margin,
            margin_free=conto.margin_free,
            margin_level=conto.margin_level,
            open_profit=portafoglio.unrealized_pnl,
            open_positions=portafoglio.open_positions_count,
            trade_allowed=conto.trade_allowed,
            simulated=simulated,
            as_of=conto.as_of,
        )
        await self._scrivi(riga, "conto", login=conto.login)

    async def save_context(self, contesto: AssetMarketContext) -> None:
        """Registra un contesto di mercato completo.

        Le colonne estratte servono alle interrogazioni (`quante volte ADX era
        sopra 25 mentre la struttura era ribassista`); il payload JSON serve alla
        ricostruzione integrale.
        """
        vista = contesto.multi_timeframe
        riga = AssetContextRow(
            symbol=contesto.symbol,
            asset_class=str(contesto.asset_class),
            as_of=contesto.as_of,
            broker_time=contesto.broker_time,
            broker_time_source=contesto.broker_time_source[:16],
            quote_state=str(contesto.quote.state),
            quote_is_inference=contesto.quote.is_inference,
            spread_points=contesto.quote.spread_points,
            structural_bias=vista.structural_bias,
            operational_bias=vista.operational_bias,
            alignment=vista.alignment,
            dominant_regime=str(vista.dominant_regime),
            dominant_structure=str(vista.dominant_structure),
            structure_execution_conflict=vista.structure_execution_conflict,
            quality=str(contesto.quality),
            simulated=contesto.simulated,
            usable_for_decisions=contesto.usable_for_decisions,
            # `mode="json"` perché il payload contiene datetime ed enum, che la
            # colonna JSON non saprebbe serializzare da sola.
            payload=contesto.model_dump(mode="json"),
        )
        await self._scrivi(riga, "contesto", symbol=contesto.symbol)

    async def save_global_context(
        self, snapshot: GlobalMarketSnapshot, contesto: GlobalMarketContext
    ) -> None:
        """Registra un contesto globale con lo stato dei dati da cui è nato."""
        riga = GlobalContextRow(
            as_of=contesto.as_of,
            engine_version=contesto.engine_version,
            input_fingerprint=contesto.input_fingerprint,
            market_phase=str(contesto.market_phase),
            regime=str(contesto.regime),
            risk_sentiment=str(contesto.risk_sentiment.label),
            volatility_level=str(contesto.volatility.level),
            volatility_trend=str(contesto.volatility.trend),
            data_quality=str(contesto.data_quality),
            conflicts=len(contesto.conflicts),
            simulated=contesto.simulated,
            usable_for_decisions=contesto.usable_for_decisions,
            payload=contesto.model_dump(mode="json"),
            snapshot=snapshot.model_dump(mode="json"),
        )
        await self._scrivi(riga, "contesto_globale", regime=str(contesto.regime))

    async def save_ai_analysis(self, record: AnalysisRecord) -> None:
        """Registra un'analisi AI (best effort; la fonte primaria è il diario su file)."""
        riga = AiAnalysisRow(
            record_id=record.record_id,
            integrity_sha256=record.integrity_sha256,
            context_as_of=record.input.context_as_of,
            context_fingerprint=record.input.context_fingerprint,
            engine_regime=str(record.input.engine_regime),
            status=record.status,
            provider=record.call.provider,
            model=(record.call.model_snapshot or record.call.model_requested)[:60],
            prompt_version=record.input.prompt_version,
            output_schema_version=record.input.output_schema_version,
            input_tokens=record.call.input_tokens,
            output_tokens=record.call.output_tokens,
            record=record.model_dump(mode="json"),
        )
        await self._scrivi(riga, "analisi_ai", record=record.record_id)

    # ------------------------------------------------------------------
    # Politica di persistenza (ADR 0019)
    # ------------------------------------------------------------------
    @property
    def reliable(self) -> bool:
        """Affidabile = almeno una scrittura riuscita, e nessun guasto dopo di essa.

        Un database mai provato non è affidabile: "nessun errore finora" non è una
        prova di funzionamento.
        """
        if self._ultimo_successo is None:
            return False
        return self._ultimo_guasto is None or self._ultimo_successo > self._ultimo_guasto

    def require(self, requisito: PersistenceRequirement, operazione: str) -> None:
        """Garanzia da chiamare prima di un'operazione che dipende dalla persistenza.

        `BEST_EFFORT` (intelligence read-only) non solleva mai. `REQUIRED` (stato del
        rischio, intenti di esecuzione: fasi 6 e 11) solleva
        `PersistenceUnavailableError` se la persistenza non è affidabile. Fail closed.
        """
        if requisito is PersistenceRequirement.BEST_EFFORT or self.reliable:
            return
        raise PersistenceUnavailableError(
            f"{operazione}: richiede una persistenza affidabile e il database non lo è "
            f"(scritture fallite {self.write_failures}, saltate {self.writes_skipped}). "
            "Nessuna operazione finché non torna disponibile.",
            operazione=operazione,
        )

    async def _scrivi(self, riga: object, tipo: str, **contesto: object) -> None:
        adesso = self._clock.now()
        if (
            self._ultimo_guasto is not None
            and (adesso - self._ultimo_guasto).total_seconds() < self.PAUSA_DOPO_GUASTO_SECONDI
        ):
            self.writes_skipped += 1
            logger.warning(
                "persistenza.scrittura_saltata",
                tipo=tipo,
                motivo="database appena risultato non raggiungibile",
                saltate=self.writes_skipped,
                **contesto,
            )
            return
        try:
            async with self._database.session() as sessione:
                sessione.add(riga)
            self._ultimo_successo = self._clock.now()
        except Exception as errore:
            self.write_failures += 1
            self._ultimo_guasto = self._clock.now()
            # Senza traceback: con il database spento ogni ciclo ne produrrebbe
            # decine identici, e un log illeggibile nasconde gli errori veri.
            logger.error(
                "persistenza.scrittura_fallita",
                tipo=tipo,
                failures=self.write_failures,
                causa=f"{errore.__class__.__name__}: {errore}"[:200],
                **contesto,
            )
