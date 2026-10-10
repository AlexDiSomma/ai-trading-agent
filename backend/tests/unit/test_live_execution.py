"""Fondazione dell'esecuzione reale: gateway, idempotenza, riconciliazione, guasti, gate.

Nessun broker reale: `ServerFinto` simula il server di trading con scenari di guasto
(timeout dopo l'esecuzione, timeout senza esecuzione, disconnessione, requote,
esecuzione parziale, rifiuto, posizione senza SL/TP). L'archivio è in memoria ma
dichiarato durevole solo qui, per provare il gateway; `LiveReadiness` non lo accetta.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.config.risk_limits import RiskLimits
from backend.core.clock import FrozenClock
from backend.core.enums import PositionSide
from backend.core.errors import (
    BrokerNotConnectedError,
    BrokerTimeoutError,
    KillSwitchEngagedError,
    PersistenceUnavailableError,
)
from backend.execution import retcodes
from backend.execution.breaker import CircuitBreaker
from backend.execution.gateway import (
    ApprovalInvalidError,
    CircuitBreakerOpenError,
    ExecutionStateUnknownError,
    LiveOrderGateway,
    LiveTradingDisabledError,
)
from backend.execution.manager import LivePositionManager
from backend.execution.models import MAGIC, RiskApproval, commento
from backend.execution.mt5_terminal import TRADE_ACTION_SLTP, RispostaOrdine
from backend.execution.readiness import FRASE_CONFERMA, MINIMO_TRADE_PAPER, LiveReadiness
from backend.execution.store import MemoryExecutionStore
from backend.schemas.account import DealSnapshot, PositionSnapshot
from backend.schemas.market import SymbolInfo, Tick

T0 = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


class ServerFinto:
    """Server di trading simulato, con scenari di guasto in coda."""

    def __init__(self) -> None:
        self.scenari: list[str] = []
        self.inviati: list[dict[str, Any]] = []
        self.posizioni: dict[int, PositionSnapshot] = {}
        self.deal: list[DealSnapshot] = []
        self.disconnesso = False
        self.check = retcodes.CHECK_OK
        self.ask, self.bid = 1.10010, 1.10000
        self._ticket = 1000

    # --- letture ---
    async def symbol(self, simbolo: str) -> SymbolInfo:
        return SymbolInfo(
            name=simbolo,
            digits=5,
            point=0.00001,
            contract_size=100_000,
            tick_size=0.00001,
            tick_value=1,
            volume_min=0.01,
            volume_max=50,
            volume_step=0.01,
            spread_points=10,
            stops_level_points=10,
            as_of=T0,
        )

    async def tick(self, simbolo: str) -> Tick:
        self._connessione()
        return Tick(symbol=simbolo, time=T0, bid=self.bid, ask=self.ask)

    async def filling(self, simbolo: str) -> int:
        return 1

    async def positions(self, simbolo: str | None = None) -> tuple[PositionSnapshot, ...]:
        self._connessione()
        return tuple(p for p in self.posizioni.values() if simbolo in (None, p.symbol))

    async def deals(self, da: datetime, a: datetime) -> tuple[DealSnapshot, ...]:
        self._connessione()
        return tuple(self.deal)

    async def health(self) -> Any:
        return None

    def _connessione(self) -> None:
        if self.disconnesso:
            raise BrokerNotConnectedError("terminale disconnesso")

    # --- scrittura ---
    async def order_check(self, r: dict[str, Any]) -> RispostaOrdine:
        self._connessione()
        return RispostaOrdine(retcode=self.check)

    def _esegui(self, r: dict[str, Any], volume: float, sltp: bool = True) -> RispostaOrdine:
        self._ticket += 1
        if "position" in r:  # chiusura
            p = self.posizioni.pop(r["position"])
            self.deal.append(
                DealSnapshot(
                    ticket=self._ticket,
                    position_id=p.ticket,
                    symbol=p.symbol,
                    entry="OUT",
                    volume=volume,
                    price=r["price"],
                    profit=12.5,
                    magic=MAGIC,
                    comment=r["comment"],
                    executed_at=T0,
                )
            )
            return RispostaOrdine(
                retcode=retcodes.DONE,
                deal=self._ticket,
                order=self._ticket,
                volume=volume,
                price=r["price"],
            )
        self.posizioni[self._ticket] = PositionSnapshot(
            ticket=self._ticket,
            symbol=r["symbol"],
            side=PositionSide.BUY if r["type"] == 0 else PositionSide.SELL,
            volume=volume,
            price_open=r["price"],
            price_current=r["price"],
            stop_loss=r["sl"] if sltp else None,
            take_profit=r["tp"] if sltp else None,
            magic=r["magic"],
            comment=r["comment"],
            opened_at=T0,
            as_of=T0,
        )
        self.deal.append(
            DealSnapshot(
                ticket=self._ticket,
                position_id=self._ticket,
                symbol=r["symbol"],
                entry="IN",
                volume=volume,
                price=r["price"],
                magic=MAGIC,
                comment=r["comment"],
                executed_at=T0,
            )
        )
        return RispostaOrdine(
            retcode=retcodes.DONE if volume == r["volume"] else retcodes.DONE_REMAINDER,
            deal=self._ticket,
            order=self._ticket,
            volume=volume,
            price=r["price"],
        )

    async def order_send(self, r: dict[str, Any]) -> RispostaOrdine:
        self.inviati.append(r)
        if r.get("action") == TRADE_ACTION_SLTP:
            if self.scenari and self.scenari[0] == "sltp_fallisce":
                self.scenari.pop(0)
                return RispostaOrdine(retcode=retcodes.INVALID_STOPS)
            p = self.posizioni[r["position"]]
            self.posizioni[p.ticket] = p.model_copy(
                update={"stop_loss": r["sl"], "take_profit": r["tp"]}
            )
            return RispostaOrdine(retcode=retcodes.DONE)
        scenario = self.scenari.pop(0) if self.scenari else "ok"
        self._connessione()
        if scenario == "ok":
            return self._esegui(r, r["volume"])
        if scenario == "parziale":
            return self._esegui(r, round(r["volume"] / 2, 2))
        if scenario == "senza_sltp":
            return self._esegui(r, r["volume"], sltp=False)
        if scenario == "requote":
            self.ask += 0.00002
            return RispostaOrdine(retcode=retcodes.REQUOTE)
        if scenario == "rifiuto":
            return RispostaOrdine(retcode=retcodes.INVALID_STOPS)
        if scenario == "timeout_eseguito":
            self._esegui(r, r["volume"])
            raise BrokerTimeoutError("MT5 non ha risposto")
        if scenario == "timeout_non_eseguito":
            raise BrokerTimeoutError("MT5 non ha risposto")
        if scenario == "timeout_e_disconnessione":
            self._esegui(r, r["volume"])
            self.disconnesso = True
            raise BrokerTimeoutError("MT5 non ha risposto")
        if scenario == "nessuna_risposta":
            return RispostaOrdine(retcode=None)
        raise AssertionError(scenario)


class ArchivioDurevoleDiProva(MemoryExecutionStore):
    """Solo per provare il gateway: si dichiara durevole. Mai usato fuori dai test."""

    durable = True


async def autorizzato() -> tuple[bool, list[str]]:
    return True, []


def costruisci(
    tmp_path: Path,
    *,
    kill: bool = False,
    autorizza: Any = autorizzato,
    store: MemoryExecutionStore | None = None,
    server: ServerFinto | None = None,
):  # type: ignore[no-untyped-def]
    server = server or ServerFinto()
    store = store if store is not None else ArchivioDurevoleDiProva()
    clock = FrozenClock(T0)
    g = LiveOrderGateway(
        terminal=server,
        store=store,  # type: ignore[arg-type]
        breaker=CircuitBreaker(tmp_path / "breaker.json", clock=clock),
        kill_switch=lambda: kill,
        autorizzazione=autorizza,
        engine_id="motore",
        clock=clock,
    )
    return g, server, store


def approvazione(**kw: Any) -> RiskApproval:
    base: dict[str, Any] = {
        "approval_id": "a1",
        "engine_id": "motore",
        "idempotency_key": "chiave0001",
        "canonical": "EURUSD",
        "broker_symbol": "EURUSD",
        "direction": "LONG",
        "volume": 0.5,
        "entry_reference": 1.10010,
        "stop_loss": 1.09800,
        "take_profit": 1.10430,
        "risk_amount": 95.0,
        "max_slippage_points": 20,
        "issued_at": T0,
        "expires_at": T0 + timedelta(seconds=30),
    }
    return RiskApproval(**(base | kw))


# ======================================================================
# Prima dell'invio: nulla parte se manca qualcosa
# ======================================================================
class TestBlocchi:
    async def test_live_non_autorizzato(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async def negato() -> tuple[bool, list[str]]:
            return False, ["configuration: APP_MODE=PAPER"]

        g, server, _ = costruisci(tmp_path, autorizza=negato)
        with pytest.raises(LiveTradingDisabledError, match="APP_MODE=PAPER"):
            await g.apri(approvazione())
        assert server.inviati == []

    async def test_kill_switch_globale(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path, kill=True)
        with pytest.raises(KillSwitchEngagedError):
            await g.apri(approvazione())
        assert server.inviati == []

    async def test_breaker_aperto(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        g.breaker.scatta("prova")
        with pytest.raises(CircuitBreakerOpenError):
            await g.apri(approvazione())
        assert server.inviati == []

    async def test_postgres_obbligatorio(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path, store=MemoryExecutionStore())
        with pytest.raises(PersistenceUnavailableError):
            await g.apri(approvazione())
        guasto = ArchivioDurevoleDiProva()
        guasto.guasto = "PostgreSQL non raggiungibile"
        g2, server2, _ = costruisci(tmp_path, store=guasto)
        with pytest.raises(PersistenceUnavailableError):
            await g2.apri(approvazione())
        assert server.inviati == server2.inviati == []

    @pytest.mark.parametrize(
        "modifica",
        [
            {"engine_id": "altro"},
            {"expires_at": T0},
            {"stop_loss": 1.1010},
            {"take_profit": 1.0990},
        ],
    )
    async def test_approvazione_non_valida(self, tmp_path, modifica: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        with pytest.raises(ApprovalInvalidError):
            await g.apri(approvazione(**modifica))
        assert server.inviati == []

    async def test_stop_entro_lo_stops_level(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        o = await g.apri(approvazione(stop_loss=1.10005))
        assert o.state == "REJECTED" and server.inviati == []


# ======================================================================
# Esiti
# ======================================================================
class TestEsiti:
    async def test_eseguito_con_sl_tp_lato_server(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        o = await g.apri(approvazione())
        assert o.state == "FILLED" and o.volume_filled == 0.5 and o.position_ticket
        p = server.posizioni[o.position_ticket]
        assert (p.stop_loss, p.take_profit, p.magic, p.comment) == (
            1.098,
            1.1043,
            MAGIC,
            commento("chiave0001"),
        )
        assert [h["event"] for h in o.history] == ["CREATED", "SENT", "FILLED"]

    async def test_idempotenza_nessun_doppio_ordine(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        primo = await g.apri(approvazione())
        secondo = await g.apri(approvazione(approval_id="a2"))
        assert len(server.inviati) == 1 and secondo.position_ticket == primo.position_ticket

    async def test_esecuzione_parziale(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["parziale"]
        o = await g.apri(approvazione())
        assert o.state == "PARTIALLY_FILLED" and o.volume_filled == 0.25

    async def test_requote_ritenta_entro_lo_slittamento(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["requote"]
        o = await g.apri(approvazione())
        assert o.state == "FILLED" and o.attempts == 2 and len(server.inviati) == 2

    async def test_requote_oltre_lo_slittamento_rifiuta(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["requote"]
        o = await g.apri(approvazione(max_slippage_points=1))
        assert o.state == "REJECTED" and len(server.inviati) == 1

    async def test_rifiuti_consecutivi_aprono_il_breaker(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["rifiuto"] * 3
        for i in range(3):
            o = await g.apri(approvazione(idempotency_key=f"k{i}"))
            assert o.state == "REJECTED"
        assert g.breaker.aperto
        with pytest.raises(CircuitBreakerOpenError):
            await g.apri(approvazione(idempotency_key="k9"))

    async def test_order_check_rifiutato_non_invia(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.check = retcodes.NO_MONEY
        o = await g.apri(approvazione())
        assert o.state == "REJECTED" and server.inviati == []

    async def test_sl_tp_mancanti_vengono_impostati(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["senza_sltp"]
        o = await g.apri(approvazione())
        p = server.posizioni[o.position_ticket]
        assert p.stop_loss == 1.098 and p.take_profit == 1.1043
        assert server.inviati[-1]["action"] == TRADE_ACTION_SLTP

    async def test_sl_tp_impossibili_chiusura_protettiva(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, store = costruisci(tmp_path)
        server.scenari = ["senza_sltp", "sltp_fallisce"]
        o = await g.apri(approvazione())
        assert server.posizioni == {} and g.breaker.aperto
        aperto = await store.get(o.idempotency_key)
        assert aperto is not None and aperto.state == "CLOSED" and aperto.close_reason == "PROTECT"


# ======================================================================
# Timeout, disconnessione, riavvio: mai un doppio ordine
# ======================================================================
class TestGuasti:
    async def test_timeout_dopo_l_esecuzione_riconciliato(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["timeout_eseguito"]
        o = await g.apri(approvazione())
        assert o.state == "FILLED" and o.position_ticket in server.posizioni
        assert len(server.inviati) == 1
        assert "RECONCILED_FILLED" in [h["event"] for h in o.history]

    async def test_timeout_senza_esecuzione(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["timeout_non_eseguito"]
        o = await g.apri(approvazione())
        assert o.state == "NOT_EXECUTED" and server.posizioni == {} and len(server.inviati) == 1

    async def test_nessuna_risposta_riconciliata(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["nessuna_risposta"]
        o = await g.apri(approvazione())
        assert o.state == "NOT_EXECUTED"

    async def test_disconnessione_lascia_ignoto_e_blocca(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        server.scenari = ["timeout_e_disconnessione"]
        o = await g.apri(approvazione())
        assert o.state == "UNKNOWN" and g.breaker.aperto
        server.disconnesso = False
        with pytest.raises(CircuitBreakerOpenError):
            await g.apri(approvazione(idempotency_key="altro"))
        g.breaker.riarma("Verificato a mano dopo la disconnessione di prova")
        with pytest.raises(ExecutionStateUnknownError):
            await g.apri(approvazione(idempotency_key="altro"))
        assert len(server.inviati) == 1

    async def test_riavvio_riconcilia_senza_reinviare(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, store = costruisci(tmp_path)
        server.scenari = ["timeout_e_disconnessione"]
        await g.apri(approvazione())
        # "Riavvio": nuovo gateway e nuovo breaker dallo stesso stato persistito.
        server.disconnesso = False
        g2, _, _ = costruisci(tmp_path, store=store, server=server)
        assert g2.breaker.aperto  # il breaker sopravvive al riavvio
        risolti = await g2.riconcilia_tutto()
        assert [o.state for o in risolti] == ["FILLED"]
        assert len(server.inviati) == 1
        g2.breaker.riarma("Riconciliazione dopo il riavvio completata")
        assert (await g2.apri(approvazione())).state == "FILLED"  # stessa chiave: nessun invio
        assert len(server.inviati) == 1


# ======================================================================
# Gestione delle posizioni reali
# ======================================================================
async def _nessuna_barra(soggetto: str, tf: str) -> list[Any]:
    return []


class TestGestione:
    async def test_chiusura_per_invalidazione(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from backend.shadow.conditions import Barra

        g, server, store = costruisci(tmp_path)
        cond = {
            "subject": "EURUSD",
            "timeframe": "M5",
            "measure": "CLOSE",
            "operator": "BELOW",
            "level": 1.0990,
            "evidence_ids": ["x"],
            "description": "Sotto il supporto.",
        }
        o = await g.apri(
            approvazione(),
            plan={"invalidations": [cond], "expires_at": (T0 + timedelta(hours=24)).isoformat()},
        )

        async def barre(soggetto: str, tf: str) -> list[Barra]:
            return [
                Barra(
                    T0 + timedelta(minutes=5), T0 + timedelta(minutes=10), 1.1, 1.1, 1.0985, 1.0988
                )
            ]

        m = LivePositionManager(
            gateway=g, terminal=server, store=store, barre=barre, clock=g._clock
        )  # type: ignore[arg-type]
        eventi = await m.ciclo()
        assert any("INVALIDATION" in e for e in eventi) and server.posizioni == {}
        assert (await store.get(o.idempotency_key)).close_reason == "INVALIDATION"  # type: ignore[union-attr]

    async def test_chiusura_lato_server_registrata(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, store = costruisci(tmp_path)
        o = await g.apri(approvazione())
        p = server.posizioni.pop(o.position_ticket)
        server.deal.append(
            DealSnapshot(
                ticket=9,
                position_id=p.ticket,
                symbol="EURUSD",
                entry="OUT",
                volume=0.5,
                price=1.098,
                profit=-95.0,
                comment="[sl 1.09800]",
                executed_at=T0,
            )
        )
        m = LivePositionManager(
            gateway=g, terminal=server, store=store, barre=_nessuna_barra, clock=g._clock
        )  # type: ignore[arg-type]
        await m.ciclo()
        chiuso = await store.get(o.idempotency_key)
        assert chiuso is not None and (chiuso.state, chiuso.close_reason, chiuso.realized_pnl) == (
            "CLOSED",
            "SL",
            -95.0,
        )

    async def test_posizione_orfana_apre_il_breaker(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, store = costruisci(tmp_path)
        server.posizioni[77] = PositionSnapshot(
            ticket=77,
            symbol="EURUSD",
            side=PositionSide.BUY,
            volume=0.1,
            price_open=1.1,
            price_current=1.1,
            magic=MAGIC,
            opened_at=T0,
            as_of=T0,
        )
        m = LivePositionManager(
            gateway=g, terminal=server, store=store, barre=_nessuna_barra, clock=g._clock
        )  # type: ignore[arg-type]
        await m.ciclo()
        assert g.breaker.aperto

    async def test_chiusura_consentita_anche_con_kill_switch(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        g, server, _ = costruisci(tmp_path)
        o = await g.apri(approvazione())
        g._kill = lambda: True
        c = await g.chiudi(o, "TIME")
        assert c.state == "FILLED" and server.posizioni == {}


# ======================================================================
# Budget giornaliero dallo storico e gate di attivazione
# ======================================================================
def test_breaker_persistente_e_riarmo_motivato(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = FrozenClock(T0)
    b = CircuitBreaker(tmp_path / "b.json", clock=clock)
    b.scatta("prova")
    assert CircuitBreaker(tmp_path / "b.json", clock=clock).aperto
    with pytest.raises(ValueError):
        b.riarma("ok")
    b.riarma("Controllato manualmente il conto")
    assert not CircuitBreaker(tmp_path / "b.json", clock=clock).aperto


class _Impostazioni:
    class app:  # noqa: N801
        from backend.core.enums import TradingMode

        mode = "PAPER"
        enable_live_trading = False
        effective_mode = TradingMode.PAPER


async def test_gate_live_tutti_mancanti_di_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = FrozenClock(T0)
    r = LiveReadiness(
        settings=_Impostazioni,
        clock=clock,
        paper_trades=lambda: (3, 3),
        paper_critical_errors=lambda: 0,
        store=MemoryExecutionStore(),
        broker=None,
        risk_engine=None,
        breaker=CircuitBreaker(tmp_path / "b.json", clock=clock),
        drills_file=tmp_path / "d.json",
        confirmation_file=tmp_path / "c.json",
    )
    esito = await r.valuta()
    assert not esito["ready"]
    assert set(esito["missing"]) >= {
        "paper_trades",
        "persistence",
        "mt5",
        "risk_engine",
        "kill_switch_tested",
        "configuration",
        "user_confirmation",
    }
    ok, motivi = await r.consenti()
    assert not ok and motivi


async def test_gate_conferma_richiede_frase_e_conto(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = FrozenClock(T0)
    f = tmp_path / "c.json"
    r = LiveReadiness(
        settings=_Impostazioni,
        clock=clock,
        paper_trades=lambda: (MINIMO_TRADE_PAPER, 30),
        paper_critical_errors=lambda: 0,
        store=None,
        broker=None,
        risk_engine=None,
        breaker=CircuitBreaker(tmp_path / "b.json", clock=clock),
        drills_file=tmp_path / "d.json",
        confirmation_file=f,
    )
    f.write_text(json.dumps({"text": "ok", "account_login": 1, "confirmed_at": T0.isoformat()}))
    assert not r._gate_conferma(1).ok
    f.write_text(
        json.dumps({"text": FRASE_CONFERMA, "account_login": 2, "confirmed_at": T0.isoformat()})
    )
    assert not r._gate_conferma(1).ok
    f.write_text(
        json.dumps({"text": FRASE_CONFERMA, "account_login": 1, "confirmed_at": T0.isoformat()})
    )
    assert r._gate_conferma(1).ok
    assert (await r.valuta())["gates"][0]["ok"]  # 30 trade validati: gate paper soddisfatto


async def test_esercitazione_kill_switch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from backend.core.kill_switch import KillSwitch
    from backend.execution.drills import esercitazione_kill_switch

    clock = FrozenClock(T0)
    ks = KillSwitch(tmp_path / "KILL", clock=clock)
    esito = await esercitazione_kill_switch(
        ks,
        clock=clock,
        limiti=RiskLimits(_env_file=None),  # type: ignore[call-arg]
        file=tmp_path / "drills.json",
        breaker_file=tmp_path / "drill_breaker.json",
    )
    assert esito["passed"], esito
    assert not ks.state().engaged
    assert json.loads((tmp_path / "drills.json").read_text())["kill_switch"]["passed"]


def test_solo_execution_mt5_terminal_chiama_order_send() -> None:
    import ast

    from backend.config.paths import BACKEND_ROOT

    trovati = set()
    for p in BACKEND_ROOT.rglob("*.py"):
        if "tests" in p.parts or "__pycache__" in p.parts:
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (
                isinstance(n, ast.Attribute)
                and n.attr in ("order_send", "order_check")
                and isinstance(getattr(n, "value", None), ast.Attribute)
                and n.value.attr == "_mt5"
            ):
                trovati.add(p.relative_to(BACKEND_ROOT).as_posix())
    assert trovati == {"execution/mt5_terminal.py"}
