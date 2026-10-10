"""Persistenza dei contesti globali.

Colonne per ciò su cui si interroga (regime, qualità, fase, impronta), JSON per
ricostruire. L'impronta (`input_fingerprint`) più la versione del motore
identificano un contesto: con le stesse barre e la stessa versione si deve
riottenere lo stesso payload, ed è il modo di verificarlo mesi dopo.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class GlobalContextRow(Base, TimestampMixin):
    """Un `GlobalMarketContext` persistito, con il suo snapshot d'ingresso."""

    __tablename__ = "global_market_contexts"
    __table_args__ = (Index("ix_global_contexts_regime_as_of", "regime", "as_of"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    market_phase: Mapped[str] = mapped_column(String(20), nullable=False)
    regime: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_sentiment: Mapped[str] = mapped_column(String(20), nullable=False)
    volatility_level: Mapped[str] = mapped_column(String(12), nullable=False)
    volatility_trend: Mapped[str] = mapped_column(String(12), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(16), nullable=False)
    conflicts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    usable_for_decisions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Contesto completo.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: Snapshot dei dati in ingresso (stato di ogni serie), senza le barre.
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
