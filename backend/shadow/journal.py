"""Diario immutabile della shadow mode: un file per snapshot, mai sovrascritto.

Stessa regola del diario della fase 5A: apertura esclusiva (`"x"`) e verifica
dell'hash di integrità alla rilettura. Le decisioni e gli esiti stanno in cartelle
separate; un esito rivalutato più tardi è un **nuovo** file con il proprio istante.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from backend.core.errors import TradingAgentError
from backend.shadow.records import (
    PIPELINE_VERSION,
    PRIMA_VERSIONE,
    DecisionSnapshot,
    OutcomeSnapshot,
)

T = TypeVar("T", DecisionSnapshot, OutcomeSnapshot)


class ShadowJournalError(TradingAgentError):
    blocks_trading = False


class ShadowJournal:
    def __init__(self, cartella: Path) -> None:
        self.cartella = cartella

    def _scrivi(self, destinazione: Path, record: BaseModel) -> Path:
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        testo = json.dumps(record.model_dump(mode="json"), indent=1, ensure_ascii=False)
        try:
            with destinazione.open("x", encoding="utf-8") as f:
                f.write(testo)
        except FileExistsError as errore:
            raise ShadowJournalError(
                f"{destinazione.name} esiste già: non si sovrascrive."
            ) from errore
        return destinazione

    def scrivi_decisione(self, d: DecisionSnapshot) -> Path:
        nome = f"{d.decided_at:%Y%m%d_%H%M}_{d.canonical}_{d.mode}_{d.decision_id}.json"
        return self._scrivi(self.cartella / "decisions" / nome, d)

    def scrivi_esito(self, o: OutcomeSnapshot) -> Path:
        nome = f"{o.decision_id}_{o.evaluated_at:%Y%m%d_%H%M%S}.json"
        return self._scrivi(self.cartella / "outcomes" / nome, o)

    @staticmethod
    def leggi(percorso: Path, tipo: type[T]) -> T:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
        record = tipo.model_validate(dati)
        if record.integrity_sha256 != dati.get("integrity_sha256"):
            raise ShadowJournalError(f"{percorso.name}: hash di integrità non corrispondente.")
        return record

    def decisioni(self, versione: str = PIPELINE_VERSION) -> list[DecisionSnapshot]:
        """Le decisioni di una versione della pipeline, verificate.

        Quelle di altre versioni restano nel diario ma non si rileggono con i contratti
        di oggi: il loro schema può essere diverso (es. filtro 1.0.0 con `reasons`).
        """
        return [
            self.leggi(p, DecisionSnapshot)
            for p in self._file_decisioni()
            if self._versione(p) == versione
        ]

    def altre_versioni(self, versione: str = PIPELINE_VERSION) -> dict[str, int]:
        conteggio: dict[str, int] = {}
        for p in self._file_decisioni():
            if (v := self._versione(p)) != versione:
                conteggio[v] = conteggio.get(v, 0) + 1
        return conteggio

    def _file_decisioni(self) -> list[Path]:
        cartella = self.cartella / "decisions"
        return sorted(cartella.glob("*.json")) if cartella.exists() else []

    @staticmethod
    def _versione(percorso: Path) -> str:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
        return str(dati.get("pipeline_version", PRIMA_VERSIONE))

    def ultimi_esiti(self) -> dict[str, OutcomeSnapshot]:
        """Per ogni decisione, la valutazione più recente."""
        cartella = self.cartella / "outcomes"
        esiti: dict[str, OutcomeSnapshot] = {}
        if cartella.exists():
            for p in sorted(cartella.glob("*.json")):
                o = self.leggi(p, OutcomeSnapshot)
                esiti[o.decision_id] = o
        return esiti
