"""Contratti dell'AI Market Analyst (fase 5A, READ ONLY).

Tre oggetti, tutti **immutabili** e **versionati**:

* `AnalysisInput` — ciò che il modello riceve: il contesto globale compattato, i
  riferimenti citabili, l'universo, le versioni di prompt e schema, l'hash del
  contenuto. Contiene solo dati disponibili all'istante del contesto (`as_of`).
* `MarketAnalysis` — ciò che il modello deve produrre: **solo analisi**. Non esiste
  nessun campo per direzione d'ingresso, prezzo d'ingresso, stop, target, volume,
  size o ordine: il modello non ha dove scriverli, e un test lo verifica sullo
  schema.
* `AnalysisRecord` — la registrazione completa di una chiamata: ingresso, parametri,
  risposta grezza, esito della validazione, analisi (se valida), con un hash che ne
  certifica l'integrità. È ciò che la fase 9 userà per misurare, **senza hindsight**,
  se le analisi erano giuste: ogni scenario ha un orizzonte e condizioni di
  invalidazione strutturate, fissate prima di conoscere l'esito.

Output non valido = analisi non disponibile (`status = UNAVAILABLE`), con i motivi.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from backend.core.enums import GlobalRegime

#: Versioni. Cambiarne una rende i record non confrontabili uno a uno con i precedenti.
INPUT_SCHEMA_VERSION = "market-analysis-input-1.0.0"
OUTPUT_SCHEMA_VERSION = "market-analysis-output-1.2.0"

_RIGIDO = ConfigDict(frozen=True, extra="forbid")
_LETTURA = ConfigDict(frozen=True, extra="ignore")

Livello = Literal["LOW", "MEDIUM", "HIGH"]
Metrica = Literal["PRICE", "CURRENCY_STRENGTH", "REGIME", "VOLATILITY", "CORRELATION", "EVENT"]
#: NOT_REACHED: "lo stato atteso NON viene raggiunto entro l'orizzonte". Senza, uno
#: scenario non poteva dire "fallisce se non succede" e il modello scriveva la propria
#: conferma come invalidazione (trovato dal vivo, prompt 1.2.0).
Verso = Literal["ABOVE", "BELOW", "CHANGES_TO", "NOT_REACHED", "OCCURS"]


def impronta(dati: Any) -> str:
    """sha256 di un JSON canonico (chiavi ordinate, separatori fissi)."""
    testo = json.dumps(dati, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


# ======================================================================
# Output del modello
# ======================================================================
class EvidenceItem(BaseModel):
    model_config = _RIGIDO

    statement: str = Field(min_length=10, max_length=500)
    #: Riferimenti a elementi del contesto (es. "currency_strength.USD"). Devono
    #: esistere fra quelli forniti in ingresso: un'evidenza senza fonte non vale.
    refs: list[str] = Field(
        min_length=1,
        max_length=6,
        description="Da 1 a 6 riferimenti copiati ESATTAMENTE da `allowed_refs`.",
    )


class Theme(BaseModel):
    model_config = _RIGIDO

    title: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=10, max_length=700)
    refs: list[str] = Field(min_length=1, max_length=6)


class InvalidationCondition(BaseModel):
    """Una condizione verificabile a posteriori, con soli dati successivi ad `as_of`."""

    model_config = _RIGIDO

    description: str = Field(min_length=10, max_length=400)
    subject: str | None = Field(
        default=None,
        max_length=60,
        description=(
            "Nome canonico di uno strumento o valuta presente in `universe` (es. XAUUSD, "
            "USD), oppure un riferimento presente in `allowed_refs` (es. "
            "intermarket.US500~GER40.D1, regime). null se non pertinente."
        ),
    )
    metric: Metrica = Field(description="Grandezza osservata per verificare la condizione.")
    direction: Verso = Field(
        description=(
            "ABOVE/BELOW: il valore supera la soglia almeno una volta entro l'orizzonte. "
            "CHANGES_TO/OCCURS: lo stato è osservato almeno una volta entro l'orizzonte. "
            "NOT_REACHED: alla fine dell'orizzonte lo stato è diverso da target_state."
        )
    )
    threshold: float | None = Field(
        default=None, description="Soglia numerica (prezzo, punteggio, percentile) o null."
    )
    #: Per CHANGES_TO / OCCURS: lo stato verso cui cambia (es. RISK_ON, WEAK, DIVERGING,
    #: ACTUAL_AVAILABLE). Senza, "cambia" non è verificabile (trovato dal vivo).
    target_state: str | None = Field(
        default=None,
        max_length=30,
        description=(
            "Obbligatorio con CHANGES_TO/NOT_REACHED/OCCURS: stato atteso (es. RISK_ON, STRONG, "
            "DIVERGING, EXPANDING, ACTUAL_AVAILABLE). null con ABOVE/BELOW."
        ),
    )
    horizon_hours: int = Field(ge=1, le=336, description="Entro quante ore, da 1 a 336.")


class Scenario(BaseModel):
    model_config = _RIGIDO

    name: str = Field(min_length=3, max_length=100)
    narrative: str = Field(min_length=20, max_length=900)
    #: Probabilità soggettiva: serve a misurare la calibrazione nel tempo (fase 9).
    likelihood: float = Field(
        ge=0, le=1, description="Probabilità soggettiva 0-1; la somma sugli scenari ≤ 1."
    )
    horizon_hours: int = Field(ge=1, le=336)
    confirming_signals: list[str] = Field(min_length=1, max_length=6)
    invalidation: list[InvalidationCondition] = Field(min_length=1, max_length=5)
    key_subjects: list[str] = Field(default_factory=list, max_length=8)


class Uncertainty(BaseModel):
    model_config = _RIGIDO

    description: str = Field(min_length=10, max_length=500)
    #: L'informazione che manca e che ridurrebbe questa incertezza, se esiste.
    missing_information: str | None = Field(default=None, max_length=300)
    impact: Livello


class AssetToWatch(BaseModel):
    model_config = _RIGIDO

    canonical: str = Field(
        min_length=3, max_length=12, description="Nome canonico presente in `universe`."
    )
    reason: str = Field(min_length=10, max_length=400)
    watch_for: str = Field(min_length=10, max_length=400)


class MarketAnalysis(BaseModel):
    """L'analisi strutturata. Nessun campo operativo, per costruzione."""

    model_config = _RIGIDO

    #: Il regime calcolato dal motore deterministico, ripetuto dal modello: se non
    #: coincide con l'ingresso, il modello non ha letto il contesto.
    engine_regime_acknowledged: GlobalRegime
    market_summary: str = Field(min_length=40, max_length=1500)
    #: Se il modello legge il mercato diversamente dal motore, lo dice qui e perché.
    analyst_view_on_regime: str = Field(min_length=10, max_length=700)
    dominant_themes: list[Theme] = Field(min_length=1, max_length=5)
    scenarios: list[Scenario] = Field(min_length=1, max_length=4)
    supporting_evidence: list[EvidenceItem] = Field(min_length=1, max_length=10)
    contradictory_evidence: list[EvidenceItem] = Field(default_factory=list, max_length=10)
    uncertainties: list[Uncertainty] = Field(min_length=1, max_length=10)
    assets_to_watch: list[AssetToWatch] = Field(default_factory=list, max_length=8)
    invalidation_conditions: list[InvalidationCondition] = Field(min_length=1, max_length=10)
    #: Fiducia complessiva nell'analisi, non in un'operazione.
    confidence: Livello

    @field_validator("scenarios")
    @classmethod
    def _probabilita_coerenti(cls, scenari: list[Scenario]) -> list[Scenario]:
        totale = sum(s.likelihood for s in scenari)
        if totale > 1.0 + 1e-6:
            raise ValueError(f"Le probabilità degli scenari sommano a {totale:.2f}, oltre 1.")
        return scenari


