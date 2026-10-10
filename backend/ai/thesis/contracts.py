"""Contratti della fase 5B: `TradeThesis` (gruppo C) e `FilterVerdict` (gruppo B).

Il modello ora può dire LONG / SHORT / WAIT e indicare prezzi, ma **non** volume,
size, leva o ordini: il contratto non ha i campi per esprimerli (`extra="forbid"`).
Quello che produce diventa al massimo uno `ShadowTradePlan`, che nessun modulo può
eseguire.

Ogni affermazione che regge la decisione cita `evidence_ids` del contesto; entry,
invalidazioni e target sono `PriceCondition` tipizzate. La `confidence` è
un'etichetta registrata per la calibrazione futura, **non una probabilità**.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.ai.contracts import LlmCall, impronta
from backend.shadow.conditions import PriceCondition

THESIS_INPUT_VERSION = "trade-thesis-input-1.2.0"
THESIS_OUTPUT_VERSION = "trade-thesis-output-2.0.0"
FILTER_OUTPUT_VERSION = "ai-filter-output-1.2.0"

_RIGIDO = ConfigDict(frozen=True, extra="forbid")
_LETTURA = ConfigDict(frozen=True, extra="ignore")

Livello = Literal["LOW", "MEDIUM", "HIGH"]


class Claim(BaseModel):
    model_config = _RIGIDO

    statement: str = Field(min_length=10, max_length=500)
    evidence_ids: tuple[str, ...] = Field(
        description="Id esatti delle evidenze che sostengono l'affermazione."
    )


class ThesisScenario(BaseModel):
    model_config = _RIGIDO

    narrative: str = Field(min_length=20, max_length=1200)
    evidence_ids: tuple[str, ...]


class Setup(BaseModel):
    model_config = _RIGIDO

    type: Literal[
        "TREND_CONTINUATION", "PULLBACK", "BREAKOUT", "MEAN_REVERSION", "REVERSAL", "RANGE", "NONE"
    ]
    description: str = Field(min_length=10, max_length=600)
    evidence_ids: tuple[str, ...]


class Entry(BaseModel):
    """Dove entrare, non come. Il tipo (MARKET/STOP/LIMIT) lo decide il codice dal
    livello e dal prezzo corrente (`paper.geometry.tipo_ingresso`): nella versione
    1.x il modello confondeva STOP e LIMIT."""

    model_config = _RIGIDO

    mode: Literal["NOW", "AT_LEVEL"] = Field(
        description="NOW: entrare al prezzo corrente (price null). AT_LEVEL: a un livello."
    )
    price: float | None = Field(description="Livello d'ingresso se AT_LEVEL; null se NOW.")
    valid_for_hours: int = Field(ge=1, le=72, description="Dopo, il setup scade.")
    evidence_ids: tuple[str, ...]


class PriceClaim(BaseModel):
    model_config = _RIGIDO

    price: float = Field(gt=0)
    evidence_ids: tuple[str, ...]


class ThesisUncertainty(BaseModel):
    model_config = _RIGIDO

    description: str = Field(min_length=10, max_length=500)
    missing_information: str | None = Field(
        description="Informazione che manca nel contesto e che ridurrebbe l'incertezza."
    )
    impact: Livello


class TradeThesis(BaseModel):
    """Tesi su un singolo strumento. WAIT è un esito di pari dignità."""

    model_config = _RIGIDO

    #: Ordine dei campi = ordine di generazione: prima le evidenze, poi la decisione.
    #: Con la decisione in testa il modello si impegnava e poi la giustificava
    #: (trovato dal vivo sul filtro, output 1.0.0).
    canonical: str
    scenario: ThesisScenario
    setup: Setup
    supporting_evidence: tuple[Claim, ...] = Field(max_length=8)
    contradictory_evidence: tuple[Claim, ...] = Field(max_length=8)
    uncertainties: tuple[ThesisUncertainty, ...] = Field(max_length=6)
    decision: Literal["LONG", "SHORT", "WAIT"]
    wait_reason: str | None = Field(description="Obbligatorio se WAIT, altrimenti null.")
    entry: Entry | None = Field(description="null se WAIT.")
    entry_conditions: tuple[PriceCondition, ...] = Field(
        max_length=3,
        description="Condizioni che devono verificarsi prima che l'ingresso sia attivo.",
    )
    stop_loss: PriceClaim | None = Field(description="null se WAIT.")
    take_profit: PriceClaim | None = Field(description="null se WAIT.")
    # Nessun `expected_rr`: il rapporto si calcola dai prezzi (1.x: aritmetica sbagliata).
    invalidation_conditions: tuple[PriceCondition, ...] = Field(max_length=4)
    target_conditions: tuple[PriceCondition, ...] = Field(max_length=3)
    expected_horizon_hours: int = Field(ge=1, le=72)
    confidence: Livello = Field(description="Etichetta qualitativa. NON è una probabilità.")


class FilterVerdict(BaseModel):
    """Giudizio dell'AI su un piano della baseline quant (gruppo B)."""

    model_config = _RIGIDO

    #: Prima si ridichiara il piano (verificato), poi i punti a favore e contro, e
    #: solo alla fine il verdetto. Nella versione 1.0.0 il verdetto veniva per primo:
    #: su 11 piani SHORT reali il filtro ne ha respinti 10, in 5 casi con motivi che
    #: sostenevano lo short e in 5 leggendo il piano come "long".
    canonical: str
    plan_direction_read: Literal["LONG", "SHORT"] = Field(
        description="Direzione del piano in quant_plan.decision, ricopiata."
    )
    points_for: tuple[Claim, ...] = Field(
        max_length=8, description="Evidenze che sostengono il piano così com'è."
    )
    points_against: tuple[Claim, ...] = Field(
        max_length=8, description="Evidenze che contraddicono il piano."
    )
    key_risks: tuple[Claim, ...] = Field(max_length=6)
    verdict: Literal["APPROVE", "REJECT"] = Field(
        description="Decidi DOPO aver pesato points_for e points_against."
    )
    confidence: Livello = Field(description="Etichetta qualitativa. NON è una probabilità.")

    @property
    def reasons(self) -> tuple[Claim, ...]:
        """I punti che motivano il verdetto."""
        return self.points_for if self.verdict == "APPROVE" else self.points_against


