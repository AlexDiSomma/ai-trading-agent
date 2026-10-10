"""Endpoint dell'intelligence globale — sola lettura.

`/api/global/context` risponde a "cosa sta succedendo complessivamente sui
mercati?" con dimensioni separate e qualità dichiarata. `/api/global/snapshot`
mostra da quali dati è nato: strumento per strumento, serie per serie, in che
stato erano. Nessuno dei due propone operazioni.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.deps import ContainerDep, Protected
from backend.core.errors import TradingAgentError
from backend.schemas.global_market import GlobalMarketContext, GlobalMarketSnapshot

router = APIRouter(prefix="/global", tags=["Intelligence globale"], dependencies=[Protected])


async def _costruisci(
    container: ContainerDep, force: bool
) -> tuple[GlobalMarketSnapshot, GlobalMarketContext]:
    try:
        servizio = container.require_global_intelligence()
        return await servizio.build(force=force)
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get(
    "/context",
    response_model=GlobalMarketContext,
    summary="Contesto globale dei mercati",
    description=(
        "Regime globale (RISK_ON, RISK_OFF, MIXED, TRANSITION, EVENT_DRIVEN, "
        "UNCERTAIN), sentiment di rischio, volatilità, forza delle valute, forza "
        "relativa, azioni, metalli, intermarket, rischio macro/eventi, conflitti e "
        "qualità per dimensione. Deterministico: `input_fingerprint` e "
        "`engine_version` identificano il contesto."
    ),
)
async def get_context(
    container: ContainerDep,
    force: bool = Query(False, description="Ignora la cache di 60 secondi."),
) -> GlobalMarketContext:
    return (await _costruisci(container, force))[1]


@router.get(
    "/snapshot",
    response_model=GlobalMarketSnapshot,
    summary="Dati in ingresso del contesto globale",
)
async def get_snapshot(container: ContainerDep) -> GlobalMarketSnapshot:
    return (await _costruisci(container, False))[0]


@router.get("/summary", summary="Sintesi leggibile del contesto globale")
async def get_summary(container: ContainerDep) -> dict[str, Any]:
    _, contesto = await _costruisci(container, False)
    return {
        "as_of": contesto.as_of,
        "sintesi": {riga.label: riga.value for riga in contesto.summary},
        "utilizzabile_per_decidere": contesto.usable_for_decisions,
        "conflitti": [c.description for c in contesto.conflicts],
        "avvisi": list(contesto.warnings),
    }


@router.get(
    "/providers",
    summary="Fonti della Global Intelligence e loro stato",
    description=(
        "XM/MT5 per gli strumenti di riferimento negoziabili; CBOE (VIX), Tesoro USA "
        "(rendimenti), Forex Factory (calendario) per i dati che XM non fornisce. "
        "Ogni fonte con stato, ultimo successo, ultimo errore e data dell'ultimo dato."
    ),
)
async def get_providers(container: ContainerDep) -> dict[str, Any]:
    try:
        servizio = container.require_global_intelligence()
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "broker": {"name": "xm/mt5", "ruolo": "fonte autorevole per strumenti negoziabili"},
        "esterni": [p.model_dump(mode="json") for p in servizio.fonti.stati()],
    }
