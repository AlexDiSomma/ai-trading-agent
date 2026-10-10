"""MVP paper: geometria, gate di rischio, esecuzione e gestione, performance, registro.

Prezzi e calcoli monetari dal terminale finto; il percorso del prezzo è costruito
qui (barre M1 e tick sostituiti), così ogni uscita è un caso noto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.config.risk_limits import RiskLimits
from backend.config.settings import PaperSettings
from backend.core.clock import FrozenClock
from backend.paper.executor import PaperExecutor
from backend.paper.geometry import risolvi_barra
from backend.paper.ledger import PaperLedger
from backend.paper.models import PaperPosition, TradePlan
from backend.paper.performance import confronto, performance
from backend.risk.gate import RichiestaIngresso, valuta_ingresso
from backend.schemas.market import Tick
from backend.shadow.conditions import Barra, PriceCondition

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)


# ======================================================================
# Geometria: ambiguità dentro la barra
# ======================================================================
class TestBarra:
    def test_gap_oltre_lo_stop_esce_all_apertura(self) -> None:
        e = risolvi_barra(1, 99.0, 102.0, open_=98.5, high=103, low=98)
        assert (e.evento, e.prezzo, e.ambiguo) == ("SL", 98.5, False)

    def test_gap_oltre_il_target(self) -> None:
        e = risolvi_barra(1, 99.0, 102.0, open_=102.5, high=103, low=98)
        assert (e.evento, e.prezzo) == ("TP", 102.0)

    def test_entrambi_nella_barra_vince_lo_stop(self) -> None:
        e = risolvi_barra(1, 99.0, 102.0, open_=100, high=102.5, low=98.5)
        assert (e.evento, e.ambiguo) == ("SL", True)

    def test_short_usa_l_ask(self) -> None:
        # bid alto 100.95 + spread 0.1 = ask 101.05 >= stop 101: stop toccato.
        e = risolvi_barra(-1, 101.0, 98.0, open_=100, high=100.95, low=99.5, spread=0.1)
        assert e.evento == "SL"
        assert (
            risolvi_barra(-1, 101.0, 98.0, open_=100, high=100.85, low=99.5, spread=0.1).evento
            is None
        )


# ======================================================================
# Gate di rischio
# ======================================================================
def richiesta(**kw: Any) -> RichiestaIngresso:
    base: dict[str, Any] = {
        "simbolo": "XAUUSD",
        "rr": 2.0,
        "stop_atr": 1.5,
        "spread_atr": 0.05,
        "rischio": 50.0,
        "equity": 10_000.0,
        "posizioni_aperte": 0,
        "simboli_aperti": frozenset(),
        "rischio_aperto": 0.0,
        "pnl_oggi": 0.0,
        "picco_equity": 10_000.0,
        "ora_broker": datetime(2026, 10, 6, 11, 0),
        "kill_switch": False,
        "finestra_evento": False,
        "dati_validi": True,
    }
    return RichiestaIngresso(**(base | kw))


LIMITI = RiskLimits(_env_file=None)  # type: ignore[call-arg]


class TestGate:
    def test_ingresso_consentito(self) -> None:
        assert valuta_ingresso(richiesta(), LIMITI) == []

    @pytest.mark.parametrize(
        ("modifica", "motivo"),
        [
            ({"kill_switch": True}, "Kill switch"),
            ({"rr": 1.2}, "RR 1.20 sotto il minimo"),
            ({"stop_atr": 0.2}, "Stop a 0.20 ATR"),
            ({"spread_atr": 0.9}, "Spread"),
            ({"simboli_aperti": frozenset({"XAUUSD"})}, "Già una posizione"),
            ({"posizioni_aperte": 3}, "Già 3 posizioni"),
            ({"rischio": 80.0}, "massimo per trade"),
            ({"rischio_aperto": 120.0}, "Rischio aperto totale"),
            ({"pnl_oggi": -250.0}, "Perdita giornaliera"),
            ({"picco_equity": 12_000.0}, "Drawdown"),
            ({"ora_broker": datetime(2026, 10, 6, 22, 0)}, "Fuori dalle sessioni"),
            ({"ora_broker": None}, "Ora del broker ignota"),
            ({"finestra_evento": True}, "evento ad alto impatto"),
            ({"dati_validi": False, "motivi_dati": ("Prezzi simulati",)}, "Prezzi simulati"),
            ({"rischio": 0.0}, "Size nulla"),
        ],
    )
    def test_violazioni(self, modifica: dict[str, Any], motivo: str) -> None:
        motivi = valuta_ingresso(richiesta(**modifica), LIMITI)
        assert any(motivo in m for m in motivi), motivi


# ======================================================================
# Esecuzione e gestione sul terminale finto
# ======================================================================
def piano(**kw: Any) -> TradePlan:
    base: dict[str, Any] = {
        "book": "QUANT",
        "canonical": "EURUSD",
        "broker_symbol": "EURUSD",
        "direction": "LONG",
        "entry_type": "MARKET",
        "entry_price": 1.1000,
        "stop": 1.0980,
        "target": 1.1040,
        "rr": 2.0,
        "atr_h1": 0.0013,
        "created_at": T0,
        "valid_until": T0 + timedelta(hours=1),
        "expires_at": T0 + timedelta(hours=24),
        "strategy_version": "test-1",
    }
    return TradePlan(**(base | kw))


def m1(
    inizio: datetime, ohlc: list[tuple[float, float, float, float]]
) -> list[tuple[Barra, float]]:
    return [
        (
            Barra(
                inizio + i * timedelta(minutes=1),
                inizio + (i + 1) * timedelta(minutes=1),
                o,
                h,
                lo,
                c,
            ),
            0.00002,
        )
        for i, (o, h, lo, c) in enumerate(ohlc)
    ]


@pytest.fixture
async def esecutore(tmp_path, clock):  # type: ignore[no-untyped-def]
    from backend.services.container import ApplicationContainer
    from backend.tests.conftest import crea_settings

    c = ApplicationContainer(crea_settings(kill_switch_file=tmp_path / "KS"), clock=clock)
    await c.startup()
    ledger = PaperLedger(tmp_path / "paper")
    ledger.inizializza(10_000.0, "EUR", T0)
    orologio = FrozenClock(T0 + timedelta(minutes=30))
    ex = PaperExecutor(
        ledger=ledger,
        market=c.market_data,
        instruments=c.instruments,  # type: ignore[arg-type]
        specifications=c.specifications,
        valuation=c.valuation,  # type: ignore[arg-type]
        intents=c.intents,
        broker_clock=c.broker_clock,
        limits=c.settings.risk,  # type: ignore[arg-type]
        settings=PaperSettings(_env_file=None, slippage_points=0, commission_per_lot=3.0),  # type: ignore[call-arg]
        clock=orologio,
        kill_switch=lambda: False,
        contesto=lambda s: c.require_analyst().analyse(s),
    )
    ex._scarto = lambda: timedelta(0)  # type: ignore[method-assign]
    yield ex, ledger, orologio
    await c.shutdown()


def _apri(ledger: PaperLedger, p: TradePlan, volume: float = 0.5) -> PaperPosition:
    pos = PaperPosition(
        position_id="p1",
        plan=p,
        status="OPEN",
        opened_at=T0,
        entry_fill=p.entry_price,
        volume=volume,
        risk_amount=100.0,
        commission=3.0 * volume,
        checked_until=T0,
        ombre=PaperExecutor._ombre_nuove(),
    )
    ledger.books[p.book].positions.append(pos)
    return pos


def _percorso(ex: PaperExecutor, barre: list[tuple[Barra, float]], bid: float = 1.1010) -> None:
    async def finte_barre(simbolo: str, tf: Any, n: int) -> list[tuple[Barra, float]]:
        return barre

    async def finto_tick(simbolo: str) -> Any:
        class F:
            value = Tick(symbol=simbolo, time=T0, bid=bid, ask=bid + 0.00002)

        return F()

    ex._barre = finte_barre  # type: ignore[method-assign]
    ex._m.get_tick = finto_tick  # type: ignore[method-assign]


class TestGestione:
    async def test_take_profit_con_pnl_e_r(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano())
        _percorso(ex, m1(T0, [(1.1000, 1.1012, 1.0995, 1.1010), (1.1010, 1.1045, 1.1008, 1.1040)]))
        await ex.gestisci()
        b = ledger.books["QUANT"]
        assert b.positions == [] and len(b.trades) == 1
        t = b.trades[0]
        assert (t.exit_reason, t.exit_price) == ("TP", 1.1040)
        assert t.gross_pnl > 0 and t.commission == 3.0
        assert t.net_pnl == pytest.approx(t.gross_pnl - 3.0, abs=0.01)
        assert t.r_multiple == pytest.approx(t.net_pnl / 100.0, abs=1e-3)
        assert t.mfe_r == 2.0 and t.mae_r == pytest.approx(0.25, abs=1e-3)
        assert b.balance == pytest.approx(10_000 + t.net_pnl)

    async def test_stop_ambiguo_marcato(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano())
        _percorso(ex, m1(T0, [(1.1000, 1.1045, 1.0975, 1.1000)]))
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert (t.exit_reason, t.ambiguous_bar) == ("SL", True) and t.net_pnl < 0

    async def test_invalidazione_chiude_alla_chiusura(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        cond = PriceCondition(
            subject="EURUSD",
            timeframe="M5",
            measure="CLOSE",
            operator="BELOW",
            level=1.0990,
            evidence_ids=("x",),
            description="Sotto il supporto.",
        )
        _apri(ledger, piano(invalidations=(cond,)))
        _percorso(ex, m1(T0, [(1.1000, 1.1003, 1.0986, 1.0988)]))
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert (t.exit_reason, t.exit_price) == ("INVALIDATION", 1.0988)

    async def test_scadenza_chiude_al_tempo(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano(expires_at=T0 + timedelta(minutes=10)))
        _percorso(ex, [], bid=1.1005)
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert (t.exit_reason, t.exit_price) == ("TIME", 1.1005)

    async def test_posizione_aperta_aggiorna_il_non_realizzato(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        p = _apri(ledger, piano())
        _percorso(ex, m1(T0, [(1.1000, 1.1012, 1.0995, 1.1010)]), bid=1.1010)
        await ex.gestisci()
        assert p.status == "OPEN" and p.unrealized_pnl is not None and p.unrealized_pnl > 0
        assert ex.equity(ledger.books["QUANT"]) > 10_000

    async def test_ordine_limit_scaduto_si_annulla(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        p = piano(
            entry_type="LIMIT",
            entry_price=1.0990,
            stop=1.0970,
            target=1.1030,
            valid_until=T0 + timedelta(minutes=2),
        )
        ledger.books["QUANT"].positions.append(
            PaperPosition(position_id="o1", plan=p, status="PENDING", checked_until=T0)
        )
        _percorso(ex, m1(T0, [(1.1000, 1.1005, 1.0995, 1.1000)] * 3))
        await ex.gestisci()
        assert ledger.books["QUANT"].positions == [] and ledger.books["QUANT"].trades == []
        assert ledger.ultimi_eventi(1)[0]["type"] == "CANCELLED"

    async def test_dati_simulati_nessuna_apertura(self, esecutore, tmp_path) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        ctx = await ex._contesto("EURUSD")
        motivi = await ex.proponi(piano(), ctx)
        assert motivi and ledger.books["QUANT"].positions == []
        assert ledger.books["QUANT"].rejections


# ======================================================================
# Registro e performance
# ======================================================================
def test_registro_sopravvive_al_riavvio(tmp_path) -> None:  # type: ignore[no-untyped-def]
    a = PaperLedger(tmp_path)
    a.inizializza(5_000.0, "EUR", T0)
    a.books["AI"].balance = 5_123.45
    a.salva()
    b = PaperLedger(tmp_path)
    b.carica()
    assert b.books["AI"].balance == 5_123.45 and set(b.books) == {"QUANT", "AI"}
    assert not (tmp_path / "ledger.tmp").exists()


def test_performance_e_confronto(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from backend.paper.models import PaperTrade

    led = PaperLedger(tmp_path)
    led.inizializza(10_000.0, "EUR", T0)
    for i, (netto, r) in enumerate([(200.0, 2.0), (-100.0, -1.0), (-100.0, -1.0), (150.0, 1.5)]):
        led.books["QUANT"].trades.append(
            PaperTrade(
                position_id=str(i),
                plan=piano(),
                opened_at=T0 + timedelta(hours=i),
                closed_at=T0 + timedelta(hours=i, minutes=30),
                entry_fill=1.1,
                exit_price=1.1,
                exit_reason="TP" if r > 0 else "SL",
                volume=0.5,
                risk_amount=100.0,
                gross_pnl=netto,
                commission=0.0,
                swap=0.0,
                spread_cost=1.0,
                slippage_cost=0.0,
                net_pnl=netto,
                r_multiple=r,
                mae_r=0.5,
                mfe_r=1.0,
            )
        )
        led.books["QUANT"].balance += netto
    p = performance(led.books["QUANT"])
    assert (p["trades"], p["win_rate"], p["profit_factor"]) == (4, 0.5, 1.75)
    assert p["expectancy_r"] == 0.375 and p["max_drawdown_ccy"] == 200.0
    assert p["equity_curve"][-1]["equity"] == 10_150.0
    assert p["by_strategy_version"]["test-1"]["trades"] == 4
    assert confronto(led.books["QUANT"], led.books["AI"])["verdict"] == "INSUFFICIENT_SAMPLE"


def test_paper_e_dashboard_non_inviano_ordini() -> None:
    import ast

    from backend.config.paths import BACKEND_ROOT

    file = [
        *(BACKEND_ROOT / "paper").rglob("*.py"),
        BACKEND_ROOT / "services" / "autopilot.py",
        BACKEND_ROOT / "api" / "routes" / "dashboard.py",
    ]
    assert len(file) >= 8
    for percorso in file:
        for nodo in ast.walk(ast.parse(percorso.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Attribute):
                assert nodo.attr not in (
                    "order_send",
                    "order_check",
                    "position_close",
                    "order_modify",
                ), percorso.name


# ======================================================================
# Comandi dell'utente: limiti, obiettivo giornaliero, mercati, chiusura manuale
# ======================================================================
def test_obiettivo_giornaliero_e_perdita_settimanale() -> None:
    con_obiettivo = RiskLimits(_env_file=None, daily_profit_target_pct=1.0)  # type: ignore[call-arg]
    assert any(
        "Obiettivo giornaliero" in m
        for m in valuta_ingresso(richiesta(pnl_oggi=120.0), con_obiettivo)
    )
    assert valuta_ingresso(richiesta(pnl_oggi=50.0), con_obiettivo) == []
    assert any(
        "Perdita settimanale" in m for m in valuta_ingresso(richiesta(pnl_settimana=-600.0), LIMITI)
    )


def _impostazioni(tmp_path, mode: str = "PAPER"):  # type: ignore[no-untyped-def]
    from backend.core.enums import TradingMode
    from backend.tests.conftest import crea_settings

    s = crea_settings(
        kill_switch_file=tmp_path / "KS", mode=TradingMode(mode), enable_live=mode == "LIVE"
    )
    return s


def test_limiti_modificabili_con_tetti_registro_e_ricarica(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from backend.config import risk_overrides as ro
    from backend.core.errors import ConfigurationError

    s = _impostazioni(tmp_path)
    vecchio = s.risk
    nuovo = ro.applica(
        s,
        {"max_open_positions": 5, "daily_profit_target_pct": 3.0},
        motivo="più posizioni in demo",
        ora=T0,
    )
    assert s.risk is nuovo and nuovo is not vecchio and nuovo.max_open_positions == 5
    assert vecchio.max_open_positions == 3  # l'oggetto precedente non è stato toccato
    assert ro.registro(s)[0]["reason"] == "più posizioni in demo"
    with pytest.raises(ConfigurationError, match="fuori da"):
        ro.applica(s, {"max_risk_per_trade_pct": 5.0}, motivo="troppo", ora=T0)
    with pytest.raises(ConfigurationError, match="non modificabile"):
        ro.applica(s, {"allowed_symbols_raw": "BTCUSD"}, motivo="no", ora=T0)
    with pytest.raises(ConfigurationError):
        ro.applica(s, {"allowed_sessions_raw": "venticinque"}, motivo="formato", ora=T0)
    # Riavvio: gli override salvati tornano sopra al .env.
    s2 = _impostazioni(tmp_path)
    assert s2.risk.max_open_positions == 3
    ro.carica_all_avvio(s2)
    assert (s2.risk.max_open_positions, s2.risk.daily_profit_target_pct) == (5, 3.0)


def test_limiti_bloccati_in_live(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from backend.config import risk_overrides as ro
    from backend.core.errors import ConfigurationError

    s = _impostazioni(tmp_path, "LIVE")
    with pytest.raises(ConfigurationError, match="LIVE"):
        ro.applica(s, {"max_open_positions": 2}, motivo="prova", ora=T0)


def test_comandi_persistenti(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from backend.services.controls import Controlli

    c = Controlli(tmp_path / "controls.json", ("EURUSD", "GOLD"))
    c.mercato("GOLD", False, T0)
    c.strategia("AI", False, T0)
    c.pausa(True, T0)
    r = Controlli(tmp_path / "controls.json", ("EURUSD", "GOLD"))
    assert r.in_pausa and not r.mercato_attivo("GOLD") and r.mercato_attivo("EURUSD")
    assert not r.strategia_attiva("AI") and r.strategia_attiva("QUANT")
    assert len(r.stato["log"]) == 3
    with pytest.raises(KeyError):
        r.mercato("BTCUSD", True, T0)


class TestChiusuraManuale:
    async def test_chiude_una_posizione_aperta(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano())
        _percorso(ex, [], bid=1.1010)
        esito = await ex.chiudi_manuale("QUANT", "p1")
        t = ledger.books["QUANT"].trades[0]
        assert esito.startswith("chiusa") and t.exit_reason == "MANUAL" and t.exit_price == 1.1010
        assert ledger.books["QUANT"].positions == []

    async def test_annulla_un_ordine_in_attesa(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        ledger.books["AI"].positions.append(
            PaperPosition(
                position_id="o9",
                plan=piano(book="AI", entry_type="LIMIT", entry_price=1.0990),
                status="PENDING",
            )
        )
        assert await ex.chiudi_manuale("AI", "o9") == "ordine annullato"
        assert ledger.books["AI"].positions == [] and ledger.books["AI"].trades == []
        assert (await ex.chiudi_manuale("AI", "o9")).startswith("non trovata")
