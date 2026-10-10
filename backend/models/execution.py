"""Ordini reali (fase 11): un record per chiave di idempotenza, aggiornato a ogni passaggio.

A differenza delle altre tabelle non è di sola aggiunta: lo stato dell'ordine
cambia (CREATED → SENT → FILLED → CLOSED). La storia completa dei passaggi resta
nel campo `payload.history`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class LiveOrderRow(Base):
    __tablename__ = "live_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    broker_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    position_ticket: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
