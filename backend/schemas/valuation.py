"""DTO del servizio di valutazione.

Ogni importo porta la sua valuta e la sua fonte. Un numero monetario senza valuta
è un numero che qualcuno prima o poi confronterà con un altro in una valuta
diversa — ed è esattamente il modo in cui si sbaglia del 12,5% senza accorgersene.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import Direction

_FROZEN = ConfigDict(frozen=True, extra="ignore")


class VolumeNormalization(BaseModel):
    """Esito della normalizzazione di un volume sulle regole del broker."""

    model_config = _FROZEN

    requested: float = Field(ge=0)
    volume: float = Field(ge=0)
    #: Perché il volume è diverso da quello richiesto: `step` (arrotondato per
    #: difetto al passo), `max` (tagliato al massimo), `below_min` (sotto il
    #: minimo: zero, mai arrotondato per eccesso), `none`.
    adjustment: str = "none"
    volume_min: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    volume_step: float = Field(gt=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tradeable(self) -> bool:
        return self.volume > 0


class StopLossEstimate(BaseModel):
    """Perdita stimata se lo stop viene toccato, calcolata dal broker."""

    model_config = _FROZEN

    broker_symbol: str
    direction: Direction
    volume: float = Field(ge=0)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    #: Importo positivo: quanto si perde, in valuta del conto.
    loss: float = Field(ge=0)
    account_currency: str
    #: Distanza dello stop in prezzo e in tick.
    stop_distance: float = Field(gt=0)
    stop_distance_ticks: float = Field(gt=0)
    source: str = "order_calc_profit"


class MarginEstimate(BaseModel):
    """Margine richiesto, calcolato dal broker su un volume già normalizzato."""

    model_config = _FROZEN

    broker_symbol: str
    direction: Direction
    volume: float = Field(gt=0)
    price: float = Field(gt=0)
    margin: float = Field(ge=0)
    account_currency: str
    source: str = "order_calc_margin"


class SizeResult(BaseModel):
    """Dimensione di una posizione dato un rischio massimo.

    Proprietà garantite dal servizio e verificate da test di proprietà:
    `expected_loss <= risk_amount`, `volume <= volume_max`, `volume` multiplo di
    `volume_step`, e `volume == 0` (mai arrotondato per eccesso) quando il minimo
    del broker supererebbe il rischio consentito.
    """

    model_config = _FROZEN

    broker_symbol: str
    direction: Direction
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    risk_amount: float = Field(ge=0)
    volume: float = Field(ge=0)
    #: Perdita allo stop con il volume scelto, ricalcolata dal broker.
    expected_loss: float = Field(ge=0)
    loss_per_lot: float = Field(gt=0)
    #: Cosa ha limitato la size: `risk`, `step`, `volume_max`, `user_max`,
    #: `below_min`.
    limited_by: str
    account_currency: str
    reasons: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tradeable(self) -> bool:
        return self.volume > 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def risk_used_pct(self) -> float:
        """Quota del rischio consentito effettivamente usata."""
        if self.risk_amount <= 0:
            return 0.0
        return round(self.expected_loss / self.risk_amount * 100, 2)


class TickValueGap(BaseModel):
    """Confronto diagnostico fra il valore derivato da `tick_value` e il broker.

    Esiste solo per mostrare *perché* non si usa `tick_value`: non entra in
    nessun calcolo di rischio.
    """

    model_config = _FROZEN

    broker_symbol: str
    from_broker: float
    from_tick_value: float
    gap_pct: float