# ======================================================================
# Ingresso
# ======================================================================
class AnalysisInput(BaseModel):
    """Ingresso versionato e immutabile del modello."""

    model_config = _LETTURA

    input_schema_version: str = INPUT_SCHEMA_VERSION
    prompt_version: str
    output_schema_version: str = OUTPUT_SCHEMA_VERSION
    #: Istante del contesto: l'analisi non può sapere nulla di successivo.
    context_as_of: datetime
    context_fingerprint: str
    engine_version: str
    engine_regime: GlobalRegime
    data_quality: str
    #: Il contesto compattato che il modello riceve, così com'è.
    payload: dict[str, Any]
    allowed_refs: tuple[str, ...]
    universe: tuple[str, ...]
    #: Limitazioni note del contesto: l'analisi deve tenerne conto.
    known_limitations: tuple[str, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def input_sha256(self) -> str:
        return impronta(
            self.model_dump(mode="json", exclude={"input_sha256"})  # type: ignore[arg-type]
        )


# ======================================================================
# Registrazione
# ======================================================================
class LlmCall(BaseModel):
    model_config = _LETTURA

    provider: str
    model_requested: str
    #: Versione esatta del modello restituita dal provider (es. gpt-5.4-mini-2026-03-17).
    model_snapshot: str | None = None
    temperature: float | None = None
    max_output_tokens: int
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    started_at: datetime
    error: str | None = None


class AnalysisRecord(BaseModel):
    """Una chiamata completa all'analista: immutabile, con hash di integrità."""

    model_config = _LETTURA

    record_version: str = "market-analysis-record-1.0.0"
    created_at: datetime
    input: AnalysisInput
    call: LlmCall
    raw_output: str | None = None
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    #: Perché l'analisi non è disponibile: errore del provider, JSON non valido,
    #: schema violato, riferimenti inesistenti, contenuto operativo...
    failure_reasons: tuple[str, ...] = ()
    analysis: MarketAnalysis | None = None

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

    def evaluation_due(self) -> dict[str, datetime]:
        """Quando ogni scenario potrà essere giudicato (as_of + orizzonte)."""
        if self.analysis is None:
            return {}
        return {
            s.name: self.input.context_as_of + timedelta(hours=s.horizon_hours)
            for s in self.analysis.scenarios
        }
