"""Dati per il grafico a candele della dashboard, in tempo reale.

Tre differenze rispetto a `/api/market/candles`:

* orari in **UTC** (epoch secondi): MT5 data le candele in ora broker, e un grafico
  che le mostrasse così sarebbe spostato di tre ore rispetto all'orologio dell'utente;
* l'ultima candela, quella in formazione, è inclusa e dichiarata: il grafico la mostra
  muoversi;
* sovrapposizioni dal conto paper: posizioni aperte (ingresso, stop, target) e trade
  chiusi (marcatori di ingresso e uscita).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from backend.api.deps import ContainerDep, Protected
from backend.core.enums import Timeframe
from backend.core.errors import SymbolNotAllowedError, TradingAgentError
from backend.paper.executor import PaperExecutor

router = APIRouter(prefix="/chart", tags=["Grafico"], dependencies=[Protected])


@router.get("/{symbol}", summary="Candele in UTC con posizioni e trade del conto paper")
async def chart(
    symbol: str,
    request: Request,
    container: ContainerDep,
    timeframe: Timeframe = Timeframe.M5,
    count: int = Query(default=300, ge=2, le=2_000),
) -> dict[str, Any]:
    mercato = container.require_market_data()
    try:
        fresh = await mercato.get_candles(symbol, timeframe, count)
    except SymbolNotAllowedError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except TradingAgentError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    serie = fresh.value
    stima = container.broker_clock.estimate(now=container.clock.now())
    scarto = (
        timedelta(seconds=stima.offset_seconds)
        if stima.reliable and stima.offset_seconds is not None
        else None
    )
    sposta = scarto or timedelta(0)
    barre = [
        {
            "time": int((c.time - sposta).timestamp()),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
        }
        for c in serie.candles
    ]
    quotazione = None
    try:
        tick = (await mercato.get_tick(symbol)).value
        quotazione = {"bid": tick.bid, "ask": tick.ask}
    except TradingAgentError:
        pass

    posizioni: list[dict[str, Any]] = []
    trade: list[dict[str, Any]] = []
    ap = getattr(request.app.state, "autopilot", None)
    if ap is not None:
        for nome, b in ap.ledger.books.items():
            for p in b.positions:
                if p.plan.broker_symbol == serie.symbol:
                    posizioni.append(
                        {
                            "book": nome,
                            "status": p.status,
                            "direction": p.plan.direction,
                            "entry": p.entry_fill or p.plan.entry_price,
                            # Stop effettivo: quello mobile, se la regola l'ha spostato.
                            "stop": PaperExecutor._stop_effettivo(
                                p, abs((p.entry_fill or p.plan.entry_price) - p.plan.stop)
                            ),
                            "target": p.plan.target,
                            "pnl": p.unrealized_pnl,
                        }
                    )
            for t in b.trades[-60:]:
                if t.plan.broker_symbol == serie.symbol:
                    trade.append(
                        {
                            "book": nome,
                            "direction": t.plan.direction,
                            "opened": int(t.opened_at.timestamp()),
                            "closed": int(t.closed_at.timestamp()),
                            "entry": t.entry_fill,
                            "exit": t.exit_price,
                            "reason": t.exit_reason,
                            "r": t.r_multiple,
                            "pnl": t.net_pnl,
                        }
                    )
    info = (await container.require_market_data().get_symbol(symbol)).value
    return {
        "symbol": serie.symbol,
        "timeframe": str(timeframe),
        "digits": info.digits,
        "bars": barre,
        "last_bar_forming": not serie.last_candle_closed,
        "utc_times": scarto is not None,
        "quote": quotazione,
        "positions": posizioni,
        "trades": trade,
    }
