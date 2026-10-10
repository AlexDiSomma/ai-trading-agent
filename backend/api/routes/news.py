"""Notizie filtrate per la dashboard: elenco, fonti, freni attivi sugli strumenti."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status

from backend.api.deps import Protected

router = APIRouter(prefix="/news", tags=["Notizie"], dependencies=[Protected])


@router.get("", summary="Notizie recenti filtrate")
async def notizie(
    request: Request,
    ore: int = Query(default=24, ge=1, le=72),
    impatto: Literal["LOW", "MEDIUM", "HIGH"] = "LOW",
    asset: str | None = Query(default=None, max_length=10),
    confermate: bool = False,
) -> dict[str, Any]:
    ap = getattr(request.app.state, "autopilot", None)
    if ap is None or ap.news is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Servizio notizie non attivo.")
    n = ap.news
    elenco = n.recenti(
        ore=ore,
        asset={asset} if asset else None,
        solo_confermate=confermate,
        impatto_minimo=impatto,
    )
    freni = {
        simbolo: motivi for simbolo, tag in ap.asset_per_simbolo.items() if (motivi := n.freni(tag))
    }
    return {
        "items": [
            {
                **i.model_dump(mode="json", exclude={"extra"}),
                "impact": i.impact,
            }
            for i in elenco
        ],
        "sources": n.stato_fonti,
        "brakes": freni,
        "stats": {
            "total_kept": len(n.items),
            "discarded_irrelevant": n.scartate,
            "high_24h": len(n.recenti(ore=24, impatto_minimo="HIGH")),
            "last_collection": ap.stato["last"].get("news"),
        },
        "brake_minutes": request.app.state.settings.news.brake_minutes,
    }
