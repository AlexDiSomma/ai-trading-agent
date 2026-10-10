"""Traduzione fra MetaTrader 5 e il dominio.

Tutto il sapere su "com'è fatto MT5" sta in questo file: costanti numeriche,
nomi dei campi, unità di misura. Il resto del backend parla solo di
`Candle`, `SymbolInfo`, `PositionSnapshot`.

Le costanti sono scritte come letterali e **non** importate dal pacchetto
`MetaTrader5`: così i test, il mock e l'intera pipeline girano anche su una
macchina senza MT5 installato (quindi anche in CI e in Docker su Linux).
I valori provengono dalla documentazione ufficiale MQL5 e sono stabili da anni;
`backend/tests/unit/test_mt5_mapping.py` li verifica contro il pacchetto vero
quando questo è presente, così una divergenza futura non passa inosservata.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.core.enums import PositionSide, Timeframe
from backend.core.errors import BrokerError
from backend.schemas.account import (
    AccountSnapshot,
    DealSnapshot,
    PendingOrderSnapshot,
    PositionSnapshot,
)
from backend.schemas.market import Candle, SymbolInfo, Tick

# ----------------------------------------------------------------------
# Costanti MT5
# ----------------------------------------------------------------------
MT5_TIMEFRAMES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 16385,
    Timeframe.H4: 16388,
    Timeframe.D1: 16408,
    Timeframe.W1: 32769,
    Timeframe.MN1: 49153,
}

POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

#: Usate solo dal calcolatore `order_calc_profit`, che non invia ordini.
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

#: `entry` di un deal: se l'operazione apre, chiude o inverte una posizione.
#: Solo le uscite realizzano il risultato, quindi solo quelle consumano il
#: budget giornaliero dell'utente.
DEAL_ENTRY_NAMES: dict[int, str] = {
    0: "IN",
    1: "OUT",
    2: "INOUT",
    3: "OUT_BY",
}

#: Tipi di deal. I tipi da 2 in su non sono operazioni di mercato (versamenti,
#: commissioni, correzioni): entrano nell'equity ma non sono trade e non vanno
#: contati nelle statistiche di performance.
DEAL_TYPE_NAMES: dict[int, str] = {
    0: "BUY",
    1: "SELL",
    2: "BALANCE",
    3: "CREDIT",
    4: "CHARGE",
    5: "CORRECTION",
    6: "BONUS",
    7: "COMMISSION",
    8: "COMMISSION_DAILY",
    9: "COMMISSION_MONTHLY",
    10: "COMMISSION_AGENT_DAILY",
    11: "COMMISSION_AGENT_MONTHLY",
    12: "INTEREST",
    13: "BUY_CANCELED",
    14: "SELL_CANCELED",
    15: "DIVIDEND",
    16: "DIVIDEND_FRANKED",
    17: "TAX",
}

#: `account_info().trade_mode`: tipo di conto dichiarato dal broker.
ACCOUNT_TRADE_MODE_NAMES: dict[int, str] = {
    0: "DEMO",
    1: "CONTEST",
    2: "REAL",
}

ORDER_TYPE_NAMES: dict[int, str] = {
    0: "BUY",
    1: "SELL",
    2: "BUY_LIMIT",
    3: "SELL_LIMIT",
    4: "BUY_STOP",
    5: "SELL_STOP",
    6: "BUY_STOP_LIMIT",
    7: "SELL_STOP_LIMIT",
}


def to_mt5_timeframe(timeframe: Timeframe) -> int:
    try:
        return MT5_TIMEFRAMES[timeframe]
    except KeyError as exc:  # pragma: no cover - difeso dall'enum
        raise BrokerError(f"Timeframe non supportato da MT5: {timeframe}") from exc


def from_broker_time(timestamp: float | int | datetime) -> datetime:
    """Converte un istante MT5 in datetime aware.

    **Avvertenza importante.** MT5 restituisce epoch seconds che rappresentano
    l'ora del *server del broker*, non UTC: con XM lo scarto è tipicamente di
    2 o 3 ore a seconda dell'ora legale. Qui il valore viene etichettato come
    UTC per avere un datetime aware e confrontabile, ma resta ora-broker.

    Conseguenza pratica: questi timestamp vanno usati per ordinare le candele e
    per i confronti fra dati dello stesso broker, **non** per decidere se un
    evento macro pubblicato a un'ora UTC sia passato. L'allineamento esplicito
    (stima dell'offset dal confronto con l'ora di sistema) arriva con il modulo
    calendario, fase 4, e viene registrato come decisione in DECISIONI.md.
    """
    if isinstance(timestamp, datetime):
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    return datetime.fromtimestamp(float(timestamp), UTC)


# ----------------------------------------------------------------------
# Conversioni verso i DTO
# ----------------------------------------------------------------------
def to_symbol_info(raw: Any, *, as_of: datetime) -> SymbolInfo:
    """Da `mt5.symbol_info()` a `SymbolInfo`.

    `trade_tick_value` e `trade_contract_size` sono quelli dichiarati dal
    broker: sono la base del calcolo della size e non vanno mai sostituiti da
    costanti nostre.
    """
    point = float(getattr(raw, "point", 0.0) or 0.0)
    tick_size = float(getattr(raw, "trade_tick_size", 0.0) or 0.0) or point
    return SymbolInfo(
        name=str(raw.name),
        description=str(getattr(raw, "description", "") or ""),
        path=str(getattr(raw, "path", "") or ""),
        currency_base=str(getattr(raw, "currency_base", "") or ""),
        currency_profit=str(getattr(raw, "currency_profit", "") or ""),
        currency_margin=str(getattr(raw, "currency_margin", "") or ""),
        digits=int(getattr(raw, "digits", 0) or 0),
        point=point,
        contract_size=float(getattr(raw, "trade_contract_size", 0.0) or 0.0),
        tick_size=tick_size,
        tick_value=float(getattr(raw, "trade_tick_value", 0.0) or 0.0),
        volume_min=float(getattr(raw, "volume_min", 0.0) or 0.0),
        volume_max=float(getattr(raw, "volume_max", 0.0) or 0.0),
        volume_step=float(getattr(raw, "volume_step", 0.0) or 0.0),
        spread_points=int(getattr(raw, "spread", 0) or 0),
        stops_level_points=int(getattr(raw, "trade_stops_level", 0) or 0),
        freeze_level_points=int(getattr(raw, "trade_freeze_level", 0) or 0),
        # `trade_mode` 0 = disabilitato, 3 = solo chiusura, 4 = pieno. Per
        # aprire una posizione serve il modo pieno; il valore grezzo viene
        # conservato perché "solo chiusura" richiede un trattamento diverso.
        trade_mode=int(getattr(raw, "trade_mode", 4) or 0),
        swap_mode=int(getattr(raw, "swap_mode", 0) or 0),
        swap_long=float(getattr(raw, "swap_long", 0.0) or 0.0),
        swap_short=float(getattr(raw, "swap_short", 0.0) or 0.0),
        swap_rollover3days=int(getattr(raw, "swap_rollover3days", 3) or 3),
        trade_allowed=int(getattr(raw, "trade_mode", 0) or 0) == 4,
        visible=bool(getattr(raw, "visible", True)),
        as_of=as_of,
    )


def to_tick(raw: Any, *, symbol: str) -> Tick:
    """Da `mt5.symbol_info_tick()` a `Tick`."""
    return Tick(
        symbol=symbol,
        time=from_broker_time(getattr(raw, "time", 0)),
        bid=float(raw.bid),
        ask=float(raw.ask),
        last=float(getattr(raw, "last", 0.0) or 0.0) or None,
        volume=float(getattr(raw, "volume", 0.0) or 0.0),
    )


def to_candles(rates: Any) -> tuple[Candle, ...]:
    """Da `mt5.copy_rates_*()` a candele.

    Le righe arrivano come array strutturato numpy (o come dizionari dal mock):
    in entrambi i casi si accede per nome di campo, mai per posizione — un
    riordino dei campi nel pacchetto non deve poter scambiare `high` con `low`.
    """
    if rates is None:
        return ()
    candles: list[Candle] = []
    for row in rates:
        candles.append(
            Candle(
                time=from_broker_time(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                tick_volume=float(row["tick_volume"]),
                spread=int(row["spread"]),
                real_volume=float(row["real_volume"]),
            )
        )
    return tuple(candles)


def to_account_snapshot(raw: Any, *, as_of: datetime) -> AccountSnapshot:
    """Da `mt5.account_info()` a `AccountSnapshot`."""
    return AccountSnapshot(
        login=int(raw.login),
        server=str(getattr(raw, "server", "") or ""),
        name=str(getattr(raw, "name", "") or ""),
        company=str(getattr(raw, "company", "") or ""),
        currency=str(getattr(raw, "currency", "") or ""),
        leverage=int(getattr(raw, "leverage", 1) or 1),
        balance=float(raw.balance),
        equity=float(raw.equity),
        margin=float(getattr(raw, "margin", 0.0) or 0.0),
        margin_free=float(getattr(raw, "margin_free", 0.0) or 0.0),
        margin_level=float(getattr(raw, "margin_level", 0.0) or 0.0),
        profit=float(getattr(raw, "profit", 0.0) or 0.0),
        trade_allowed=bool(getattr(raw, "trade_allowed", True)),
        trade_expert_allowed=bool(getattr(raw, "trade_expert", True)),
        # Se il campo manca si resta su UNKNOWN, che il dominio tratta come
        # denaro reale: in dubbio, la prudenza sta da quel lato.
        account_type=ACCOUNT_TRADE_MODE_NAMES.get(  # type: ignore[arg-type]
            int(getattr(raw, "trade_mode", -1)), "UNKNOWN"
        ),
        as_of=as_of,
    )


def to_position_snapshot(raw: Any, *, as_of: datetime) -> PositionSnapshot:
    """Da `mt5.positions_get()` a `PositionSnapshot`."""
    position_type = int(getattr(raw, "type", POSITION_TYPE_BUY))
    stop_loss = float(getattr(raw, "sl", 0.0) or 0.0)
    take_profit = float(getattr(raw, "tp", 0.0) or 0.0)
    return PositionSnapshot(
        ticket=int(raw.ticket),
        symbol=str(raw.symbol),
        side=PositionSide.BUY if position_type == POSITION_TYPE_BUY else PositionSide.SELL,
        volume=float(raw.volume),
        price_open=float(raw.price_open),
        price_current=float(getattr(raw, "price_current", raw.price_open)),
        # MT5 usa 0.0 per "non impostato": tradurlo in None evita che uno stop
        # assente venga letto come uno stop a prezzo zero.
        stop_loss=stop_loss or None,
        take_profit=take_profit or None,
        profit=float(getattr(raw, "profit", 0.0) or 0.0),
        swap=float(getattr(raw, "swap", 0.0) or 0.0),
        commission=float(getattr(raw, "commission", 0.0) or 0.0),
        magic=int(getattr(raw, "magic", 0) or 0),
        comment=str(getattr(raw, "comment", "") or ""),
        opened_at=from_broker_time(getattr(raw, "time", 0)),
        as_of=as_of,
    )


def to_deal_snapshot(raw: Any) -> DealSnapshot:
    """Da `mt5.history_deals_get()` a `DealSnapshot`.

    Non porta `as_of`: un deal è un fatto già accaduto e immutabile, non una
    fotografia che invecchia.
    """
    entry = int(getattr(raw, "entry", -1))
    deal_type = int(getattr(raw, "type", -1))
    return DealSnapshot(
        ticket=int(raw.ticket),
        order_ticket=int(getattr(raw, "order", 0) or 0),
        position_id=int(getattr(raw, "position_id", 0) or 0),
        symbol=str(getattr(raw, "symbol", "") or ""),
        entry=DEAL_ENTRY_NAMES.get(entry, "UNKNOWN"),  # type: ignore[arg-type]
        deal_type=DEAL_TYPE_NAMES.get(deal_type, f"UNKNOWN_{deal_type}"),
        volume=float(getattr(raw, "volume", 0.0) or 0.0),
        price=float(getattr(raw, "price", 0.0) or 0.0),
        profit=float(getattr(raw, "profit", 0.0) or 0.0),
        commission=float(getattr(raw, "commission", 0.0) or 0.0),
        swap=float(getattr(raw, "swap", 0.0) or 0.0),
        fee=float(getattr(raw, "fee", 0.0) or 0.0),
        magic=int(getattr(raw, "magic", 0) or 0),
        comment=str(getattr(raw, "comment", "") or ""),
        executed_at=from_broker_time(getattr(raw, "time", 0)),
    )


def to_pending_order_snapshot(raw: Any, *, as_of: datetime) -> PendingOrderSnapshot:
    """Da `mt5.orders_get()` a `PendingOrderSnapshot`."""
    order_type = int(getattr(raw, "type", 0))
    expiration = int(getattr(raw, "time_expiration", 0) or 0)
    stop_loss = float(getattr(raw, "sl", 0.0) or 0.0)
    take_profit = float(getattr(raw, "tp", 0.0) or 0.0)
    return PendingOrderSnapshot(
        ticket=int(raw.ticket),
        symbol=str(raw.symbol),
        order_type=ORDER_TYPE_NAMES.get(order_type, f"UNKNOWN_{order_type}"),
        volume_initial=float(getattr(raw, "volume_initial", raw.volume_current)),
        volume_current=float(getattr(raw, "volume_current", 0.0) or 0.0),
        price_open=float(raw.price_open),
        stop_loss=stop_loss or None,
        take_profit=take_profit or None,
        magic=int(getattr(raw, "magic", 0) or 0),
        comment=str(getattr(raw, "comment", "") or ""),
        placed_at=from_broker_time(getattr(raw, "time_setup", 0)),
        expires_at=from_broker_time(expiration) if expiration else None,
        as_of=as_of,
    )
