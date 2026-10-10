"""Acquisizione delle candele su più timeframe.

La gerarchia è quella del progetto: **D1 → H4 → H1 → M15 → M5**, dal lento al
veloce. L'ordine non è decorativo: si parte dalla struttura e si scende, perché
un setup su M15 che contraddice la struttura su D1 non è un'opportunità ma un
controsenso.

Quante barre chiedere non è un numero arbitrario. La EMA200 richiede 200 barre
per esistere, e una EMA appena nata è ancora dominata dal suo valore iniziale:
per averla stabile servono circa 250 barre. Con meno, gli indicatori restituiscono
numeri plausibili e privi di significato — per questo la qualità del dato è un
campo del risultato e non un dettaglio implementativo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.core.clock import Clock, SystemClock
from backend.core.enums import DataQuality, Timeframe
from backend.core.errors import TradingAgentError
from backend.core.freshness import Fresh
from backend.core.logging import get_logger
from backend.market_data.reader import MarketDataService
from backend.schemas.market import CandleSeries

logger = get_logger(__name__)

#: Gerarchia di analisi, dal lento al veloce.
TIMEFRAMES_ANALISI: tuple[Timeframe, ...] = (
    Timeframe.D1,
    Timeframe.H4,
    Timeframe.H1,
    Timeframe.M15,
    Timeframe.M5,
)

#: Barre richieste per timeframe. Più del minimo teorico: una EMA200 appena nata
#: è ancora influenzata dal proprio seeding, e serve margine anche per il rango
#: percentile dell'ampiezza di Bollinger (120 barre di storia).
BARRE_RICHIESTE: dict[Timeframe, int] = {
    Timeframe.MN1: 120,
    Timeframe.W1: 260,
    Timeframe.D1: 300,
    Timeframe.H4: 350,
    Timeframe.H1: 350,
    Timeframe.M30: 350,
    Timeframe.M15: 350,
    Timeframe.M5: 350,
    Timeframe.M1: 350,
}

#: Sotto questa soglia la EMA200 non esiste: l'analisi è parziale.
BARRE_PER_EMA200 = 210

#: Sotto questa soglia non si calcola nulla di sensato, nemmeno a breve termine.
BARRE_MINIME_ASSOLUTE = 60

#: Gravità relativa dei problemi di qualità: il peggiore vince.
_GRAVITA: dict[DataQuality, int] = {
    DataQuality.GOOD: 0,
    DataQuality.SIMULATED: 1,
    DataQuality.SHORT_HISTORY: 2,
    DataQuality.STALE: 3,
    DataQuality.INSUFFICIENT: 4,
}


@dataclass(frozen=True, slots=True)
class TimeframeCandles:
    """Candele di un timeframe con il giudizio sulla loro utilizzabilità."""

    timeframe: Timeframe
    fresh: Fresh[CandleSeries]
    quality: DataQuality
    note: str = ""

    @property
    def series(self) -> CandleSeries:
        return self.fresh.value

    @property
    def closed_bars(self) -> int:
        return len(self.fresh.value.closed)

    @property
    def last_closed_at(self) -> datetime | None:
        chiuse = self.fresh.value.closed
        return chiuse[-1].time if chiuse else None

    @property
    def usable(self) -> bool:
        """Utilizzabile per un'analisi completa."""
        return self.quality in (DataQuality.GOOD, DataQuality.SIMULATED)


def peggiore(qualita: list[DataQuality]) -> DataQuality:
    """La qualità peggiore fra quelle rilevate."""
    if not qualita:
        return DataQuality.GOOD
    return max(qualita, key=lambda q: _GRAVITA[q])


class CandleIngestion:
    """Scarica le candele e ne valuta la qualità, timeframe per timeframe."""

    def __init__(self, market: MarketDataService, *, clock: Clock | None = None) -> None:
        self._market = market
        self._clock = clock or SystemClock()

    async def fetch_one(
        self, symbol: str, timeframe: Timeframe, count: int | None = None
    ) -> TimeframeCandles:
        """Candele di un singolo timeframe, con qualità valutata."""
        richieste = count or BARRE_RICHIESTE.get(timeframe, 300)
        fresh = await self._market.get_candles(symbol, timeframe, richieste)
        qualita, nota = self._valuta(fresh, richieste)
        return TimeframeCandles(timeframe=timeframe, fresh=fresh, quality=qualita, note=nota)

    async def fetch_all(
        self,
        symbol: str,
        timeframes: tuple[Timeframe, ...] = TIMEFRAMES_ANALISI,
    ) -> dict[Timeframe, TimeframeCandles]:
        """Candele su tutta la gerarchia.

        Le letture sono **sequenziali** perché il connettore MT5 serializza
        comunque le chiamate su un thread solo: lanciarle in parallelo darebbe
        l'illusione della concorrenza e renderebbe gli errori più difficili da
        attribuire a un timeframe.

        Un timeframe che fallisce non ferma gli altri: l'analisi resta possibile
        in forma ridotta, e il contesto dichiara cosa manca.
        """
        risultato: dict[Timeframe, TimeframeCandles] = {}
        for timeframe in timeframes:
            try:
                risultato[timeframe] = await self.fetch_one(symbol, timeframe)
            except TradingAgentError as errore:
                logger.warning(
                    "ingestione.timeframe_non_disponibile",
                    symbol=symbol,
                    timeframe=str(timeframe),
                    motivo=str(errore),
                )
        return risultato

    def _valuta(self, fresh: Fresh[CandleSeries], richieste: int) -> tuple[DataQuality, str]:
        """Qualità del dato e motivo, in italiano."""
        serie = fresh.value
        chiuse = len(serie.closed)
        adesso = self._clock.now()
        problemi: list[DataQuality] = []
        note: list[str] = []

        if chiuse < BARRE_MINIME_ASSOLUTE:
            problemi.append(DataQuality.INSUFFICIENT)
            note.append(
                f"solo {chiuse} barre chiuse: sotto {BARRE_MINIME_ASSOLUTE} "
                "nessun indicatore ha significato"
            )
        elif chiuse < BARRE_PER_EMA200:
            problemi.append(DataQuality.SHORT_HISTORY)
            note.append(
                f"{chiuse} barre chiuse su {richieste} richieste: la EMA200 non è "
                "disponibile o non è stabile"
            )

        if fresh.is_stale(adesso):
            problemi.append(DataQuality.STALE)
            note.append(
                f"dato prodotto {fresh.age(adesso):.0f}s fa, oltre il limite di "
                f"{fresh.ttl_seconds:.0f}s"
            )

        if "mock" in serie.source:
            problemi.append(DataQuality.SIMULATED)
            note.append("prezzi simulati: nessun valore ha significato di mercato")

        return peggiore(problemi), "; ".join(note)
