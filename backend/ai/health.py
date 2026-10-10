"""Salute del provider AI: funziona? se no, perché? quanto stiamo consumando?

Nasce da un caso reale (7 ottobre 2026): il credito OpenAI si è esaurito di notte e
per ore il conto AI non ha operato senza che la dashboard lo dicesse — l'errore era
solo dentro le singole tesi. `ClientMonitorato` avvolge il client LLM di tutti i
componenti (analisi, tesi, notizie) e registra ogni esito qui.

Il motivo del guasto è classificato in italiano (credito, limite, chiave, rete); il
testo grezzo del provider non viene mostrato. Il consumo (chiamate e token del giorno
UTC) è salvato su file per sopravvivere ai riavvii.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.ai.providers.base import LlmClient, RispostaLlm
from backend.core.clock import Clock
from backend.core.errors import AIOutputInvalidError, AIProviderError

#: (parole nel messaggio del provider, tipo, spiegazione per l'utente)
_MOTIVI: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("insufficient_quota", "credit_balance", "no credits", "billing"),
        "CREDITO",
        "Credito del provider esaurito: ricarica dalla pagina di fatturazione.",
    ),
    (
        ("authentication", "invalid_api_key", "incorrect api key", "401"),
        "CHIAVE",
        "Chiave API rifiutata: controlla la chiave nel file .env.",
    ),
    (
        ("ratelimit", "rate limit", "429"),
        "LIMITE",
        "Troppe richieste al provider: riprova da solo al ciclo successivo.",
    ),
    (
        ("timeout", "connection", "connect"),
        "RETE",
        "Il provider non risponde (rete o servizio): riprova da solo.",
    ),
    (
        ("nessun provider",),
        "NON_CONFIGURATO",
        "Nessun provider AI configurato.",
    ),
)


def classifica_errore(messaggio: str) -> tuple[str, str]:
    testo = messaggio.lower()
    for parole, tipo, spiegazione in _MOTIVI:
        if any(p in testo for p in parole):
            return tipo, spiegazione
    return "ERRORE", "Il provider AI ha restituito un errore."


class SaluteAI:
    def __init__(self, clock: Clock, file: Path | None = None) -> None:
        self._clock = clock
        self._file = file
        self.ok: bool | None = None  # None = nessuna chiamata ancora
        self.tipo: str | None = None
        self.spiegazione: str | None = None
        self.dal: datetime | None = None
        self.ultimo_ok: datetime | None = None
        self.ultimo_errore: datetime | None = None
        self.errori_consecutivi = 0
        self.consumo: dict[str, Any] = {}
        #: Chiamato a ogni passaggio funziona ↔ guasto (es. evento nel diario paper).
        self.al_cambio: Callable[[bool, str | None, str | None], None] | None = None
        if file is not None and file.exists():
            try:
                self.consumo = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.consumo = {}

    def _giorno(self) -> dict[str, int]:
        oggi = self._clock.now().date().isoformat()
        if self.consumo.get("day") != oggi:
            self.consumo = {"day": oggi, "calls": 0, "failures": 0, "input": 0, "output": 0}
        return self.consumo

    def _salva(self) -> None:
        if self._file is None:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.consumo), encoding="utf-8")
            os.replace(tmp, self._file)
        except OSError:
            pass  # il contatore è informativo: un errore di disco non ferma l'AI

    def _cambia(self, ok: bool) -> None:
        if self.ok is ok:
            return
        self.ok = ok
        self.dal = self._clock.now()
        if self.al_cambio is not None:
            self.al_cambio(ok, self.tipo, self.spiegazione)

    def successo(self, r: RispostaLlm) -> None:
        g = self._giorno()
        g["calls"] += 1
        g["input"] += r.input_tokens or 0
        g["output"] += r.output_tokens or 0
        self._salva()
        self.ultimo_ok = self._clock.now()
        self.errori_consecutivi = 0
        self.tipo = self.spiegazione = None
        self._cambia(True)

    def guasto(self, errore: AIProviderError) -> None:
        g = self._giorno()
        g["calls"] += 1
        g["failures"] += 1
        self._salva()
        self.ultimo_errore = self._clock.now()
        self.errori_consecutivi += 1
        self.tipo, self.spiegazione = classifica_errore(str(errore))
        self._cambia(False)

    def stato(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "kind": self.tipo,
            "message": self.spiegazione,
            "since": self.dal.isoformat() if self.dal else None,
            "last_ok_at": self.ultimo_ok.isoformat() if self.ultimo_ok else None,
            "last_error_at": self.ultimo_errore.isoformat() if self.ultimo_errore else None,
            "consecutive_failures": self.errori_consecutivi,
            "usage_today": dict(self._giorno()),
        }


class ClientMonitorato:
    """Stesso contratto del client LLM; in più registra ogni esito in `SaluteAI`."""

    def __init__(self, interno: LlmClient, salute: SaluteAI) -> None:
        self._interno = interno
        self._salute = salute
        self.name = interno.name
        self.model = interno.model

    async def completa_json(self, **kw: Any) -> RispostaLlm:
        try:
            r = await self._interno.completa_json(**kw)
        except AIOutputInvalidError:
            # Il provider ha risposto: un output non valido non è un guasto del servizio.
            raise
        except AIProviderError as errore:
            self._salute.guasto(errore)
            raise
        self._salute.successo(r)
        return r
