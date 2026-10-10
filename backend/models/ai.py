"""Persistenza delle analisi AI (best effort: la fonte primaria è il diario su file)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class AiAnalysisRow(Base, TimestampMixin):
    """Un `AnalysisRecord`, di sola aggiunta."""

    __tablename__ = "ai_market_analyses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    integrity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    context_as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    engine_regime: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    record: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
