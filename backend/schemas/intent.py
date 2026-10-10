"""TradeIntent: un'intenzione d'operazione con i numeri del broker.

**Non è un ordine e non viene eseguita da nessuno.** Nel sistema non esiste codice
che invii ordini (ADR 0004): `TradeIntent` è il formato in cui un'idea operativa
viene tradotta in volume, perdita attesa e margine, perché il Risk Engine della
fase 6 possa giudicarla.

`executable` significa *"i dati su cui si basa sono validi"*: quotazione `VALID`,
mercato `OPEN_CONFIRMED`, size positiva, perdita entro il budget. È una condizione
**necessaria e non sufficiente**. Un intent eseguibile non è approvato: lo
diventerà solo con una `RiskApproval` (ADR 0005), che oggi non esiste.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.core.enums import Direction, MarketStatus, QuoteQualityStatus

_FROZEN = ConfigDict(frozen=True, extra="ignore")


class TradeIntent(BaseModel):
    """Intenzione dimensionata, non approvata, non eseguita."""

    model_config = _FROZEN

    canonical: str
    broker_symbol: str
    direction: Direction
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    risk_budget: float = Field(ge=0)

    volume: float = Field(ge=0)
    expected_loss: float = Field(ge=0)
    margin_required: float | None = Field(default=None, ge=0)
    account_currency: str

    quote_status: QuoteQualityStatus
    market_status: MarketStatus

    #: Precondizioni sui dati soddisfatte. Non è un'approvazione di rischio.
    executable: bool
    blocking_reasons: tuple[str, ...] = ()
    created_at: datetime
