"""Dal `GlobalMarketContext` all'ingresso del modello: compatto, citabile, versionato.

Il modello non riceve tutto il contesto (decine di migliaia di righe con le serie per
timeframe): riceve ciò che serve per ragionare, e **ogni elemento ha un riferimento**
(`ref`) che può citare. L'elenco dei riferimenti ammessi viene registrato con
l'ingresso: un'evidenza che cita un riferimento inesistente rende l'analisi invalida.

Funzione pura: stesso contesto → stesso ingresso → stesso hash.
"""

from __future__ import annotations

from typing import Any

from backend.ai.contracts import AnalysisInput
from backend.ai.prompts import PROMPT_VERSION
from backend.config.global_universe import UNIVERSO, VALUTE
from backend.schemas.global_market import GlobalMarketContext


def _r(v: float | None, cifre: int = 3) -> float | None:
    return None if v is None else round(v, cifre)


def _tf(per_tf: dict) -> dict[str, float | None]:  # type: ignore[type-arg]
    return {str(k): _r(v) for k, v in per_tf.items()}


def _verso(punteggio: float | None) -> str:
    if punteggio is None:
        return "UNKNOWN"
    if punteggio >= 0.5:
        return "RISK_ON (forte)"
    if punteggio > 0.1:
        return "RISK_ON (debole)"
    if punteggio <= -0.5:
        return "RISK_OFF (forte)"
    if punteggio < -0.1:
        return "RISK_OFF (debole)"
    return "NEUTRAL"


def _vix_in_parole(livello: object, percentile: float | None) -> str:
    if percentile is None:
        return "non interpretabile: storia insufficiente"
    posizione = f"{percentile:.0%} dell'ultimo anno"
    if percentile <= 0.25:
        return f"VIX BASSO ({posizione}): il mercato prezza poca paura, coerente con RISK_ON."
    if percentile >= 0.75:
        return f"VIX ALTO ({posizione}): il mercato prezza paura, coerente con RISK_OFF."
    return f"VIX nella norma ({posizione}): nessuna indicazione forte."


