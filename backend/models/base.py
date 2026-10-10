"""Base dichiarativa SQLAlchemy.

Due scelte che si pagano subito e rendono fino alla fine:

* **convenzione di nomi dei vincoli.** Senza di essa PostgreSQL inventa nomi per
  indici e chiavi, e Alembic genera migrazioni che non sanno cosa rinominare: le
  migrazioni diventano irripetibili;
* **tutti i timestamp `timezone=True`.** Una colonna naive su dati di trading
  significa, prima o poi, un confronto fra ora broker e ora UTC che sembra
  funzionare e sposta tutto di due ore.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Radice di tutte le entità persistite."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Quando la riga è stata scritta e aggiornata.

    Distinto da `as_of`/`occurred_at`: quello è il momento del *fatto*, questo è
    il momento della *registrazione*. Tenerli separati permette di accorgersi di
    un ritardo di scrittura, che è a sua volta un sintomo da indagare.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
