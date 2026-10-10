"""Contesto globale dei mercati

Revision ID: 0003_contesto_globale
Revises: 0002_contesto_mercato
Create Date: 2026-10-04

Fase 4: il `GlobalMarketContext` va conservato come l'`AssetMarketContext`. Una
decisione futura dovrà poter dire non solo cosa vedeva sullo strumento, ma in quale
regime globale l'ha visto. Impronta delle barre e versione del motore rendono il
contesto ricostruibile e verificabile.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_contesto_globale"
down_revision: str | None = "0002_contesto_mercato"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "global_market_contexts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("market_phase", sa.String(length=20), nullable=False),
        sa.Column("regime", sa.String(length=20), nullable=False),
        sa.Column("risk_sentiment", sa.String(length=20), nullable=False),
        sa.Column("volatility_level", sa.String(length=12), nullable=False),
        sa.Column("volatility_trend", sa.String(length=12), nullable=False),
        sa.Column("data_quality", sa.String(length=16), nullable=False),
        sa.Column("conflicts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("usable_for_decisions", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_global_market_contexts"),
    )
    op.create_index("ix_global_market_contexts_created_at", "global_market_contexts", ["created_at"])
    op.create_index("ix_global_market_contexts_as_of", "global_market_contexts", ["as_of"])
    op.create_index(
        "ix_global_market_contexts_input_fingerprint", "global_market_contexts", ["input_fingerprint"]
    )
    op.create_index("ix_global_market_contexts_simulated", "global_market_contexts", ["simulated"])
    op.create_index("ix_global_contexts_regime_as_of", "global_market_contexts", ["regime", "as_of"])


def downgrade() -> None:
    op.drop_table("global_market_contexts")
