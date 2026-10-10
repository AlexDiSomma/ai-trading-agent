"""Endpoint dell'AI Market Analyst — fase 5A, READ ONLY.

`POST /api/ai/market-analysis` produce un'analisi del contesto globale corrente e la
registra in modo immutabile. Non tocca il conto, non crea intenti, non propone
ordini: restituisce testo strutturato e il suo stato (AVAILABLE / UNAVAILABLE).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.ai.contracts import AnalysisRecord
from backend.ai.journal import AnalysisJournal
from backend.api.deps import ContainerDep, Protected
from backend.core.errors import TradingAgentError

router = APIRouter(prefix="/ai", tags=["AI Market Analyst (read only)"], dependencies=[Protected])


@router.post(
    "/market-analysis",
    response_model=AnalysisRecord,
    summary="Analisi AI del contesto globale (sola lettura)",
    description=(
        "Calcola (o riusa dalla cache di 60 s) il contesto globale e chiede al modello "
        "un'analisi strutturata: sintesi, temi, scenari, evidenze a favore e contrarie, "
        "incertezze, strumenti da osservare, condizioni di invalidazione. Output non "
        "valido = `status: UNAVAILABLE` con i motivi. Nessun ordine, nessuna size."
    ),
)
async def create_market_analysis(container: ContainerDep) -> AnalysisRecord:
    try:
        _, contesto = await container.require_global_intelligence().build()
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return await container.market_ai.analizza(contesto)


@router.get("/market-analysis", summary="Ultime analisi registrate (diario immutabile)")
async def list_market_analyses(
    container: ContainerDep,
    limite: int = Query(10, ge=1, le=100),
) -> list[dict[str, Any]]:
    from backend.config.paths import resolve

    diario = AnalysisJournal(resolve(container.settings.ai.journal_dir))
    risultato = []
    for percorso in diario.elenco(limite):
        record = AnalysisJournal.leggi(percorso)
        risultato.append(
            {
                "record_id": record.record_id,
                "context_as_of": record.input.context_as_of,
                "status": record.status,
                "engine_regime": record.input.engine_regime,
                "model": record.call.model_snapshot or record.call.model_requested,
                "failure_reasons": record.failure_reasons,
                "integrity_sha256": record.integrity_sha256,
            }
        )
    return risultato
