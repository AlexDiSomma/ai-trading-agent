"""QuoteQuality: la qualità di una quotazione come dato.

Risponde a una domanda sola: *questo prezzo è un'informazione sul presente su cui
si può ragionare?* Le politiche di rischio (spread troppo caro, strumento
disabilitato dal broker) stanno altrove: qui c'è il dato e quanto vale.

Due assi distinti, perché non dicono la stessa cosa:

* `status` — la qualità del dato: valido, vecchio, mancante, mercato chiuso,
  incoerente;
* `market_status` — cosa sappiamo del mercato: aperto **confermato** da una
  quotazione fresca, chiuso **dedotto**, oppure sconosciuto.

Invariante, verificata dal costruttore e da test: `market_status` è
`CLOSED_INFERRED` solo con `is_inference=True`, ed è `OPEN_CONFIRMED` solo con
`status=VALID`. Un'inferenza non diventa mai certezza.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from backend.core.enums import MarketStatus, QuoteQualityStatus

_FROZEN = ConfigDict(frozen=True, extra="ignore")


class QuoteQuality(BaseModel):
    """Qualità di una quotazione, con le misure che la giustificano."""

    model_config = _FROZEN

    broker_symbol: str
    status: QuoteQualityStatus
    market_status: MarketStatus
    #: Vero quando lo stato è una nostra deduzione e non un fatto osservato.
    is_inference: bool
    reasons: tuple[str, ...] = ()

    bid: float | None = None
    ask: float | None = None
    #: Spread in prezzo (ask - bid). Negativo solo se la quotazione è incoerente.
    spread: float | None = None
    spread_points: float | None = None
    #: Spread diviso per l'ATR del timeframe operativo (ADR 0013).
    spread_to_atr: float | None = None

    last_tick_time: datetime | None = None
    last_candle_time: datetime | None = None
    #: Età dell'ultima quotazione e dell'ultima candela chiusa, in secondi.
    tick_age_seconds: float | None = None
    candle_age_seconds: float | None = None
    #: Limite di freschezza applicato: oltre questa età il dato è `STALE`.
    max_tick_age_seconds: float = Field(gt=0)
    evaluated_at: datetime

    @model_validator(mode="after")
    def _inferenza_mai_certezza(self) -> Self:
        if self.market_status is MarketStatus.CLOSED_INFERRED and not self.is_inference:
            raise ValueError("CLOSED_INFERRED richiede is_inference=True: è una deduzione.")
        if self.market_status is MarketStatus.OPEN_CONFIRMED and (
            self.status is not QuoteQualityStatus.VALID or self.is_inference
        ):
            raise ValueError("OPEN_CONFIRMED richiede una quotazione VALID osservata.")
        if self.status is QuoteQualityStatus.MARKET_CLOSED and (
            self.market_status is not MarketStatus.CLOSED_INFERRED
        ):
            raise ValueError("MARKET_CLOSED deve dichiararsi CLOSED_INFERRED.")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_fresh(self) -> bool:
        return self.tick_age_seconds is not None and (
            self.tick_age_seconds <= self.max_tick_age_seconds
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usable(self) -> bool:
        """Il dato si può usare per decidere: valido **e** mercato confermato aperto."""
        return (
            self.status is QuoteQualityStatus.VALID
            and self.market_status is MarketStatus.OPEN_CONFIRMED
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2
