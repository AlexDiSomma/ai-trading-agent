"""Snapshot immutabili: input → output AI → piani ombra → comportamento del mercato.

Due record distinti, scritti in momenti diversi e mai modificati:

* `DecisionSnapshot` — **alla decisione**: contesto dello strumento, catalogo delle
  evidenze, record AI (tesi e filtro, con risposta grezza), i tre piani. Non contiene
  nulla di successivo a `decided_at`.
* `OutcomeSnapshot` — **dopo**: le barre M5 osservate dopo la decisione e l'esito di
  ciascun piano. Rimanda alla decisione per id e hash: una valutazione successiva è un
  nuovo file, la decisione resta intatta.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, computed_field

from backend.ai.contracts import impronta
from backend.ai.thesis.contracts import (
    FILTER_OUTPUT_VERSION,
    THESIS_INPUT_VERSION,
    THESIS_OUTPUT_VERSION,
    AiDecisionRecord,
)
from backend.ai.thesis.prompts import FILTER_PROMPT_VERSION, THESIS_PROMPT_VERSION
from backend.shadow.evaluator import ShadowOutcome
from backend.shadow.plans import ShadowTradePlan
from backend.shadow.quant_baseline import QUANT_VERSION

_LETTURA = ConfigDict(frozen=True, extra="ignore")

#: Versione dell'intera catena (baseline, prompt e schemi AI). Le metriche confrontano
#: solo decisioni con la stessa versione: mescolarle misurerebbe due sistemi diversi.
PIPELINE_VERSION = "|".join(
    (
        QUANT_VERSION,
        THESIS_INPUT_VERSION,
        THESIS_PROMPT_VERSION,
        THESIS_OUTPUT_VERSION,
        FILTER_PROMPT_VERSION,
        FILTER_OUTPUT_VERSION,
    )
)
#: Decisioni registrate prima che il campo esistesse (fase 5B, primo giro).
PRIMA_VERSIONE = (
    "quant-baseline-1.0.0|trade-thesis-prompt-1.0.0|trade-thesis-output-1.0.0|"
    "ai-filter-prompt-1.0.0|ai-filter-output-1.0.0"
)


class DecisionSnapshot(BaseModel):
    model_config = _LETTURA

    snapshot_version: str = "shadow-decision-1.0.0"
    pipeline_version: str = PRIMA_VERSIONE
    canonical: str
    broker_symbol: str
    decided_at: datetime
    mode: Literal["LIVE", "REPLAY"]
    #: Perché questo strumento è stato analizzato (es. "richiesto", "scelto: allineamento").
    selection_reason: str
    asset_context: dict[str, Any]
    evidence_catalog: dict[str, Any]
    reference: dict[str, Any]
    global_regime: str
    global_data_quality: str
    thesis_record: AiDecisionRecord | None = None
    filter_record: AiDecisionRecord | None = None
    plans: tuple[ShadowTradePlan, ...]
    notes: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def decision_id(self) -> str:
        return impronta(
            {
                "c": self.canonical,
                "t": self.decided_at.isoformat(),
                "m": self.mode,
                "p": [p.plan_id for p in self.plans],
            }
        )[:24]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def integrity_sha256(self) -> str:
        return impronta(
            self.model_dump(mode="json", exclude={"integrity_sha256", "decision_id"})  # type: ignore[arg-type]
        )

    def piano(self, gruppo: str) -> ShadowTradePlan:
        return next(p for p in self.plans if p.group == gruppo)


class OutcomeSnapshot(BaseModel):
    model_config = _LETTURA

    snapshot_version: str = "shadow-outcome-snapshot-1.0.0"
    decision_id: str
    decision_integrity_sha256: str
    canonical: str
    decided_at: datetime
    evaluated_at: datetime
    #: Barre M5 dopo la decisione: [inizio UTC ISO, open, high, low, close].
    bars_m5: tuple[tuple[str, float, float, float, float], ...]
    bars_sha256: str
    outcomes: tuple[ShadowOutcome, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def integrity_sha256(self) -> str:
        return impronta(self.model_dump(mode="json", exclude={"integrity_sha256"}))  # type: ignore[arg-type]