def costruisci_ingresso(c: GlobalMarketContext) -> AnalysisInput:
    refs: list[str] = []

    def ref(nome: str) -> str:
        refs.append(nome)
        return nome

    payload: dict[str, Any] = {
        "context_as_of": c.as_of.isoformat(),
        "engine_regime": str(c.regime),
        "market": {
            "ref": ref("market_phase"),
            "phase": str(c.market_phase),
            "reason": c.market_phase_reason,
        },
        "regime": {
            "ref": ref("regime"),
            "value": str(c.regime),
            "reasons": list(c.regime_reasons),
        },
        "risk_sentiment": {
            "ref": ref("risk_sentiment"),
            "label": str(c.risk_sentiment.label),
            "composite": _r(c.risk_sentiment.score),
            "agreement": _r(c.risk_sentiment.agreement),
            "by_timeframe": _tf(c.risk_sentiment.by_timeframe),
            "strong_risk_on": list(c.risk_sentiment.strong_risk_on),
            "strong_risk_off": list(c.risk_sentiment.strong_risk_off),
            "evidence": [
                {
                    "ref": ref(f"risk_sentiment.evidence.{e.name}"),
                    "name": e.name,
                    # Interpretazione deterministica: il modello non deve dedurre il
                    # verso dal segno (trovato dal vivo: leggeva un VIX basso come
                    # avversione al rischio).
                    "points_to": _verso(e.score),
                    "score_risk_on_positive": _r(e.score),
                    "weight": e.weight,
                    "description": e.description,
                }
                for e in c.risk_sentiment.evidence
            ],
        },
        "currency_strength": {
            "ref": ref("currency_strength"),
            "method": c.currency_strength.method,
            "graph_residual_median": _tf(c.currency_strength.graph_residual),
            "outliers_removed": {
                str(k): v for k, v in c.currency_strength.outliers_removed.items()
            },
            "currencies": [
                {
                    "ref": ref(f"currency_strength.{x.currency}"),
                    "currency": x.currency,
                    "rank": x.rank,
                    "score": _r(x.score),
                    "label": str(x.label),
                    "by_timeframe": _tf(x.by_timeframe),
                    "timeframes_agree": x.timeframes_agree,
                }
                for x in c.currency_strength.currencies
            ],
        },
        "equities": {
            "ref": ref("equities"),
            "label": str(c.equities.label),
            "score": _r(c.equities.score),
            "breadth": _r(c.equities.breadth),
            "by_timeframe": _tf(c.equities.by_timeframe),
            "members": {k: _r(v) for k, v in c.equities.members.items()},
        },
        "metals": {
            "ref": ref("metals"),
            "label": str(c.metals.label),
            "score": _r(c.metals.score),
            "members": {k: _r(v) for k, v in c.metals.members.items()},
        },
        "volatility": {
            "ref": ref("volatility"),
            "level": str(c.volatility.level),
            "trend": str(c.volatility.trend),
            "shock": c.volatility.shock,
            "median_atr_percentile": _tf(c.volatility.median_atr_percentile),
            "by_group": {k: str(v) for k, v in c.volatility.by_group.items()},
        },
        "instruments": [
            {
                "ref": ref(f"instruments.{i.canonical}"),
                "canonical": i.canonical,
                "group": str(i.group),
                "market_status": str(i.market_status),
                "score": _r(i.score),
                "label": str(i.label),
                "by_timeframe": _tf(i.by_timeframe),
            }
            for i in c.instruments
        ],
        "intermarket": {
            "ref": ref("intermarket"),
            "state": str(c.intermarket.state),
            "note": (
                "Correlazioni sui rendimenti: corrente = ultimi N, base = tutti i "
                "precedenti senza sovrapposizione. CONSISTENT = compatibile con la storia, "
                "non 'confermata'."
            ),
            "relationships": [
                {
                    "ref": ref(f"intermarket.{r.a}~{r.b}.{r.timeframe}"),
                    "pair": f"{r.a}~{r.b}",
                    "timeframe": str(r.timeframe),
                    "current": _r(r.current_correlation),
                    "current_n": r.current_sample,
                    "baseline": _r(r.baseline_correlation),
                    "baseline_n": r.baseline_sample,
                    "difference_z": _r(r.difference_z, 2),
                    "stability": _r(r.stability, 2),
                    "state": str(r.state),
                }
                for r in c.intermarket.relationships
            ],
        },
        "conflicts": [
            {
                "ref": ref(f"conflicts.{k.code}"),
                "code": k.code,
                "severity": str(k.severity),
                "description": k.description,
            }
            for k in c.conflicts
        ],
        "data_quality": {
            "ref": ref("data_quality"),
            "overall": str(c.data_quality),
            "reasons": list(c.data_quality_reasons),
            "by_source": [
                {
                    "ref": ref(f"source_quality.{q.domain}"),
                    "domain": str(q.domain),
                    "quality": str(q.quality),
                    "configured": q.configured,
                    "intended_use": str(q.intended_use),
                    "reasons": list(q.reasons),
                }
                for q in c.source_quality
            ],
        },
        "known_limitations": [
            {"ref": ref(f"limitations.{n}"), "text": t}
            for n, t in enumerate(c.known_limitations, start=1)
        ],
    }
    iv = c.implied_volatility
    if iv is not None:
        payload["implied_volatility"] = {
            "ref": ref("implied_volatility"),
            "source": iv.source,
            "intended_use": str(iv.intended_use),
            "value": iv.value,
            "day": str(iv.day) if iv.day else None,
            "percentile_1y": iv.percentile_1y,
            "change_20d": iv.change_20d,
            "level": str(iv.level),
            "trend": str(iv.trend),
            "quality": str(iv.quality.quality),
            "interpretation": _vix_in_parole(iv.level, iv.percentile_1y),
        }
    rt = c.rates
    if rt is not None:
        payload["rates_us"] = {
            "ref": ref("rates"),
            "source": rt.source,
            "intended_use": str(rt.intended_use),
            "day": str(rt.day) if rt.day else None,
            "yields_pct": rt.yields,
            "change_20d_bp": rt.change_20d_bp,
            "slope_2s10s_bp": rt.slope_2s10s_bp,
            "slope_3m10y_bp": rt.slope_3m10y_bp,
            "ten_year_trend": rt.ten_year_trend,
            "quality": str(rt.quality.quality),
        }
    dx = c.dollar_index
    if dx is not None:
        payload["dollar_index_cross_check"] = {
            "ref": ref("dollar_index"),
            "intended_use": str(dx.intended_use),
            "value": dx.value,
            "label": str(dx.label),
            "correlation_with_usd_strength": dx.correlation_with_usd_strength,
            "agrees_with_usd_strength": dx.agrees_with_usd_strength,
        }
    m = c.macro_event_risk
    payload["calendar"] = {
        "ref": ref("calendar"),
        "provider": m.calendar_provider,
        "available": m.calendar_available,
        "has_actuals": m.calendar_has_actuals,
        "coverage_end": m.calendar_coverage_end.isoformat() if m.calendar_coverage_end else None,
        "level": str(m.calendar_level),
        "reason": m.calendar_reason,
        "market_implied_uncertainty": str(m.market_implied_uncertainty),
        "implied_drivers": list(m.drivers),
        "events": [
            {
                "ref": ref(f"calendar.events.{e.event.event_id}"),
                "title": e.event.title,
                "currency": e.event.currency,
                "importance": str(e.event.importance),
                "scheduled_at": e.event.scheduled_at.isoformat(),
                "phase": str(e.phase),
                "minutes_to_event": e.minutes_to_event,
                "forecast": e.event.forecast,
                "previous": e.event.previous,
                "actual": e.actual,
                "surprise": e.surprise,
            }
            for e in m.events
        ],
        "next_high_impact": {
            v: {"title": e.title, "scheduled_at": e.scheduled_at.isoformat()}
            for v, e in m.next_high_impact.items()
        },
    }
    universo = tuple(s.canonical for s in UNIVERSO) + VALUTE
    payload["allowed_refs"] = list(refs)
    payload["universe"] = list(universo)
    return AnalysisInput(
        prompt_version=PROMPT_VERSION,
        context_as_of=c.as_of,
        context_fingerprint=c.input_fingerprint,
        engine_version=c.engine_version,
        engine_regime=c.regime,
        data_quality=str(c.data_quality),
        payload=payload,
        allowed_refs=tuple(refs),
        universe=universo,
        known_limitations=c.known_limitations,
    )
