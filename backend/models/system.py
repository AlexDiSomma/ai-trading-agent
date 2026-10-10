"""Entità di piattaforma: diario di sistema e versioni della configurazione.

`SystemEvent` è l'audit trail. Non è un log: i log si ruotano e si perdono,
questa tabella resta. Serve a rispondere, settimane dopo, a domande come "il 12
marzo il kill switch era attivo?" o "con quali limiti di rischio stava girando
il sistema quando ha aperto quella posizione?".

`ConfigVersion` risponde alla seconda: ogni volta che i limiti dell'utente
cambiano viene registrata una nuova versione con la sua impronta. I piani
operativi faranno riferimento alla versione attiva, così una perdita non viene
mai giudicata con i limiti di oggi se era stata decisa con quelli di ieri.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class SystemEvent(Base, TimestampMixin):
    """Fatto accaduto nel sistema, registrato in modo permanente."""

    __tablename__ = "system_events"
    __table_args__ = (
        # L'interrogazione tipica è "cosa è successo in questa finestra, di
        # questo tipo": l'indice composto la copre.
        Index("ix_system_events_type_occurred", "event_type", "occurred_at"),
        Index("ix_system_events_correlation", "correlation_id"),
    )

    id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")

    # Modalità operativa al momento del fatto: un evento letto senza sapere se
    # era PAPER o LIVE porta a conclusioni sbagliate sul conto.
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")

    #: Lega fra loro tutti gli eventi nati dalla stessa osservazione di mercato.
    correlation_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - diagnostica
        return f"<SystemEvent {self.event_type} @ {self.occurred_at:%Y-%m-%d %H:%M:%S}>"


class ConfigVersion(Base, TimestampMixin):
    """Fotografia immutabile della configurazione di rischio attiva."""

    __tablename__ = "config_versions"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_config_versions_fingerprint"),)

    id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True, default=uuid4)

    #: Impronta del contenuto: se i limiti non cambiano non nasce una versione
    #: nuova a ogni riavvio.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    #: I limiti così come erano. Mai aggiornato: a un cambio nasce una riga nuova.
    risk_limits: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    app_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:  # pragma: no cover - diagnostica
        return f"<ConfigVersion {self.fingerprint[:8]} attiva={self.is_active}>"
