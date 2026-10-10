"""Fondazione LIVE: stato dei gate, esercitazione del kill switch, riarmo del breaker.

Nessuna rotta apre, modifica o chiude ordini: l'esecuzione reale parte solo dallo
scheduler, e solo con tutti i gate veri. La conferma dell'utente non si può dare
da qui: è un file scritto a mano (`var/LIVE_CONFIRMATION.json`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.api.deps import ContainerDep, Protected
from backend.execution.drills import esercitazione_kill_switch
from backend.execution.readiness import FRASE_CONFERMA

router = APIRouter(
    prefix="/live", tags=["LIVE (fondazione, disabilitato)"], dependencies=[Protected]
)


def _stack(request: Request):  # type: ignore[no-untyped-def]
    ap = getattr(request.app.state, "autopilot", None)
    if ap is None or ap.live is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Stack di esecuzione non costruito."
        )
    return ap


@router.get("/readiness", summary="Gate di attivazione del LIVE")
async def readiness(request: Request) -> dict[str, Any]:
    ap = _stack(request)
    esito = await ap.live.readiness.valuta()
    ap.prontezza = esito
    return {
        **esito,
        "breaker": ap.live.breaker.stato,
        "gateway_built": ap.live.gateway is not None,
        "notes": list(ap.live.note),
        "confirmation_file": "var/LIVE_CONFIRMATION.json",
        "confirmation_text": FRASE_CONFERMA,
    }


@router.post("/drills/kill-switch", summary="Esercitazione del kill switch (nessun ordine)")
async def drill(request: Request, container: ContainerDep) -> dict[str, Any]:
    ap = _stack(request)
    return await esercitazione_kill_switch(
        container.kill_switch,
        clock=container.clock,
        limiti=container.settings.risk,
        file=ap.live.drills_file,
        breaker_file=ap.live.drills_file.parent / "drill_breaker.json",
    )


class Riarmo(BaseModel):
    reason: str = Field(min_length=10, max_length=300)


@router.post("/breaker/reset", summary="Riarmo manuale del circuit breaker, con motivo")
async def reset_breaker(request: Request, corpo: Riarmo) -> dict[str, Any]:
    ap = _stack(request)
    ap.live.breaker.riarma(corpo.reason)
    return ap.live.breaker.stato
