"""Catalogo delle evidenze di uno strumento: ogni fatto citabile ha un `evidence_id`.

Il modello può affermare solo ciò che il catalogo contiene. Gli id sono stabili e
leggibili (`asset.XAUUSD.H1.trend`, `asset.EURUSD.H4.level.R1`) e si affiancano a
quelli del contesto globale della fase 5A (`currency_strength.USD`,
`risk_sentiment`, ...). Un claim con un id che non esiste qui non è sostenuto da
nessun dato.

Il catalogo contiene anche i numeri di riferimento che la validazione usa per
controllare la geometria del piano: prezzo, ATR H1, spread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.core.enums import LevelKind, Timeframe
from backend.schemas.context import AssetMarketContext

#: Timeframe esposti al modello. M5 resta fuori: è rumore per un orizzonte di ore.
TIMEFRAMES_EVIDENZA = (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15)
#: Livelli per lato e per timeframe.
LIVELLI_PER_LATO = 3


def _r(v: float | None, cifre: int) -> float | None:
    return None if v is None else round(v, cifre)


@dataclass(frozen=True)
class CatalogoEvidenze:
    canonical: str
    voci: dict[str, dict[str, Any]]
    #: Prezzo di riferimento all'istante della decisione (medio, o ultima chiusura).
    prezzo: float
    atr_h1: float | None
    spread_prezzo: float
    cifre: int
    #: Qualità complessiva dei dati dello strumento.
    qualita: str
    note: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self.voci)


def costruisci_catalogo(
    ctx: AssetMarketContext, canonical: str, *, prezzo: float | None = None
) -> CatalogoEvidenze:
    c = ctx.contract.digits + 1
    p = f"asset.{canonical}"
    voci: dict[str, dict[str, Any]] = {}

    m = ctx.multi_timeframe
    voci[f"{p}.mtf"] = {
        "structural_bias_D1_H4": m.structural_bias,
        "operational_bias_H1": m.operational_bias,
        "setup_bias_M15": m.setup_bias,
        "execution_bias_M5": m.execution_bias,
        "alignment": m.alignment,
        "aligned_timeframes": f"{m.aligned_timeframes}/{m.total_timeframes}",
        "structure_execution_conflict": m.structure_execution_conflict,
        "dominant_regime": str(m.dominant_regime),
        "notes": list(m.notes),
    }

    q = ctx.quote
    spread = (q.ask - q.bid) if q.bid and q.ask else ctx.contract.spread_price
    voci[f"{p}.quote"] = {
        "state": str(q.state),
        "market_status": str(q.market_status),
        "bid": q.bid,
        "ask": q.ask,
        "spread_price": _r(spread, c),
        "spread_atr_ratio_H1": _r(q.spread_atr_ratio, 3),
    }

    b = ctx.global_backdrop
    if b is not None:
        voci[f"{p}.backdrop"] = {
            "global_regime": str(b.regime),
            "volatility": f"{b.volatility_level}/{b.volatility_trend}",
            "base_strength": str(b.base_strength.label) if b.base_strength else None,
            "quote_strength": str(b.quote_strength.label) if b.quote_strength else None,
            "strength_differential": _r(b.strength_differential, 3),
            "group_direction": str(b.group_direction) if b.group_direction else None,
            "event_windows": [w.model_dump(mode="json") for w in b.event_windows][:3],
            "next_high_impact": [
                f"{e.currency} {e.title} {e.scheduled_at:%d/%m %H:%M} UTC"
                for e in b.next_high_impact[:4]
            ],
        }

    for tf in TIMEFRAMES_EVIDENZA:
        a = ctx.timeframes.get(tf)
        if a is None:
            continue
        i = a.indicators
        t = f"{p}.{tf}"
        voci[f"{t}.trend"] = {
            "regime": str(a.trend.regime),
            "direction": a.trend.direction,
            "score": round(a.trend.score, 3),
            "factor_agreement": round(a.trend.confidence, 3),
            "reasons": list(a.trend.reasons[:3]),
            "last_closed_bar_utc_or_broker": a.last_closed_at.isoformat(),
            "quality": str(a.quality),
        }
        s = a.structure
        voci[f"{t}.structure"] = {
            "trend": str(s.trend),
            "last_swing_high": _r(s.last_swing_high, c),
            "last_swing_low": _r(s.last_swing_low, c),
            "previous_swing_high": _r(s.previous_swing_high, c),
            "previous_swing_low": _r(s.previous_swing_low, c),
            "break": str(s.structure_break),
            "change_of_character": s.change_of_character,
        }
        r = a.breakout
        voci[f"{t}.breakout"] = {
            "kind": str(r.kind),
            "range_high": _r(r.range_high, c),
            "range_low": _r(r.range_low, c),
            "range_width_atr": _r(r.range_width_atr, 2),
            "broken_level": _r(r.broken_level, c),
            "bars_since_break": r.bars_since_break,
        }
        voci[f"{t}.ema"] = {
            "close": _r(i.close, c),
            "ema20": _r(i.ema20, c),
            "ema50": _r(i.ema50, c),
            "ema200": _r(i.ema200, c),
            "ema_alignment": i.ema_alignment,
            "price_vs_ema200_pct": _r(i.price_vs_ema200_pct, 3),
            "ema50_slope_pct_per_bar": _r(i.ema50_slope_pct, 4),
        }
        voci[f"{t}.momentum"] = {
            "rsi14": _r(i.rsi14, 1),
            "macd_histogram": _r(i.macd_histogram, c),
            "momentum_5": _r(i.momentum_5, 3),
            "momentum_20": _r(i.momentum_20, 3),
            "momentum_60": _r(i.momentum_60, 3),
            "adx14": _r(i.adx14, 1),
            "di_plus": _r(i.di_plus, 1),
            "di_minus": _r(i.di_minus, 1),
        }
        voci[f"{t}.volatility"] = {
            "atr14": _r(i.atr14, c),
            "atr_pct": _r(i.atr_pct, 3),
            "bollinger_upper": _r(i.bollinger_upper, c),
            "bollinger_lower": _r(i.bollinger_lower, c),
            "bollinger_width_rank": _r(i.bollinger_width_rank, 2),
        }
        supporti = sorted(
            (lv for lv in a.levels if lv.kind is LevelKind.SUPPORT),
            key=lambda lv: -lv.price,
        )[:LIVELLI_PER_LATO]
        resistenze = sorted(
            (lv for lv in a.levels if lv.kind is LevelKind.RESISTANCE),
            key=lambda lv: lv.price,
        )[:LIVELLI_PER_LATO]
        for prefisso, gruppo in (("S", supporti), ("R", resistenze)):
            for n, lv in enumerate(gruppo, start=1):
                voci[f"{t}.level.{prefisso}{n}"] = {
                    "price": _r(lv.price, c),
                    "touches": lv.touches,
                    "distance_atr": _r(lv.distance_atr, 2),
                    "strength": round(lv.strength, 2),
                }

    h1 = ctx.timeframes.get(Timeframe.H1)
    atr_h1 = h1.indicators.atr14 if h1 else None
    if prezzo is None:
        if q.bid and q.ask:
            prezzo = (q.bid + q.ask) / 2
        else:
            veloce = next(
                (
                    ctx.timeframes[tf]
                    for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1)
                    if tf in ctx.timeframes
                ),
                None,
            )
            prezzo = veloce.indicators.close if veloce else 0.0
    return CatalogoEvidenze(
        canonical=canonical,
        voci=voci,
        prezzo=round(prezzo, c),
        atr_h1=atr_h1,
        spread_prezzo=max(spread or 0.0, 0.0),
        cifre=c,
        qualita=str(ctx.quality),
        note=tuple(ctx.warnings[:5]),
    )
