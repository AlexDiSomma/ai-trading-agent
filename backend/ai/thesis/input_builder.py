"""Ingresso della fase 5B: contesto globale (5A) + catalogo dello strumento.

Gli id ammessi sono l'unione dei riferimenti globali della fase 5A e degli id del
catalogo dello strumento. `reference` porta i numeri con cui la tesi viene
controllata: prezzo, ATR H1, spread e ultime chiusure degli strumenti su cui si
possono scrivere condizioni.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from backend.ai.contracts import AnalysisInput
from backend.ai.thesis.contracts import (
    FILTER_OUTPUT_VERSION,
    THESIS_OUTPUT_VERSION,
    ThesisInput,
)
from backend.ai.thesis.prompts import FILTER_PROMPT_VERSION, THESIS_PROMPT_VERSION
from backend.shadow.evidence import CatalogoEvidenze
from backend.shadow.plans import ShadowTradePlan


def _riferimento(
    catalogo: CatalogoEvidenze, ultime_chiusure: Mapping[tuple[str, str], float]
) -> dict[str, Any]:
    return {
        "price": catalogo.prezzo,
        "atr_h1": None if catalogo.atr_h1 is None else round(catalogo.atr_h1, catalogo.cifre),
        "spread": round(catalogo.spread_prezzo, catalogo.cifre),
        "asset_data_quality": catalogo.qualita,
        "asset_warnings": list(catalogo.note),
        "last_closes": {f"{s} {tf}": v for (s, tf), v in sorted(ultime_chiusure.items())},
    }


def costruisci_ingresso_tesi(
    globale: AnalysisInput,
    catalogo: CatalogoEvidenze,
    *,
    as_of: datetime,
    mode: Literal["LIVE", "REPLAY"],
    ultime_chiusure: Mapping[tuple[str, str], float],
    quant: ShadowTradePlan | None = None,
    vincoli: dict[str, Any] | None = None,
    notizie: list[dict[str, Any]] | None = None,
) -> ThesisInput:
    filtro = quant is not None
    # Il piano da giudicare va in testa: in fondo a ~19k token il modello lo leggeva male.
    payload: dict[str, Any] = {"quant_plan": None} if filtro else {}
    payload |= {
        "context_as_of": as_of.isoformat(),
        "mode": mode,
        "canonical": catalogo.canonical,
        "reference": _riferimento(catalogo, ultime_chiusure),
        "asset_evidence": catalogo.voci,
    }
    if vincoli:
        # Limiti di rischio dell'utente (RR minimo, distanza dello stop): il modello li
        # conosce per non proporre piani che il gate scarterebbe comunque.
        payload["risk_constraints"] = vincoli
    # Le sezioni globali della 5A stanno al PRIMO livello, non sotto un contenitore:
    # con `global_context.regime` come percorso il modello citava quell'id, che non
    # esiste (l'id è `regime`). 4 tesi su 7 rifiutate per questo (pipeline 1.1.0).
    for k, v in globale.payload.items():
        if k not in ("allowed_refs", "universe", "context_as_of"):
            payload.setdefault(k, v)
    if filtro:
        assert quant is not None
        payload["quant_plan"] = quant.model_dump(
            mode="json",
            include={
                "decision",
                "entry_type",
                "entry_price",
                "stop_loss",
                "take_profit",
                "computed_rr",
                "horizon_hours",
                "invalidation_conditions",
                "reasons",
                "quant_version",
            },
        )
    if notizie:
        # Notizie recenti da fonti filtrate: citabili come evidenze (`news.<id>`).
        payload["news"] = notizie
    ammessi = tuple(
        dict.fromkeys((*globale.allowed_refs, *catalogo.ids, *(n["ref"] for n in notizie or [])))
    )
    universo = tuple(sorted({s for s, _ in ultime_chiusure}))
    payload["allowed_evidence_ids"] = list(ammessi)
    payload["universe"] = list(universo)
    return ThesisInput(
        kind="AI_FILTER" if filtro else "TRADE_THESIS",
        canonical=catalogo.canonical,
        context_as_of=as_of,
        mode=mode,
        output_schema_version=FILTER_OUTPUT_VERSION if filtro else THESIS_OUTPUT_VERSION,
        prompt_version=FILTER_PROMPT_VERSION if filtro else THESIS_PROMPT_VERSION,
        global_input_sha256=globale.input_sha256,
        payload=payload,
        allowed_evidence_ids=ammessi,
        universe=universo,
        known_limitations=globale.known_limitations,
    )
