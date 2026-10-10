"""Diario immutabile delle analisi AI.

Ogni `AnalysisRecord` viene scritto **una volta sola** in un file JSON il cui nome
contiene l'identificativo del record: l'apertura in modalità esclusiva (`"x"`) rende
impossibile sovrascriverlo. Il file porta l'hash di integrità; `verifica()` lo
ricalcola e segnala ogni modifica successiva.

Il file è la fonte primaria, non il database: deve esistere anche con PostgreSQL
spento, perché è ciò su cui la fase 9 misurerà se le analisi erano corrette.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.ai.contracts import AnalysisRecord
from backend.core.errors import TradingAgentError


class JournalError(TradingAgentError):
    blocks_trading = False


class AnalysisJournal:
    def __init__(self, cartella: Path) -> None:
        self._cartella = cartella

    def percorso(self, record: AnalysisRecord) -> Path:
        giorno = record.input.context_as_of.strftime("%Y-%m-%d")
        istante = record.input.context_as_of.strftime("%H%M%S")
        return self._cartella / giorno / f"{istante}_{record.record_id}.json"

    def scrivi(self, record: AnalysisRecord) -> Path:
        destinazione = self.percorso(record)
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        testo = json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False)
        try:
            with destinazione.open("x", encoding="utf-8") as f:
                f.write(testo)
        except FileExistsError as errore:
            raise JournalError(
                f"Il record {record.record_id} esiste già: i record non si sovrascrivono."
            ) from errore
        return destinazione

    @staticmethod
    def leggi(percorso: Path) -> AnalysisRecord:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
        record = AnalysisRecord.model_validate(dati)
        if record.integrity_sha256 != dati.get("integrity_sha256"):
            raise JournalError(f"{percorso.name}: hash di integrità non corrispondente.")
        return record

    def elenco(self, limite: int = 20) -> list[Path]:
        if not self._cartella.exists():
            return []
        return sorted(self._cartella.rglob("*.json"), reverse=True)[:limite]
