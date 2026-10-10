"""Dashboard dell'MVP paper: una lettura aggregata, più il comando "scansiona ora".

`GET /api/dashboard` restituisce tutto ciò che la pagina mostra in un colpo solo,
letto dallo stato dello scheduler e dai conti paper: non fa analisi né chiamate al
modello dentro la richiesta (ADR 0002), salvo il saldo del conto XM.

`POST /api/autopilot/scan` chiede una scansione immediata: è eseguita in background
dallo scheduler, non dentro la richiesta. Nessun endpoint scrive sul conto XM.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from backend import __version__
from backend.api.deps import ContainerDep, Protected
from backend.api.routes.controls import stato_comandi
from backend.core.errors import TradingAgentError
from backend.paper.executor import PaperExecutor
from backend.paper.performance import confronto, performance

router = APIRouter(tags=["Dashboard (paper)"], dependencies=[Protected])


def _autopilot(request: Request):  # type: ignore[no-untyped-def]
    return getattr(request.app.state, "autopilot", None)


def _globale(g: Any) -> dict[str, Any] | None:
    if g is None:
        return None
    forza = sorted(
        (c for c in g.currency_strength.currencies if c.score is not None),
        key=lambda c: -(c.score or 0),
    )
    return {
        "as_of": g.as_of.isoformat(),
        "market_phase": str(g.market_phase),
        "regime": str(g.regime),
        "regime_reasons": list(g.regime_reasons),
        "risk_sentiment": g.risk_sentiment.model_dump(mode="json", exclude={"evidence"}),
        "volatility": f"{g.volatility.level} / {g.volatility.trend}",
        "vix": g.implied_volatility.value if g.implied_volatility else None,
        "currency_strength": [
            {"currency": c.currency, "score": round(c.score or 0, 2), "label": str(c.label)}
            for c in forza
        ],
        "event_risk": g.macro_event_risk.model_dump(mode="json", exclude={"events"}),
        "next_events": [e.model_dump(mode="json") for e in g.macro_event_risk.events[:6]],
        "data_quality": str(g.data_quality),
        "data_quality_reasons": list(g.data_quality_reasons),
        "source_quality": [s.model_dump(mode="json") for s in g.source_quality],
        "known_limitations": list(g.known_limitations),
        "summary": [s.model_dump(mode="json") for s in g.summary],
    }


def _analisi(rec: Any) -> dict[str, Any] | None:
    if rec is None:
        return None
    a = rec.analysis
    return {
        "at": rec.input.context_as_of.isoformat(),
        "status": rec.status,
        "model": rec.call.model_snapshot or rec.call.model_requested,
        "failure_reasons": list(rec.failure_reasons[:4]),
        "summary": a.market_summary if a else None,
        "view_on_regime": a.analyst_view_on_regime if a else None,
        "themes": [{"title": t.title, "description": t.description} for t in a.dominant_themes]
        if a
        else [],
        "scenarios": [
            {
                "name": s.name,
                "likelihood": s.likelihood,
                "horizon_hours": s.horizon_hours,
                "narrative": s.narrative,
            }
            for s in a.scenarios
        ]
        if a
        else [],
        "uncertainties": [
            {"description": u.description, "missing": u.missing_information, "impact": u.impact}
            for u in a.uncertainties
        ]
        if a
        else [],
        "confidence_label": a.confidence if a else None,
    }


@router.get("/dashboard", summary="Stato completo per la dashboard (paper)")
async def dashboard(request: Request, container: ContainerDep) -> dict[str, Any]:
    ap = _autopilot(request)
    conto: dict[str, Any] | None = None
    errore_conto: str | None = None
    if container.broker is not None:
        try:
            a = await asyncio.wait_for(container.require_broker().get_account(), timeout=5)
            conto = {
                "login": a.login,
                "server": a.server,
                "currency": a.currency,
                "balance": a.balance,
                "equity": a.equity,
                "margin_free": a.margin_free,
                "account_type": a.account_type,
                "leverage": a.leverage,
            }
        except (TradingAgentError, TimeoutError) as e:
            errore_conto = str(e)
    ks = container.kill_switch.state()
    s = container.settings
    sistema = {
        "version": __version__,
        "mode": str(s.app.effective_mode),
        "live_enabled": False,
        "broker_connected": container.broker is not None,
        "broker_error": container.broker_error or errore_conto,
        "broker_time_source": container.broker_clock.estimate(now=container.clock.now()).source,
        "kill_switch": {"engaged": ks.engaged, "reason": ks.reason},
        "autopilot": ap.stato
        if ap
        else {
            "running": False,
            "last_error": {"start": "Scheduler disattivato (PAPER_AUTOPILOT_ENABLED=false)."},
        },
        "ai_provider": s.ai.provider,
        "ai_health": container.ai_health.stato(),
    }
    libri: dict[str, Any] = {}
    if ap is not None:
        for nome, b in ap.ledger.books.items():
            libri[nome] = {
                "performance": performance(b),
                "open_positions": [p.model_dump(mode="json") for p in b.positions],
                "closed_trades": [t.model_dump(mode="json") for t in reversed(b.trades[-100:])],
                "rejections": list(reversed(b.rejections[-15:])),
                "equity_now": round(PaperExecutor.equity(b), 2),
            }
    paragone = (
        confronto(ap.ledger.books["QUANT"], ap.ledger.books["AI"])
        if ap is not None and {"QUANT", "AI"} <= set(ap.ledger.books)
        else None
    )
    analisi = ap.analisi if ap else None
    if analisi is None:
        from backend.ai.journal import AnalysisJournal
        from backend.config.paths import resolve

        elenco = AnalysisJournal(resolve(s.ai.journal_dir)).elenco(1)
        analisi = AnalysisJournal.leggi(elenco[0]) if elenco else None
    r = s.risk
    return {
        "system": sistema,
        "account": conto,
        "global": _globale(ap.globale if ap else None),
        "opportunities": ap.opportunita if ap else [],
        "ai_analysis": _analisi(analisi),
        "ai_theses": ap.tesi if ap else {},
        "decisions": list(ap.decisioni)[:40] if ap else [],
        "books": libri,
        "comparison": paragone,
        "events": ap.ledger.ultimi_eventi(40) if ap else [],
        "controls": stato_comandi(ap, container) if ap is not None else None,
        "risk_notes": list(getattr(container, "risk_override_notes", [])),
        "news_summary": (
            {
                "high_24h": len(ap.news.recenti(ore=24, impatto_minimo="HIGH")),
                "brakes": {
                    simbolo: motivi
                    for simbolo, tag in ap.asset_per_simbolo.items()
                    if (motivi := ap.news.freni(tag))
                },
                "latest": [
                    {
                        "id": i.id,
                        "title": i.title,
                        "summary_it": i.summary_it,
                        "impact": i.impact,
                        "source": i.source,
                        "published_at": i.published_at.isoformat(),
                        "confirmed": i.confirmed,
                        "assets": list(i.assets),
                    }
                    for i in ap.news.recenti(ore=12, impatto_minimo="MEDIUM")[:5]
                ],
            }
            if ap is not None and ap.news is not None
            else None
        ),
        "live": (
            {
                "readiness": ap.prontezza,
                "breaker": ap.live.breaker.stato,
                "gateway_built": ap.live.gateway is not None,
                "notes": list(ap.live.note),
            }
            if ap is not None and ap.live is not None
            else None
        ),
        "settings": {
            "risk": {
                "max_risk_per_trade_pct": r.max_risk_per_trade_pct,
                "max_open_risk_pct": r.max_open_risk_pct,
                "max_open_positions": r.max_open_positions,
                "daily_max_loss_pct": r.daily_max_loss_pct,
                "max_drawdown_pct": r.max_drawdown_pct,
                "minimum_rr": r.minimum_rr,
                "stop_distance_atr": [r.min_stop_distance_atr, r.max_stop_distance_atr],
                "max_spread_atr_ratio": r.max_spread_atr_ratio,
                "max_position_size_lots": r.max_position_size_lots,
                "allowed_symbols": list(r.allowed_symbols),
                "allowed_sessions": r.allowed_sessions_raw,
                "allowed_weekdays": r.allowed_weekdays_raw,
                "block_high_impact_minutes": [
                    r.block_high_impact_before_minutes,
                    r.block_high_impact_after_minutes,
                ],
            },
            "paper": s.paper.model_dump(mode="json"),
        },
    }


@router.post("/autopilot/scan", summary="Scansione immediata (in background)")
async def scan_now(request: Request) -> dict[str, str]:
    ap = _autopilot(request)
    if ap is None or ap.executor is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Scheduler non attivo.")
    if ap.stato.get("busy") == "scan":
        return {"status": "già in corso"}
    # Riferimento tenuto dallo scheduler: il task non viene raccolto a metà.
    ap.scansione_manuale = asyncio.create_task(ap.scansione())
    return {"status": "avviata"}
