"""Orologio iniettabile e gestione del tempo.

Tre motivi per non usare `datetime.now()` sparso nel codice:
1. i test (e il backtest) devono poter muovere il tempo;
2. l'ora del server del broker non coincide con UTC e la giornata di trading si
   chiude su *quella*, non sull'ora locale del PC;
3. ogni contesto porta un `as_of` confrontabile: senza orologio unico il
   controllo di freschezza non e' affidabile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Sorgente del tempo. Restituisce sempre datetime timezone-aware in UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """Orologio reale."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FrozenClock:
    """Orologio fermo o avanzabile a mano, per test e backtest."""

    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> datetime:
        self.current = self.current + timedelta(**delta)
        return self.current


def ensure_utc(value: datetime) -> datetime:
    """Normalizza a UTC.

    Un datetime naive viene *interpretato* come UTC: il broker restituisce
    timestamp naive e trattarli come ora locale sposterebbe le candele di ore.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def age_seconds(value: datetime, *, now: datetime) -> float:
    """Eta' in secondi di un timestamp rispetto a `now`."""
    return (ensure_utc(now) - ensure_utc(value)).total_seconds()
