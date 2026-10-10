"""Comandi dell'utente sullo scheduler: pausa, mercati e strategie attive, stop mobile.

Stato su file (`var/paper/controls.json`, scrittura atomica): sopravvive ai riavvii.
Ogni cambio finisce in un registro con istante e motivo. I comandi **riducono** ciò
che il sistema può fare (spegnere un mercato, una strategia, mettere in pausa le
aperture); la gestione delle posizioni già aperte continua sempre.

Lo stop mobile è l'unico comando che cambia la gestione: si sceglie per conto, vale per
le posizioni aperte **dopo** la scelta ed è spento di default (ADR 0027).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.paper.trailing import REGOLE

LIBRI = ("QUANT", "AI")
#: Target di QUANT sceglibile dall'utente, in R.
TARGET_QUANT = (1.0, 3.0)


class Controlli:
    def __init__(self, file: Path, simboli: tuple[str, ...]) -> None:
        self._file = file
        self.stato: dict[str, Any] = {
            "paused": False,
            "symbols": dict.fromkeys(simboli, True),
            "strategies": dict.fromkeys(LIBRI, True),
            "trailing": dict.fromkeys(LIBRI, None),
            "inverted": dict.fromkeys(LIBRI, False),
            #: Target di QUANT in R; None = quello della regola (2R).
            "quant_target_r": None,
            "log": [],
        }
        if file.exists():
            salvato = json.loads(file.read_text(encoding="utf-8"))
            self.stato["paused"] = bool(salvato.get("paused", False))
            self.stato["strategies"].update(salvato.get("strategies", {}))
            self.stato["log"] = salvato.get("log", [])[-100:]
            t = salvato.get("quant_target_r")
            self.stato["quant_target_r"] = float(t) if t is not None else None
            for libro, inv in salvato.get("inverted", {}).items():
                if libro in LIBRI:
                    self.stato["inverted"][libro] = bool(inv)
            for libro, regola in salvato.get("trailing", {}).items():
                # Una regola sconosciuta (rimossa dal codice) vale come spenta.
                if libro in LIBRI:
                    self.stato["trailing"][libro] = regola if regola in REGOLE else None
            for s, v in salvato.get("symbols", {}).items():
                if s in self.stato["symbols"]:
                    self.stato["symbols"][s] = bool(v)

    def _salva(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.stato, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._file)

    def _registra(self, ora: datetime, testo: str) -> None:
        self.stato["log"] = [*self.stato["log"][-99:], {"at": ora.isoformat(), "action": testo}]
        self._salva()

    @property
    def in_pausa(self) -> bool:
        return bool(self.stato["paused"])

    def mercato_attivo(self, simbolo: str) -> bool:
        return bool(self.stato["symbols"].get(simbolo, False))

    def strategia_attiva(self, libro: str) -> bool:
        return bool(self.stato["strategies"].get(libro, False))

    @property
    def target_quant(self) -> float | None:
        t = self.stato["quant_target_r"]
        return float(t) if t is not None else None

    def imposta_target_quant(self, target_r: float | None, ora: datetime) -> None:
        if target_r is not None and not TARGET_QUANT[0] <= target_r <= TARGET_QUANT[1]:
            raise ValueError(f"Target fuori da {TARGET_QUANT[0]}-{TARGET_QUANT[1]}R")
        self.stato["quant_target_r"] = target_r
        testo = f"{target_r:g}R" if target_r is not None else "2R (regola standard)"
        self._registra(ora, f"Target QUANT: {testo} (vale per le nuove aperture)")

    def invertito(self, libro: str) -> bool:
        return bool(self.stato["inverted"].get(libro, False))

    def inversione(self, libro: str, attiva: bool, ora: datetime) -> None:
        if libro not in LIBRI:
            raise KeyError(libro)
        self.stato["inverted"][libro] = attiva
        self._registra(
            ora,
            f"Segnali {libro} {'INVERTITI' if attiva else 'normali'} (vale per le nuove aperture)",
        )

    def regola_mobile(self, libro: str) -> str | None:
        regola = self.stato["trailing"].get(libro)
        return regola if regola in REGOLE else None

    def pausa(self, attiva: bool, ora: datetime) -> None:
        self.stato["paused"] = attiva
        self._registra(
            ora, "Scheduler in pausa (nessuna nuova apertura)" if attiva else "Scheduler ripreso"
        )

    def mercato(self, simbolo: str, attivo: bool, ora: datetime) -> None:
        if simbolo not in self.stato["symbols"]:
            raise KeyError(simbolo)
        self.stato["symbols"][simbolo] = attivo
        self._registra(ora, f"{simbolo} {'attivato' if attivo else 'disattivato'}")

    def strategia(self, libro: str, attiva: bool, ora: datetime) -> None:
        if libro not in LIBRI:
            raise KeyError(libro)
        self.stato["strategies"][libro] = attiva
        self._registra(ora, f"Strategia {libro} {'attivata' if attiva else 'disattivata'}")

    def stop_mobile(self, libro: str, regola: str | None, ora: datetime) -> None:
        if libro not in LIBRI:
            raise KeyError(libro)
        if regola is not None and regola not in REGOLE:
            raise ValueError(regola)
        self.stato["trailing"][libro] = regola
        testo = f"«{REGOLE[regola].nome}»" if regola else "spento"
        self._registra(ora, f"Stop mobile {libro}: {testo} (vale per le nuove posizioni)")
