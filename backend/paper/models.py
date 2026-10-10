"""Oggetti del trading paper: piano deterministico, posizione, trade chiuso, conto.

`TradePlan` è il punto in cui un'idea (della baseline quant o dell'AI) diventa
numeri decisi **dal codice**: tipo d'ingresso, RR, scadenza. Tutto ciò che segue —
gate di rischio, size, ingresso, gestione, uscita — lavora su questo oggetto.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.ai.contracts import impronta
from backend.paper.trailing import Ombra
from backend.shadow.conditions import PriceCondition

_FROZEN = ConfigDict(frozen=True, extra="ignore")

Libro = Literal["QUANT", "AI"]
Verso = Literal["LONG", "SHORT"]
TipoIngresso = Literal["MARKET", "STOP", "LIMIT"]
#: TRAIL = stop mobile (solo se l'utente l'ha acceso per quel conto).
MotivoUscita = Literal["TP", "SL", "TRAIL", "INVALIDATION", "TIME", "MANUAL"]


class TradePlan(BaseModel):
    model_config = _FROZEN

    book: Libro
    canonical: str
    broker_symbol: str
    direction: Verso
    entry_type: TipoIngresso
    entry_price: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float = Field(gt=0)
    #: Calcolato dai prezzi (`paper.geometry.rr`), mai dichiarato.
    rr: float
    atr_h1: float = Field(gt=0)
    created_at: datetime
    #: Un ingresso STOP/LIMIT non innescato entro questo istante decade.
    valid_until: datetime
    #: Oltre questo istante una posizione aperta si chiude al prezzo corrente.
    expires_at: datetime
    invalidations: tuple[PriceCondition, ...] = ()
    #: Esperimento: versione della strategia che ha prodotto il piano. Etichetta per
    #: le statistiche, mai un blocco.
    strategy_version: str
    source_record_id: str | None = None
    ai_confidence_label: str | None = None
    reasons: tuple[str, ...] = ()

    @property
    def verso(self) -> int:
        return 1 if self.direction == "LONG" else -1

    @property
    def plan_id(self) -> str:
        return impronta(
            {
                "b": self.book,
                "c": self.canonical,
                "t": self.created_at.isoformat(),
                "d": self.direction,
                "e": self.entry_price,
            }
        )[:20]


class PaperPosition(BaseModel):
    """Ordine in attesa (`PENDING`) o posizione aperta (`OPEN`) nel conto paper."""

    model_config = ConfigDict(extra="ignore")

    position_id: str
    plan: TradePlan
    status: Literal["PENDING", "OPEN"]
    opened_at: datetime | None = None
    entry_fill: float | None = None
    volume: float = 0.0
    #: Perdita allo stop in valuta del conto, dal broker, al volume eseguito: è 1R.
    risk_amount: float = 0.0
    spread_at_entry: float = 0.0
    slippage_entry: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    #: Rollover già addebitati (date broker), per non contarli due volte.
    swap_days: list[str] = Field(default_factory=list)
    mae_price: float = 0.0
    mfe_price: float = 0.0
    #: Chiusura dell'ultima barra M1 già esaminata (UTC).
    checked_until: datetime | None = None
    last_price: float | None = None
    unrealized_pnl: float | None = None
    notes: list[str] = Field(default_factory=list)
    #: Picco favorevole in R sulle sole barre M1 chiuse: guida lo stop mobile.
    picco_r: float = 0.0
    #: Regola di stop mobile applicata davvero, fissata all'apertura (None = nessuna).
    trailing_rule: str | None = None
    #: Stop mobile corrente in R dall'ingresso (None = stop iniziale del piano).
    stop_r: float | None = None
    #: Tutte le regole in ombra: che uscita avrebbe avuto il trade con ciascuna.
    ombre: dict[str, Ombra] = Field(default_factory=dict)


class PaperTrade(BaseModel):
    model_config = _FROZEN

    position_id: str
    plan: TradePlan
    opened_at: datetime
    closed_at: datetime
    entry_fill: float
    exit_price: float
    exit_reason: MotivoUscita
    volume: float
    risk_amount: float
    gross_pnl: float
    commission: float
    swap: float
    #: Costo dello spread e dello slittamento, già dentro ai prezzi; riportato in valuta.
    spread_cost: float
    slippage_cost: float
    net_pnl: float
    #: Risultato netto in R (net_pnl / risk_amount).
    r_multiple: float
    mae_r: float
    mfe_r: float
    ambiguous_bar: bool = False
    notes: tuple[str, ...] = ()
    trailing_rule: str | None = None
    #: Uscita reale in R di prezzo (senza costi in valuta): la base del confronto ombra.
    price_r: float | None = None
    #: Uscita in R di prezzo che ogni regola di stop mobile avrebbe avuto.
    shadow_r: dict[str, float] = Field(default_factory=dict)

    @property
    def hours(self) -> float:
        return (self.closed_at - self.opened_at).total_seconds() / 3600


class PaperBook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Libro
    currency: str
    initial_balance: float
    balance: float
    created_at: datetime
    positions: list[PaperPosition] = Field(default_factory=list)
    trades: list[PaperTrade] = Field(default_factory=list)
    #: Piani rifiutati dal gate, gli ultimi: servono a capire perché non opera.
    rejections: list[dict[str, object]] = Field(default_factory=list)
