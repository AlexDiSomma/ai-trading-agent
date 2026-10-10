"""Interfacce del broker.

Perché dei `Protocol` e non una classe base: il resto del sistema deve poter
essere scritto e testato senza MetaTrader 5 installato. Lo scanner, il risk
engine e il backtest dipendono da queste firme, non da XM né da MT5 — se un
giorno si aggiunge un secondo broker, nessuno di quei moduli cambia.

La separazione in **tre** protocolli è deliberata:

* `MarketDataReader`  — leggere prezzi;
* `AccountDataReader` — leggere conto e posizioni;
* `OrderWriter`       — inviare ordini.

Fino alla fase 11 nel sistema non esiste **nessuna** implementazione di
`OrderWriter`. Il connettore MT5 di oggi soddisfa solo i primi due protocolli:
l'invio di un ordine non è disabilitato da un flag che qualcuno può invertire,
semplicemente non c'è codice che lo faccia. Vedi
docs/adr/0004-broker-read-only-per-costruzione.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.core.enums import HealthStatus, Timeframe
from backend.schemas.account import (
    AccountSnapshot,
    PendingOrderSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)
from backend.schemas.market import CandleSeries, SymbolInfo, Tick


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    """Stato della connessione al broker."""

    status: HealthStatus
    detail: str
    connected: bool
    latency_ms: float | None = None
    #: Ora del server del broker, che **non** coincide con UTC.
    server_time: datetime | None = None
    terminal_build: int | None = None
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@runtime_checkable
class MarketDataReader(Protocol):
    """Lettura dei dati di mercato."""

    async def list_symbols(self, pattern: str | None = None) -> tuple[str, ...]:
        """Simboli disponibili presso il broker."""
        ...

    async def get_symbol(self, symbol: str) -> SymbolInfo:
        """Specifiche di contratto, lette dal broker a ogni richiesta."""
        ...

    async def get_tick(self, symbol: str) -> Tick:
        """Ultima quotazione. Chi la usa deve controllarne l'età."""
        ...

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        *,
        end: datetime | None = None,
    ) -> CandleSeries:
        """Ultime `count` candele chiuse, più quella in formazione.

        La serie restituita dichiara con `last_candle_closed` se l'ultima barra
        è conclusa: è l'unico modo di evitare il look-ahead per distrazione.
        """
        ...


@runtime_checkable
class AccountDataReader(Protocol):
    """Lettura di conto, posizioni e ordini pendenti."""

    async def get_account(self) -> AccountSnapshot: ...

    async def get_positions(self, symbol: str | None = None) -> tuple[PositionSnapshot, ...]: ...

    async def get_pending_orders(
        self, symbol: str | None = None
    ) -> tuple[PendingOrderSnapshot, ...]: ...

    async def get_portfolio(self) -> PortfolioSnapshot:
        """Conto e posizioni nello stesso istante, per coerenza del rischio."""
        ...


@runtime_checkable
class ReadOnlyBroker(MarketDataReader, AccountDataReader, Protocol):
    """Broker in sola lettura: tutto ciò che serve fino alla fase 10 compresa."""

    @property
    def name(self) -> str: ...

    @property
    def is_simulated(self) -> bool:
        """True se i dati non vengono dal broker reale.

        Deve risalire fino alla dashboard: un numero simulato presentato come
        reale è peggio di un numero assente.
        """
        ...

    async def connect(self) -> BrokerHealth: ...

    async def disconnect(self) -> None: ...

    async def health(self) -> BrokerHealth: ...

    async def server_time(self) -> datetime:
        """Ora del server del broker.

        Sessioni autorizzate, reset della perdita giornaliera e filtro eventi si
        calcolano su questa, non sull'ora del PC dell'utente.
        """
        ...


@runtime_checkable
class OrderWriter(Protocol):
    """Invio di ordini. **Nessuna implementazione prima della fase 11.**

    Il protocollo è dichiarato qui per fissare la forma del contratto e per
    rendere evidente, a chi legge il codice, che la scrittura è un'interfaccia
    separata: un componente riceve la capacità di operare solo se gli viene
    passato un oggetto che soddisfa *questo* protocollo, e oggi non esiste.

    Il parametro `approval` non è decorativo: l'Execution Engine accetterà solo
    piani già validati dal Risk Engine, così nessun percorso alternativo può
    inviare un ordine aggirando i limiti dell'utente
    (docs/adr/0005-risk-engine-non-aggirabile.md).
    """

    async def send_order(self, approved_plan: object, approval: object) -> object: ...

    async def modify_position(self, ticket: int, approval: object) -> object: ...

    async def close_position(
        self, ticket: int, volume: float | None, approval: object
    ) -> object: ...
