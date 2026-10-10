"""Backtest: simulatore deterministico su percorsi costruiti a mano, rapporto, storico.

Segnale EURUSD LONG: riferimento 1.1000, ATR H1 0.0010, stop 1.0985 (1,5 ATR), target
1.1030 (2R). Barre M5 in UTC, scarto zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.backtesting.rapporto import montecarlo, riassunto
from backend.backtesting.segnali import Segnale
from backend.backtesting.simulatore import (
    Mercato,
    Variante,
    limiti_senza_blocchi_di_conto,
    simula,
)
from backend.backtesting.storico import Barra, carica, salva
from backend.config.risk_limits import RiskLimits

T0 = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)  # martedì
LIMITI = RiskLimits(_env_file=None, allowed_sessions="00:00-23:59")  # type: ignore[call-arg]
BASE = Variante("base", "test")


def segnale(**kw: object) -> Segnale:
    base: dict[str, object] = {
        "simbolo": "EURUSD",
        "canonico": "EURUSD",
        "t": T0,
        "verso": 1,
        "riferimento": 1.1000,
        "stop": 1.0985,
        "target": 1.1030,
        "atr_h1": 0.0010,
        "invalidazione": None,
        "swing": None,
        "spread_atr": 0.02,
        "punto": 0.00001,
        "cifre": 5,
    }
    return Segnale(**(base | kw))  # type: ignore[arg-type]


def mercato(ohlc: list[tuple[float, float, float, float]], spread: int = 0) -> Mercato:
    m5 = [
        Barra(T0 + i * timedelta(minutes=5), o, h, lo, c, spread)
        for i, (o, h, lo, c) in enumerate(ohlc)
    ]
    h1 = [Barra(T0 - timedelta(hours=1), 1.1, 1.1, 1.1, 1.1, 0)]
    return Mercato.da_broker(m5, h1, timedelta(0))


def una(ohlc: list[tuple[float, float, float, float]], variante: Variante = BASE, **kw: object):  # type: ignore[no-untyped-def]
    e = simula([segnale(**kw)], {"EURUSD": mercato(ohlc)}, variante, LIMITI, scarto=timedelta(0))
    assert len(e.operazioni) == 1, e.rifiuti
    return e.operazioni[0]


class TestSimulatore:
    def test_take_profit(self) -> None:
        op = una([(1.1000, 1.1010, 1.0995, 1.1008), (1.1008, 1.1035, 1.1005, 1.1030)])
        assert (op.motivo, op.entrata) == ("TP", 1.1000)
        assert op.r == pytest.approx(2.0, abs=1e-3) and op.mfe_r >= 2.0

    def test_spread_all_ingresso_costa(self) -> None:
        e = simula(
            [segnale()],
            {"EURUSD": mercato([(1.1000, 1.1035, 1.0999, 1.1030)], spread=20)},
            BASE,
            LIMITI,
            scarto=timedelta(0),
        )
        op = e.operazioni[0]
        # LONG entra all'ask: 1.1000 + 20 punti.
        assert op.entrata == pytest.approx(1.1002) and op.costo_r > 0 and op.r < 2.0

    def test_stop_e_target_nella_stessa_barra_vince_lo_stop(self) -> None:
        op = una([(1.1000, 1.1035, 1.0980, 1.1000)])
        assert (op.motivo, op.ambigua) == ("SL", True) and op.r == pytest.approx(-1.0, abs=1e-3)

    def test_stop_mobile_blocca_mezzo_r(self) -> None:
        percorso = [(1.1000, 1.1017, 1.0999, 1.1015), (1.1015, 1.1016, 1.0980, 1.0982)]
        assert una(percorso).motivo == "SL"
        op = una(percorso, Variante("b", "t", regola_mobile="blocca_05r"))
        assert op.motivo == "TRAIL" and op.r == pytest.approx(0.5, abs=1e-3)

    def test_scadenza(self) -> None:
        op = una([(1.1000, 1.1005, 1.0995, 1.1001)] * 3, Variante("v", "t", orizzonte_ore=0))
        assert op.motivo == "TIME"

    def test_nessuna_barra_prima_della_decisione(self) -> None:
        # Segnale alle 10:07: si entra all'apertura della barra delle 10:10, non prima.
        percorso = [(1.0990, 1.0991, 1.0989, 1.0990), (1.0995, 1.0996, 1.0994, 1.0995)]
        percorso += [(1.1000, 1.1035, 1.0999, 1.1030)]
        op = una(percorso, t=T0 + timedelta(minutes=7))
        assert op.aperta == T0 + timedelta(minutes=10) and op.entrata == 1.1000

    def test_una_per_scommessa(self) -> None:
        m = mercato([(1.1000, 1.1005, 1.0995, 1.1001)] * 400)
        due = [segnale(), segnale(simbolo="GBPUSD", canonico="GBPUSD")]
        mercati = {"EURUSD": m, "GBPUSD": m}
        assert len(simula(due, mercati, BASE, LIMITI, scarto=timedelta(0)).operazioni) == 2
        v = Variante("u", "t", una_per_scommessa=True)
        e = simula(due, mercati, v, LIMITI, scarto=timedelta(0))
        assert len(e.operazioni) == 1 and e.rifiuti == {"Stessa scommessa già aperta": 1}

    def test_deterministico(self) -> None:
        percorso = [(1.1000, 1.1017, 1.0999, 1.1015), (1.1015, 1.1016, 1.0980, 1.0982)]
        assert una(percorso) == una(percorso)

    def test_limiti_senza_blocchi_di_conto(self) -> None:
        liberi = limiti_senza_blocchi_di_conto(LIMITI)
        assert liberi.max_drawdown_pct == 100.0 and liberi.daily_profit_target_pct is None
        assert liberi.max_risk_per_trade_pct == LIMITI.max_risk_per_trade_pct
        assert liberi.max_open_positions == LIMITI.max_open_positions


class TestRapporto:
    def test_montecarlo_ripetibile_e_sensato(self) -> None:
        r = [2.0, -1.0, -1.0] * 20
        a, b = montecarlo(r), montecarlo(r)
        assert a == b and a["low_90"] < 0 < a["high_90"]  # type: ignore[operator]
        assert montecarlo([1.0] * 5)["positive_share"] is None

    def test_riassunto_divide_le_meta(self) -> None:
        e = simula(
            [segnale(), segnale(t=T0 + timedelta(hours=30))],
            {"EURUSD": mercato([(1.1000, 1.1035, 1.0999, 1.1030)] * 400)},
            BASE,
            LIMITI,
            scarto=timedelta(0),
        )
        r = riassunto(e, T0 + timedelta(hours=12))
        assert r["trades"] == 2 and r["first_half"]["trades"] == 1
        assert r["second_half"]["trades"] == 1


def test_storico_su_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    barre = [Barra(T0, 1.1, 1.2, 1.0, 1.15, 12)]
    salva(tmp_path / "x.json", barre)
    assert carica(tmp_path / "x.json") == barre


def test_taglio_senza_look_ahead() -> None:
    """Fase 9, accettazione: il taglio a T è identico con o senza le barre dopo T."""
    from backend.backtesting.segnali import Tagliatore
    from backend.core.enums import Timeframe
    from backend.market_data.ingestion import TIMEFRAMES_ANALISI

    def serie(tf: Timeframe, n: int) -> list[Barra]:
        passo = timedelta(minutes=tf.minutes)
        return [
            Barra(T0 + i * passo, 1 + i * 1e-4, 1 + i * 1e-4, 1 + i * 1e-4, 1 + i * 1e-4, 1)
            for i in range(n)
        ]

    completo = {tf: serie(tf, 600) for tf in TIMEFRAMES_ANALISI}
    limite = T0 + timedelta(days=2, minutes=7)
    troncato = {
        tf: [b for b in barre if b.t + timedelta(minutes=tf.minutes) <= limite]
        for tf, barre in completo.items()
    }
    a = Tagliatore("EURUSD", completo).taglia(limite)
    b = Tagliatore("EURUSD", troncato).taglia(limite)
    for tf in TIMEFRAMES_ANALISI:
        assert a[tf].series.candles == b[tf].series.candles
        assert all(c.time + timedelta(minutes=tf.minutes) <= limite for c in a[tf].series.candles)


def test_variante_invertita_specchia_il_piano() -> None:
    # Il prezzo scende di 3 ATR: il LONG originale perde, l'invertito vince 2R.
    discesa = [(1.1000, 1.1001, 1.0969, 1.0970)]
    assert una(discesa).r == pytest.approx(-1.0, abs=1e-3)
    op = una(discesa, Variante("i", "t", invertito=True))
    assert op.verso == -1 and op.motivo == "TP" and op.r == pytest.approx(2.0, abs=1e-3)


def test_pausa_dopo_stop_solo_a_stop_avvenuto() -> None:
    # Primo trade: stop subito. Secondo segnale 2 ore dopo sullo stesso strumento.
    percorso = [(1.1000, 1.1001, 1.0980, 1.0982)] + [(1.0990, 1.0991, 1.0989, 1.0990)] * 60
    segnali = [
        segnale(),
        segnale(t=T0 + timedelta(hours=2), riferimento=1.0990, stop=1.0975, target=1.1020),
    ]
    m = {"EURUSD": mercato(percorso)}
    assert len(simula(segnali, m, BASE, LIMITI, scarto=timedelta(0)).operazioni) == 2
    v = Variante("p", "t", pausa_dopo_stop_ore=4)
    e = simula(segnali, m, v, LIMITI, scarto=timedelta(0))
    assert len(e.operazioni) == 1 and e.rifiuti == {"Pausa dopo uno stop sullo strumento": 1}
