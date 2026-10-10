"""Limiti di rischio modificati dall'utente dalla dashboard (sopra quelli del `.env`).

Regole, decise con l'utente il 2026-10-06 (supera in parte l'invariante 4):

* li modifica **solo l'utente**, con un'azione esplicita e confermata nella dashboard;
  l'AI non ha alcun accesso a questo modulo (nessun import da `ai/`);
* ogni campo ha un **tetto di sicurezza** che nemmeno l'utente può superare da qui
  (per andare oltre serve il `.env`, consapevolmente);
* **mai in LIVE**: con `APP_MODE=LIVE` le modifiche da interfaccia sono rifiutate;
* ogni modifica finisce in un registro di sola aggiunta (`var/risk_audit.jsonl`),
  con valore precedente, nuovo e motivo;
* il risultato è un **nuovo** `RiskLimits` validato e immutabile, che sostituisce il
  precedente: nessun oggetto viene modificato sul posto.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config.paths import resolve
from backend.config.risk_limits import RiskLimits
from backend.core.errors import ConfigurationError

FILE_OVERRIDE = "risk_overrides.json"
FILE_REGISTRO = "risk_audit.jsonl"


def _cartella(settings: Any) -> Path:
    """Nella cartella paper configurata: nei test è temporanea, in uso è var/paper."""
    return resolve(settings.paper.data_dir)


#: Campo → (minimo, massimo) ammessi da interfaccia. Il resto si cambia solo nel `.env`.
MODIFICABILI: dict[str, tuple[float, float] | None] = {
    "max_risk_per_trade_pct": (0.05, 2.0),
    "max_open_risk_pct": (0.1, 6.0),
    "max_open_positions": (1, 10),
    "daily_max_loss_pct": (0.2, 10.0),
    "weekly_max_loss_pct": (0.5, 20.0),
    "max_drawdown_pct": (1.0, 30.0),
    "daily_profit_target_pct": (0.1, 20.0),
    "minimum_rr": (0.5, 5.0),
    "min_stop_distance_atr": (0.1, 5.0),
    "max_stop_distance_atr": (0.5, 10.0),
    "max_spread_atr_ratio": (0.05, 1.0),
    "max_position_size_lots": (0.01, 5.0),
    "allowed_sessions_raw": None,
    "allowed_weekdays_raw": None,
}


def _attuali(limiti: RiskLimits) -> dict[str, Any]:
    return {k: getattr(limiti, k) for k in RiskLimits.model_fields}


def valida(limiti: RiskLimits, modifiche: dict[str, Any]) -> RiskLimits:
    errori: list[str] = []
    for campo, valore in modifiche.items():
        if campo not in MODIFICABILI:
            errori.append(f"{campo}: non modificabile da interfaccia")
            continue
        tetto = MODIFICABILI[campo]
        if tetto is not None and valore is not None:
            minimo, massimo = tetto
            if not minimo <= float(valore) <= massimo:
                errori.append(f"{campo}: {valore} fuori da [{minimo}, {massimo}]")
    if errori:
        raise ConfigurationError("Modifica rifiutata: " + "; ".join(errori))
    try:
        nuovo = RiskLimits.model_validate({**_attuali(limiti), **modifiche})
        # Le proprietà derivate si calcolano subito: un formato di sessione errato
        # deve fallire qui, non alla prima decisione.
        _ = nuovo.allowed_sessions, nuovo.allowed_weekdays
    except Exception as e:
        raise ConfigurationError(f"Modifica rifiutata: {e}") from e
    if nuovo.min_stop_distance_atr >= nuovo.max_stop_distance_atr:
        raise ConfigurationError("Modifica rifiutata: distanza minima dello stop ≥ massima.")
    return nuovo


def _salva_override(cartella: Path, modifiche: dict[str, Any]) -> None:
    file = cartella / FILE_OVERRIDE
    esistenti = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    esistenti.update(modifiche)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(esistenti, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, file)


def applica(settings: Any, modifiche: dict[str, Any], *, motivo: str, ora: datetime) -> RiskLimits:
    """Valida, applica e registra. Restituisce i nuovi limiti attivi."""
    if str(settings.app.effective_mode) == "LIVE":
        raise ConfigurationError("In LIVE i limiti si cambiano solo nel file .env, con riavvio.")
    vecchi = settings.risk
    nuovo = valida(vecchi, modifiche)
    _salva_override(_cartella(settings), modifiche)
    registro = _cartella(settings) / FILE_REGISTRO
    registro.parent.mkdir(parents=True, exist_ok=True)
    with registro.open("a", encoding="utf-8") as f:
        for campo, valore in modifiche.items():
            f.write(
                json.dumps(
                    {
                        "at": ora.isoformat(),
                        "field": campo,
                        "old": getattr(vecchi, campo),
                        "new": valore,
                        "reason": motivo,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    settings.risk = nuovo
    return nuovo


def carica_all_avvio(settings: Any) -> list[str]:
    """Applica gli override salvati sopra al `.env`. Override non validi: ignorati e segnalati."""
    file = _cartella(settings) / FILE_OVERRIDE
    if not file.exists():
        return []
    modifiche = json.loads(file.read_text(encoding="utf-8"))
    try:
        settings.risk = valida(settings.risk, modifiche)
        return [f"Limiti modificati da interfaccia: {', '.join(sorted(modifiche))}."]
    except ConfigurationError as e:
        return [f"Override dei limiti ignorati: {e}"]


def registro(settings: Any, n: int = 30) -> list[dict[str, Any]]:
    file = _cartella(settings) / FILE_REGISTRO
    if not file.exists():
        return []
    return [json.loads(r) for r in file.read_text(encoding="utf-8").splitlines()[-n:]][::-1]


def override_attivi(settings: Any) -> dict[str, Any]:
    file = _cartella(settings) / FILE_OVERRIDE
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
