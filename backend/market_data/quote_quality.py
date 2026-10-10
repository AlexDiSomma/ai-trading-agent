"""Valutazione della qualità di una quotazione.

Unico punto in cui si decide se un prezzo è un'informazione sul presente. Il
detector di stato (`sessions.QuoteStateDetector`) e tutto il resto del sistema
partono da qui, senza ricalcolare età o freschezza per conto proprio.

**Difetto corretto con questo modulo (2026-10-04, ADR 0015).** I timestamp di MT5
sono in ora del broker etichettata UTC; XM oggi è UTC+3. La versione precedente
calcolava l'età di un tick contro l'ora UTC del sistema: a mercato aperto un tick
appena arrivato risultava "vecchio di −3 ore", e **un tick fermo da 2 ore e 59
minuti passava per fresco**. Era un fail-open: il dato vecchio veniva dichiarato
valido. Il test sul conto di sabato non poteva accorgersene, perché a mercato
chiuso tutti i tick sono comunque vecchi di più di trenta ore.

Ora l'età si calcola con lo scarto dell'ora broker. Se lo scarto non è noto, l'età
esatta non è nota: si lavora con l'intervallo plausibile (±14 ore, l'ampiezza dei
fusi orari), e con un intervallo così largo **una quotazione non può essere
dichiarata valida**. Si può ancora dedurre che il mercato è chiuso, se anche il
caso più favorevole dà un'età oltre la soglia.
"""

from __future__ import annotations

import math
from datetime import datetime

from backend.core.enums import MarketStatus, QuoteQualityStatus, Timeframe
from backend.market_data.broker_time import BrokerClock
from backend.schemas.market import SymbolInfo, Tick
from backend.schemas.quote import QuoteQuality

#: Oltre questa età, su uno strumento liquido a mercato aperto una quotazione non
#: esiste: si deduce che il mercato sia chiuso. Quindici minuti è prudente —
#: perfino un indice poco scambiato stampa qualcosa in quindici minuti.
SOGLIA_MERCATO_CHIUSO_SECONDI = 15 * 60

#: Quanto un tick può risultare "dal futuro" prima di essere incoerente: ritardi di
#: rete e arrotondamento dello scarto al quarto d'ora.
TOLLERANZA_FUTURO_SECONDI = 120.0

#: Distanza fra prezzo e ultima chiusura, in ATR, oltre la quale la quotazione è
#: incoerente. Venticinque ATR del timeframe operativo sono più di quanto si muova
#: un mercato normale in una settimana: oltre, è più probabile un dato corrotto
#: (o un simbolo scambiato) che un movimento reale.
SOGLIA_INCOERENZA_ATR = 25.0


