"""Endpoint di stato.

`/api/health` resta **pubblico**: lo interrogano il docker healthcheck e la
pagina System Health prima di avere un token, e soprattutto deve rispondere
anche quando tutto il resto è rotto. Non espone credenziali: solo stati, motivi
e latenze.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from backend.api.deps import ContainerDep, HealthServiceDep
from backend.core.enums import HealthStatus
from backend.schemas.health import SystemHealth

router = APIRouter(prefix="/health", tags=["Stato del sistema"])


@router.get(
    "",
    response_model=SystemHealth,
    summary="Stato complessivo del sistema",
    description=(
        "Dice se il sistema può operare e, in caso contrario, quali condizioni "
        "lo bloccano. Il codice HTTP riflette lo stato: 200 se sano o degradato, "
        "503 se un componente critico è fuori uso."
    ),
)
async def get_health(health: HealthServiceDep, response: Response) -> SystemHealth:
    result = await health.check()
    if result.status is HealthStatus.UNHEALTHY:
        # Codice coerente con lo stato: un orchestratore o un monitor esterno
        # deve poter reagire senza interpretare il corpo della risposta.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.get(
    "/live",
    summary="Il processo è in esecuzione",
    description="Controllo minimo: non interroga nessuna risorsa esterna.",
)
async def get_liveness(container: ContainerDep) -> dict[str, object]:
    return {
        "stato": "attivo",
        "uptime_secondi": round(container.uptime_seconds, 1),
        "modalita": str(container.settings.app.effective_mode),
    }
