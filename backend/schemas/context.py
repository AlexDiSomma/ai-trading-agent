"""Contesto di mercato normalizzato: `AssetMarketContext` e `MarketSnapshot`.

Sono gli oggetti che la futura intelligence riceverà in ingresso. Due requisiti
hanno guidato la forma.

**Tipizzati e persistibili.** Ogni campo ha un tipo e un significato dichiarato, e
l'oggetto intero si serializza in JSON per finire su database: una decisione
dovrà essere ricostruibile a mesi di distanza, e per farlo serve il contesto che
l'ha prodotta, non solo la decisione (ADR 0001, docs/DATABASE.md).

**Autodescrittivi sulla propria affidabilità.** Ogni contesto porta la qualità del
dato, l'età, lo stato della quotazione e se i prezzi sono simulati. Un agente che
riceve un contesto deve poter rifiutare di decidere senza dover interrogare altri
oggetti — e nel dubbio il contesto dice di no (`usable_for_decisions`).

Nessuna decisione operativa vive qui: non ci sono entry, stop o direzioni
suggerite. Questo è il mondo descritto, non il piano d'azione.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import (
    AssetClass,
    DataQuality,
    MarketPhase,
    MarketRegime,
    MarketStatus,
    QuoteState,
    StructureTrend,
    Timeframe,
)
from backend.schemas.account import AccountSnapshot
from backend.schemas.exposure import ExposureSnapshot
from backend.schemas.global_market import AssetGlobalBackdrop, GlobalMarketContext
from backend.schemas.instrument import Instrument
from backend.schemas.quant import TimeframeAnalysis
from backend.schemas.quote import QuoteQuality

# I DTO di questo modulo vengono salvati su database e **riletti**: il dump
# contiene anche i campi calcolati, che in ingresso non sono accettabili come
# dati. Con `extra="forbid"` la rilettura del proprio payload fallirebbe; con
# `ignore` i campi calcolati vengono scartati e ricalcolati, che è il
# comportamento corretto. La protezione contro i nomi sbagliati resta: i campi
# dichiarati sono obbligatori, quindi un errore di battitura diventa un campo
# mancante.
_FROZEN = ConfigDict(frozen=True, extra="ignore")

#: Peso di ogni timeframe nell'allineamento complessivo. La struttura lenta pesa
#: più dell'esecuzione: è la gerarchia del progetto tradotta in numeri. Pesi
#: **provvisori**, da validare in walk-forward (fase 9).
PESI_TIMEFRAME: dict[Timeframe, float] = {
    Timeframe.MN1: 0.10,
    Timeframe.W1: 0.15,
    Timeframe.D1: 0.35,
    Timeframe.H4: 0.25,
    Timeframe.H1: 0.20,
    Timeframe.M30: 0.12,
    Timeframe.M15: 0.12,
    Timeframe.M5: 0.08,
    Timeframe.M1: 0.04,
}


class QuoteStatus(BaseModel):
    """Stato della quotazione, con la distinzione fra fatto e deduzione.

    `is_inference=True` segnala che lo stato è una nostra conclusione e non un
    dato del broker: succede per `MARKET_CLOSED`, perché l'API Python di MT5 non
    espone gli orari di sessione (ADR 0011).
    """

    model_config = _FROZEN

    state: QuoteState
    reason: str
    is_inference: bool
    tick_age_seconds: float | None = None
    spread_points: int | None = None
    #: Spread in rapporto all'ATR del timeframe **operativo** (H1). E' questo e non
    #: i punti assoluti a dire se lo spread e' caro: 616 punti su US100Cash sono
    #: normali, 616 su EURUSD sarebbero un allargamento di sessanta volte.
    #: Il riferimento e' H1 e non M5 perche' l'ATR a cinque minuti collassa nelle
    #: ore calme e farebbe sembrare caro ogni strumento.
    spread_atr_ratio: float | None = None
    last_bar_age_seconds: float | None = None
    bid: float | None = None
    ask: float | None = None
    #: Stato del mercato secondo la quotazione: aperto **confermato**, chiuso
    #: **dedotto**, oppure sconosciuto. Mai "chiuso" come certezza.
    market_status: MarketStatus = MarketStatus.UNKNOWN
    #: Qualità completa del dato (ADR 0015), da cui `state` deriva.
    quality: QuoteQuality | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allows_new_positions(self) -> bool:
        return self.state.allows_new_positions


class ContractSummary(BaseModel):
    """Le specifiche che servono a valutare e dimensionare, già lette dal broker."""

    model_config = _FROZEN

    digits: int = Field(ge=0)
    point: float = Field(gt=0)
    contract_size: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    spread_points: int = Field(ge=0)
    stops_level_points: int = Field(ge=0)
    currency_base: str = ""
    currency_profit: str = ""

    #: Valore in valuta del conto di un movimento di 1.0 di prezzo, per 1 lotto,
    #: **calcolato dal broker** e non derivato da `tick_value` (ADR 0012).
    #: `None` se il broker non l'ha fornito: in quel caso la posizione non è
    #: dimensionabile e il Risk Engine rifiuterà, invece di approssimare.
    value_per_price_unit: float | None = None
    value_source: str = "broker_calculator"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread_price(self) -> float:
        return self.spread_points * self.point


class MultiTimeframeView(BaseModel):
    """Lettura d'insieme della gerarchia dei timeframe.

    Risponde alla domanda che la gerarchia esiste per porre: *i timeframe
    raccontano la stessa storia?* Un allineamento alto con un conflitto fra
    struttura ed esecuzione è informazione diversa da un allineamento basso, e
    tenerle distinte evita di mediare due situazioni opposte in un unico numero.
    """

    model_config = _FROZEN

    #: Bias strutturale da D1 e H4: dove va il mercato nel suo complesso.
    structural_bias: int = Field(ge=-1, le=1)
    #: Tendenza operativa da H1: il timeframe su cui si decide.
    operational_bias: int = Field(ge=-1, le=1)
    #: Timeframe del setup (M15) e dell'esecuzione (M5).
    setup_bias: int = Field(ge=-1, le=1)
    execution_bias: int = Field(ge=-1, le=1)

    #: Allineamento pesato in [-1, 1]: +1 tutti rialzisti, -1 tutti ribassisti.
    alignment: float = Field(ge=-1, le=1)
    #: Quanti timeframe concordano con il segno dell'allineamento.
    aligned_timeframes: int = Field(ge=0)
    total_timeframes: int = Field(ge=0)
    #: Vero quando struttura lenta ed esecuzione puntano in direzioni opposte.
    structure_execution_conflict: bool = False
    #: Regime del timeframe strutturale dominante.
    dominant_regime: MarketRegime = MarketRegime.UNCERTAIN
    dominant_structure: StructureTrend = StructureTrend.UNDEFINED
    notes: tuple[str, ...] = ()


class AssetMarketContext(BaseModel):
    """Tutto ciò che sappiamo di uno strumento a un istante.

    È l'unità che la fase 5 ordinerà in graduatoria e che la fase 7 passerà
    all'agente. Non contiene proposte operative: descrive.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    asset_class: AssetClass
    as_of: datetime
    #: Ora stimata del server del broker, se misurabile (ADR 0010).
    broker_time: datetime | None = None
    broker_time_source: str = "UNAVAILABLE"

    #: Identità dello strumento (ADR 0014): nome canonico, classe, valute. Il
    #: resto del sistema ragiona su questo, non sulla grafia di XM.
    instrument: Instrument | None = None

    quote: QuoteStatus
    contract: ContractSummary
    #: Il contesto globale visto da questo strumento (fase 4). Presente quando il
    #: contesto è costruito dall'istantanea di mercato, che calcola prima il globale.
    global_backdrop: AssetGlobalBackdrop | None = None
    timeframes: dict[Timeframe, TimeframeAnalysis] = Field(default_factory=dict)
    multi_timeframe: MultiTimeframeView

    quality: DataQuality
    #: Vero se i prezzi vengono dal terminale finto. Deve risalire fino alla
    #: dashboard: un numero simulato presentato come reale è peggio di un numero
    #: assente.
    simulated: bool = False
    warnings: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable_for_decisions(self) -> bool:
        """Se falso, nessun modulo a valle deve decidere su questo contesto.

        Richiede tre cose insieme: dati di qualità, quotazione valida e prezzi
        reali. È il fail closed applicato al contesto — nel dubbio, no.
        """
        return (
            self.quality.usable_for_decisions
            and self.quote.allows_new_positions
            and not self.simulated
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        """Perché questo contesto non è utilizzabile."""
        motivi: list[str] = []
        if not self.quality.usable_for_decisions:
            motivi.append(f"Qualità dei dati: {self.quality.value}")
        if not self.quote.allows_new_positions:
            motivi.append(self.quote.reason)
        if self.simulated:
            motivi.append("Prezzi simulati: nessun valore ha significato di mercato")
        return tuple(motivi)

    def analysis(self, timeframe: Timeframe) -> TimeframeAnalysis | None:
        return self.timeframes.get(timeframe)


class WhitelistProblem(BaseModel):
    """Una voce della whitelist che non corrisponde a un simbolo del broker."""

    model_config = _FROZEN

    requested: str
    severity: str
    message: str
    suggestions: tuple[str, ...] = ()


class MarketSnapshot(BaseModel):
    """Istantanea normalizzata di tutto ciò che il sistema osserva.

    Un oggetto solo, con un solo `as_of`: conto, esposizione e contesti degli
    strumenti **letti nello stesso giro**. Comporre un'analisi da letture fatte in
    momenti diversi produce numeri che non sono mai esistiti insieme, ed è il tipo
    di incoerenza che non si scopre guardando i singoli valori.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    as_of: datetime
    broker: str
    simulated: bool

    broker_time: datetime | None = None
    broker_time_source: str = "UNAVAILABLE"
    broker_time_offset_seconds: float | None = None

    market_phase: MarketPhase
    market_phase_reason: str = ""

    account: AccountSnapshot
    exposure: ExposureSnapshot
    #: Contesto globale, calcolato PRIMA degli strumenti (pipeline della fase 4).
    global_context: GlobalMarketContext | None = None
    assets: tuple[AssetMarketContext, ...] = ()

    whitelist_problems: tuple[WhitelistProblem, ...] = ()
    warnings: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable_assets(self) -> tuple[str, ...]:
        """Strumenti su cui si potrebbe ragionare adesso."""
        return tuple(a.symbol for a in self.assets if a.usable_for_decisions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def observed_assets(self) -> int:
        return len(self.assets)

    def asset(self, symbol: str) -> AssetMarketContext | None:
        cercato = symbol.lower()
        return next((a for a in self.assets if a.symbol.lower() == cercato), None)
