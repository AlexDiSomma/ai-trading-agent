"""Registro dei conti paper: un file JSON con scrittura atomica, più un diario eventi.

Perché un file e non PostgreSQL: il database è opzionale nel sistema (fase 4,
persistenza BEST_EFFORT) e spesso spento in sviluppo, mentre lo stato di un conto
che apre e chiude posizioni deve sopravvivere ai riavvii **sempre**. Il file si
scrive su un temporaneo e si sostituisce con `os.replace` (atomico): un arresto a
metà lascia il vecchio stato, mai uno corrotto. Ogni evento (ingresso, uscita,
rifiuto) va anche in `events.jsonl`, in sola aggiunta.

Un solo processo scrive (lo scheduler): l'accesso è serializzato da un lock.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.paper.models import Libro, PaperBook

LIBRI: tuple[Libro, ...] = ("QUANT", "AI")
MAX_RIFIUTI = 50


class PaperLedger:
    def __init__(self, cartella: Path) -> None:
        self.cartella = cartella
        self._file = cartella / "ledger.json"
        self._eventi = cartella / "events.jsonl"
        self.lock = asyncio.Lock()
        self.books: dict[str, PaperBook] = {}

    def carica(self) -> None:
        if self._file.exists():
            dati = json.loads(self._file.read_text(encoding="utf-8"))
            self.books = {k: PaperBook.model_validate(v) for k, v in dati["books"].items()}

    def inizializza(self, saldo: float, valuta: str, adesso: datetime) -> None:
        for nome in LIBRI:
            if nome not in self.books:
                self.books[nome] = PaperBook(
                    name=nome,
                    currency=valuta,
                    initial_balance=saldo,
                    balance=saldo,
                    created_at=adesso,
                )
        self.salva()

    def salva(self) -> None:
        self.cartella.mkdir(parents=True, exist_ok=True)
        temporaneo = self._file.with_suffix(".tmp")
        temporaneo.write_text(
            json.dumps(
                {"books": {k: b.model_dump(mode="json") for k, b in self.books.items()}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(temporaneo, self._file)

    def evento(self, tipo: str, istante: datetime, **dati: Any) -> None:
        self.cartella.mkdir(parents=True, exist_ok=True)
        riga = json.dumps(
            {"type": tipo, "at": istante.isoformat(), **dati}, ensure_ascii=False, default=str
        )
        with self._eventi.open("a", encoding="utf-8") as f:
            f.write(riga + "\n")

    def rifiuto(self, libro: str, istante: datetime, simbolo: str, motivi: list[str]) -> None:
        b = self.books[libro]
        b.rejections.append({"at": istante.isoformat(), "symbol": simbolo, "reasons": motivi})
        del b.rejections[:-MAX_RIFIUTI]
        self.evento("REJECTED", istante, book=libro, symbol=simbolo, reasons=motivi)

    def ultimi_eventi(self, n: int = 50) -> list[dict[str, Any]]:
        if not self._eventi.exists():
            return []
        righe = self._eventi.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(r) for r in reversed(righe)]
