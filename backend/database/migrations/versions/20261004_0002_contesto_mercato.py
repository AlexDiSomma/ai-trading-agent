"""Contesto di mercato e specifiche arricchite

Revision ID: 0002_contesto_mercato
Revises: 0001_fondazione
Create Date: 2026-10-04

Perché questa migrazione: la fase 3 produce analisi che devono poter essere
rilette mesi dopo. Una decisione senza il contesto che l'ha prodotta non è
ricostruibile, e davanti a una perdita la domanda non è "cosa ha deciso" ma
"cosa vedeva quando ha deciso".

Le colonne aggiunte a `symbols` vengono da scoperte fatte sul conto XM reale:

* `path` e `asset_class` — XM dichiara `GOLD` come USD/USD/USD, quindi le valute
  da sole non distinguono l'oro da un indice americano. Il percorso di categoria
  (`Derivatives\\Spot Metals\\GOLD`) è un dato del broker, non una congettura.
* `trade_mode` — distingue "vietato" da "solo chiusura", che un booleano
  appiattisce.
* `value_per_price_unit` — il valore monetario di un movimento di prezzo,
  calcolato dal broker. `trade_tick_value` non basta: su questo conto in EUR è in
  EUR per il forex ma in USD per metalli e indici (ADR 0012).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_contesto_mercato"
down_revision: str | None = "0001_fondazione"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_market_contexts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("asset_class", sa.String(length=16), nullable=False, server_default="OTHER"),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broker_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "broker_time_source", sa.String(length=16), nullable=False, server_default="UNAVAILABLE"
        ),
        sa.Column("quote_state", sa.String(length=20), nullable=False),
        sa.Column("quote_is_inference", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("spread_points", sa.Integer(), nullable=True),
        sa.Column("structural_bias", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operational_bias", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alignment", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "dominant_regime", sa.String(length=32), nullable=False, server_default="UNCERTAIN"
        ),
        sa.Column(
            "dominant_structure", sa.String(length=16), nullable=False, server_default="UNDEFINED"
        ),
        sa.Column(
            "structure_execution_conflict",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("quality", sa.String(length=20), nullable=False, server_default="GOOD"),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("usable_for_decisions", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_asset_market_contexts"),
    )
    op.create_index("ix_asset_market_contexts_created_at", "asset_market_contexts", ["created_at"])
    op.create_index("ix_asset_contexts_symbol_as_of", "asset_market_contexts", ["symbol", "as_of"])
    op.create_index(
        "ix_asset_contexts_regime", "asset_market_contexts", ["dominant_regime", "as_of"]
    )
    op.create_index("ix_asset_market_contexts_simulated", "asset_market_contexts", ["simulated"])

    op.add_column(
        "symbols", sa.Column("path", sa.String(length=200), nullable=False, server_default="")
    )
    op.add_column(
        "symbols",
        sa.Column("asset_class", sa.String(length=16), nullable=False, server_default="OTHER"),
    )
    op.add_column(
        "symbols", sa.Column("trade_mode", sa.Integer(), nullable=False, server_default="4")
    )
    op.add_column("symbols", sa.Column("value_per_price_unit", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("symbols", "value_per_price_unit")
    op.drop_column("symbols", "trade_mode")
    op.drop_column("symbols", "asset_class")
    op.drop_column("symbols", "path")
    op.drop_table("asset_market_contexts")
