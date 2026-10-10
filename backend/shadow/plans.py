"""ShadowTradePlan: cosa avrebbe fatto un gruppo, in prezzi e in R. Nient'altro.

Un piano ombra **non ha volume, non ha conto, non ha valuta**: rischio e risultato si
misurano in R (multipli della distanza dello stop). Non è un `TradeIntent`, non passa
dal Risk Engine e nessun modulo del sistema lo accetta in ingresso per eseguirlo:
`backend.execution` non esiste e il package `shadow` non importa nulla che parli col
broker (test AST).

Tre gruppi, sempre separati (fase 5B):

* **A — QUANT_ONLY**: la baseline deterministica (`quant_baseline`).
* **B — QUANT_AI_FILTER**: lo stesso piano di A, tenuto solo se l'AI lo approva.
* **C — AI_THESIS**: la tesi dell'AI, costruita senza vedere il piano di A.

Stati: `ACTIVE` (LONG/SHORT da simulare), `WAIT` (scelta di non operare),
`FILTERED` (B: piano di A respinto dall'AI), `REJECTED` (output AI invalido o senza
evidenze: fail closed, nessuna operazione).

La `confidence` dell'AI è registrata **come etichetta**, non come probabilità: servirà
solo a misurarne a posteriori la calibrazione.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.ai.contracts import impronta
from backend.paper.geometry import rr  # noqa: F401 — una sola definizione del RR
from backend.shadow.conditions import PriceCondition

_LETTURA = ConfigDict(frozen=True, extra="ignore")

Gruppo = Literal["A_QUANT_ONLY", "B_QUANT_AI_FILTER", "C_AI_THESIS"]
StatoPiano = Literal["ACTIVE", "WAIT", "FILTERED", "REJECTED"]
Decisione = Literal["LONG", "SHORT", "WAIT"]
TipoEntrata = Literal["MARKET", "STOP", "LIMIT"]


class ShadowTradePlan(BaseModel):
    model_config = _LETTURA

    plan_version: str = "shadow-plan-1.0.0"
    group: Gruppo
    canonical: str
    #: Istante della decisione (UTC). Nessun dato successivo è entrato nel piano.
    decided_at: datetime
    mode: Literal["LIVE", "REPLAY"]
    decision: Decisione
    status: StatoPiano

    entry_type: TipoEntrata | None = None
    #: Prezzo d'ingresso: per MARKET è il prezzo di riferimento della decisione;
    #: la simulazione usa l'apertura della prima barra successiva.
    entry_price: float | None = None
    entry_valid_hours: int | None = None
    entry_conditions: tuple[PriceCondition, ...] = ()
    stop_loss: float | None = None
    take_profit: float | None = None
    invalidation_conditions: tuple[PriceCondition, ...] = ()
    target_conditions: tuple[PriceCondition, ...] = ()
    horizon_hours: int = Field(ge=1, le=168)

    #: Rapporto rendimento/rischio **calcolato** dai prezzi del piano.
    computed_rr: float | None = None
    #: Contesto numerico per misurare l'esito: prezzo e ATR H1 alla decisione,
    #: spread (costo in R sottratto al risultato simulato).
    reference_price: float
    atr_h1: float | None = None
    spread_price: float = 0.0

    #: Da dove viene il piano: versione della baseline, record AI della tesi e del filtro.
    quant_version: str | None = None
    thesis_record_id: str | None = None
    filter_record_id: str | None = None
    #: Etichetta dell'AI (LOW/MEDIUM/HIGH). NON è una probabilità.
    ai_confidence_label: str | None = None
    reasons: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def plan_id(self) -> str:
        return impronta(
            {
                "g": self.group,
                "c": self.canonical,
                "t": self.decided_at.isoformat(),
                "m": self.mode,
                "q": self.quant_version,
                "a": self.thesis_record_id,
                "f": self.filter_record_id,
            }
        )[:24]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def integrity_sha256(self) -> str:
        return impronta(
            self.model_dump(mode="json", exclude={"integrity_sha256", "plan_id"})  # type: ignore[arg-type]
        )

    @property
    def verso(self) -> int:
        return {"LONG": 1, "SHORT": -1}.get(self.decision, 0)

    @property
    def scadenza(self) -> datetime:
        return self.decided_at + timedelta(hours=self.horizon_hours)
