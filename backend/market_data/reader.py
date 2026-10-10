"""Lettura dei dati di mercato per il resto del sistema.

Perché esiste questo strato invece di usare il connettore direttamente:

1. **Fa rispettare la whitelist.** Il connettore è broker-agnostico e legge
   qualsiasi simbolo; il perimetro di ciò su cui il sistema può ragionare è una
   decisione dell'utente. Il controllo sta qui, in un punto solo, e vale anche
   per la semplice lettura: se un simbolo non è autorizzato non deve nemmeno
   comparire in un'analisi, altrimenti prima o poi qualcuno lo userà.
2. **Dichiara la freschezza.** Restituisce `Fresh[...]`, con `as_of` e TTL, non
   valori nudi. Chi decide chiama `require_fresh()` e ottiene un errore invece
   di un dato vecchio.
3. **Isola il multi-timeframe.** La gerarchia D1/H4 -> H1 -> M30/M15 -> M5 è
   descritta qui una volta e riusata da scanner, agente AI e backtest.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.brokers.base import ReadOnlyBroker
from backend.config.risk_limits import RiskLimits
from backend.core.clock import Clock, SystemClock
from backend.core.enums import Timeframe
from backend.core.errors import SymbolNotAllowedError
from backend.core.freshness import Fresh
from backend.core.logging import get_logger
from backend.market_data.broker_time import BrokerClock
from backend.schemas.market import CandleSeries, SymbolInfo, Tick

logger = get_logger(__name__)

#: Gerarchia dei timeframe. L'ordine è dal lento al veloce: la lettura parte
#: sempre dalla struttura e scende, non viceversa. È la pipeline concettuale del
#: progetto applicata al singolo asset.
TIMEFRAME_HIERARCHY: tuple[Timeframe, ...] = (
    Timeframe.D1,
    Timeframe.H4,
    Timeframe.H1,
    Timeframe.M30,
    Timeframe.M15,
    Timeframe.M5,
)

#: Per quanto tempo un dato resta utilizzabile, per timeframe. Circa un terzo
#: della barra: oltre, il prezzo si è mosso abbastanza da rendere l'analisi
#: vecchia anche se la candela non è ancora chiusa.
_TTL_SECONDS: dict[Timeframe, float] = {
    Timeframe.M1: 20,
    Timeframe.M5: 100,
    Timeframe.M15: 300,
    Timeframe.M30: 600,
    Timeframe.H1: 1_200,
    Timeframe.H4: 4_800,
    Timeframe.D1: 28_800,
    Timeframe.W1: 86_400,
    Timeframe.MN1: 86_400,
}

#: Barre minime richieste: la EMA a 200 periodi non significa niente su 50 barre.
MIN_CANDLES = 260

#: Un tick è un'informazione sul presente per pochi secondi.
TICK_TTL_SECONDS = 15.0

#: Le specifiche di contratto cambiano raramente, ma cambiano (spread, stop
#: level, volume minimo): un'ora è un compromesso fra traffico e correttezza.
SYMBOL_TTL_SECONDS = 3_600.0


class MarketDataService:
    """Accesso ai dati di mercato dentro il perimetro autorizzato."""

    def __init__(
        self,
        broker: ReadOnlyBroker,
        limits: RiskLimits,
        *,
        clock: Clock | None = None,
        broker_clock: BrokerClock | None = None,
    ) -> None:
        self._broker = broker
        self._limits = limits
        self._clock = clock or SystemClock()
        self._broker_clock = broker_clock or BrokerClock(clock=self._clock)

    def now(self) -> datetime:
        """Istante corrente secondo l'orologio del servizio.

        Esposto perche' gli endpoint calcolano l'eta' dei dati: devono usare lo
        stesso orologio del servizio, altrimenti nei test con tempo fermo l'eta'
        risulterebbe enorme.
        """
        return self._clock.now()

    # ------------------------------------------------------------------
    # Perimetro
    # ------------------------------------------------------------------
    @property
    def watchlist(self) -> tuple[str, ...]:
        """Gli strumenti su cui il sistema è autorizzato a ragionare."""
        return self._limits.allowed_symbols

    def canonical_symbol(self, symbol: str) -> str | None:
        """Grafia ufficiale del broker per uno strumento autorizzato, o None.

        Pubblico perche' le rotte HTTP devono poter distinguere "non autorizzato"
        (403) da "non disponibile" (503) prima di chiamare l'analisi, senza
        frugare nei limiti di rischio.
        """
        return self._limits.canonical_symbol(symbol)

    def _require_allowed(self, symbol: str) -> str:
        """Verifica il perimetro e restituisce la grafia esatta del broker."""
        canonico = self._limits.canonical_symbol(symbol)
        if canonico is None:
            raise SymbolNotAllowedError(
                f"'{symbol}' non è fra gli strumenti autorizzati. "
                "Aggiungilo a RISK_ALLOWED_SYMBOLS se lo vuoi includere.",
                symbol=symbol,
                allowed=list(self._limits.allowed_symbols),
            )
        return canonico

    # ------------------------------------------------------------------
    # Letture
    # ------------------------------------------------------------------
    async def get_symbol(self, symbol: str) -> Fresh[SymbolInfo]:
        name = self._require_allowed(symbol)
        info = await self._broker.get_symbol(name)
        return Fresh(
            value=info,
            as_of=info.as_of,
            ttl_seconds=SYMBOL_TTL_SECONDS,
            source=f"{self._broker.name}:symbol_info",
        )

    async def get_tick(self, symbol: str) -> Fresh[Tick]:
        name = self._require_allowed(symbol)
        tick = await self._broker.get_tick(name)
        return Fresh(
            value=tick,
            as_of=self.tick_as_of(tick),
            ttl_seconds=TICK_TTL_SECONDS,
            source=f"{self._broker.name}:tick",
        )

    def tick_as_of(self, tick: Tick) -> datetime:
        """Istante UTC vero di un tick, o il più prudente possibile (ADR 0015).

        `tick.time` è ora del broker etichettata UTC. Con lo scarto noto si
        converte; senza, si assume il caso peggiore — il tick è vecchio quanto il
        fuso più lontano (14 ore) — così `Fresh.require_fresh` non può dichiarare
        fresco un dato di età ignota.
        """
        convertito = self._broker_clock.to_utc(tick.time)
        if convertito is not None:
            return convertito
        return tick.time - timedelta(seconds=BrokerClock.SCARTO_MASSIMO_SECONDI)

    async def get_candles(
        self, symbol: str, timeframe: Timeframe, count: int = MIN_CANDLES
    ) -> Fresh[CandleSeries]:
        name = self._require_allowed(symbol)
        series = await self._broker.get_candles(name, timeframe, count)
        if not series.has_enough(min(count, 50)):
            logger.warning(
                "market_data.serie_corta",
                symbol=name,
                timeframe=str(timeframe),
                candele_chiuse=len(series.closed),
                motivo="indicatori su serie troppo corte restituiscono numeri privi di senso",
            )
        return Fresh(
            value=series,
            as_of=series.as_of,
            ttl_seconds=_TTL_SECONDS[timeframe],
            source=f"{self._broker.name}:candles",
        )

    async def get_multi_timeframe(
        self,
        symbol: str,
        timeframes: tuple[Timeframe, ...] = TIMEFRAME_HIERARCHY,
        count: int = MIN_CANDLES,
    ) -> dict[Timeframe, Fresh[CandleSeries]]:
        """Serie su più timeframe per lo stesso strumento.

        Le letture sono sequenziali perché il connettore MT5 serializza comunque
        le chiamate: lanciarle in parallelo darebbe l'illusione della
        concorrenza senza guadagno, e renderebbe gli errori più difficili da
        attribuire.
        """
        name = self._require_allowed(symbol)
        result: dict[Timeframe, Fresh[CandleSeries]] = {}
        for timeframe in timeframes:
            result[timeframe] = await self.get_candles(name, timeframe, count)
        return result
