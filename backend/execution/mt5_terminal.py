"""Terminale di trading MT5: l'**unico** modulo che chiama `order_check` e `order_send`.

È autorizzato esplicitamente nel test di sola lettura (`MODULI_AUTORIZZATI`). Ogni
chiamata passa dal thread serializzato del connettore (`run_serialized`), con lo
stesso lock e lo stesso timeout delle letture: un timeout diventa
`BrokerTimeoutError`, e il gateway lo tratta come esito **ignoto** da riconciliare.

Nessuna logica qui: richieste e risposte grezze, tradotte in `RispostaOrdine`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from backend.brokers.base import BrokerHealth
from backend.brokers.mt5.connector import MT5Connector
from backend.core.errors import BrokerError
from backend.schemas.account import DealSnapshot, PositionSnapshot
from backend.schemas.market import SymbolInfo, Tick

#: Costanti MT5 (documentazione MQL5), ripetute per poter testare senza il pacchetto.
TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 6
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2


@dataclass(frozen=True)
class RispostaOrdine:
    retcode: int | None
    comment: str = ""
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    request_id: int = 0


class TradingTerminal(Protocol):
    """Ciò che il gateway usa: scrittura, più le letture per riconciliare."""

    async def order_check(self, request: dict[str, Any]) -> RispostaOrdine: ...
    async def order_send(self, request: dict[str, Any]) -> RispostaOrdine: ...
    async def filling(self, symbol: str) -> int: ...
    async def positions(self, symbol: str | None = None) -> tuple[PositionSnapshot, ...]: ...
    async def deals(self, da: datetime, a: datetime) -> tuple[DealSnapshot, ...]: ...
    async def symbol(self, symbol: str) -> SymbolInfo: ...
    async def tick(self, symbol: str) -> Tick: ...
    async def health(self) -> BrokerHealth: ...


class MT5TradingTerminal:
    def __init__(self, connector: MT5Connector) -> None:
        try:
            import MetaTrader5
        except ImportError as exc:  # pragma: no cover - dipende dalla macchina
            raise BrokerError(
                "Pacchetto MetaTrader5 non disponibile: niente esecuzione reale."
            ) from exc
        self._mt5 = MetaTrader5
        self._c = connector

    @staticmethod
    def _risposta(r: Any) -> RispostaOrdine:
        return RispostaOrdine(
            retcode=int(getattr(r, "retcode", -1)),
            comment=str(getattr(r, "comment", "") or ""),
            deal=int(getattr(r, "deal", 0) or 0),
            order=int(getattr(r, "order", 0) or 0),
            volume=float(getattr(r, "volume", 0.0) or 0.0),
            price=float(getattr(r, "price", 0.0) or 0.0),
            request_id=int(getattr(r, "request_id", 0) or 0),
        )

    async def order_check(self, request: dict[str, Any]) -> RispostaOrdine:
        r = await self._c.run_serialized("order_check", self._mt5.order_check, request)
        if r is None:
            codice, testo = await self._c.last_error()
            return RispostaOrdine(retcode=None, comment=f"order_check: [{codice}] {testo}")
        return self._risposta(r)

    async def order_send(self, request: dict[str, Any]) -> RispostaOrdine:
        r = await self._c.run_serialized("order_send", self._mt5.order_send, request)
        if r is None:
            # Nessuna risposta: l'ordine può essere partito o no. Esito ignoto.
            codice, testo = await self._c.last_error()
            return RispostaOrdine(retcode=None, comment=f"order_send: [{codice}] {testo}")
        return self._risposta(r)

    async def filling(self, symbol: str) -> int:
        info = await self._c.run_serialized("symbol_info", self._mt5.symbol_info, symbol)
        modi = int(getattr(info, "filling_mode", 0) or 0)
        # Bit 1 = FOK, bit 2 = IOC. IOC accetta l'esecuzione parziale, che il gateway gestisce.
        if modi & 2:
            return ORDER_FILLING_IOC
        if modi & 1:
            return ORDER_FILLING_FOK
        return ORDER_FILLING_RETURN

    async def positions(self, symbol: str | None = None) -> tuple[PositionSnapshot, ...]:
        return await self._c.get_positions(symbol)

    async def deals(self, da: datetime, a: datetime) -> tuple[DealSnapshot, ...]:
        return await self._c.get_deals(da, a)

    async def symbol(self, symbol: str) -> SymbolInfo:
        return await self._c.get_symbol(symbol)

    async def tick(self, symbol: str) -> Tick:
        return await self._c.get_tick(symbol)

    async def health(self) -> BrokerHealth:
        return await self._c.health()
