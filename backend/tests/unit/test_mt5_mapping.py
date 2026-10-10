"""Test della traduzione fra MetaTrader 5 e il dominio.

Le costanti numeriche di MT5 sono scritte a mano in `mapping.py` per non
dipendere dal pacchetto su Linux. Il rischio di questa scelta è che un giorno
divergano: il primo test le confronta con il pacchetto vero quando è installato
e viene saltato altrove. Così la scelta resta comoda senza diventare un errore
silenzioso.
"""

from __future__ import annotations

import pytest

from backend.brokers.mt5.mapping import (
    DEAL_ENTRY_NAMES,
    MT5_TIMEFRAMES,
    ORDER_TYPE_NAMES,
)
from backend.core.enums import Timeframe

mt5 = pytest.importorskip(
    "MetaTrader5", reason="Pacchetto MetaTrader5 assente: gira solo su Windows"
)


class TestCostantiControIlPacchettoVero:
    """Eseguito solo dove MetaTrader5 è installato (Windows con terminale)."""

    def test_timeframe_coincidono(self) -> None:
        attesi = {
            Timeframe.M1: mt5.TIMEFRAME_M1,
            Timeframe.M5: mt5.TIMEFRAME_M5,
            Timeframe.M15: mt5.TIMEFRAME_M15,
            Timeframe.M30: mt5.TIMEFRAME_M30,
            Timeframe.H1: mt5.TIMEFRAME_H1,
            Timeframe.H4: mt5.TIMEFRAME_H4,
            Timeframe.D1: mt5.TIMEFRAME_D1,
            Timeframe.W1: mt5.TIMEFRAME_W1,
            Timeframe.MN1: mt5.TIMEFRAME_MN1,
        }
        assert attesi == MT5_TIMEFRAMES

    def test_tipi_di_ordine_coincidono(self) -> None:
        assert ORDER_TYPE_NAMES[mt5.ORDER_TYPE_BUY] == "BUY"
        assert ORDER_TYPE_NAMES[mt5.ORDER_TYPE_SELL_LIMIT] == "SELL_LIMIT"
        assert ORDER_TYPE_NAMES[mt5.ORDER_TYPE_BUY_STOP] == "BUY_STOP"

    def test_entry_dei_deal_coincidono(self) -> None:
        assert DEAL_ENTRY_NAMES[mt5.DEAL_ENTRY_IN] == "IN"
        assert DEAL_ENTRY_NAMES[mt5.DEAL_ENTRY_OUT] == "OUT"
