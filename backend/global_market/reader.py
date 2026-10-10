"""Lettura dal broker dell'universo di riferimento globale.

Per ogni strumento di `config/global_universe.UNIVERSO`:

1. **identità** — la specifica viene riletta e il canonico ricavato dal sistema
   (`InstrumentRegistry`) deve coincidere con quello dichiarato. Se `GOLD` un
   giorno diventasse un'altra cosa, lo strumento risulterebbe mancante invece di
   essere usato al posto di un altro;
2. **quotazione** — il tick valutato da `QuoteQualityEvaluator`: è da qui che si
   sa se il mercato di quello strumento è aperto confermato, chiuso dedotto o
   ignoto;
3. **candele** — D1, H4, H1, M15, solo barre chiuse. Ogni serie riceve uno stato:
   `CLOSED` se il mercato è dedotto chiuso, `FRESH` se l'ultima barra chiusa è
   recente con età **verificata** (ADR 0015), `UNVERIFIED` se l'età non è
   verificabile, `STALE` se il mercato risulta aperto ma le barre sono ferme.

Sola lettura: candele, tick e specifiche. Nessun calcolatore, nessun ordine.
Effetto collaterale dichiarato: MT5 richiede che un simbolo sia nel Market Watch
per leggerne i dati, quindi i 37 strumenti vengono aggiunti alla lista del
terminale (non al conto).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.brokers.base import ReadOnlyBroker
from backend.config.global_universe import UNIVERSO, StrumentoDiRiferimento
from backend.core.clock import Clock, SystemClock
from backend.core.enums import MarketStatus, SeriesStatus, Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.global_market import parametri as par
from backend.global_market.engine import IngressoStrumento
from backend.global_market.features import SerieDiRiferimento
from backend.market_data.broker_time import BrokerClock
from backend.market_data.instruments import InstrumentRegistry
from backend.market_data.quote_quality import QuoteQualityEvaluator

logger = get_logger(__name__)

#: Ritardo tollerato oltre la durata della barra corrente prima di dirla ferma.
TOLLERANZA_BARRA_SECONDI = 120.0


@dataclass(frozen=True)
class LetturaGlobale:
    ingressi: tuple[IngressoStrumento, ...]
    as_of: datetime
    #: Lo stesso istante in ora broker; `None` se lo scarto non è noto.
    limite_broker: datetime | None
    broker_offset_seconds: float | None
    broker_time_source: str
    simulated: bool
    source: str


class GlobalDataReader:
    def __init__(
        self,
        broker: ReadOnlyBroker,
        instruments: InstrumentRegistry,
        broker_clock: BrokerClock,
        *,
        max_tick_age_seconds: float,
        clock: Clock | None = None,
        universe: Sequence[StrumentoDiRiferimento] = UNIVERSO,
        attesa_rilettura_secondi: float = 0.5,
        barre: int = par.BARRE_RICHIESTE,
    ) -> None:
        #: Barre per serie. Il replay della fase 5B ne chiede di più, per poter
        #: tagliare la storia a un istante passato e avere ancora serie complete.
        self._barre = barre
        #: Pausa prima di rileggere una serie ferma a mercato aperto (vedi sotto).
        self._attesa_rilettura = attesa_rilettura_secondi
        self._broker = broker
        self._strumenti = instruments
        self._ora_broker = broker_clock
        self._valutatore = QuoteQualityEvaluator(max_tick_age_seconds=max_tick_age_seconds)
        self._clock = clock or SystemClock()
        self._universo = tuple(universe)

    async def leggi(self) -> LetturaGlobale:
        adesso = self._clock.now()
        stima = self._ora_broker.estimate(now=adesso)
        limite = (
            adesso + timedelta(seconds=stima.offset_seconds)
            if stima.reliable and stima.offset_seconds is not None
            else None
        )
        ingressi = [await self._leggi_strumento(s, adesso) for s in self._universo]
        return LetturaGlobale(
            ingressi=tuple(ingressi),
            as_of=adesso,
            limite_broker=limite,
            broker_offset_seconds=stima.offset_seconds if stima.reliable else None,
            broker_time_source=stima.source,
            simulated=self._broker.is_simulated,
            source=self._broker.name,
        )

    async def _leggi_strumento(
        self, rif: StrumentoDiRiferimento, adesso: datetime
    ) -> IngressoStrumento:
        mancante = dict.fromkeys(par.TIMEFRAMES, SeriesStatus.MISSING)

        # --- 1. identità ---
        try:
            strumento = await self._strumenti.by_broker_symbol(rif.broker_symbol)
            info = await self._broker.get_symbol(rif.broker_symbol)
        except TradingAgentError as errore:
            return IngressoStrumento(
                canonical=rif.canonical,
                broker_symbol=rif.broker_symbol,
                group=rif.group,
                stati=mancante,
                reason=f"Simbolo non leggibile: {errore}",
            )
        if strumento.canonical != rif.canonical:
            logger.error(
                "globale.identita_diversa",
                simbolo=rif.broker_symbol,
                atteso=rif.canonical,
                trovato=strumento.canonical,
            )
            return IngressoStrumento(
                canonical=rif.canonical,
                broker_symbol=rif.broker_symbol,
                group=rif.group,
                stati=mancante,
                reason=(
                    f"Il broker identifica {rif.broker_symbol} come {strumento.canonical}, "
                    f"non {rif.canonical}: escluso invece di usarlo al posto di un altro."
                ),
            )

        # --- 2. candele ---
        serie: dict[Timeframe, SerieDiRiferimento] = {}
        #: Apertura dell'ultima barra ricevuta (quella in formazione).
        ultima_apertura: dict[Timeframe, datetime] = {}
        note: dict[Timeframe, str] = {}
        for tf in par.TIMEFRAMES:
            try:
                letta = await self._broker.get_candles(rif.broker_symbol, tf, self._barre)
            except TradingAgentError as errore:
                note[tf] = str(errore)
                continue
            chiuse = letta.closed
            if letta.candles:
                ultima_apertura[tf] = letta.candles[-1].time
            serie[tf] = SerieDiRiferimento(
                canonical=rif.canonical,
                group=rif.group,
                timeframe=tf,
                tempi=tuple(c.time for c in chiuse),
                massimi=tuple(c.high for c in chiuse),
                minimi=tuple(c.low for c in chiuse),
                chiusure=tuple(c.close for c in chiuse),
            )

        # --- 3. quotazione e stato del mercato ---
        tick = None
        try:
            tick = await self._broker.get_tick(rif.broker_symbol)
        except TradingAgentError:
            tick = None
        m15 = serie.get(Timeframe.M15)
        # Il tick si valuta contro l'ora in cui è stato LETTO, non contro `adesso`
        # (inizio della lettura). Difetto trovato sul conto reale il 2026-10-04:
        # con 37 strumenti letti in sequenza, i tick degli ultimi risultavano
        # "dal futuro" fino a 165 secondi e venivano dichiarati incoerenti.
        letto_alle = self._clock.now()
        qualita = self._valutatore.evaluate(
            info,
            tick,
            now=letto_alle,
            broker_clock=self._ora_broker,
            last_candle_time=m15.tempi[-1] if m15 and len(m15) else None,
            last_candle_timeframe=Timeframe.M15,
            quote_missing=tick is None,
        )
        mercato = qualita.market_status

        # --- 4. stato di ogni serie ---
        # La freschezza si misura sull'apertura della barra IN FORMAZIONE, non sulla
        # fine dell'ultima chiusa: alla riapertura del lunedì l'ultima H4 chiusa è
        # quella del venerdì, ma la serie è viva se la barra corrente è appena nata.
        stati: dict[Timeframe, SeriesStatus] = {}
        eta: dict[Timeframe, float | None] = {}
        for tf in par.TIMEFRAMES:
            s = serie.get(tf)
            if s is None or not len(s) or tf not in ultima_apertura:
                stati[tf] = SeriesStatus.MISSING
                continue
            minima, _, esatta = self._ora_broker.age_bounds(ultima_apertura[tf], now=letto_alle)
            if (
                mercato is MarketStatus.OPEN_CONFIRMED
                and esatta
                and minima > tf.minutes * 60 + TOLLERANZA_BARRA_SECONDI
            ):
                # Misurato il 2026-10-05: alla prima richiesta dopo la riapertura MT5
                # può restituire lo storico locale non ancora sincronizzato (USDCAD H4
                # fermo a venerdì mentre H1 e M15 erano vivi). Una sola rilettura dopo
                # una breve pausa; se è ancora ferma, la serie resta STALE ed esclusa.
                await asyncio.sleep(self._attesa_rilettura)
                try:
                    riletta = await self._broker.get_candles(rif.broker_symbol, tf, self._barre)
                except TradingAgentError:
                    riletta = None
                if riletta is not None and riletta.candles:
                    chiuse = riletta.closed
                    serie[tf] = s = SerieDiRiferimento(
                        canonical=rif.canonical,
                        group=rif.group,
                        timeframe=tf,
                        tempi=tuple(c.time for c in chiuse),
                        massimi=tuple(c.high for c in chiuse),
                        minimi=tuple(c.low for c in chiuse),
                        chiusure=tuple(c.close for c in chiuse),
                    )
                    ultima_apertura[tf] = riletta.candles[-1].time
                    minima, _, esatta = self._ora_broker.age_bounds(
                        ultima_apertura[tf], now=self._clock.now()
                    )
            eta[tf] = round(minima, 1) if esatta else None
            if mercato is MarketStatus.CLOSED_INFERRED:
                stati[tf] = SeriesStatus.CLOSED
            elif not esatta:
                stati[tf] = SeriesStatus.UNVERIFIED
            elif minima <= tf.minutes * 60 + TOLLERANZA_BARRA_SECONDI:
                stati[tf] = SeriesStatus.FRESH
            else:
                stati[tf] = SeriesStatus.STALE
                note[tf] = (
                    f"Barra corrente aperta {minima / 60:.0f} minuti fa (durata {tf.minutes}) "
                    "con mercato non dichiarato chiuso: barre ferme, serie esclusa."
                )

        return IngressoStrumento(
            canonical=rif.canonical,
            broker_symbol=rif.broker_symbol,
            group=rif.group,
            market_status=mercato,
            quote_status=qualita.status,
            stati=stati,
            serie=serie,
            eta=eta,
            note=note,
            reason=" ".join(qualita.reasons),
        )
