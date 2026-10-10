"""DTO dell'esposizione di portafoglio.

Tre dimensioni invece di una, e non per completezza: ognuna risponde a una
domanda diversa che la precedente non copre.

* **Per valuta** — "quanto dipendo dal dollaro?". È la domanda classica, ma su XM
  da sola inganna: il broker dichiara `GOLD` come USD/USD/USD, quindi oro e
  indici americani finirebbero nello stesso secchiello del forex in dollari.
* **Per classe di attività** — "quanto dipendo dai metalli?". Usa il percorso di
  categoria dichiarato dal broker (`Derivatives\\Spot Metals\\GOLD`), che è un
  dato e non una congettura.
* **Per strumento e direzione** — "sto facendo la stessa scommessa tre volte?".
  Tre long correlati non sono tre operazioni indipendenti.

Il **rischio aperto** è calcolato solo sulle posizioni che hanno uno stop loss.
Una posizione senza stop ha rischio non misurabile, e sommarla come se fosse zero
darebbe un portafoglio che sembra prudente mentre non lo è: viene contata a parte
e dichiarata.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import AssetClass

# I DTO di questo modulo vengono salvati su database e **riletti**: il dump
# contiene anche i campi calcolati, che in ingresso non sono accettabili come
# dati. Con `extra="forbid"` la rilettura del proprio payload fallirebbe; con
# `ignore` i campi calcolati vengono scartati e ricalcolati, che è il
# comportamento corretto. La protezione contro i nomi sbagliati resta: i campi
# dichiarati sono obbligatori, quindi un errore di battitura diventa un campo
# mancante.
_FROZEN = ConfigDict(frozen=True, extra="ignore")


class SymbolExposure(BaseModel):
    """Esposizione aggregata su un singolo strumento."""

    model_config = _FROZEN

    symbol: str
    asset_class: AssetClass
    #: Volume netto in lotti: positivo se complessivamente long.
    net_volume: float
    gross_volume: float = Field(ge=0)
    positions: int = Field(ge=0)
    #: Valore nozionale netto in valuta del conto, con il valore del movimento
    #: chiesto al broker (ADR 0012).
    net_notional: float
    gross_notional: float = Field(ge=0)
    #: Perdita in valuta del conto se tutti gli stop venissero toccati.
    #: `None` se almeno una posizione è priva di stop: in quel caso il numero
    #: sarebbe falsamente rassicurante.
    risk_at_stop: float | None = None
    positions_without_stop: int = Field(default=0, ge=0)
    unrealized_pnl: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def direction(self) -> str:
        if self.net_volume > 0:
            return "LONG"
        if self.net_volume < 0:
            return "SHORT"
        return "FLAT"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hedged(self) -> bool:
        """Posizioni opposte sullo stesso strumento.

        Non è un errore in sé, ma è una situazione che paga due spread per non
        avere esposizione, e va vista.
        """
        return self.positions > 1 and abs(self.net_volume) < self.gross_volume


class CurrencyExposure(BaseModel):
    """Esposizione netta su una valuta, in valuta del conto."""

    model_config = _FROZEN

    currency: str
    net_notional: float
    gross_notional: float = Field(ge=0)
    symbols: tuple[str, ...] = ()


class AssetClassExposure(BaseModel):
    """Esposizione netta su una classe di attività, in valuta del conto."""

    model_config = _FROZEN

    asset_class: AssetClass
    net_notional: float
    gross_notional: float = Field(ge=0)
    net_volume: float = 0.0
    symbols: tuple[str, ...] = ()
    risk_at_stop: float | None = None


class ExposureSnapshot(BaseModel):
    """Fotografia completa dell'esposizione, a un istante."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    as_of: datetime
    account_currency: str
    equity: float
    open_positions: int = Field(ge=0)

    by_symbol: tuple[SymbolExposure, ...] = ()
    by_currency: tuple[CurrencyExposure, ...] = ()
    by_asset_class: tuple[AssetClassExposure, ...] = ()

    gross_notional: float = Field(default=0.0, ge=0)
    net_notional: float = 0.0
    unrealized_pnl: float = 0.0

    #: Rischio complessivo agli stop, in valuta del conto. `None` se qualche
    #: posizione non ha stop: il totale non sarebbe veritiero.
    total_risk_at_stop: float | None = None
    positions_without_stop: tuple[str, ...] = ()

    #: Avvisi in italiano: posizioni senza stop, coperture, valori non calcolabili.
    warnings: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gross_leverage(self) -> float | None:
        """Leva effettiva lorda: nozionale complessivo diviso equity.

        È la misura che l'utente controlla con `RISK_MAX_LEVERAGE`. Calcolata sul
        nozionale e non sul margine richiesto, perché il margine dipende dalle
        regole del broker mentre l'esposizione al mercato è quella che è.
        """
        if self.equity <= 0:
            return None
        return round(self.gross_notional / self.equity, 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_risk_pct(self) -> float | None:
        """Rischio complessivo in percentuale dell'equity."""
        if self.total_risk_at_stop is None or self.equity <= 0:
            return None
        return round(self.total_risk_at_stop / self.equity * 100, 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def risk_is_measurable(self) -> bool:
        """Falso se esistono posizioni senza stop loss."""
        return not self.positions_without_stop
