"""Contesto globale dei mercati: cosa si è letto, e cosa se ne conclude.

Due oggetti distinti, perché rispondono a domande diverse.

* `GlobalMarketSnapshot` — **i dati in ingresso**: quali strumenti, quali serie,
  in che stato (fresche, chiuse, ferme, mancanti), con un'impronta (`sha256`) che
  identifica esattamente le barre usate.
* `GlobalMarketContext` — **la lettura**: regime, sentiment di rischio,
  volatilità, forza delle valute, direzione di azioni e metalli, conferme
  intermarket, rischio macro/eventi, conflitti, qualità. Deterministico: stessa
  impronta e stessa versione del motore danno lo stesso contesto, byte per byte.

**Nessun punteggio globale unico.** Ogni dimensione ha il suo stato, la sua
qualità e il suo dettaglio per timeframe. "Rischio MIXED, USD forte, oro forte,
azioni deboli, volatilità in espansione, incertezza alta" deve essere
rappresentabile così com'è, senza che un numero medio cancelli la contraddizione.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.calendar.risk import EventView, EventWindow
from backend.core.enums import (
    ConflictSeverity,
    DataCategory,
    GlobalDataQuality,
    GlobalRegime,
    IntendedUse,
    IntermarketState,
    MarketPhase,
    MarketStatus,
    ProviderState,
    QuoteQualityStatus,
    ReferenceGroup,
    RelationshipState,
    SeriesStatus,
    SourceDomain,
    StrengthLabel,
    Timeframe,
    UncertaintyLevel,
    VolatilityLevel,
    VolatilityTrend,
)
from backend.schemas.external import EconomicEvent, ProviderStatus, SourceDescriptor

_FROZEN = ConfigDict(frozen=True, extra="ignore")

PerTimeframe = dict[Timeframe, float | None]


# ======================================================================
# Ingresso
# ======================================================================
class ReferenceSeriesStatus(BaseModel):
    """Una serie di candele dell'universo di riferimento."""

    model_config = _FROZEN

    timeframe: Timeframe
    status: SeriesStatus
    bars: int = Field(ge=0)
    #: Apertura dell'ultima barra **chiusa**, in ora broker (come la dà MT5).
    last_bar_time: datetime | None = None
    #: Età dell'apertura della barra in formazione, solo se verificabile (ADR 0015).
    last_bar_age_seconds: float | None = None
    note: str = ""


class ReferenceInstrumentStatus(BaseModel):
    """Uno strumento dell'universo di riferimento e lo stato dei suoi dati."""

    model_config = _FROZEN

    canonical: str
    broker_symbol: str
    group: ReferenceGroup
    #: Fase 4.5: gli strumenti XM di riferimento sono REFERENCE_INSTRUMENT, non
    #: negoziabili per il sistema anche se lo sono sul conto (ADR 0016, 0018).
    category: DataCategory = DataCategory.REFERENCE_INSTRUMENT
    source: str = "xm"
    market_status: MarketStatus = MarketStatus.UNKNOWN
    quote_status: QuoteQualityStatus | None = None
    series: tuple[ReferenceSeriesStatus, ...] = ()
    reason: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable_timeframes(self) -> tuple[Timeframe, ...]:
        return tuple(s.timeframe for s in self.series if s.status.usable)


