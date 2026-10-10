"""DTO del conto e delle posizioni.

L'equity del broker è la **sola fonte di verità** sul capitale. Il sistema non
tiene un proprio saldo in memoria: commissioni, swap, conversioni valuta e
chiusure manuali fatte dall'utente dal terminale lo renderebbero sbagliato, e un
budget di rischio calcolato su un saldo sbagliato non protegge nulla.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import PositionSide

# I DTO di questo modulo vengono salvati su database e **riletti**: il dump
# contiene anche i campi calcolati, che in ingresso non sono accettabili come
# dati. Con `extra="forbid"` la rilettura del proprio payload fallirebbe; con
# `ignore` i campi calcolati vengono scartati e ricalcolati, che è il
# comportamento corretto. La protezione contro i nomi sbagliati resta: i campi
# dichiarati sono obbligatori, quindi un errore di battitura diventa un campo
# mancante.
_FROZEN = ConfigDict(frozen=True, extra="ignore")


class AccountSnapshot(BaseModel):
    """Fotografia del conto a un istante."""

    model_config = _FROZEN

    login: int
    server: str
    name: str = ""
    company: str = ""
    currency: str
    leverage: int = Field(ge=1)

    balance: float
    equity: float
    margin: float = Field(ge=0)
    margin_free: float
    margin_level: float = Field(default=0.0, ge=0)
    profit: float = 0.0

    # Il broker può vietare di operare (conto in sola lettura, mercato chiuso,
    # investor password). Va letto, non assunto.
    trade_allowed: bool = True
    trade_expert_allowed: bool = True

    # Demo, contest o reale: lo dichiara il broker (`account_info().trade_mode`).
    # UNKNOWN solo se il campo manca, e in quel caso va trattato come reale:
    # sbagliare in questa direzione costa meno.
    account_type: Literal["DEMO", "CONTEST", "REAL", "UNKNOWN"] = "UNKNOWN"

    as_of: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def used_margin_pct(self) -> float:
        if self.equity <= 0:
            return 100.0
        return round(self.margin / self.equity * 100, 4)

    @property
    def is_demo(self) -> bool:
        """Vero solo se il broker dichiara il conto come demo.

        Prima versione: euristica su "demo" nel nome del server. Sbagliata — il
        conto demo XM di prova sta su `XMGlobalMU-MT5 2`, senza la parola
        "demo", e veniva annunciato come conto reale. Un'etichetta sbagliata in
        questa direzione fa prendere alla leggera un conto vero, o ignorare
        l'avviso su uno finto.
        """
        return self.account_type == "DEMO"

    @property
    def is_real_money(self) -> bool:
        """Fail closed: se il tipo di conto non è noto, si assume denaro vero."""
        return self.account_type in ("REAL", "UNKNOWN")


class PositionSnapshot(BaseModel):
    """Posizione aperta presso il broker."""

    model_config = _FROZEN

    ticket: int
    symbol: str
    side: PositionSide
    volume: float = Field(gt=0)
    price_open: float = Field(gt=0)
    price_current: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0

    # `magic` e `comment` sono il legame fra la posizione sul broker e il
    # TradePlan nel database: senza di essi una riconciliazione dopo un riavvio
    # non può dire quale strategia ha aperto cosa.
    magic: int = 0
    comment: str = ""

    opened_at: datetime
    as_of: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_profit(self) -> float:
        return round(self.profit + self.swap + self.commission, 2)

    @property
    def has_protection(self) -> bool:
        """Una posizione senza stop loss è un rischio non misurabile."""
        return self.stop_loss is not None and self.stop_loss > 0


class PendingOrderSnapshot(BaseModel):
    """Ordine pendente (limit/stop) non ancora eseguito."""

    model_config = _FROZEN

    ticket: int
    symbol: str
    order_type: str
    volume_initial: float = Field(gt=0)
    volume_current: float = Field(ge=0)
    price_open: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    magic: int = 0
    comment: str = ""
    placed_at: datetime
    expires_at: datetime | None = None
    as_of: datetime


class DealSnapshot(BaseModel):
    """Operazione già regolata sul conto (storico del broker).

    È la fonte del PnL **realizzato**: il budget di rischio giornaliero si
    ricostruisce da qui a ogni avvio. Un contatore tenuto in memoria ripartirebbe
    da zero dopo un riavvio e il limite dell'utente sarebbe di fatto raddoppiato.
    """

    model_config = _FROZEN

    ticket: int
    order_ticket: int = 0
    position_id: int = 0
    symbol: str = ""
    # `entry`: ingresso, uscita o chiusura inversa. Solo le uscite realizzano
    # il profitto, quindi solo quelle consumano il budget giornaliero.
    entry: Literal["IN", "OUT", "INOUT", "OUT_BY", "UNKNOWN"] = "UNKNOWN"
    deal_type: str = ""
    volume: float = 0.0
    price: float = 0.0
    profit: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    fee: float = 0.0
    magic: int = 0
    comment: str = ""
    executed_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_result(self) -> float:
        """Risultato al netto di costi: è questo che muove l'equity."""
        return round(self.profit + self.commission + self.swap + self.fee, 2)

    @property
    def is_realizing(self) -> bool:
        return self.entry in ("OUT", "INOUT", "OUT_BY")


class PortfolioSnapshot(BaseModel):
    """Conto e posizioni letti nello stesso istante.

    Stanno insieme perché valutare il rischio con un'equity di adesso e
    posizioni di dieci secondi fa produce numeri che non sono mai esistiti.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    account: AccountSnapshot
    positions: tuple[PositionSnapshot, ...] = ()
    pending_orders: tuple[PendingOrderSnapshot, ...] = ()
    as_of: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def open_positions_count(self) -> int:
        return len(self.positions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl(self) -> float:
        return round(sum(p.net_profit for p in self.positions), 2)

    @property
    def unprotected_positions(self) -> tuple[PositionSnapshot, ...]:
        return tuple(p for p in self.positions if not p.has_protection)

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({p.symbol for p in self.positions}))
