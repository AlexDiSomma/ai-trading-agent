"""Da un piano (quant o AI) al `TradePlan` deterministico.

Il piano ombra porta già tipo d'ingresso e RR calcolati dal codice
(`geometry.tipo_ingresso`, `geometry.rr`); qui si fissano le scadenze e l'etichetta
di versione. Un piano non attivo (WAIT, REJECTED, FILTERED) non produce nulla.
"""

from __future__ import annotations

from datetime import timedelta

from backend.paper.geometry import rr
from backend.paper.models import Libro, TradePlan
from backend.shadow.plans import ShadowTradePlan


def da_piano_ombra(
    p: ShadowTradePlan, *, book: Libro, broker_symbol: str, strategy_version: str
) -> TradePlan | None:
    if p.status != "ACTIVE" or p.decision == "WAIT":
        return None
    if None in (p.entry_type, p.entry_price, p.stop_loss, p.take_profit) or not p.atr_h1:
        return None
    entrata, stop, target = p.entry_price, p.stop_loss, p.take_profit
    assert entrata is not None and stop is not None and target is not None
    return TradePlan(
        book=book,
        canonical=p.canonical,
        broker_symbol=broker_symbol,
        direction=p.decision,  # type: ignore[arg-type]
        entry_type=p.entry_type,  # type: ignore[arg-type]
        entry_price=entrata,
        stop=stop,
        target=target,
        rr=rr(entrata, stop, target) or 0.0,
        atr_h1=p.atr_h1,
        created_at=p.decided_at,
        valid_until=p.decided_at + timedelta(hours=p.entry_valid_hours or 1),
        expires_at=p.decided_at + timedelta(hours=p.horizon_hours),
        invalidations=p.invalidation_conditions,
        strategy_version=strategy_version,
        source_record_id=p.thesis_record_id or p.filter_record_id,
        ai_confidence_label=p.ai_confidence_label,
        reasons=p.reasons,
    )


#: Suffisso di versione dei piani invertiti: statistiche sempre separate.
SUFFISSO_INVERSO = "|inverso"


def inverti(p: TradePlan) -> TradePlan:
    """Piano speculare rispetto all'ingresso: verso opposto, stop e target alla stessa
    distanza dall'altra parte (stesso RR). Esperimento chiesto dall'utente (ADR 0029).

    Le invalidazioni si tolgono: descrivevano la struttura del piano originale. Un
    LIMIT diventa STOP e viceversa: il livello resta lo stesso, ma per il verso
    opposto cambia il significato (sotto il prezzo: comprare = LIMIT, vendere = STOP).
    """
    tipo = {"MARKET": "MARKET", "LIMIT": "STOP", "STOP": "LIMIT"}[p.entry_type]
    return p.model_copy(
        update={
            "direction": "SHORT" if p.direction == "LONG" else "LONG",
            "entry_type": tipo,
            "stop": round(2 * p.entry_price - p.stop, 10),
            "target": round(2 * p.entry_price - p.target, 10),
            "invalidations": (),
            "strategy_version": p.strategy_version + SUFFISSO_INVERSO,
            "reasons": ("Segnale invertito (esperimento dell'utente).", *p.reasons),
        }
    )


def con_target(p: TradePlan, target_r: float) -> TradePlan:
    """Stesso piano con il target a `target_r` volte la distanza dello stop (ADR 0029).

    Il RR si ricalcola dai prezzi; la versione porta il target (`|tp1.3`) così le
    statistiche restano separate da quelle a 2R.
    """
    distanza = abs(p.entry_price - p.stop)
    target = round(p.entry_price + p.verso * target_r * distanza, 10)
    return p.model_copy(
        update={
            "target": target,
            "rr": rr(p.entry_price, p.stop, target) or 0.0,
            "strategy_version": f"{p.strategy_version}|tp{target_r:g}",
        }
    )
