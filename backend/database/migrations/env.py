"""Ambiente Alembic.

La URL del database non sta in `alembic.ini`: viene dalla configurazione, come
per il resto del sistema. Un DSN duplicato in un file .ini è un segreto in più
da proteggere e una fonte di divergenza fra "dove gira l'app" e "dove gira la
migrazione".

Le migrazioni girano in modo **asincrono** con lo stesso driver dell'app
(asyncpg): usare un driver diverso solo per le migrazioni significherebbe
scoprire le incompatibilità di tipo al primo avvio e non alla migrazione.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from backend.config.settings import get_settings

# Importare il package dei modelli popola Base.metadata: senza questo import le
# migrazioni generate sarebbero vuote.
from backend.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.db.async_dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Genera lo SQL senza connettersi (`alembic upgrade head --sql`)."""
    context.configure(
        url=settings.db.async_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # `compare_type` e `compare_server_default` attivi: senza di essi un
        # cambio di tipo di colonna passa inosservato nell'autogenerazione.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
