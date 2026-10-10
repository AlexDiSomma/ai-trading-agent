"""Contratto di freschezza dei dati.

Ogni snapshot che circola nel sistema (prezzi, contesto globale, macro, news,
portafoglio) dichiara *quando* e' stato prodotto e *per quanto* e' valido. Un
dato scaduto non viene silenziosamente riusato: diventa WAIT.

Nel progetto precedente l'origine dei problemi era non sapere se un numero a
schermo fosse di adesso o di mezz'ora prima. Qui la domanda ha una risposta
obbligatoria, perche' sta nel tipo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.core.clock import age_seconds, ensure_utc
from backend.core.errors import StaleDataError


@dataclass(frozen=True, slots=True)
class Fresh[T]:
    """Un valore con la sua data di produzione e la sua scadenza."""

    value: T
    as_of: datetime
    ttl_seconds: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", ensure_utc(self.as_of))

    def age(self, now: datetime) -> float:
        return age_seconds(self.as_of, now=now)

    def is_stale(self, now: datetime) -> bool:
        return self.age(now) > self.ttl_seconds

    def require_fresh(self, now: datetime) -> T:
        """Restituisce il valore solo se ancora valido, altrimenti alza.

        Volutamente non esiste un modo "morbido" per leggere un valore scaduto:
        chi vuole ignorare la scadenza deve leggere `.value` in modo esplicito e
        quella riga si vede nelle review.
        """
        if self.is_stale(now):
            raise StaleDataError(
                "Dato scaduto: non si decide su informazioni vecchie",
                source=self.source,
                as_of=self.as_of.isoformat(),
                age_seconds=round(self.age(now), 3),
                ttl_seconds=self.ttl_seconds,
            )
        return self.value
