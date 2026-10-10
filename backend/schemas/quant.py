"""DTO dell'analisi quantitativa.

Questi oggetti sono la *fotografia* dell'analisi a una barra: i valori correnti,
non le serie storiche. Le serie restano dentro `quant/` e `structure/`, dove
servono al calcolo; quello che attraversa i confini e finisce su database è
questo.

La distinzione non è cosmetica: persistere le serie complete di cinque timeframe
per dieci strumenti a ogni ciclo significherebbe scrivere milioni di numeri che
il broker già conserva (ADR 0009). Qui si registra *cosa abbiamo concluso*, non
*da quali numeri*.

Tutti i campi derivati sono dichiarati con la loro unità di misura nel nome o nel
commento. Un ATR senza unità è un numero che qualcuno prima o poi confronterà con
una percentuale.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import (
    BreakoutKind,
    DataQuality,
    LevelKind,
    MarketRegime,
    StructureBreak,
    StructureTrend,
    SwingKind,
    Timeframe,
)

# I DTO di questo modulo vengono salvati su database e **riletti**: il dump
# contiene anche i campi calcolati, che in ingresso non sono accettabili come
# dati. Con `extra="forbid"` la rilettura del proprio payload fallirebbe; con
# `ignore` i campi calcolati vengono scartati e ricalcolati, che è il
# comportamento corretto. La protezione contro i nomi sbagliati resta: i campi
# dichiarati sono obbligatori, quindi un errore di battitura diventa un campo
# mancante.
_FROZEN = ConfigDict(frozen=True, extra="ignore")


class IndicatorSnapshot(BaseModel):
    """Valori correnti degli indicatori su un timeframe."""

    model_config = _FROZEN

    # --- medie ---
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None

    # --- oscillatori ---
    rsi14: float | None = Field(default=None, ge=0, le=100)
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None

    # --- volatilità ---
    atr14: float | None = Field(default=None, ge=0)
    #: ATR in percentuale del prezzo: confrontabile fra EURUSD e US100Cash.
    atr_pct: float | None = Field(default=None, ge=0)
    bollinger_upper: float | None = None
    bollinger_middle: float | None = None
    bollinger_lower: float | None = None
    #: (superiore - inferiore) / centrale, adimensionale.
    bollinger_width: float | None = Field(default=None, ge=0)
    #: Posizione dell'ampiezza nella sua storia recente, 0 = minimo storico.
    #: È questo e non l'ampiezza assoluta a indicare una compressione.
    bollinger_width_rank: float | None = Field(default=None, ge=0, le=1)
    #: Deviazione standard dei rendimenti logaritmici, per barra.
    realized_volatility: float | None = Field(default=None, ge=0)

    # --- direzione ---
    adx14: float | None = Field(default=None, ge=0, le=100)
    di_plus: float | None = Field(default=None, ge=0, le=100)
    di_minus: float | None = Field(default=None, ge=0, le=100)
    #: Pendenza della EMA50, in percentuale per barra.
    ema50_slope_pct: float | None = None

    # --- momentum su più orizzonti ---
    momentum_5: float | None = None
    momentum_20: float | None = None
    momentum_60: float | None = None

    close: float
    bars_used: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def price_vs_ema200_pct(self) -> float | None:
        """Distanza dalla EMA200 in percentuale. Negativa se il prezzo è sotto."""
        if self.ema200 is None or self.ema200 == 0:
            return None
        return (self.close / self.ema200 - 1.0) * 100.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ema_alignment(self) -> int:
        """+1 se ema20>ema50>ema200, -1 se invertite, 0 se mescolate."""
        if None in (self.ema20, self.ema50, self.ema200):
            return 0
        if self.ema20 > self.ema50 > self.ema200:  # type: ignore[operator]
            return 1
        if self.ema20 < self.ema50 < self.ema200:  # type: ignore[operator]
            return -1
        return 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bollinger_position(self) -> float | None:
        """Posizione del prezzo nelle bande: 0 = inferiore, 1 = superiore."""
        if None in (self.bollinger_upper, self.bollinger_lower):
            return None
        ampiezza = self.bollinger_upper - self.bollinger_lower  # type: ignore[operator]
        if ampiezza <= 0:
            return 0.5
        return (self.close - self.bollinger_lower) / ampiezza  # type: ignore[operator]


class SwingPoint(BaseModel):
    """Massimo o minimo locale.

    `confirmed` è il campo che impedisce il look-ahead: uno swing richiede
    `strength` barre **dopo** di sé per essere riconosciuto, quindi le ultime
    barre non sono classificabili. Usare uno swing non confermato equivale a
    sapere il futuro, ed è l'errore che rende brillanti i backtest sbagliati.
    """

    model_config = _FROZEN

    index: int = Field(ge=0)
    time: datetime
    price: float = Field(gt=0)
    kind: SwingKind
    confirmed: bool
    #: Barre esaminate per lato nel riconoscimento.
    strength: int = Field(ge=1)


class PriceLevel(BaseModel):
    """Livello di supporto o resistenza, ricavato dagli swing confermati."""

    model_config = _FROZEN

    price: float = Field(gt=0)
    kind: LevelKind
    #: Quanti swing distinti cadono entro la tolleranza di questo livello.
    touches: int = Field(ge=1)
    #: Indice della barra dell'ultimo contatto: un livello vecchio conta meno.
    last_touch_index: int = Field(ge=0)
    #: Distanza dal prezzo corrente in multipli di ATR. In ATR e non in
    #: percentuale perché è ciò che rende confrontabili strumenti diversi e
    #: perché è l'unità in cui si ragiona sugli stop.
    distance_atr: float | None = None
    #: Punteggio 0-1 composto da numero di contatti e attualità. Euristico:
    #: nessuna validazione statistica dietro, e va dichiarato.
    strength: float = Field(ge=0, le=1)


class StructureSnapshot(BaseModel):
    """Struttura di mercato dalla successione degli swing confermati."""

    model_config = _FROZEN

    trend: StructureTrend
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    previous_swing_high: float | None = None
    previous_swing_low: float | None = None
    #: Rottura dell'ultimo swing confermato da parte della chiusura corrente.
    structure_break: StructureBreak = StructureBreak.NONE
    #: Rottura **contro** la struttura prevalente: il primo segnale che la
    #: tendenza in atto potrebbe essere finita. Probabilistico, non certo.
    change_of_character: bool = False
    confirmed_swings: int = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def range_height(self) -> float | None:
        if self.last_swing_high is None or self.last_swing_low is None:
            return None
        return self.last_swing_high - self.last_swing_low


class BreakoutSnapshot(BaseModel):
    """Stato di consolidamento e rottura.

    Tutte classificazioni probabilistiche: "BREAKOUT_UP" significa
    "configurazione compatibile con una rottura rialzista", non "questa è una
    rottura". Le soglie sono dichiarate nei campi perché sono euristiche da
    validare in walk-forward, non costanti di natura.
    """

    model_config = _FROZEN

    kind: BreakoutKind
    #: Estremi del consolidamento osservato nelle barre precedenti l'ultima.
    range_high: float | None = None
    range_low: float | None = None
    #: Ampiezza del consolidamento in ATR: sotto la soglia si parla di compressione.
    range_width_atr: float | None = Field(default=None, ge=0)
    bars_in_range: int = Field(default=0, ge=0)
    #: Livello rotto, se c'è stata una rottura.
    broken_level: float | None = None
    #: Barre trascorse dalla rottura. 0 = la barra appena chiusa.
    bars_since_break: int | None = None
    #: Soglie usate, riportate perché il risultato non si interpreta senza.
    compression_threshold_atr: float = Field(default=3.0, gt=0)
    lookback_bars: int = Field(default=20, ge=5)


class TrendClassification(BaseModel):
    """Classificazione della tendenza su un timeframe.

    I **fattori restano separati e leggibili** accanto al punteggio: è la
    decisione presa in `docs/ANALISI_SPECIFICA.md` §2.4. Un punteggio unico di
    cui non si possono leggere le componenti non permette di capire perché una
    strategia smette di funzionare.
    """

    model_config = _FROZEN

    regime: MarketRegime
    #: +1 rialzista, -1 ribassista, 0 nessuna direzione.
    direction: int = Field(ge=-1, le=1)
    #: Punteggio composito in [-1, 1]. Euristico e non calibrato: serve a
    #: ordinare, non a stimare probabilità.
    score: float = Field(ge=-1, le=1)
    #: Quanto i fattori concordano fra loro, in [0, 1]. Non è una probabilità.
    confidence: float = Field(ge=0, le=1)
    #: Contributo di ogni fattore, nome -> valore normalizzato in [-1, 1].
    factors: dict[str, float] = Field(default_factory=dict)
    #: Motivazioni in italiano, pronte da mostrare e da registrare nel journal.
    reasons: tuple[str, ...] = ()


class TimeframeAnalysis(BaseModel):
    """Analisi completa di uno strumento su un singolo timeframe."""

    model_config = _FROZEN

    timeframe: Timeframe
    #: A cosa serve questo timeframe: structure, trend, setup, execution.
    role: str
    #: Istante di apertura dell'ultima candela **chiusa** usata nei calcoli.
    last_closed_at: datetime
    bars_closed: int = Field(ge=0)
    quality: DataQuality

    indicators: IndicatorSnapshot
    structure: StructureSnapshot
    breakout: BreakoutSnapshot
    trend: TrendClassification
    levels: tuple[PriceLevel, ...] = ()
    swings: tuple[SwingPoint, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def nearest_support(self) -> float | None:
        supporti = [lv.price for lv in self.levels if lv.kind is LevelKind.SUPPORT]
        return max(supporti) if supporti else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def nearest_resistance(self) -> float | None:
        resistenze = [lv.price for lv in self.levels if lv.kind is LevelKind.RESISTANCE]
        return min(resistenze) if resistenze else None
