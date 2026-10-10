"""Comandi dell'utente dalla dashboard: kill switch, pausa, mercati, strategie, limiti,
chiusura manuale delle posizioni paper.

Nessuno di questi endpoint scrive sul conto XM: agiscono sullo scheduler e sul conto
paper. Le azioni sono registrate (controlli, registro dei limiti, diario eventi).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.api.deps import ContainerDep, Protected
from backend.config import risk_overrides
from backend.core.errors import ConfigurationError
from backend.paper.trailing import REGOLE

router = APIRouter(tags=["Comandi dell'utente"], dependencies=[Protected])


def _ap(request: Request):  # type: ignore[no-untyped-def]
    ap = getattr(request.app.state, "autopilot", None)
    if ap is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Scheduler non attivo.")
    return ap


def stato_comandi(ap: Any, container: Any) -> dict[str, Any]:
    ks = container.kill_switch.state()
    r = container.settings.risk
    return {
        "paused": ap.controlli.in_pausa,
        "symbols": ap.controlli.stato["symbols"],
        "strategies": ap.controlli.stato["strategies"],
        "inverted": ap.controlli.stato["inverted"],
        "quant_target_r": ap.controlli.target_quant,
        "trailing": {
            "books": ap.controlli.stato["trailing"],
            "rules": [
                {"id": k, "name": r.nome, "description": r.descrizione} for k, r in REGOLE.items()
            ],
        },
        "log": list(reversed(ap.controlli.stato["log"][-20:])),
        "kill_switch": {
            "engaged": ks.engaged,
            "reason": ks.reason,
            "since": ks.engaged_at.isoformat() if ks.engaged_at else None,
        },
        "risk": {
            "editable": {
                campo: {
                    "value": getattr(r, campo),
                    "min": tetto[0] if tetto else None,
                    "max": tetto[1] if tetto else None,
                }
                for campo, tetto in risk_overrides.MODIFICABILI.items()
            },
            "overrides": risk_overrides.override_attivi(container.settings),
            "audit": risk_overrides.registro(container.settings, 20),
            "live_locked": str(container.settings.app.effective_mode) == "LIVE",
        },
    }


@router.get("/controls", summary="Stato dei comandi")
async def get_controls(request: Request, container: ContainerDep) -> dict[str, Any]:
    return stato_comandi(_ap(request), container)


class Interruttore(BaseModel):
    enabled: bool


class Mercato(Interruttore):
    symbol: str


class Strategia(Interruttore):
    book: Literal["QUANT", "AI"]


class StopMobile(BaseModel):
    book: Literal["QUANT", "AI"]
    #: None = spento.
    rule: str | None = None


class TargetQuant(BaseModel):
    #: None = regola standard (2R).
    target_r: float | None = None


class Inversione(BaseModel):
    book: Literal["QUANT", "AI"]
    enabled: bool


class Pausa(BaseModel):
    paused: bool


class KillSwitchComando(BaseModel):
    engage: bool
    reason: str = Field(default="", max_length=300)


class ModificaRischio(BaseModel):
    changes: dict[str, Any]
    reason: str = Field(min_length=3, max_length=300)
    confirm: Literal[True]


class Chiusura(BaseModel):
    book: Literal["QUANT", "AI"]
    position_id: str


class ChiudiTutto(BaseModel):
    book: Literal["QUANT", "AI"] | None = None
    confirm: Literal[True]


@router.post("/controls/pause", summary="Pausa o ripresa delle nuove aperture")
async def pausa(request: Request, corpo: Pausa, container: ContainerDep) -> dict[str, Any]:
    ap = _ap(request)
    ap.controlli.pausa(corpo.paused, container.clock.now())
    return stato_comandi(ap, container)


@router.post("/controls/symbols", summary="Attiva o disattiva un mercato")
async def mercato(request: Request, corpo: Mercato, container: ContainerDep) -> dict[str, Any]:
    ap = _ap(request)
    try:
        ap.controlli.mercato(corpo.symbol, corpo.enabled, container.clock.now())
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Strumento non autorizzato: {e}") from e
    return stato_comandi(ap, container)


@router.post("/controls/strategies", summary="Attiva o disattiva una strategia")
async def strategia(request: Request, corpo: Strategia, container: ContainerDep) -> dict[str, Any]:
    ap = _ap(request)
    ap.controlli.strategia(corpo.book, corpo.enabled, container.clock.now())
    return stato_comandi(ap, container)


@router.post("/controls/trailing", summary="Stop mobile per conto (vale per le nuove posizioni)")
async def stop_mobile(
    request: Request, corpo: StopMobile, container: ContainerDep
) -> dict[str, Any]:
    ap = _ap(request)
    try:
        ap.controlli.stop_mobile(corpo.book, corpo.rule, container.clock.now())
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Regola sconosciuta: {e}") from e
    return stato_comandi(ap, container)


@router.post("/controls/quant-target", summary="Target di QUANT in R (nuove aperture)")
async def target_quant(
    request: Request, corpo: TargetQuant, container: ContainerDep
) -> dict[str, Any]:
    ap = _ap(request)
    try:
        ap.controlli.imposta_target_quant(corpo.target_r, container.clock.now())
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return stato_comandi(ap, container)


@router.post("/controls/invert", summary="Inverte i segnali di un conto (esperimento)")
async def inverti_segnali(
    request: Request, corpo: Inversione, container: ContainerDep
) -> dict[str, Any]:
    ap = _ap(request)
    ap.controlli.inversione(corpo.book, corpo.enabled, container.clock.now())
    return stato_comandi(ap, container)


@router.post("/controls/kill-switch", summary="Attiva o rilascia il kill switch globale")
async def kill_switch(
    request: Request, corpo: KillSwitchComando, container: ContainerDep
) -> dict[str, Any]:
    ap = _ap(request)
    if corpo.engage:
        container.kill_switch.engage(corpo.reason or "Attivato dalla dashboard")
    else:
        container.kill_switch.release()
    ap.ledger.evento(
        "KILL_SWITCH",
        container.clock.now(),
        reason=("ON: " if corpo.engage else "OFF: ") + (corpo.reason or "dashboard"),
    )
    return stato_comandi(ap, container)


@router.post("/controls/risk", summary="Modifica dei limiti di rischio (con conferma)")
async def rischio(
    request: Request, corpo: ModificaRischio, container: ContainerDep
) -> dict[str, Any]:
    ap = _ap(request)
    try:
        risk_overrides.applica(
            container.settings, corpo.changes, motivo=corpo.reason, ora=container.clock.now()
        )
    except ConfigurationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return stato_comandi(ap, container)


@router.post("/paper/close", summary="Chiusura manuale di una posizione paper")
async def chiudi(request: Request, corpo: Chiusura) -> dict[str, str]:
    ap = _ap(request)
    if ap.executor is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Esecutore paper non attivo.")
    return {"result": await ap.executor.chiudi_manuale(corpo.book, corpo.position_id)}


@router.post("/paper/close-all", summary="Chiude tutte le posizioni paper (o di un conto)")
async def chiudi_tutto(request: Request, corpo: ChiudiTutto) -> dict[str, Any]:
    ap = _ap(request)
    if ap.executor is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Esecutore paper non attivo.")
    esiti = []
    for nome, b in ap.ledger.books.items():
        if corpo.book not in (None, nome):
            continue
        for p in list(b.positions):
            esiti.append(
                {
                    "book": nome,
                    "symbol": p.plan.canonical,
                    "result": await ap.executor.chiudi_manuale(nome, p.position_id),
                }
            )
    return {"closed": esiti}
