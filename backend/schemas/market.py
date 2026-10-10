"""DTO dei dati di mercato.

Sono immutabili (`frozen=True`) di proposito: una candela o uno snapshot di
simbolo che qualcuno modifica a valle rende impossibile capire su quali numeri
una decisione è stata presa. Chi deve trasformare crea un oggetto nuovo.

`SymbolInfo` riporta le specifiche di contratto **così come le dichiara il
broker**. Nessun valore del pip è scritto nel codice: con XM lo stesso simbolo
può avere digits e contract size diversi fra tipi di conto, e un pip value
indovinato significa una size sbagliata — cioè il rischio dell'utente violato.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from backend.core.enums import Timeframe

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class SymbolInfo(BaseModel):
    """Specifiche di contratto di uno strumento, lette dal broker."""

    model_config = _FROZEN

    name: str
    description: str = ""

    # Percorso di categoria dichiarato dal broker, es.
    # "Derivatives\Spot Metals\GOLD". È la fonte autorevole della classe di
    # attività: XM dichiara GOLD come USD/USD/USD, quindi le valute da sole non
    # distinguono l'oro da un indice americano.
    path: str = ""

    currency_base: str = ""
    currency_profit: str = ""
    currency_margin: str = ""

    digits: int = Field(ge=0, le=10)
    point: float = Field(gt=0, description="Valore di un punto di prezzo")

    # --- dimensione del contratto: serve al position sizing ---
    contract_size: float = Field(gt=0)
    tick_size: float = Field(gt=0, description="Variazione minima di prezzo")
    tick_value: float = Field(
        ge=0,
        description=(
            "trade_tick_value come dichiarato da MT5. NON e' sempre in valuta del "
            "conto (su XM: USD per metalli e indici). Solo diagnostica: ogni calcolo "
            "monetario passa da BrokerValuationService (ADR 0014)."
        ),
    )
    volume_min: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    volume_step: float = Field(gt=0)

    # --- condizioni correnti ---
    spread_points: int = Field(ge=0)
    stops_level_points: int = Field(
        default=0, ge=0, description="Distanza minima di SL/TP imposta dal broker, in punti"
    )
    freeze_level_points: int = Field(default=0, ge=0)

    # `trade_mode` grezzo del broker: 0 vietato, 1 solo long, 2 solo short,
    # 3 solo chiusura, 4 pieno. Conservato oltre al booleano perché "solo
    # chiusura" e "vietato" richiedono comportamenti diversi, e un bool li
    # appiattisce.
    trade_mode: int = Field(default=4, ge=0, le=4)
    # --- swap (overnight), come lo dichiara il broker ---
    #: `swap_mode` MT5: 1 = in punti (XM). Gli altri modi non sono simulati.
    swap_mode: int = 0
    swap_long: float = 0.0
    swap_short: float = 0.0
    #: Giorno della settimana (0 = domenica) in cui si addebita lo swap triplo.
    swap_rollover3days: int = 3
    trade_allowed: bool = True
    visible: bool = True

    as_of: datetime

    @model_validator(mode="after")
    def _check_volumes(self) -> Self:
        if self.volume_min > self.volume_max:
            raise ValueError(f"{self.name}: volume_min maggiore di volume_max")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread_price(self) -> float:
        """Spread espresso in prezzo, non in punti."""
        return self.spread_points * self.point

    def round_price(self, price: float) -> float:
        return round(price, self.digits)

    @property
    def close_only(self) -> bool:
        """Il broker consente solo di chiudere, non di aprire."""
        return self.trade_mode == 3

    @property
    def direction_restricted(self) -> bool:
        """Il broker consente una sola direzione (accade su alcuni titoli)."""
        return self.trade_mode in (1, 2)


class Tick(BaseModel):
    """Ultima quotazione disponibile."""

    model_config = _FROZEN

    symbol: str
    time: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    last: float | None = None
    volume: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread(self) -> float:
        return self.ask - self.bid


class Candle(BaseModel):
    """Una barra OHLC.

    `time` è l'istante di **apertura** della barra, come lo restituisce MT5.
    Confonderlo con la chiusura sposta ogni analisi di un timeframe intero: è
    uno degli errori che generano look-ahead bias nei backtest.
    """

    model_config = _FROZEN

    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: float = 0.0
    spread: int = 0
    real_volume: float = 0.0

    @model_validator(mode="after")
    def _check_ohlc(self) -> Self:
        if self.high < self.low:
            raise ValueError(f"Candela incoerente a {self.time.isoformat()}: high < low")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(
                f"Candela incoerente a {self.time.isoformat()}: open/close fuori da high-low"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


class CandleSeries(BaseModel):
    """Serie storica ordinata di candele, con la sua provenienza."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: Timeframe
    candles: tuple[Candle, ...]
    as_of: datetime
    source: str = "mt5"

    # L'ultima candela è in formazione: usarla come chiusa è il modo più comune
    # di ottenere un backtest brillante e un sistema reale perdente.
    last_candle_closed: bool = False

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        times = [candle.time for candle in self.candles]
        if times != sorted(times):
            raise ValueError(f"{self.symbol} {self.timeframe}: candele non ordinate per tempo")
        if len(set(times)) != len(times):
            raise ValueError(f"{self.symbol} {self.timeframe}: candele duplicate")
        return self

    def __len__(self) -> int:
        return len(self.candles)

    @property
    def closed(self) -> tuple[Candle, ...]:
        """Solo le candele concluse: è su queste che si ragiona."""
        if self.last_candle_closed or not self.candles:
            return self.candles
        return self.candles[:-1]

    @property
    def closes(self) -> tuple[float, ...]:
        return tuple(candle.close for candle in self.closed)

    @property
    def latest(self) -> Candle | None:
        return self.candles[-1] if self.candles else None

    def has_enough(self, minimum: int) -> bool:
        """Vero se ci sono abbastanza candele chiuse per un calcolo richiesto.

        Gli indicatori su serie troppo corte restituiscono numeri plausibili e
        privi di significato: il controllo va fatto prima, non dopo.
        """
        return len(self.closed) >= minimum
