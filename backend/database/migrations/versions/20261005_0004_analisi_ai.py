"""Analisi dell'AI Market Analyst

Revision ID: 0004_analisi_ai
Revises: 0003_contesto_globale
Create Date: 2026-10-05

Fase 5A: ogni analisi AI (ingresso, chiamata, risposta grezza, esito) viene
conservata per misurare in seguito, senza hindsight, se era corretta. La fonte
primaria è il diario su file (var/ai_journal); questa tabella serve alle
interrogazioni. Di sola aggiunta.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_analisi_ai"
down_revision: str | None = "0003_contesto_globale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_market_analyses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("record_id", sa.String(length=24), nullable=False),
        sa.Column("integrity_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("engine_regime", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("output_schema_version", sa.String(length=40), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("record", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_market_analyses"),
        sa.UniqueConstraint("record_id", name="uq_ai_market_analyses_record_id"),
    )
    op.create_index("ix_ai_market_analyses_created_at", "ai_market_analyses", ["created_at"])
    op.create_index("ix_ai_market_analyses_context_as_of", "ai_market_analyses", ["context_as_of"])
    op.create_index(
        "ix_ai_market_analyses_context_fingerprint", "ai_market_analyses", ["context_fingerprint"]
    )
    op.create_index("ix_ai_market_analyses_status", "ai_market_analyses", ["status"])


def downgrade() -> None:
    op.drop_table("ai_market_analyses")
