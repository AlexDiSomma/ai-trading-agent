"""Ordini reali con chiave di idempotenza

Revision ID: 0005_ordini_live
Revises: 0004_analisi_ai
Create Date: 2026-10-06

Fase 11 (fondazione dell'esecuzione reale, LIVE ancora disabilitato): ogni ordine
si registra prima dell'invio; la chiave di idempotenza è unica, così nessuna
intenzione può essere inviata due volte, nemmeno da due processi in gara.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ordini_live"
down_revision: str | None = "0004_analisi_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("broker_symbol", sa.String(length=32), nullable=False),
        sa.Column("position_ticket", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_live_orders"),
        sa.UniqueConstraint("idempotency_key", name="uq_live_orders_idempotency_key"),
    )
    op.create_index("ix_live_orders_state", "live_orders", ["state"])
    op.create_index("ix_live_orders_broker_symbol", "live_orders", ["broker_symbol"])
    op.create_index("ix_live_orders_position_ticket", "live_orders", ["position_ticket"])


def downgrade() -> None:
    op.drop_index("ix_live_orders_position_ticket", table_name="live_orders")
    op.drop_index("ix_live_orders_broker_symbol", table_name="live_orders")
    op.drop_index("ix_live_orders_state", table_name="live_orders")
    op.drop_table("live_orders")
