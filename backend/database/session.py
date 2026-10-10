"""Connessione al database.

Scelte e motivi:

* **`pool_pre_ping=True`.** Una sessione di trading resta aperta per giorni e la
  connessione cade nel sonno della notte o a un riavvio di PostgreSQL. Senza il
  ping il primo ordine del mattino fallirebbe su una connessione morta.
* **L'applicazione parte anche se il database è giù.** Sembra contraddire il
  fail closed, e non lo fa: il trading resta bloccato (`/api/health` lo dichiara
  come motivo), ma l'API si avvia per *poter dire* cos'è rotto. Un backend che
  non parte non spiega niente a nessuno.
* **`expire_on_commit=False`.** Dopo il commit gli oggetti restano leggibili:
  con `True` ogni attributo letto dopo il commit genererebbe una query dentro
  codice asincrono, e in SQLAlchemy async diventa un errore difficile da
  interpretare.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config.settings import DatabaseSettings
from backend.core.enums import HealthStatus
from backend.core.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Engine e sessioni. Un'istanza per processo."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_async_engine(
            settings.async_dsn,
            echo=settings.echo,
            pool_size=settings.pool_size,
            max_overflow=settings.pool_size,
            pool_pre_ping=True,
            # Ricicla le connessioni dopo un'ora: evita quelle chiuse dal
            # server senza che il client lo sappia.
            pool_recycle=3_600,
            connect_args={"timeout": settings.connect_timeout_seconds},
        )
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def safe_dsn(self) -> str:
        """DSN senza password, mostrabile nei log e in System Health."""
        return self._settings.safe_dsn

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Sessione con commit/rollback automatici.

        Il rollback su eccezione non è una cortesia: senza di esso una scrittura
        a metà resterebbe in una transazione aperta e la connessione tornerebbe
        nel pool inutilizzabile.
        """
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def check(self) -> tuple[HealthStatus, str, float | None]:
        """Verifica la raggiungibilità. Non solleva: riferisce."""
        started = perf_counter()
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            latency_ms = round((perf_counter() - started) * 1000, 2)
            return HealthStatus.HEALTHY, f"Connesso a {self.safe_dsn}.", latency_ms
        # `OSError` accanto a `SQLAlchemyError` non è ridondante: quando il
        # server rifiuta la connessione, asyncpg solleva `ConnectionRefusedError`
        # (una OSError) *prima* che SQLAlchemy possa incapsularla in un errore
        # del proprio albero. Catturare solo SQLAlchemyError lascerebbe passare
        # l'eccezione proprio nel caso più comune — database spento — e
        # l'endpoint di stato andrebbe in errore invece di spiegarlo.
        except (SQLAlchemyError, OSError) as exc:
            # Solo la classe dell'errore nel messaggio: il testo di un errore di
            # connessione può contenere la stringa DSN completa, password inclusa.
            return (
                HealthStatus.UNHEALTHY,
                f"PostgreSQL non raggiungibile su {self.safe_dsn} ({exc.__class__.__name__}).",
                None,
            )

    async def dispose(self) -> None:
        await self._engine.dispose()
        logger.info("database.chiuso")


_database: Database | None = None


def get_database(settings: DatabaseSettings | None = None) -> Database:
    """Istanza condivisa dell'accesso al database."""
    global _database
    if _database is None:
        if settings is None:
            from backend.config.settings import get_settings

            settings = get_settings().db
        _database = Database(settings)
    return _database


async def reset_database() -> None:
    """Chiude e dimentica l'istanza. Serve ai test e allo spegnimento."""
    global _database
    if _database is not None:
        await _database.dispose()
        _database = None
