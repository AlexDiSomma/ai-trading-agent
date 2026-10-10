"""Archivio degli ordini reali. In LIVE è **PostgreSQL, obbligatorio**.

Ogni ordine si registra **prima** di essere inviato e si aggiorna a ogni passaggio:
dopo un crash o un timeout, ciò che è nel database dice cosa potrebbe essere stato
inviato, e la riconciliazione parte da lì. Un database non disponibile blocca
l'esecuzione (fail closed, ADR 0019): non si invia ciò che non si può registrare.

La chiave di idempotenza è `UNIQUE` nel database: anche due processi in gara non
possono registrare due volte la stessa intenzione.

`MemoryExecutionStore` esiste solo per i test e non è mai accettato in LIVE
(`LiveReadiness` verifica il tipo).
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.core.enums import HealthStatus
from backend.core.errors import PersistenceUnavailableError, TradingAgentError
from backend.database.session import Database
from backend.execution.models import LiveOrder
from backend.models.execution import LiveOrderRow


class IdempotencyConflictError(TradingAgentError):
    """La chiave esiste già: l'ordine non si invia una seconda volta."""


class ExecutionStore(Protocol):
    durable: bool

    async def healthy(self) -> tuple[bool, str]: ...
    async def get(self, key: str) -> LiveOrder | None: ...
    async def insert(self, order: LiveOrder) -> None: ...
    async def update(self, order: LiveOrder) -> None: ...
    async def lista(self, stati: frozenset[str] | None = None) -> list[LiveOrder]: ...


class MemoryExecutionStore:
    """Solo test: non durevole, mai accettato per il LIVE."""

    durable = False

    def __init__(self) -> None:
        self.righe: dict[str, LiveOrder] = {}
        self.guasto: str | None = None

    async def healthy(self) -> tuple[bool, str]:
        return (self.guasto is None, self.guasto or "memoria (non durevole)")

    def _verifica(self) -> None:
        if self.guasto:
            raise PersistenceUnavailableError(self.guasto)

    async def get(self, key: str) -> LiveOrder | None:
        self._verifica()
        r = self.righe.get(key)
        return r.model_copy(deep=True) if r else None

    async def insert(self, order: LiveOrder) -> None:
        self._verifica()
        if order.idempotency_key in self.righe:
            raise IdempotencyConflictError(f"Chiave {order.idempotency_key} già registrata.")
        self.righe[order.idempotency_key] = order.model_copy(deep=True)

    async def update(self, order: LiveOrder) -> None:
        self._verifica()
        self.righe[order.idempotency_key] = order.model_copy(deep=True)

    async def lista(self, stati: frozenset[str] | None = None) -> list[LiveOrder]:
        self._verifica()
        return [
            r.model_copy(deep=True)
            for r in self.righe.values()
            if stati is None or r.state in stati
        ]


class PostgresExecutionStore:
    durable = True

    def __init__(self, database: Database) -> None:
        self._db = database

    async def healthy(self) -> tuple[bool, str]:
        stato, dettaglio, _ = await self._db.check()
        if stato is not HealthStatus.HEALTHY:
            return False, f"PostgreSQL non disponibile: {dettaglio}"
        try:
            async with self._db.session() as s:
                await s.execute(text("SELECT 1 FROM live_orders LIMIT 1"))
        except (SQLAlchemyError, OSError) as e:
            return False, f"Tabella live_orders non disponibile (migrazione 0005?): {e}"
        return True, "PostgreSQL disponibile"

    async def _esegui(self, funzione):  # type: ignore[no-untyped-def]
        try:
            async with self._db.session() as s:
                return await funzione(s)
        except IntegrityError as e:
            raise IdempotencyConflictError(str(e.orig)) from e
        except (SQLAlchemyError, OSError) as e:
            raise PersistenceUnavailableError(f"Scrittura ordini fallita: {e}") from e

    @staticmethod
    def _riga(o: LiveOrder) -> dict[str, object]:
        return {
            "idempotency_key": o.idempotency_key,
            "state": o.state,
            "broker_symbol": o.broker_symbol,
            "position_ticket": o.position_ticket,
            "payload": o.model_dump(mode="json"),
            "created_at": o.created_at,
            "updated_at": o.updated_at,
        }

    async def get(self, key: str) -> LiveOrder | None:
        async def f(s):  # type: ignore[no-untyped-def]
            r = (
                await s.execute(select(LiveOrderRow).where(LiveOrderRow.idempotency_key == key))
            ).scalar_one_or_none()
            return LiveOrder.model_validate(r.payload) if r else None

        return await self._esegui(f)  # type: ignore[no-any-return]

    async def insert(self, order: LiveOrder) -> None:
        async def f(s):  # type: ignore[no-untyped-def]
            s.add(LiveOrderRow(**self._riga(order)))

        await self._esegui(f)

    async def update(self, order: LiveOrder) -> None:
        async def f(s):  # type: ignore[no-untyped-def]
            r = (
                await s.execute(
                    select(LiveOrderRow).where(
                        LiveOrderRow.idempotency_key == order.idempotency_key
                    )
                )
            ).scalar_one()
            for k, v in self._riga(order).items():
                setattr(r, k, v)

        await self._esegui(f)

    async def lista(self, stati: frozenset[str] | None = None) -> list[LiveOrder]:
        async def f(s):  # type: ignore[no-untyped-def]
            q = select(LiveOrderRow)
            if stati is not None:
                q = q.where(LiveOrderRow.state.in_(stati))
            return [LiveOrder.model_validate(r.payload) for r in (await s.execute(q)).scalars()]

        return await self._esegui(f)  # type: ignore[no-any-return]
