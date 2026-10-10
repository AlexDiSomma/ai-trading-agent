"""Dai record AI ai piani dei gruppi B e C. Fail closed in ogni ramo.

* **B**: il piano di A resta attivo **solo** se il filtro è `VALID` e `APPROVE`.
  Filtro `REJECTED`/`UNAVAILABLE` → `REJECTED` (nessuna operazione), verdetto
  `REJECT` → `FILTERED`. Se A è WAIT, anche B è WAIT: il filtro non crea operazioni.
* **C**: la tesi diventa un piano attivo **solo** se `VALID` e LONG/SHORT. Tesi
  `REJECTED`/`UNAVAILABLE` → `REJECTED`; WAIT → `WAIT`.
"""

from __future__ import annotations

from backend.ai.thesis.contracts import AiDecisionRecord
from backend.paper.geometry import tipo_ingresso
from backend.shadow.plans import ShadowTradePlan, rr


def piano_filtro(quant: ShadowTradePlan, record: AiDecisionRecord | None) -> ShadowTradePlan:
    comune = quant.model_dump(exclude={"plan_id", "integrity_sha256", "group", "reasons"})
    comune["group"] = "B_QUANT_AI_FILTER"
    if quant.status != "ACTIVE" or record is None:
        return ShadowTradePlan(**comune, reasons=("La baseline non propone operazioni.",))
    comune["filter_record_id"] = record.record_id
    v = record.verdict
    if record.status != "VALID" or v is None:
        comune.update(status="REJECTED", decision="WAIT")
        return ShadowTradePlan(
            **comune,
            reasons=(
                f"Filtro AI {record.status}: nessuna operazione.",
                *record.failure_reasons[:3],
            ),
        )
    comune["ai_confidence_label"] = v.confidence
    if v.verdict == "REJECT":
        comune.update(status="FILTERED", decision="WAIT")
        return ShadowTradePlan(**comune, reasons=tuple(c.statement for c in v.reasons[:3]))
    return ShadowTradePlan(**comune, reasons=tuple(c.statement for c in v.reasons[:3]))


def piano_tesi(
    record: AiDecisionRecord,
    *,
    reference_price: float,
    atr_h1: float | None,
    spread_price: float,
) -> ShadowTradePlan:
    t = record.thesis
    base = {
        "group": "C_AI_THESIS",
        "canonical": record.input.canonical,
        "decided_at": record.input.context_as_of,
        "mode": record.input.mode,
        "reference_price": reference_price,
        "atr_h1": atr_h1,
        "spread_price": spread_price,
        "thesis_record_id": record.record_id,
    }
    if record.status != "VALID" or t is None:
        return ShadowTradePlan(
            **base,  # type: ignore[arg-type]
            decision="WAIT",
            status="REJECTED",
            horizon_hours=24,
            reasons=(f"Tesi AI {record.status}: nessuna operazione.", *record.failure_reasons[:4]),
        )
    base["ai_confidence_label"] = t.confidence
    if t.decision == "WAIT":
        return ShadowTradePlan(
            **base,  # type: ignore[arg-type]
            decision="WAIT",
            status="WAIT",
            horizon_hours=t.expected_horizon_hours,
            invalidation_conditions=t.invalidation_conditions,
            target_conditions=t.target_conditions,
            reasons=(t.wait_reason or "",),
        )
    assert t.entry and t.stop_loss and t.take_profit
    verso = 1 if t.decision == "LONG" else -1
    livello = t.entry.price if t.entry.mode == "AT_LEVEL" else None
    tipo = tipo_ingresso(verso, livello, reference_price, atr_h1 or 0.0)
    entrata = reference_price if tipo == "MARKET" else (livello or reference_price)
    return ShadowTradePlan(
        **base,  # type: ignore[arg-type]
        decision=t.decision,
        status="ACTIVE",
        entry_type=tipo,
        entry_price=entrata,
        entry_valid_hours=t.entry.valid_for_hours,
        entry_conditions=t.entry_conditions,
        stop_loss=t.stop_loss.price,
        take_profit=t.take_profit.price,
        invalidation_conditions=t.invalidation_conditions,
        target_conditions=t.target_conditions,
        horizon_hours=t.expected_horizon_hours,
        computed_rr=rr(entrata, t.stop_loss.price, t.take_profit.price),
        reasons=(t.setup.description,),
    )
