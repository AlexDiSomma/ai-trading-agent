"""Test delle conversioni MT5 -> dominio, senza il pacchetto MetaTrader5.

Girano su qualunque sistema operativo: è il motivo per cui le costanti sono
letterali e le conversioni accettano oggetti con gli stessi nomi di campo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from backend.brokers.mt5.mapping import (
    from_broker_time,
    to_account_snapshot,
    to_candles,
    to_deal_snapshot,
    to_mt5_timeframe,
    to_position_snapshot,
    to_symbol_info,
)
from backend.core.enums import PositionSide, Timeframe
from backend.core.errors import BrokerError

ADESSO = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)


@dataclass
class RigaSimbolo:
    name: str = "XAUUSD"
    digits: int = 2
    point: float = 0.01
    trade_contract_size: float = 100.0
    trade_tick_size: float = 0.01
    trade_tick_value: float = 1.0
    volume_min: float = 0.01
    volume_max: float = 20.0
    volume_step: float = 0.01
    spread: int = 28
    trade_stops_level: int = 50
    trade_freeze_level: int = 0
    trade_mode: int = 4
    visible: bool = True
    description: str = "Gold"
    currency_base: str = "XAU"
    currency_profit: str = "USD"
    currency_margin: str = "XAU"


@dataclass
class RigaPosizione:
    ticket: int = 10
    symbol: str = "EURUSD"
    type: int = 1
    volume: float = 0.03
    price_open: float = 1.0900
    price_current: float = 1.0880
    sl: float = 0.0
    tp: float = 1.0800
    profit: float = 6.0
    swap: float = -0.2
    commission: float = -0.1
    magic: int = 42
    comment: str = "piano-7"
    time: float = 1_772_000_000


@dataclass
class RigaDeal:
    ticket: int = 99
    order: int = 98
    position_id: int = 10
    symbol: str = "EURUSD"
    type: int = 1
    entry: int = 1
    volume: float = 0.03
    price: float = 1.0880
    profit: float = 6.0
    commission: float = -0.1
    swap: float = -0.2
    fee: float = 0.0
    magic: int = 42
    comment: str = ""
    time: float = 1_772_000_000


class TestTimeframe:
    def test_conversione(self) -> None:
        assert to_mt5_timeframe(Timeframe.H1) == 16385
        assert to_mt5_timeframe(Timeframe.M5) == 5

    def test_tutti_i_timeframe_del_dominio_sono_mappati(self) -> None:
        for timeframe in Timeframe:
            assert to_mt5_timeframe(timeframe) > 0

    def test_minuti_e_ruolo(self) -> None:
        assert Timeframe.H4.minutes == 240
        assert Timeframe.H4.role == "structure"
        assert Timeframe.M5.role == "execution"
        assert Timeframe.H1.role == "trend"


class TestOraBroker:
    def test_epoch_diventa_aware(self) -> None:
        istante = from_broker_time(1_772_000_000)
        assert istante.tzinfo is UTC

    def test_datetime_naive_etichettato(self) -> None:
        istante = from_broker_time(datetime(2026, 3, 10, 10, 0))
        assert istante.tzinfo is UTC


class TestSimbolo:
    def test_specifiche_lette_dal_broker(self) -> None:
        info = to_symbol_info(RigaSimbolo(), as_of=ADESSO)
        assert info.contract_size == 100.0
        assert info.tick_value == 1.0
        assert info.stops_level_points == 50
        assert info.spread_price == pytest.approx(0.28)

    def test_trade_mode_parziale_non_e_operabile(self) -> None:
        """Solo il modo pieno (4) consente di aprire posizioni."""
        info = to_symbol_info(RigaSimbolo(trade_mode=2), as_of=ADESSO)
        assert info.trade_allowed is False

    def test_tick_size_assente_ricade_sul_point(self) -> None:
        info = to_symbol_info(RigaSimbolo(trade_tick_size=0.0), as_of=ADESSO)
        assert info.tick_size == info.point


class TestPosizione:
    def test_short_riconosciuto(self) -> None:
        posizione = to_position_snapshot(RigaPosizione(), as_of=ADESSO)
        assert posizione.side is PositionSide.SELL

    def test_long_riconosciuto(self) -> None:
        posizione = to_position_snapshot(RigaPosizione(type=0), as_of=ADESSO)
        assert posizione.side is PositionSide.BUY

    def test_stop_a_zero_diventa_none(self) -> None:
        posizione = to_position_snapshot(RigaPosizione(), as_of=ADESSO)
        assert posizione.stop_loss is None
        assert posizione.take_profit == 1.08

    def test_profitto_netto_include_swap_e_commissioni(self) -> None:
        posizione = to_position_snapshot(RigaPosizione(), as_of=ADESSO)
        assert posizione.net_profit == pytest.approx(5.7)


class TestDeal:
    def test_uscita_realizza(self) -> None:
        deal = to_deal_snapshot(RigaDeal(entry=1))
        assert deal.entry == "OUT"
        assert deal.is_realizing is True
        assert deal.net_result == pytest.approx(5.7)

    def test_ingresso_non_realizza(self) -> None:
        """Solo le uscite consumano il budget giornaliero."""
        deal = to_deal_snapshot(RigaDeal(entry=0))
        assert deal.entry == "IN"
        assert deal.is_realizing is False

    def test_entry_sconosciuto_non_si_finge_noto(self) -> None:
        deal = to_deal_snapshot(RigaDeal(entry=77))
        assert deal.entry == "UNKNOWN"
        assert deal.is_realizing is False

    def test_tipo_non_di_mercato_riconoscibile(self) -> None:
        """Versamenti e commissioni muovono l'equity ma non sono trade."""
        deal = to_deal_snapshot(RigaDeal(type=2))
        assert deal.deal_type == "BALANCE"


