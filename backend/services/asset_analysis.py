"""Composizione dell'analisi di uno strumento: da candele a `AssetMarketContext`.

Questo è lo strato che **orchestra** e non calcola: gli indicatori stanno in
`quant/`, la struttura in `structure/`, la classificazione in `regime/`. Qui si
mettono insieme, timeframe per timeframe, e si costruisce l'oggetto tipizzato che
la futura intelligence riceverà.

Due proprietà volute.

**Nessuna proposta operativa.** Non si calcolano entry, stop o direzioni da
prendere. Il contesto descrive il mercato; decidere è compito della fase 7, e
tenere separate le due cose è ciò che permette al Risk Engine di restare l'unico
varco verso l'esecuzione.

**La qualità viaggia con il dato.** Un timeframe con storia insufficiente produce
comunque un'analisi, con i campi non calcolabili a `None` e la qualità che dice
perché. L'alternativa — omettere il timeframe — farebbe sembrare l'analisi
completa quando non lo è.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from backend.config.risk_limits import RiskLimits
from backend.core.clock import Clock, SystemClock
from backend.core.enums import (
    DataQuality,
    MarketRegime,
    QuoteState,
    StructureTrend,
    Timeframe,
)
from backend.core.errors import SymbolNotAllowedError, TradingAgentError
from backend.core.logging import get_logger
from backend.market_data.ingestion import (
    TIMEFRAMES_ANALISI,
    CandleIngestion,
    TimeframeCandles,
    peggiore,
)
from backend.market_data.instruments import InstrumentRegistry
from backend.market_data.sessions import BrokerClock, QuoteStateDetector
from backend.market_data.specifications import SpecificationRepository
from backend.quant.indicators import (
    adx,
    ampiezza_bollinger,
    atr,
    bollinger,
    macd,
    momentum,
    rsi,
    volatilita_realizzata,
)
from backend.quant.series import SerieChiuse, ema, pendenza, ultimo
from backend.regime.trend import classifica_trend
from backend.schemas.context import (
    PESI_TIMEFRAME,
    AssetMarketContext,
    ContractSummary,
    MultiTimeframeView,
    QuoteStatus,
)
from backend.schemas.quant import IndicatorSnapshot, TimeframeAnalysis
from backend.structure.breakout import rileva_breakout
from backend.structure.levels import trova_livelli
from backend.structure.market_structure import analizza_struttura
from backend.structure.swings import trova_swing
from backend.valuation.service import BrokerValuationService

logger = get_logger(__name__)

#: Barre su cui misurare la pendenza della EMA50. Dieci barre: abbastanza per non
#: inseguire il rumore, poche per accorgersi di un cambio di passo.
BARRE_PENDENZA = 10

#: Finestra per il rango percentile dell'ampiezza di Bollinger.
FINESTRA_COMPRESSIONE = 120

#: Timeframe che definiscono la struttura, la tendenza operativa, il setup e
#: l'esecuzione. È la gerarchia del progetto resa esplicita.
TIMEFRAMES_STRUTTURA = (Timeframe.D1, Timeframe.H4)
TIMEFRAME_OPERATIVO = Timeframe.H1
TIMEFRAME_SETUP = Timeframe.M15
TIMEFRAME_ESECUZIONE = Timeframe.M5


class AssetAnalyst:
    """Costruisce il contesto di mercato di un singolo strumento."""

    def __init__(
        self,
        *,
        ingestion: CandleIngestion,
        specifications: SpecificationRepository,
        detector: QuoteStateDetector,
        broker_clock: BrokerClock,
        instruments: InstrumentRegistry,
        valuation: BrokerValuationService,
        get_tick: Callable[[str], Awaitable[object]],
        limits: RiskLimits,
        clock: Clock | None = None,
        ensure_broker_time: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self._ingestion = ingestion
        self._specifiche = specifications
        # I limiti servono per una ragione di sicurezza, non di comodità: il
        # repository delle specifiche parla direttamente col broker e non conosce
        # la whitelist, quindi senza un controllo qui `analyse()` potrebbe leggere
        # e analizzare uno strumento che l'utente non ha autorizzato. Difetto
        # trovato dai test il 2026-10-04.
        self._limits = limits
        self._detector = detector
        self._broker_clock = broker_clock
        # Il valore monetario passa SOLO dal servizio di valutazione (ADR 0014):
        # qui non c'è più nessuna formula, nemmeno delegata a una funzione.
        self._strumenti = instruments
        self._valutazione = valuation
        self._get_tick = get_tick
        self._clock = clock or SystemClock()
        self._assicura_ora_broker = ensure_broker_time

    # ------------------------------------------------------------------
    # Analisi completa di uno strumento
    # ------------------------------------------------------------------
    async def analyse(
        self, symbol: str, timeframes: tuple[Timeframe, ...] = TIMEFRAMES_ANALISI
    ) -> AssetMarketContext:
        """Contesto completo: specifiche, quotazione, analisi multi-timeframe.

        Solleva `SymbolNotAllowedError` se lo strumento non è nella whitelist.
        Restituire un contesto vuoto sarebbe peggio: il chiamante lo leggerebbe
        come "nessun dato" invece di "non autorizzato", e sono cose diverse.
        """
        adesso = self._clock.now()
        avvisi: list[str] = []

        canonico = self._limits.canonical_symbol(symbol)
        if canonico is None:
            raise SymbolNotAllowedError(
                f"'{symbol}' non è fra gli strumenti autorizzati: nessuna analisi.",
                symbol=symbol,
                allowed=list(self._limits.allowed_symbols),
            )
        symbol = canonico

        info = (await self._specifiche.get(symbol)).value
        classe = self._specifiche.asset_class(symbol)
        strumento = await self._strumenti.by_broker_symbol(symbol)

        # Senza scarto dell'ora broker nessuna quotazione può risultare valida
        # (ADR 0015): prima di valutare, si prova a misurarlo.
        if self._assicura_ora_broker is not None:
            await self._assicura_ora_broker()

        # --- quotazione: tick e stato ---
        tick = None
        quote_missing = False
        try:
            tick = await self._get_tick(symbol)
        except TradingAgentError as errore:
            # bid/ask a zero arriva come StaleDataError dal connettore: è
            # "nessuna quotazione", non un guasto.
            quote_missing = True
            avvisi.append(str(errore))

        if tick is not None:
            self._broker_clock.observe(tick, now=adesso)  # type: ignore[arg-type]

        candele = await self._ingestion.fetch_all(symbol, timeframes)
        mancanti = [tf for tf in timeframes if tf not in candele]
        if mancanti:
            avvisi.append("Timeframe non disponibili: " + ", ".join(str(tf) for tf in mancanti))

        analisi: dict[Timeframe, TimeframeAnalysis] = {}
        for timeframe, dati in candele.items():
            risultato = self._analizza_timeframe(dati)
            if risultato is not None:
                analisi[timeframe] = risultato

        # La valutazione della quotazione viene **dopo** l'analisi perche' il
        # giudizio sullo spread richiede un ATR: un limite in punti assoluti non e'
        # confrontabile fra EURUSD e US100Cash.
        #
        # L'ATR di riferimento e' quello del timeframe **operativo** (H1), non di
        # quello di esecuzione. Misurato sul conto reale alla chiusura del venerdi':
        # con l'ATR di M5 il rapporto spread/ATR di EURUSD risultava 0.74 e quello
        # di USDJPY 1.46, perche' l'ATR a cinque minuti collassa quando il mercato
        # rallenta — e gli strumenti sarebbero risultati tutti troppo cari. Lo
        # spread va confrontato con il movimento che l'operazione si aspetta di
        # catturare, e quell'orizzonte e' H1.
        ultima_barra = candele.get(TIMEFRAME_ESECUZIONE) or next(iter(candele.values()), None)
        riferimento_atr = analisi.get(TIMEFRAME_OPERATIVO) or analisi.get(TIMEFRAME_ESECUZIONE)
        atr_esecuzione = riferimento_atr.indicators.atr14 if riferimento_atr else None
        # Ultima chiusura dello stesso timeframe dell'ATR: serve a riconoscere un
        # prezzo incoerente (troppi ATR lontano da dove il mercato era).
        ultima_chiusura = riferimento_atr.indicators.close if riferimento_atr else None
        valutazione = self._detector.assess(
            info,
            tick,  # type: ignore[arg-type]
            now=adesso,
            last_bar_time=ultima_barra.last_closed_at if ultima_barra else None,
            last_bar_timeframe=ultima_barra.timeframe if ultima_barra else Timeframe.M5,
            quote_missing=quote_missing,
            atr=atr_esecuzione,
            last_close=ultima_chiusura,
        )

        # --- valore monetario di un movimento, chiesto al broker ---
        valore_unita: float | None = None
        fonte_valore = "broker_calculator"
        prezzo_riferimento = self._prezzo_riferimento(tick, analisi)
        if prezzo_riferimento is not None:
            try:
                valore_unita = round(
                    await self._valutazione.value_per_price_unit(strumento, prezzo_riferimento),
                    6,
                )
            except TradingAgentError as errore:
                fonte_valore = "non_disponibile"
                avvisi.append(
                    f"Valore monetario non calcolabile ({errore}): lo strumento non è "
                    "dimensionabile finché il broker non lo fornisce."
                )
        else:
            fonte_valore = "non_disponibile"
            avvisi.append("Nessun prezzo di riferimento: valore monetario non calcolabile.")

        qualita = (
            peggiore([d.quality for d in candele.values()]) if candele else DataQuality.INSUFFICIENT
        )
        if valutazione.state is not QuoteState.TRADEABLE:
            avvisi.append(valutazione.reason)

        stima_broker = self._broker_clock.estimate(now=adesso)

        return AssetMarketContext(
            symbol=info.name,
            asset_class=classe,
            as_of=adesso,
            broker_time=stima_broker.server_time,
            broker_time_source=stima_broker.source,
            instrument=strumento,
            quote=QuoteStatus(
                state=valutazione.state,
                reason=valutazione.reason,
                is_inference=valutazione.is_inference,
                tick_age_seconds=valutazione.tick_age_seconds,
                spread_points=valutazione.spread_points,
                spread_atr_ratio=valutazione.evidence.get("spread_su_atr"),  # type: ignore[arg-type]
                last_bar_age_seconds=valutazione.last_bar_age_seconds,
                bid=getattr(tick, "bid", None),
                ask=getattr(tick, "ask", None),
                market_status=valutazione.market_status,
                quality=valutazione.quality,
            ),
            contract=ContractSummary(
                digits=info.digits,
                point=info.point,
                contract_size=info.contract_size,
                tick_size=info.tick_size,
                volume_min=info.volume_min,
                volume_step=info.volume_step,
                volume_max=info.volume_max,
                spread_points=info.spread_points,
                stops_level_points=info.stops_level_points,
                currency_base=info.currency_base,
                currency_profit=info.currency_profit,
                value_per_price_unit=valore_unita,
                value_source=fonte_valore,
            ),
            timeframes=analisi,
            multi_timeframe=self._multi_timeframe(analisi),
            quality=qualita,
            simulated=any("mock" in d.series.source for d in candele.values()),
            warnings=tuple(dict.fromkeys(avvisi)),
        )

    # ------------------------------------------------------------------
    # Contesto storico (replay della fase 5B)
    # ------------------------------------------------------------------
    async def analyse_replay(
        self,
        symbol: str,
        candele: dict[Timeframe, TimeframeCandles],
        *,
        as_of: datetime,
    ) -> AssetMarketContext:
        """Contesto da candele **già tagliate** all'istante `as_of` (nessuna barra dopo).

        Non esiste la quotazione dell'epoca: lo stato è `UNKNOWN`, dichiarato come
        deduzione, e il contesto risulta non utilizzabile per decisioni reali
        (`usable_for_decisions` falso). Serve solo alla shadow mode, che misura cosa
        si sarebbe deciso, e lo registra come `REPLAY`.
        """
        canonico = self._limits.canonical_symbol(symbol)
        if canonico is None:
            raise SymbolNotAllowedError(
                f"'{symbol}' non è fra gli strumenti autorizzati: nessuna analisi.",
                symbol=symbol,
                allowed=list(self._limits.allowed_symbols),
            )
        info = (await self._specifiche.get(canonico)).value
        strumento = await self._strumenti.by_broker_symbol(canonico)
        analisi = {
            tf: risultato
            for tf, dati in candele.items()
            if (risultato := self._analizza_timeframe(dati)) is not None
        }
        veloce = candele.get(TIMEFRAME_ESECUZIONE)
        ultima = veloce.series.closed[-1] if veloce and veloce.series.closed else None
        spread = ultima.spread if ultima and ultima.spread else info.spread_points
        riferimento = analisi.get(TIMEFRAME_OPERATIVO)
        atr = riferimento.indicators.atr14 if riferimento else None
        return AssetMarketContext(
            symbol=info.name,
            asset_class=self._specifiche.asset_class(canonico),
            as_of=as_of,
            broker_time_source="REPLAY",
            instrument=strumento,
            quote=QuoteStatus(
                state=QuoteState.UNKNOWN,
                reason="Replay storico: nessuna quotazione dell'epoca, prezzo dall'ultima "
                "chiusura M5 e spread dall'ultima barra.",
                is_inference=True,
                spread_points=spread,
                spread_atr_ratio=round(spread * info.point / atr, 4) if atr else None,
            ),
            contract=ContractSummary(
                digits=info.digits,
                point=info.point,
                contract_size=info.contract_size,
                tick_size=info.tick_size,
                volume_min=info.volume_min,
                volume_step=info.volume_step,
                volume_max=info.volume_max,
                spread_points=spread,
                stops_level_points=info.stops_level_points,
                currency_base=info.currency_base,
                currency_profit=info.currency_profit,
                value_source="non_disponibile",
            ),
            timeframes=analisi,
            multi_timeframe=self._multi_timeframe(analisi),
            quality=(
                peggiore([d.quality for d in candele.values()])
                if candele
                else DataQuality.INSUFFICIENT
            ),
            simulated=any("mock" in d.series.source for d in candele.values()),
            warnings=tuple(d.note for d in candele.values() if d.note),
        )

    # ------------------------------------------------------------------
    # Analisi di un singolo timeframe
    # ------------------------------------------------------------------
    def _analizza_timeframe(self, dati: TimeframeCandles) -> TimeframeAnalysis | None:
        """Indicatori, struttura, livelli, rottura e tendenza su un timeframe.

        Restituisce `None` solo se non c'è nemmeno una candela chiusa: con una
        serie vuota non c'è niente da descrivere, nemmeno il prezzo.
        """
        chiuse = dati.series.closed
        if not chiuse:
            return None

        serie = SerieChiuse.da_candele(chiuse)
        chiusure = serie.chiusure

        ema20 = ema(chiusure, 20)
        ema50 = ema(chiusure, 50)
        ema200 = ema(chiusure, 200)
        rsi14 = rsi(chiusure, 14)
        valori_macd = macd(chiusure)
        atr14 = atr(serie.massimi, serie.minimi, chiusure, 14)
        valori_adx = adx(serie.massimi, serie.minimi, chiusure, 14)
        bande = bollinger(chiusure, 20)
        ampiezza, rango = ampiezza_bollinger(bande, FINESTRA_COMPRESSIONE)
        vol_barra, _ = volatilita_realizzata(chiusure, 20)
        mom = momentum(chiusure, (5, 20, 60))

        atr_corrente = ultimo(atr14)
        chiusura_corrente = serie.ultima_chiusura
        atr_pct = (
            round(atr_corrente / chiusura_corrente * 100, 6)
            if atr_corrente and chiusura_corrente
            else None
        )

        indicatori = IndicatorSnapshot(
            ema20=_arrotonda(ultimo(ema20)),
            ema50=_arrotonda(ultimo(ema50)),
            ema200=_arrotonda(ultimo(ema200)),
            rsi14=_arrotonda(ultimo(rsi14), 4),
            macd_line=_arrotonda(ultimo(valori_macd.linea)),
            macd_signal=_arrotonda(ultimo(valori_macd.segnale)),
            macd_histogram=_arrotonda(ultimo(valori_macd.istogramma)),
            atr14=_arrotonda(atr_corrente),
            atr_pct=atr_pct,
            bollinger_upper=_arrotonda(ultimo(bande.superiore)),
            bollinger_middle=_arrotonda(ultimo(bande.centrale)),
            bollinger_lower=_arrotonda(ultimo(bande.inferiore)),
            bollinger_width=_arrotonda(ampiezza, 8),
            bollinger_width_rank=_arrotonda(rango, 4),
            realized_volatility=_arrotonda(vol_barra, 8),
            adx14=_arrotonda(ultimo(valori_adx.adx), 4),
            di_plus=_arrotonda(ultimo(valori_adx.di_positivo), 4),
            di_minus=_arrotonda(ultimo(valori_adx.di_negativo), 4),
            ema50_slope_pct=_arrotonda(pendenza(ema50, BARRE_PENDENZA), 6),
            momentum_5=_arrotonda(mom.get(5), 4),
            momentum_20=_arrotonda(mom.get(20), 4),
            momentum_60=_arrotonda(mom.get(60), 4),
            close=chiusura_corrente,
            bars_used=len(chiuse),
        )

        swing = trova_swing(serie.massimi, serie.minimi, serie.tempi)  # type: ignore[arg-type]
        struttura = analizza_struttura(swing, chiusura_corrente)
        livelli = trova_livelli(
            swing,
            chiusura_corrente,
            atr_corrente,
            ultimo_indice=len(chiuse) - 1,
        )
        rottura = rileva_breakout(serie.massimi, serie.minimi, chiusure, atr_corrente)
        tendenza = classifica_trend(indicatori, struttura)

        return TimeframeAnalysis(
            timeframe=dati.timeframe,
            role=dati.timeframe.role,
            last_closed_at=chiuse[-1].time,
            bars_closed=len(chiuse),
            quality=dati.quality,
            indicators=indicatori,
            structure=struttura,
            breakout=rottura,
            trend=tendenza,
            levels=livelli,
            # Solo gli ultimi swing: la serie completa gonfierebbe il contesto
            # persistito senza aggiungere informazione utile a valle.
            swings=swing[-8:],
        )

    # ------------------------------------------------------------------
    # Lettura d'insieme
    # ------------------------------------------------------------------
    @staticmethod
    def _multi_timeframe(analisi: dict[Timeframe, TimeframeAnalysis]) -> MultiTimeframeView:
        """Allineamento fra timeframe e conflitti fra struttura ed esecuzione."""
        if not analisi:
            return MultiTimeframeView(
                structural_bias=0,
                operational_bias=0,
                setup_bias=0,
                execution_bias=0,
                alignment=0.0,
                aligned_timeframes=0,
                total_timeframes=0,
                notes=("Nessun timeframe analizzato.",),
            )

        def bias(timeframe: Timeframe) -> int:
            voce = analisi.get(timeframe)
            return voce.trend.direction if voce else 0

        strutturali = [bias(tf) for tf in TIMEFRAMES_STRUTTURA if tf in analisi]
        bias_strutturale = _segno(sum(strutturali)) if strutturali else 0
        bias_operativo = bias(TIMEFRAME_OPERATIVO)
        bias_setup = bias(TIMEFRAME_SETUP)
        bias_esecuzione = bias(TIMEFRAME_ESECUZIONE)

        peso_totale = sum(PESI_TIMEFRAME.get(tf, 0.1) for tf in analisi)
        allineamento = (
            sum(PESI_TIMEFRAME.get(tf, 0.1) * voce.trend.direction for tf, voce in analisi.items())
            / peso_totale
            if peso_totale
            else 0.0
        )

        segno = _segno(allineamento)
        concordi = sum(1 for voce in analisi.values() if voce.trend.direction == segno and segno)

        veloce = bias_esecuzione or bias_setup
        conflitto = bool(bias_strutturale and veloce and bias_strutturale != veloce)

        note: list[str] = []
        if conflitto:
            note.append(
                "Struttura lenta e timeframe veloci in direzioni opposte: "
                "configurazione di ritracciamento o di inversione, non di continuazione."
            )
        if bias_strutturale == 0 and strutturali:
            note.append("Struttura lenta senza direzione: nessun contesto dominante.")

        dominante = next(
            (analisi[tf] for tf in TIMEFRAMES_STRUTTURA if tf in analisi),
            next(iter(analisi.values())),
        )

        return MultiTimeframeView(
            structural_bias=bias_strutturale,
            operational_bias=bias_operativo,
            setup_bias=bias_setup,
            execution_bias=bias_esecuzione,
            alignment=round(max(-1.0, min(1.0, allineamento)), 4),
            aligned_timeframes=concordi,
            total_timeframes=len(analisi),
            structure_execution_conflict=conflitto,
            dominant_regime=dominante.trend.regime if dominante else MarketRegime.UNCERTAIN,
            dominant_structure=dominante.structure.trend if dominante else StructureTrend.UNDEFINED,
            notes=tuple(note),
        )

    @staticmethod
    def _prezzo_riferimento(
        tick: object | None, analisi: dict[Timeframe, TimeframeAnalysis]
    ) -> float | None:
        """Prezzo su cui calcolare il valore monetario di un movimento.

        Il tick se c'è, altrimenti l'ultima chiusura: a mercato chiuso il tick è
        vecchio ma il prezzo resta l'ordine di grandezza giusto, e il valore
        monetario serve comunque per leggere l'esposizione esistente.
        """
        prezzo = getattr(tick, "mid", None)
        if prezzo:
            return float(prezzo)
        for voce in analisi.values():
            if voce.indicators.close:
                return voce.indicators.close
        return None


def _arrotonda(valore: float | None, cifre: int = 8) -> float | None:
    return round(valore, cifre) if valore is not None else None


def _segno(valore: float) -> int:
    if valore > 0.05:
        return 1
    if valore < -0.05:
        return -1
    return 0
