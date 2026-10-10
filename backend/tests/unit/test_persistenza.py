"""Persistenza con il database assente: l'analisi non deve aspettarlo.

Misurato sul conto reale il 2026-10-04: con PostgreSQL spento ogni scrittura
attendeva ~4 secondi il rifiuto, e la prima analisi globale (37 specifiche nuove)
durava 146 secondi. Dopo un guasto le scritture vengono saltate per 30 secondi.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from backend.core.clock import FrozenClock
from backend.services.persistence import MarketPersistence


class DatabaseSpento:
    def __init__(self) -> None:
        self.tentativi = 0

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        self.tentativi += 1
        raise ConnectionRefusedError("database spento")
        yield  # pragma: no cover


async def test_dopo_un_guasto_le_scritture_vengono_saltate_per_un_po() -> None:
    orologio = FrozenClock(datetime(2026, 10, 4, 21, 0, tzinfo=UTC))
    db = DatabaseSpento()
    persistenza = MarketPersistence(db, clock=orologio)  # type: ignore[arg-type]

    await persistenza._scrivi(object(), "prova")
    assert (db.tentativi, persistenza.write_failures) == (1, 1)

    for _ in range(5):
        await persistenza._scrivi(object(), "prova")
    assert db.tentativi == 1
    assert persistenza.writes_skipped == 5

    orologio.advance(seconds=31)
    await persistenza._scrivi(object(), "prova")
    assert db.tentativi == 2
    assert persistenza.write_failures == 2