class QuoteQualityEvaluator:
    """Produce `QuoteQuality` da tick, candele e ora del broker."""

    def __init__(
        self,
        *,
        max_tick_age_seconds: float,
        soglia_mercato_chiuso: float = SOGLIA_MERCATO_CHIUSO_SECONDI,
        soglia_incoerenza_atr: float = SOGLIA_INCOERENZA_ATR,
    ) -> None:
        self._max_eta = max_tick_age_seconds
        self._soglia_chiuso = soglia_mercato_chiuso
        self._soglia_incoerenza = soglia_incoerenza_atr

    def evaluate(
        self,
        info: SymbolInfo,
        tick: Tick | None,
        *,
        now: datetime,
        broker_clock: BrokerClock | None,
        last_candle_time: datetime | None = None,
        last_candle_timeframe: Timeframe = Timeframe.M5,
        atr: float | None = None,
        last_close: float | None = None,
        quote_missing: bool = False,
    ) -> QuoteQuality:
        orologio = broker_clock or BrokerClock()
        comune: dict[str, object] = {
            "broker_symbol": info.name,
            "last_candle_time": last_candle_time,
            "max_tick_age_seconds": self._max_eta,
            "evaluated_at": now,
        }

        eta_candela, _, candela_esatta = (
            orologio.age_bounds(last_candle_time, now=now)
            if last_candle_time is not None
            else (None, None, False)
        )
        comune["candle_age_seconds"] = (
            round(eta_candela, 1) if candela_esatta and eta_candela is not None else None
        )

        # --- 1. manca il prezzo ---
        if quote_missing or tick is None or tick.bid <= 0 or tick.ask <= 0:
            return QuoteQuality(
                status=QuoteQualityStatus.MISSING,
                market_status=MarketStatus.UNKNOWN,
                is_inference=False,
                reasons=(
                    "Nessuna quotazione: bid/ask assenti o a zero. Strumento appena "
                    "attivato, non quotato sul conto, o mai aperto.",
                ),
                **comune,  # type: ignore[arg-type]
            )

        spread = tick.ask - tick.bid
        comune.update(
            bid=tick.bid,
            ask=tick.ask,
            spread=spread,
            spread_points=round(spread / info.point, 1) if info.point else None,
            spread_to_atr=round(spread / atr, 4) if atr and atr > 0 and spread >= 0 else None,
            last_tick_time=tick.time,
        )

        # --- 2. prezzo incoerente ---
        incoerenze: list[str] = []
        if not (math.isfinite(tick.bid) and math.isfinite(tick.ask)):
            incoerenze.append("Bid o ask non numerici.")
        elif tick.ask < tick.bid:
            incoerenze.append(f"Ask {tick.ask} sotto il bid {tick.bid}: quotazione incrociata.")
        if atr and atr > 0 and last_close:
            distanza = abs((tick.bid + tick.ask) / 2 - last_close) / atr
            if distanza > self._soglia_incoerenza:
                incoerenze.append(
                    f"Prezzo a {distanza:.0f} ATR dall'ultima chiusura (soglia "
                    f"{self._soglia_incoerenza:.0f}): più probabile un dato errato "
                    "che un movimento reale."
                )

        minimo, massimo, esatta = orologio.age_bounds(tick.time, now=now)
        if esatta and minimo < -TOLLERANZA_FUTURO_SECONDI:
            incoerenze.append(
                f"Tick datato {abs(minimo):.0f}s nel futuro: scarto dell'ora broker "
                "errato o orologio di sistema sbagliato."
            )
        if incoerenze:
            return QuoteQuality(
                status=QuoteQualityStatus.INCONSISTENT,
                market_status=MarketStatus.UNKNOWN,
                is_inference=False,
                reasons=tuple(incoerenze),
                tick_age_seconds=round(minimo, 1) if esatta else None,
                **comune,  # type: ignore[arg-type]
            )

        eta_esatta = round(minimo, 1) if esatta else None

        # --- 3. mercato chiuso: deduzione su due prove, nel caso più favorevole ---
        soglia_barre = max(self._soglia_chiuso, last_candle_timeframe.minutes * 60 * 2)
        barre_ferme = eta_candela is None or eta_candela > soglia_barre
        if minimo > self._soglia_chiuso and barre_ferme:
            precisione = "" if esatta else " (scarto ora broker ignoto: età minima garantita)"
            return QuoteQuality(
                status=QuoteQualityStatus.MARKET_CLOSED,
                market_status=MarketStatus.CLOSED_INFERRED,
                is_inference=True,
                reasons=(
                    f"Ultima quotazione di almeno {minimo / 3_600:.1f} ore fa e nessuna "
                    f"barra nuova{precisione}: mercato dedotto chiuso. MT5 non espone gli "
                    "orari di sessione (ADR 0011).",
                ),
                tick_age_seconds=eta_esatta,
                **comune,  # type: ignore[arg-type]
            )

        # --- 4. valido: solo con età esatta e dentro il limite ---
        if esatta and massimo <= self._max_eta:
            return QuoteQuality(
                status=QuoteQualityStatus.VALID,
                market_status=MarketStatus.OPEN_CONFIRMED,
                is_inference=False,
                reasons=(
                    f"Quotazione di {massimo:.0f}s fa, entro il limite di {self._max_eta:.0f}s.",
                ),
                tick_age_seconds=eta_esatta,
                **comune,  # type: ignore[arg-type]
            )

        # --- 5. tutto il resto: non utilizzabile ---
        motivo = (
            f"Quotazione vecchia di {minimo:.0f}s (limite {self._max_eta:.0f}s): flusso "
            "prezzi in ritardo o mercato in chiusura."
            if esatta
            else "Scarto dell'ora broker non noto: la freschezza della quotazione non è "
            "verificabile, quindi non può essere dichiarata valida (ADR 0015)."
        )
        return QuoteQuality(
            status=QuoteQualityStatus.STALE,
            market_status=MarketStatus.UNKNOWN,
            is_inference=False,
            reasons=(motivo,),
            tick_age_seconds=eta_esatta,
            **comune,  # type: ignore[arg-type]
        )
