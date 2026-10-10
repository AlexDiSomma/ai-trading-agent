"""Baseline quantitativa (gruppo A): regola deterministica, semplice, dichiarata.

**Perché esiste.** Il sistema non ha ancora un Quant Engine che proponga operazioni:
lo scanner è la fase 5, non ancora fatta. Per misurare se l'AI aggiunge valore serve
però un termine di confronto fisso. Questa baseline è quel termine: trasparente,
senza parametri ottimizzati, versionata. Quando arriverà lo scanner, diventerà una
seconda baseline con la sua versione; i record di questa restano confrontabili tra
loro.

**Regola (quant-baseline-1.0.0).** Continuazione della tendenza multi-timeframe:

* dati GOOD su D1, H4, H1 e ATR H1 disponibile, altrimenti WAIT;
* bias strutturale (D1+H4) e bias operativo (H1) concordi e non nulli;
* allineamento pesato |a| ≥ 0,35 e nessun conflitto struttura/esecuzione;
* ingresso MARKET, stop a 1,5 ATR H1, target a 2R (3 ATR H1), orizzonte 24 ore;
* invalidazione: chiusura H1 oltre l'ultimo swing H1 opposto, se sta fra ingresso e
  stop (la struttura operativa si rompe prima dello stop).

Le soglie sono **provvisorie** come i pesi dei timeframe (fase 3): la baseline non
deve essere buona, deve essere fissa.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from backend.core.enums import DataQuality, Timeframe
from backend.schemas.context import AssetMarketContext
from backend.shadow.conditions import PriceCondition
from backend.shadow.evidence import CatalogoEvidenze
from backend.shadow.plans import ShadowTradePlan, rr

QUANT_VERSION = "quant-baseline-1.0.0"
ALLINEAMENTO_MINIMO = 0.35
STOP_ATR = 1.5
TARGET_R = 2.0
ORIZZONTE_ORE = 24


def piano_quant(
    ctx: AssetMarketContext,
    catalogo: CatalogoEvidenze,
    *,
    decided_at: datetime,
    mode: Literal["LIVE", "REPLAY"],
) -> ShadowTradePlan:
    base = {
        "group": "A_QUANT_ONLY",
        "canonical": catalogo.canonical,
        "decided_at": decided_at,
        "mode": mode,
        "horizon_hours": ORIZZONTE_ORE,
        "reference_price": catalogo.prezzo,
        "atr_h1": catalogo.atr_h1,
        "spread_price": catalogo.spread_prezzo,
        "quant_version": QUANT_VERSION,
    }

    def attesa(*motivi: str) -> ShadowTradePlan:
        return ShadowTradePlan(**base, decision="WAIT", status="WAIT", reasons=motivi)  # type: ignore[arg-type]

    mancanti = [
        str(tf)
        for tf in (Timeframe.D1, Timeframe.H4, Timeframe.H1)
        if tf not in ctx.timeframes or ctx.timeframes[tf].quality is not DataQuality.GOOD
    ]
    if mancanti:
        return attesa(f"Dati non GOOD su {', '.join(mancanti)}.")
    if not catalogo.atr_h1 or catalogo.prezzo <= 0:
        return attesa("ATR H1 o prezzo non disponibili.")

    m = ctx.multi_timeframe
    if m.structural_bias == 0 or m.structural_bias != m.operational_bias:
        return attesa(
            f"Bias strutturale {m.structural_bias:+d} e operativo {m.operational_bias:+d} "
            "non concordi."
        )
    if abs(m.alignment) < ALLINEAMENTO_MINIMO:
        return attesa(f"Allineamento {m.alignment:+.2f} sotto ±{ALLINEAMENTO_MINIMO}.")
    if m.structure_execution_conflict:
        return attesa("Conflitto fra struttura lenta ed esecuzione.")

    verso = m.structural_bias
    entrata = catalogo.prezzo
    stop = round(entrata - verso * STOP_ATR * catalogo.atr_h1, catalogo.cifre)
    target = round(entrata + verso * STOP_ATR * TARGET_R * catalogo.atr_h1, catalogo.cifre)

    invalidazioni: list[PriceCondition] = []
    struttura = ctx.timeframes[Timeframe.H1].structure
    swing = struttura.last_swing_low if verso > 0 else struttura.last_swing_high
    if swing is not None and min(entrata, stop) < swing < max(entrata, stop):
        invalidazioni.append(
            PriceCondition(
                subject=catalogo.canonical,
                timeframe="H1",
                measure="CLOSE",
                operator="BELOW" if verso > 0 else "ABOVE",
                level=round(swing, catalogo.cifre),
                evidence_ids=(f"asset.{catalogo.canonical}.H1.structure",),
                description="Chiusura H1 oltre l'ultimo swing opposto: struttura operativa rotta.",
            )
        )

    return ShadowTradePlan(
        **base,  # type: ignore[arg-type]
        decision="LONG" if verso > 0 else "SHORT",
        status="ACTIVE",
        entry_type="MARKET",
        entry_price=entrata,
        entry_valid_hours=1,
        stop_loss=stop,
        take_profit=target,
        invalidation_conditions=tuple(invalidazioni),
        computed_rr=rr(entrata, stop, target),
        reasons=(
            f"Bias D1+H4 {verso:+d} concorde con H1, allineamento {m.alignment:+.2f}.",
            f"Stop {STOP_ATR} ATR H1, target {TARGET_R:g}R.",
        ),
    )
