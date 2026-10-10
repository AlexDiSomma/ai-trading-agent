"""Fondazione: diario di sistema, versioni di configurazione, conto, strumenti

Revision ID: 0001_fondazione
Revises:
Create Date: 2026-10-04

Perché queste quattro tabelle e non lo schema completo: ogni tabella creata qui
è una tabella che un modulo di fase 1-2 scrive davvero. Le tabelle dei piani
operativi, delle decisioni AI e delle performance nascono con i moduli che le
usano, così la forma viene dall'uso e non da un'ipotesi fatta oggi.

`system_events` e `config_versions` esistono già adesso perché servono dal primo
avvio: senza di esse non si può ricostruire in che stato era il sistema, e
quella ricostruzione è il motivo per cui questo progetto esiste.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_fondazione"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="INFO"),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="unknown"),
        sa.Column("mode", sa.String(length=10), nullable=False, server_default="PAPER"),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
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
        sa.PrimaryKeyConstraint("id", name="pk_system_events"),
    )
    op.create_index("ix_system_events_created_at", "system_events", ["created_at"])
    op.create_index(
        "ix_system_events_type_occurred", "system_events", ["event_type", "occurred_at"]
    )
    op.create_index("ix_system_events_correlation", "system_events", ["correlation_id"])

    op.create_table(
        "config_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("risk_limits", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("app_mode", sa.String(length=10), nullable=False, server_default="PAPER"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
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
        sa.PrimaryKeyConstraint("id", name="pk_config_versions"),
        sa.UniqueConstraint("fingerprint", name="uq_config_versions_fingerprint"),
    )
    op.create_index("ix_config_versions_created_at", "config_versions", ["created_at"])
    op.create_index("ix_config_versions_is_active", "config_versions", ["is_active"])

    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("login", sa.BigInteger(), nullable=False),
        sa.Column("server", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EUR"),
        sa.Column("leverage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("margin", sa.Float(), nullable=False, server_default="0"),
        sa.Column("margin_free", sa.Float(), nullable=False, server_default="0"),
        sa.Column("margin_level", sa.Float(), nullable=False, server_default="0"),
        sa.Column("open_profit", sa.Float(), nullable=False, server_default="0"),
        sa.Column("open_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trade_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
    )
    op.create_index("ix_accounts_created_at", "accounts", ["created_at"])
    op.create_index("ix_accounts_login_as_of", "accounts", ["login", "as_of"])
    op.create_index("ix_accounts_simulated", "accounts", ["simulated"])

    op.create_table(
        "symbols",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("digits", sa.Integer(), nullable=False),
        sa.Column("point", sa.Float(), nullable=False),
        sa.Column("contract_size", sa.Float(), nullable=False),
        sa.Column("tick_size", sa.Float(), nullable=False),
        sa.Column("tick_value", sa.Float(), nullable=False),
        sa.Column("volume_min", sa.Float(), nullable=False),
        sa.Column("volume_max", sa.Float(), nullable=False),
        sa.Column("volume_step", sa.Float(), nullable=False),
        sa.Column("spread_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stops_level_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency_base", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("currency_profit", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("currency_margin", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("trade_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("in_whitelist", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_symbols"),
    )
    op.create_index("ix_symbols_created_at", "symbols", ["created_at"])
    op.create_index("ix_symbols_name_as_of", "symbols", ["name", "as_of"])
    op.create_index("ix_symbols_in_whitelist", "symbols", ["in_whitelist"])


def downgrade() -> None:
    op.drop_table("symbols")
    op.drop_table("accounts")
    op.drop_table("config_versions")
    op.drop_table("system_events")
