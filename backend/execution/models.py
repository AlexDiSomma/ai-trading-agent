"""Oggetti dell'esecuzione reale: approvazione di rischio, ordine, esito.

`RiskApproval` è il solo biglietto d'ingresso verso il gateway (ADR 0005): lo emette
il Risk Engine con size, rischio e scadenza; il gateway rifiuta tutto il resto.

`LiveOrder` è il record persistito di un'intenzione d'ordine, con chiave di
idempotenza: lo stesso piano produce sempre la stessa chiave, e una chiave già
registrata non genera mai un secondo invio.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(frozen=True, extra="ignore")

StatoOrdine = Literal[
    "CREATED",  # registrato, non ancora inviato
    "SENT",  # inviato, risposta non ancora registrata
    "FILLED",  # eseguito per intero
    "PARTIALLY_FILLED",  # eseguito in parte; il resto è annullato
    "REJECTED",  # rifiutato (order_check o server)
    "UNKNOWN",  # esito ignoto: da riconciliare, blocca nuovi ordini
    "NOT_EXECUTED",  # riconciliato: il broker non ha eseguito nulla
    "CLOSED",  # posizione chiusa (SL/TP server, invalidazione, tempo, protezione)
]
Scopo = Literal["OPEN", "CLOSE", "PROTECT"]
STATI_FINALI = frozenset({"REJECTED", "NOT_EXECUTED", "CLOSED"})


class RiskApproval(BaseModel):
    """Emessa solo da `LiveRiskEngine`. Breve validità: il mercato si muove."""

    model_config = _FROZEN

    approval_id: str
    engine_id: str
    idempotency_key: str
    canonical: str
    broker_symbol: str
    direction: Literal["LONG", "SHORT"]
    volume: float = Field(gt=0)
    entry_reference: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    risk_amount: float = Field(gt=0)
    max_slippage_points: float = Field(ge=0)
    issued_at: datetime
    expires_at: datetime
    reasons: tuple[str, ...] = ()


class LiveOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")

    idempotency_key: str
    purpose: Scopo
    canonical: str
    broker_symbol: str
    direction: Literal["LONG", "SHORT"]
    volume_requested: float
    volume_filled: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    state: StatoOrdine = "CREATED"
    attempts: int = 0
    position_ticket: int | None = None
    order_ticket: int | None = None
    deal_ticket: int | None = None
    fill_price: float | None = None
    last_retcode: int | None = None
    approval_id: str | None = None
    plan_ref: str | None = None
    #: Per la gestione: invalidazioni e scadenza del piano (JSON del TradePlan).
    plan: dict[str, object] | None = None
    close_reason: str | None = None
    realized_pnl: float | None = None
    created_at: datetime
    updated_at: datetime
    history: list[dict[str, object]] = Field(default_factory=list)

    def evento(self, istante: datetime, testo: str, **dati: object) -> None:
        self.history.append({"at": istante.isoformat(), "event": testo, **dati})
        self.updated_at = istante


def chiave_idempotenza(scopo: str, riferimento: str) -> str:
    """Deterministica: lo stesso piano (o la stessa chiusura) dà sempre la stessa chiave."""
    testo = json.dumps({"r": riferimento, "s": scopo}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()[:16]


#: Numero magico degli ordini del sistema: distingue i nostri da quelli manuali.
MAGIC = 26_10_06


def commento(chiave: str) -> str:
    """Commento MT5 (max 31 caratteri) che porta la chiave: serve alla riconciliazione."""
    return f"ata:{chiave}"