class GlobalMarketSnapshot(BaseModel):
    """I dati su cui è stato costruito un contesto globale."""

    model_config = _FROZEN

    as_of: datetime
    broker_time_offset_seconds: float | None = None
    broker_time_source: str = "UNAVAILABLE"
    market_phase: MarketPhase
    market_phase_reason: str
    instruments: tuple[ReferenceInstrumentStatus, ...]
    #: Impronta delle barre effettivamente usate (dopo il taglio temporale).
    input_fingerprint: str
    simulated: bool = False
    source: str = "mt5"
    #: Tutti i dati usati, con categoria e fonte (XM, CBOE, Tesoro USA, derivati...).
    sources: tuple[SourceDescriptor, ...] = ()
    providers: tuple[ProviderStatus, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(
            i.canonical
            for i in self.instruments
            if all(s.status is SeriesStatus.MISSING for s in i.series) or not i.series
        )


# ======================================================================
# Dimensioni
# ======================================================================
class DimensionQuality(BaseModel):
    model_config = _FROZEN

    dimension: str
    quality: GlobalDataQuality
    #: Quota degli strumenti richiesti effettivamente usati, da 0 a 1.
    coverage: float = Field(ge=0, le=1)
    reasons: tuple[str, ...] = ()


class CurrencyStrength(BaseModel):
    """Forza di una valuta contro le altre sette, da tutti i cross disponibili."""

    model_config = _FROZEN

    currency: str
    #: Momentum medio normalizzato per volatilità (unità di deviazione standard).
    score: float | None
    label: StrengthLabel
    rank: int | None = None
    by_timeframe: PerTimeframe = Field(default_factory=dict)
    crosses_used: int = Field(ge=0)
    crosses_expected: int = 7
    #: Vero se tutti i timeframe disponibili hanno lo stesso segno.
    timeframes_agree: bool | None = None


class CurrencyStrengthView(BaseModel):
    model_config = _FROZEN

    currencies: tuple[CurrencyStrength, ...]
    quality: DimensionQuality
    method: str = (
        "Modello a grafo robusto: per ogni barra, rendimenti delle 8 valute stimati con "
        "minimi quadrati pesati (peso = 1/varianza del cross) dalle equazioni "
        "r(A,B) = x(A) − x(B), con Σx = 0; cross fermi esclusi; outlier (residuo oltre 6 "
        "deviazioni robuste e 1 pip) rimossi e barra ristimata. Poi, per valuta: "
        "rendimento su 20 barre diviso per la sua volatilità. Composito D1 .4, H4 .3, "
        "H1 .2, M15 .1. Misura primaria delle valute; il DXY ricostruito la verifica."
    )
    #: Residuo RMS mediano delle equazioni dei cross, per timeframe: misura di
    #: coerenza dei prezzi (arbitraggio triangolare). Alto = dati sospetti.
    graph_residual: PerTimeframe = Field(default_factory=dict)
    #: Barre in cui il grafo era stimabile (abbastanza cross, tutte le valute collegate).
    bars_estimated: dict[Timeframe, int] = Field(default_factory=dict)
    #: Equazioni scartate come outlier (prezzo incoerente con gli altri cross).
    outliers_removed: dict[Timeframe, int] = Field(default_factory=dict)
    #: Cross esclusi per tutto il timeframe (quotazione ferma), con il motivo.
    crosses_excluded: dict[Timeframe, tuple[str, ...]] = Field(default_factory=dict)

    def of(self, currency: str) -> CurrencyStrength | None:
        return next((c for c in self.currencies if c.currency == currency), None)


class RelativeStrengthEntry(BaseModel):
    model_config = _FROZEN

    name: str
    group: str
    score: float | None
    rank_overall: int | None = None
    rank_in_group: int | None = None
    #: Contro cosa è misurata la forza: "paniere G8" per le valute, la valuta di
    #: quotazione per metalli e indici. Non sono la stessa unità di misura.
    measured_against: str
    by_timeframe: PerTimeframe = Field(default_factory=dict)


class RelativeStrengthView(BaseModel):
    model_config = _FROZEN

    entries: tuple[RelativeStrengthEntry, ...]
    quality: DimensionQuality
    note: str = (
        "Classifica in unità di momentum normalizzato per volatilità. Le valute sono "
        "misurate contro il paniere delle altre sette, metalli e indici contro la "
        "propria valuta di quotazione: il confronto fra gruppi è indicativo."
    )


class GroupDirection(BaseModel):
    """Direzione di un gruppo (azioni, metalli): composita, con ampiezza."""

    model_config = _FROZEN

    group: str
    label: StrengthLabel
    score: float | None
    #: Quota dei componenti con lo stesso segno del composito.
    breadth: float | None = None
    by_timeframe: PerTimeframe = Field(default_factory=dict)
    members: dict[str, float | None] = Field(default_factory=dict)
    timeframes_agree: bool | None = None
    quality: DimensionQuality
    note: str = ""


class AssetVolatility(BaseModel):
    model_config = _FROZEN

    canonical: str
    group: ReferenceGroup
    atr_percentile: PerTimeframe = Field(default_factory=dict)
    bollinger_width_percentile: PerTimeframe = Field(default_factory=dict)
    #: Volatilità realizzata breve (10 barre) su lunga (50): >1 espansione.
    realized_vol_ratio: PerTimeframe = Field(default_factory=dict)
    realized_vol: PerTimeframe = Field(default_factory=dict)
    trend: VolatilityTrend = VolatilityTrend.UNKNOWN


class VolatilityView(BaseModel):
    model_config = _FROZEN

    level: VolatilityLevel
    trend: VolatilityTrend
    #: Shock: molti strumenti insieme oltre il 97° percentile dell'ATR veloce.
    shock: bool = False
    median_atr_percentile: PerTimeframe = Field(default_factory=dict)
    median_bollinger_width_percentile: PerTimeframe = Field(default_factory=dict)
    median_realized_vol_ratio: PerTimeframe = Field(default_factory=dict)
    by_group: dict[str, VolatilityLevel] = Field(default_factory=dict)
    trend_by_group: dict[str, VolatilityTrend] = Field(default_factory=dict)
    assets: tuple[AssetVolatility, ...] = ()
    quality: DimensionQuality
    reasons: tuple[str, ...] = ()


class Relationship(BaseModel):
    """Una relazione intermarket, sui **rendimenti** logaritmici allineati per orario.

    Fase 4.5: la correlazione corrente si confronta con una base storica calcolata
    su finestre che **non si sovrappongono** a quella corrente, e la differenza si
    giudica con un test (trasformata di Fisher). Una correlazione da sola non
    conferma nulla: conta se è diversa, in modo misurabile, da quella abituale.
    """

    model_config = _FROZEN

    a: str
    b: str
    timeframe: Timeframe
    method: str = "log_returns"
    #: Correlazione sulle ultime `current_sample` coppie di rendimenti.
    current_correlation: float | None = Field(default=None, ge=-1, le=1)
    current_sample: int = Field(default=0, ge=0)
    #: Correlazione sulle coppie precedenti (la base storica, senza sovrapposizione).
    baseline_correlation: float | None = Field(default=None, ge=-1, le=1)
    baseline_sample: int = Field(default=0, ge=0)
    #: La base è significativamente diversa da zero (t ≥ 2) e non trascurabile.
    baseline_significant: bool = False
    #: Dispersione delle correlazioni mobili nella base (scarto interquartile).
    baseline_dispersion: float | None = Field(default=None, ge=0)
    #: Quota delle finestre mobili della base con lo stesso segno della base.
    stability: float | None = Field(default=None, ge=0, le=1)
    #: Differenza corrente − base in unità di errore standard (Fisher).
    difference_z: float | None = None
    state: RelationshipState
    note: str = ""


class IntermarketView(BaseModel):
    model_config = _FROZEN

    state: IntermarketState
    relationships: tuple[Relationship, ...]
    consistent: int = 0
    weakening: int = 0
    diverging: int = 0
    unstable: int = 0
    quality: DimensionQuality


class RiskEvidence(BaseModel):
    """Un indizio sul sentiment di rischio, con il suo segno (+ risk-on)."""

    model_config = _FROZEN

    name: str
    score: float | None
    by_timeframe: PerTimeframe = Field(default_factory=dict)
    weight: float = Field(gt=0)
    description: str


class RiskSentiment(BaseModel):
    model_config = _FROZEN

    #: Composito pesato degli indizi, positivo = risk-on.
    score: float | None
    #: Quota (pesata) degli indizi concordi col segno del composito.
    agreement: float | None = None
    by_timeframe: PerTimeframe = Field(default_factory=dict)
    evidence: tuple[RiskEvidence, ...]
    #: Dimensioni che indicano con forza risk-on / risk-off (|valore| ≥ 0.5).
    #: Se entrambe le liste sono piene, il sentiment è MIXED qualunque sia il
    #: composito: la discordanza fra dimensioni non si media (fase 4.5).
    strong_risk_on: tuple[str, ...] = ()
    strong_risk_off: tuple[str, ...] = ()
    label: GlobalRegime
    quality: DimensionQuality


class EventRisk(BaseModel):
    """Rischio macro ed eventi (rivisto in fase 4.5).

    Due misure da non confondere. `calendar_level` viene dal calendario economico,
    e vale solo dentro la finestra che il calendario copre davvero
    (`calendar_coverage_end`): oltre, è `UNKNOWN`. `market_implied_uncertainty` è
    un indizio dedotto dai prezzi (volatilità realizzata e implicita, intermarket,
    conflitti): utile, ma non è un calendario.
    """

    model_config = _FROZEN

    calendar_provider: str = "nessuno"
    calendar_state: ProviderState = ProviderState.NOT_CONFIGURED
    calendar_available: bool
    calendar_coverage_end: datetime | None = None
    #: Il provider pubblica anche il valore effettivo (per misurare la sorpresa).
    calendar_has_actuals: bool = False
    calendar_level: UncertaintyLevel
    calendar_reason: str = ""
    #: Finestre di blocco attive adesso (limiti dell'utente: 60' prima, 30' dopo).
    active_windows: tuple[EventWindow, ...] = ()
    #: Eventi di importanza media o alta nelle prossime 24 ore.
    upcoming_events: tuple[EconomicEvent, ...] = ()
    #: Eventi da 24 ore fa a 24 ore da adesso con la loro fase (PRE_EVENT,
    #: EVENT_WINDOW, POST_EVENT, ACTUAL_AVAILABLE) e l'eventuale sorpresa.
    events: tuple[EventView, ...] = ()
    #: Prossimo evento ad alto impatto per valuta, entro la copertura.
    next_high_impact: dict[str, EconomicEvent] = Field(default_factory=dict)
    market_implied_uncertainty: UncertaintyLevel
    drivers: tuple[str, ...] = ()
    quality: DimensionQuality


class ImpliedVolatility(BaseModel):
    """Volatilità implicita del mercato azionario USA: il VIX, da fonte esterna."""

    model_config = _FROZEN

    source: str
    #: Giornaliero: sfondo dei giorni, non delle ore (correzione pre-fase 5).
    intended_use: IntendedUse = IntendedUse.STRUCTURAL_CONTEXT
    value: float | None = None
    day: date | None = None
    #: Posizione dell'ultima chiusura nell'ultimo anno (252 sedute), da 0 a 1.
    percentile_1y: float | None = Field(default=None, ge=0, le=1)
    change_20d: float | None = None
    level: VolatilityLevel = VolatilityLevel.UNKNOWN
    trend: VolatilityTrend = VolatilityTrend.UNKNOWN
    quality: DimensionQuality


class RatesView(BaseModel):
    """Rendimenti dei Titoli di Stato USA (fonte: Tesoro USA). Categoria MACRO_SERIES."""

    model_config = _FROZEN

    source: str
    intended_use: IntendedUse = IntendedUse.STRUCTURAL_CONTEXT
    day: date | None = None
    yields: dict[str, float] = Field(default_factory=dict)
    #: Variazione su 20 sedute, in punti base.
    change_20d_bp: dict[str, float] = Field(default_factory=dict)
    slope_2s10s_bp: float | None = None
    slope_3m10y_bp: float | None = None
    curve_inverted: bool | None = None
    #: Direzione del decennale: RISING / FALLING / STABLE (soglia ±15 pb in 20 sedute).
    ten_year_trend: str = "UNKNOWN"
    quality: DimensionQuality


class DollarIndexView(BaseModel):
    """DXY ricostruito con la formula ICE dai prezzi XM (sei cambi, pesi fissi).

    Non è il DXY ufficiale (proprietà ICE, non disponibile gratuitamente): è la
    stessa formula applicata ai prezzi del broker. Serve a verificare la forza del
    dollaro stimata dal grafo valutario con un indicatore costruito diversamente.
    """

    model_config = _FROZEN

    source: str = "derivato: formula ICE su EURUSD, USDJPY, GBPUSD, USDCAD, USDSEK, USDCHF (XM)"
    #: Correzione pre-fase 5: il DXY ricostruito è una verifica incrociata della forza
    #: USD stimata dal grafo, non una fonte primaria. Non entra nel regime.
    intended_use: IntendedUse = IntendedUse.CROSS_CHECK
    value: float | None = None
    by_timeframe: PerTimeframe = Field(default_factory=dict)
    score: float | None = None
    label: StrengthLabel = StrengthLabel.UNKNOWN
    #: Correlazione fra i rendimenti H1 del DXY ricostruito e quelli dell'USD stimati
    #: dal grafo: misura di coerenza fra i due metodi.
    correlation_with_usd_strength: float | None = Field(default=None, ge=-1, le=1)
    agrees_with_usd_strength: bool | None = None
    missing_components: tuple[str, ...] = ()
    quality: DimensionQuality


class SourceQuality(BaseModel):
    """Qualità di un dominio di fonti (ADR 0021): una per dominio, non una sola."""

    model_config = _FROZEN

    domain: SourceDomain
    quality: GlobalDataQuality
    #: Falso = nessun provider scelto per questo dominio (limitazione dichiarata).
    configured: bool
    providers: tuple[str, ...] = ()
    intended_use: IntendedUse
    reasons: tuple[str, ...] = ()


class CrossCheck(BaseModel):
    """Una verifica incrociata fra due misure costruite in modo diverso."""

    model_config = _FROZEN

    name: str
    #: None = non verificabile (una delle due misure manca).
    passed: bool | None
    detail: str
    #: Cosa succede se fallisce: nessun effetto sul regime, solo sulla qualità.
    effect: str


class InstrumentReading(BaseModel):
    """Lettura multi-timeframe di uno strumento dell'universo (D1, H4, H1, M15)."""

    model_config = _FROZEN

    canonical: str
    group: ReferenceGroup
    market_status: MarketStatus
    #: Momentum normalizzato per timeframe e composito.
    by_timeframe: PerTimeframe = Field(default_factory=dict)
    score: float | None = None
    label: StrengthLabel = StrengthLabel.UNKNOWN
    timeframes_agree: bool | None = None
    usable_timeframes: tuple[Timeframe, ...] = ()


class Conflict(BaseModel):
    model_config = _FROZEN

    code: str
    dimensions: tuple[str, ...]
    severity: ConflictSeverity
    description: str


class SummaryLine(BaseModel):
    model_config = _FROZEN

    label: str
    value: str


# ======================================================================
# Contesto
# ======================================================================
class GlobalMarketContext(BaseModel):
    """La risposta a "cosa sta succedendo complessivamente sui mercati?"."""

    model_config = _FROZEN

    as_of: datetime
    engine_version: str
    input_fingerprint: str
    market_phase: MarketPhase
    market_phase_reason: str

    regime: GlobalRegime
    regime_reasons: tuple[str, ...]

    risk_sentiment: RiskSentiment
    volatility: VolatilityView
    implied_volatility: ImpliedVolatility | None = None
    rates: RatesView | None = None
    dollar_index: DollarIndexView | None = None
    currency_strength: CurrencyStrengthView
    relative_strength: RelativeStrengthView
    equities: GroupDirection
    metals: GroupDirection
    intermarket: IntermarketView
    macro_event_risk: EventRisk
    conflicts: tuple[Conflict, ...]
    #: Lettura multi-timeframe di ogni strumento dell'universo di riferimento.
    instruments: tuple[InstrumentReading, ...] = ()

    #: Qualità COMPLESSIVA del contesto, derivata dalle qualità per dominio con la
    #: regola di `global_market/sources.py` (ADR 0021). Non è una media.
    data_quality: GlobalDataQuality
    data_quality_reasons: tuple[str, ...] = ()
    #: Qualità per dominio di fonte: broker, riferimento, calendario, macro, news.
    source_quality: tuple[SourceQuality, ...] = ()
    dimension_quality: tuple[DimensionQuality, ...]
    cross_checks: tuple[CrossCheck, ...] = ()
    #: Ciò che il contesto non può sapere, in frasi esplicite.
    known_limitations: tuple[str, ...] = ()
    simulated: bool = False
    providers: tuple[ProviderStatus, ...] = ()
    warnings: tuple[str, ...] = ()
    summary: tuple[SummaryLine, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable_for_decisions(self) -> bool:
        """Fail closed: dati reali, mercato aperto confermato, qualità non insufficiente.

        `DEGRADED` è ammesso: oggi il calendario economico non esiste e il
        contesto non potrebbe mai essere `GOOD`. A valle, la fase 6 userà la
        qualità per ridurre il rischio, non per ignorarla.
        """
        return (
            not self.simulated
            and self.market_phase is MarketPhase.OPEN_CONFIRMED
            and self.data_quality is not GlobalDataQuality.INSUFFICIENT
            and self.regime is not GlobalRegime.UNCERTAIN
        )


class AssetGlobalBackdrop(BaseModel):
    """Il contesto globale visto da un singolo strumento."""

    model_config = _FROZEN

    canonical: str
    regime: GlobalRegime
    volatility_level: VolatilityLevel
    volatility_trend: VolatilityTrend
    #: Per il forex: forza della valuta base e di quella di quotazione.
    base_strength: CurrencyStrength | None = None
    quote_strength: CurrencyStrength | None = None
    #: Base meno quotazione: positivo favorisce il rialzo del cross.
    strength_differential: float | None = None
    #: Per metalli e indici: direzione del gruppo di appartenenza.
    group_direction: StrengthLabel | None = None
    #: Finestre di blocco da calendario attive sulle valute dello strumento.
    event_windows: tuple[EventWindow, ...] = ()
    next_high_impact: tuple[EconomicEvent, ...] = ()
    notes: tuple[str, ...] = ()
    global_data_quality: GlobalDataQuality
    global_usable_for_decisions: bool
