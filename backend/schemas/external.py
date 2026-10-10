"""Dati da fonti esterne al broker (fase 4.5, ADR 0018).

XM/MT5 è la fonte autorevole per ciò che si negozia; queste strutture portano i dati
di riferimento e macro che XM non ha: il VIX come indice (non come future in
scadenza), la curva dei rendimenti, il calendario economico.

Regola comune: **ogni osservazione dichiara da quando era pubblicamente
disponibile** (`available_at`). Il motore globale usa solo osservazioni disponibili
all'istante di analisi: è così che i dati esterni non portano look-ahead. Una
chiusura del VIX del 2 ottobre non era nota alle 10 del mattino del 2 ottobre.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import (
    DataCategory,
    DataFrequency,
    EventImportance,
    IntendedUse,
    ProviderKind,
    ProviderState,
    SourceDomain,
)

_FROZEN = ConfigDict(frozen=True, extra="ignore")


class ProviderStatus(BaseModel):
    """Stato di una fonte esterna, mostrato in System Health e nel contesto globale."""

    model_config = _FROZEN

    name: str
    kind: ProviderKind
    state: ProviderState
    #: Metadati obbligatori di ogni fonte (correzioni pre-fase 5).
    domain: SourceDomain
    frequency: DataFrequency = DataFrequency.NONE
    #: Quando un dato diventa pubblico rispetto al periodo che descrive.
    publication_delay: str = ""
    intended_use: IntendedUse
    official: bool = False
    requires_key: bool = False
    free: bool = True
    coverage: str = ""
    license_note: str = ""
    last_success: datetime | None = None
    last_error: str | None = None
    #: Istante dell'ultima osservazione contenuta nei dati (= last observation).
    data_as_of: datetime | None = None


class DailyObservation(BaseModel):
    model_config = _FROZEN

    day: date
    #: Da quando l'osservazione era pubblicamente disponibile (UTC).
    available_at: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float


class ExternalSeries(BaseModel):
    """Una serie giornaliera da una fonte esterna (es. VIX da CBOE)."""

    model_config = _FROZEN

    series_id: str
    name: str
    category: DataCategory
    unit: str
    provider: str
    observations: tuple[DailyObservation, ...]
    retrieved_at: datetime

    def available(self, as_of: datetime) -> tuple[DailyObservation, ...]:
        """Solo le osservazioni già pubbliche ad `as_of`: nessun look-ahead."""
        return tuple(o for o in self.observations if o.available_at <= as_of)


class YieldCurvePoint(BaseModel):
    model_config = _FROZEN

    day: date
    available_at: datetime
    #: Rendimento in percentuale per scadenza: "3M", "2Y", "10Y", ...
    yields: dict[str, float]


class YieldCurveHistory(BaseModel):
    model_config = _FROZEN

    provider: str
    points: tuple[YieldCurvePoint, ...]
    retrieved_at: datetime

    def available(self, as_of: datetime) -> tuple[YieldCurvePoint, ...]:
        return tuple(p for p in self.points if p.available_at <= as_of)


class EconomicEvent(BaseModel):
    """Un evento del calendario economico."""

    model_config = _FROZEN

    event_id: str
    title: str
    #: Valuta interessata (USD, EUR, ...) o "ALL" per eventi globali.
    currency: str
    importance: EventImportance
    scheduled_at: datetime
    forecast: str | None = None
    previous: str | None = None
    #: Valore pubblicato. Forex Factory non lo fornisce (né JSON né XML, verificato
    #: il 2026-10-05): resta None, e la fase dell'evento non è mai ACTUAL_AVAILABLE.
    actual: str | None = None
    #: Quando il valore pubblicato è arrivato a noi. Serve contro l'hindsight: in
    #: un'analisi all'istante T il valore conta solo se `actual_seen_at <= T`.
    actual_seen_at: datetime | None = None
    source: str


class CalendarSnapshot(BaseModel):
    """Calendario letto da un provider, con la finestra temporale che copre davvero.

    `coverage_end` è essenziale: "nessun evento ad alto impatto" vale solo dentro la
    finestra coperta. Oltre, il calendario non sa niente, e il sistema non deve
    dedurre "tranquillo" da un'assenza di dati.
    """

    model_config = _FROZEN

    provider: str
    retrieved_at: datetime
    coverage_start: datetime
    coverage_end: datetime
    events: tuple[EconomicEvent, ...]
    has_actuals: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def event_count(self) -> int:
        return len(self.events)


class SourceDescriptor(BaseModel):
    """Un dato usato dall'intelligence globale: cos'è e da dove viene."""

    model_config = _FROZEN

    id: str
    name: str
    category: DataCategory
    source: str
    domain: SourceDomain
    frequency: DataFrequency
    intended_use: IntendedUse
    publication_delay: str = ""
    tradable_on_broker: bool = False
    state: ProviderState
    note: str = ""
    observations: int = Field(default=0, ge=0)
    last_observation: datetime | date | None = None