class TestTipoDiConto:
    """Il tipo di conto si legge dal broker, non dal nome del server.

    Il conto demo XM di prova sta su `XMGlobalMU-MT5 2`: un'euristica sulla
    parola "demo" lo annuncerebbe come conto reale.
    """

    @dataclass
    class RigaConto:
        login: int = 169_444_760
        server: str = "XMGlobalMU-MT5 2"
        name: str = "Demo Account"
        company: str = "XM International MU Limited"
        currency: str = "EUR"
        leverage: int = 1_000
        balance: float = 9_849.66
        equity: float = 9_856.21
        margin: float = 0.0
        margin_free: float = 9_856.21
        margin_level: float = 0.0
        profit: float = 6.55
        trade_allowed: bool = True
        trade_expert: bool = True
        trade_mode: int = 0

    def test_demo_riconosciuto(self) -> None:
        conto = to_account_snapshot(self.RigaConto(), as_of=ADESSO)
        assert conto.account_type == "DEMO"
        assert conto.is_demo is True
        assert conto.is_real_money is False

    def test_reale_riconosciuto(self) -> None:
        conto = to_account_snapshot(self.RigaConto(trade_mode=2), as_of=ADESSO)
        assert conto.account_type == "REAL"
        assert conto.is_real_money is True

    def test_campo_assente_trattato_come_reale(self) -> None:
        """Fail closed: in dubbio si assume denaro vero."""
        conto = to_account_snapshot(self.RigaConto(trade_mode=99), as_of=ADESSO)
        assert conto.account_type == "UNKNOWN"
        assert conto.is_real_money is True


class TestCandele:
    def test_conversione_da_dizionari(self) -> None:
        righe = [
            {
                "time": 1_772_000_000,
                "open": 1.0850,
                "high": 1.0870,
                "low": 1.0840,
                "close": 1.0860,
                "tick_volume": 1200,
                "spread": 12,
                "real_volume": 0,
            }
        ]
        candele = to_candles(righe)
        assert len(candele) == 1
        assert candele[0].is_bullish is True
        assert candele[0].range == pytest.approx(0.0030)

    def test_nessuna_riga(self) -> None:
        assert to_candles(None) == ()

    def test_candela_incoerente_rifiutata(self) -> None:
        """Una barra con high < low è un dato corrotto, non un dato strano."""
        righe = [
            {
                "time": 1_772_000_000,
                "open": 1.0850,
                "high": 1.0800,
                "low": 1.0900,
                "close": 1.0860,
                "tick_volume": 1,
                "spread": 1,
                "real_volume": 0,
            }
        ]
        with pytest.raises(ValueError, match="incoerente"):
            to_candles(righe)


class TestTimeframeNonSupportato:
    def test_errore_parlante(self) -> None:
        with pytest.raises(BrokerError):
            to_mt5_timeframe("Q1")  # type: ignore[arg-type]
