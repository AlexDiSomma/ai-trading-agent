"""Composizione delle rotte.

Un solo prefisso `/api`, un solo punto in cui si vede l'intera superficie
dell'API. Le rotte delle fasi successive (scanner, segnali, piani, performance)
si aggiungono qui.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.api.routes import (
    account,
    ai,
    analysis,
    chart,
    controls,
    dashboard,
    global_market,
    health,
    live,
    market,
    news,
    portfolio,
    system,
)

api_router = APIRouter(prefix="/api")

api_router.include_router(health.router)
api_router.include_router(system.router)
api_router.include_router(account.router)
api_router.include_router(market.router)
api_router.include_router(global_market.router)
api_router.include_router(analysis.router)
api_router.include_router(ai.router)
api_router.include_router(portfolio.router)
api_router.include_router(dashboard.router)
api_router.include_router(live.router)
api_router.include_router(controls.router)
api_router.include_router(chart.router)
api_router.include_router(news.router)

__all__ = ["api_router"]
