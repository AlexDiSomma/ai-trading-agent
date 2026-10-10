"""Entità di mercato persistite nella fase 2: conto e strumenti.

Perché salvare uno storico degli snapshot di conto invece del solo valore
corrente: il drawdown e la perdita settimanale si misurano sull'equity nel
tempo, e l'unico modo di averla dopo un riavvio è averla scritta.

Perché salvare le specifiche dei simboli: XM può cambiare spread, stop level o
volume minimo. Se cambiano, una size calcolata ieri non è valida oggi, e senza
storico non si capirebbe perché un ordine è stato rifiutato.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class AccountSnapshotRow(Base, TimestampMixin):
    """Serie storica dello stato del conto."""

    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_login_as_of", "login", "as_of"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    login: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    margin_free: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    margin_level: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    open_profit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    trade_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Se il dato viene dal terminale finto va marcato: una statistica calcolata
    # su equity simulata e reale mescolate non significa nulla.
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SymbolRow(Base, TimestampMixin):
    """Specifiche di contratto di uno strumento, come dichiarate dal broker."""

    __tablename__ = "symbols"
    __table_args__ = (Index("ix_symbols_name_as_of", "name", "as_of"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(160), nullable=False, default="")

    # Percorso di categoria dichiarato dal broker e classe derivata: sono la
    # fonte della vista per classe di attività, che le sole valute non danno
    # (XM dichiara GOLD come USD/USD).
    path: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False, default="OTHER")

    digits: Mapped[int] = mapped_column(Integer, nullable=False)
    point: Mapped[float] = mapped_column(Float, nullable=False)
    contract_size: Mapped[float] = mapped_column(Float, nullable=False)
    tick_size: Mapped[float] = mapped_column(Float, nullable=False)
    tick_value: Mapped[float] = mapped_column(Float, nullable=False)
    volume_min: Mapped[float] = mapped_column(Float, nullable=False)
    volume_max: Mapped[float] = mapped_column(Float, nullable=False)
    volume_step: Mapped[float] = mapped_column(Float, nullable=False)

    spread_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stops_level_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    currency_base: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    currency_profit: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    currency_margin: Mapped[str] = mapped_column(String(8), nullable=False, default="")

    # `trade_mode` grezzo: distingue "vietato" da "solo chiusura", che un
    # booleano appiattirebbe.
    trade_mode: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    trade_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: Valore in valuta del conto di un movimento di 1.0 di prezzo per 1 lotto,
    #: calcolato dal broker (ADR 0012). Conservato perché cambia con il cambio e
    #: perché è l'ingresso del dimensionamento: una size contestata va rivista
    #: con il valore di allora, non con quello di oggi.
    value_per_price_unit: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Vero se lo strumento è nella whitelist dell'utente: la whitelist resta in
    # configurazione, questa colonna serve solo a filtrare nelle query.
    in_whitelist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
