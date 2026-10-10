"""Endpoint dei dati di mercato — sola lettura, solo strumenti autorizzati.

Ogni risposta porta `as_of` e `eta_secondi`: chi guarda la dashboard deve poter
distinguere un prezzo di adesso da uno di mezz'ora prima senza indovinare. Era
uno dei problemi del progetto precedente.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.deps import ContainerDep, MarketDataDep, Protected
from backend.core.enums import Timeframe
from backend.core.errors import StaleDataError, SymbolNotAllowedError, TradingAgentError
from backend.market_data.reader import MIN_CANDLES

router = APIRouter(prefix="/market", tags=["Mercato"], dependencies=[Protected])


@router.get(
    "/watchlist",
    summary="Strumenti autorizzati",
    description=(
        "La whitelist definita dall'utente in RISK_ALLOWED_SYMBOLS. "
        "Il sistema non guarda, non analizza e non opera nulla al di fuori di questa lista."
    ),
)
async def get_watchlist(container: ContainerDep, market: MarketDataDep) -> dict[str, Any]:
    return {
        "strumenti": list(market.watchlist),
        "broker": container.require_broker().name,
        "dati_simulati": container.require_broker().is_simulated,
    }


@router.get(
    "/symbols-available",
    summary="Simboli esistenti presso il broker",
    description=(
        "Serve a scoprire la grafia esatta dei nomi, che cambia da broker a "
        "broker: su XM l'oro si chiama GOLD e gli indici US30Cash, non XAUUSD e "
        "US30. Un nome sbagliato in RISK_ALLOWED_SYMBOLS rende lo strumento "
        "invisibile al sistema. Questo elenco **non** autorizza nulla: la "
        "whitelist resta quella definita dall'utente."
    ),
)
async def get_available_symbols(
    container: ContainerDep,
    market: MarketDataDep,
    cerca: str | None = Query(
        default=None,
        description="Filtro sul nome, senza distinzione di maiuscole (es. gold, us30)",
    ),
    limite: int = Query(default=100, ge=1, le=2_000),
) -> dict[str, Any]:
    broker = container.require_broker()
    try:
        tutti = await broker.list_symbols()
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    filtrati = [n for n in tutti if cerca is None or cerca.lower() in n.lower()]
    autorizzati = {n.lower() for n in market.watchlist}
    return {
        "totale_presso_il_broker": len(tutti),
        "corrispondenze": len(filtrati),
        "mostrati": min(len(filtrati), limite),
        "simboli": [
            {"nome": nome, "autorizzato": nome.lower() in autorizzati}
            for nome in sorted(filtrati)[:limite]
        ],
    }


@router.get(
    "/symbols/{symbol}",
    summary="Specifiche di contratto",
    description=(
        "Valori dichiarati dal broker: digits, point, tick value, volume minimo e "
        "passo. Sono la base del calcolo della dimensione della posizione e non "
        "vengono mai sostituiti da costanti interne."
    ),
)
async def get_symbol(symbol: str, market: MarketDataDep) -> dict[str, Any]:
    try:
        fresh = await market.get_symbol(symbol)
    except SymbolNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TradingAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {
        "simbolo": fresh.value.model_dump(mode="json"),
        "as_of": fresh.as_of,
        "eta_secondi": round(fresh.age(market.now()), 2),
        "fonte": fresh.source,
    }


@router.get(
    "/tick/{symbol}",
    summary="Ultima quotazione",
)
async def get_tick(symbol: str, market: MarketDataDep) -> dict[str, Any]:
    try:
        fresh = await market.get_tick(symbol)
    except SymbolNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TradingAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    now = market.now()
    return {
        "tick": fresh.value.model_dump(mode="json"),
        "as_of": fresh.as_of,
        "eta_secondi": round(fresh.age(now), 2),
        # Il dato viene restituito comunque, ma etichettato: fuori orario di
        # mercato l'ultimo tick è legittimamente vecchio, e nasconderlo non
        # aiuterebbe nessuno.
        "scaduto": fresh.is_stale(now),
        "fonte": fresh.source,
    }


@router.get(
    "/candles/{symbol}",
    summary="Candele",
    description=(
        "L'ultima candela è quella in formazione: la risposta lo dichiara con "
        "`ultima_chiusa`. Usarla come chiusa è il modo più comune di ottenere un "
        "backtest brillante e un sistema reale perdente."
    ),
)
async def get_candles(
    symbol: str,
    market: MarketDataDep,
    timeframe: Timeframe = Timeframe.H1,
    count: int = Query(default=MIN_CANDLES, ge=10, le=2_000),
) -> dict[str, Any]:
    try:
        fresh = await market.get_candles(symbol, timeframe, count)
    except SymbolNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except StaleDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except TradingAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    series = fresh.value
    return {
        "simbolo": series.symbol,
        "timeframe": str(series.timeframe),
        "ruolo_timeframe": series.timeframe.role,
        "candele": [candle.model_dump(mode="json") for candle in series.candles],
        "numero_candele": len(series),
        "numero_chiuse": len(series.closed),
        "ultima_chiusa": series.last_candle_closed,
        "as_of": fresh.as_of,
        "eta_secondi": round(fresh.age(market.now()), 2),
        "fonte": series.source,
    }
