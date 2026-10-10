"""Entità SQLAlchemy.

Tutti i modelli vanno importati qui: Alembic scopre le tabelle attraverso
`Base.metadata`, e un modello non importato non esiste per le migrazioni —
errore che si manifesta solo in produzione, quando una tabella non c'è.

Lo schema completo previsto dall'architettura (trade_plans, ai_decisions,
risk_decisions, journal, performance...) è descritto in docs/DATABASE.md e
viene introdotto fase per fase: creare adesso tabelle che nessuno scrive
produrrebbe uno schema inventato invece di uno derivato dall'uso reale.
"""

from backend.models.ai import AiAnalysisRow
from backend.models.base import Base, TimestampMixin
from backend.models.context import AssetContextRow
from backend.models.execution import LiveOrderRow
from backend.models.global_market import GlobalContextRow
from backend.models.market import AccountSnapshotRow, SymbolRow
from backend.models.system import ConfigVersion, SystemEvent

__all__ = [
    "AccountSnapshotRow",
    "AiAnalysisRow",
    "AssetContextRow",
    "Base",
    "ConfigVersion",
    "GlobalContextRow",
    "LiveOrderRow",
    "SymbolRow",
    "SystemEvent",
    "TimestampMixin",
]
