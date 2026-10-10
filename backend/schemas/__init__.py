"""Schemi Pydantic: i contratti dei dati che attraversano i moduli.

Distinzione tenuta per tutto il progetto:
- `schemas/` = DTO immutabili, validati, serializzabili verso l'API;
- `models/`  = entità SQLAlchemy persistite;
- i due non si importano a vicenda, la conversione è esplicita nei servizi.

Senza questa separazione lo schema del database finirebbe per dettare la forma
delle risposte HTTP, e ogni migrazione diventerebbe un cambio di API.
"""

from backend.schemas.account import (
    AccountSnapshot,
    DealSnapshot,
    PendingOrderSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)
from backend.schemas.health import ComponentHealth, SystemHealth
from backend.schemas.market import Candle, CandleSeries, SymbolInfo, Tick

__all__ = [
    "AccountSnapshot",
    "Candle",
    "CandleSeries",
    "ComponentHealth",
    "DealSnapshot",
    "PendingOrderSnapshot",
    "PortfolioSnapshot",
    "PositionSnapshot",
    "SymbolInfo",
    "SystemHealth",
    "Tick",
]
