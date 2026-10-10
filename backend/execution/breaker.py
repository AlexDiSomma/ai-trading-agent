"""Circuit breaker dell'esecuzione reale: persistente, si riarma solo a mano.

Scatta su:
* `SOGLIA_CONSECUTIVI` rifiuti o errori consecutivi del broker;
* un esito d'ordine **ignoto** che non si è potuto riconciliare;
* una posizione rimasta senza SL/TP lato server;
* una posizione del sistema sconosciuta all'archivio (stato inatteso).

Quando è aperto blocca le **aperture**; chiusure e protezioni restano possibili
(come il kill switch, ADR 0006). Lo stato è su file con scrittura atomica: un
riavvio non lo azzera, altrimenti riavviare sarebbe un modo di aggirarlo.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.clock import Clock

SOGLIA_CONSECUTIVI = 3


class CircuitBreaker:
    def __init__(self, file: Path, *, clock: Clock) -> None:
        self._file = file
        self._clock = clock
        self.stato: dict[str, Any] = {
            "tripped": False,
            "reason": None,
            "tripped_at": None,
            "consecutive_failures": 0,
            "history": [],
        }
        if file.exists():
            self.stato.update(json.loads(file.read_text(encoding="utf-8")))

    @property
    def aperto(self) -> bool:
        return bool(self.stato["tripped"])

    def _salva(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.stato, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self._file)

    def _storia(self, evento: str, motivo: str) -> None:
        self.stato["history"] = [
            *self.stato["history"][-49:],
            {"at": self._clock.now().isoformat(), "event": evento, "reason": motivo},
        ]

    def scatta(self, motivo: str) -> None:
        if not self.stato["tripped"]:
            self.stato.update(tripped=True, reason=motivo, tripped_at=self._clock.now().isoformat())
        self._storia("TRIPPED", motivo)
        self._salva()

    def fallimento(self, motivo: str) -> None:
        self.stato["consecutive_failures"] += 1
        self._storia("FAILURE", motivo)
        if self.stato["consecutive_failures"] >= SOGLIA_CONSECUTIVI:
            self.scatta(f"{SOGLIA_CONSECUTIVI} fallimenti consecutivi; ultimo: {motivo}")
        else:
            self._salva()

    def successo(self) -> None:
        if self.stato["consecutive_failures"]:
            self.stato["consecutive_failures"] = 0
            self._salva()

    def riarma(self, motivo: str, ora: datetime | None = None) -> None:
        """Solo manuale, con un motivo che resta nella storia."""
        if len(motivo.strip()) < 10:
            raise ValueError("Serve un motivo esplicito (almeno 10 caratteri) per riarmare.")
        self.stato.update(tripped=False, reason=None, tripped_at=None, consecutive_failures=0)
        self._storia("RESET", motivo)
        self._salva()