# ======================================================================
# Ingresso e registrazione
# ======================================================================
class ThesisInput(BaseModel):
    model_config = _LETTURA

    kind: Literal["TRADE_THESIS", "AI_FILTER"]
    canonical: str
    context_as_of: datetime
    mode: Literal["LIVE", "REPLAY"]
    input_schema_version: str = THESIS_INPUT_VERSION
    output_schema_version: str
    prompt_version: str
    #: Impronta dell'ingresso globale della fase 5A da cui derivano i riferimenti globali.
    global_input_sha256: str
    payload: dict[str, Any]
    allowed_evidence_ids: tuple[str, ...]
    universe: tuple[str, ...]
    known_limitations: tuple[str, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def input_sha256(self) -> str:
        return impronta(self.model_dump(mode="json", exclude={"input_sha256"}))  # type: ignore[arg-type]


class AiDecisionRecord(BaseModel):
    """Una chiamata della fase 5B: immutabile, con hash di integrità.

    `status`: `VALID` (usabile), `REJECTED` (conforme allo schema ma viola le regole:
    evidenze mancanti, geometria incoerente, condizioni non verificabili),
    `UNAVAILABLE` (provider assente, errore, JSON non conforme). Solo `VALID`
    produce un piano; gli altri sono fail closed.
    """

    model_config = _LETTURA

    record_version: str = "ai-decision-record-1.0.0"
    created_at: datetime
    input: ThesisInput
    call: LlmCall
    raw_output: str | None = None
    status: Literal["VALID", "REJECTED", "UNAVAILABLE"]
    failure_reasons: tuple[str, ...] = ()
    #: Affermazioni non decisive con evidenze inesistenti: tenute, ma marcate.
    unsupported_claims: tuple[str, ...] = ()
    thesis: TradeThesis | None = None
    verdict: FilterVerdict | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def record_id(self) -> str:
        return impronta(
            {
                "input": self.input.input_sha256,
                "created_at": self.created_at.isoformat(),
                "model": self.call.model_requested,
            }
        )[:24]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def integrity_sha256(self) -> str:
        return impronta(
            self.model_dump(mode="json", exclude={"integrity_sha256", "record_id"})  # type: ignore[arg-type]
        )
