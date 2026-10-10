"""Assemblaggio dello stack di esecuzione reale. Esiste anche con il LIVE spento.

Il gateway viene costruito se il terminale reale è disponibile, ma **ogni** invio
rivaluta tutti i gate di `LiveReadiness`: con `APP_MODE=PAPER`, senza conferma
dell'utente o con un qualunque gate mancante, nessun ordine può partire.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config.paths import resolve
from backend.core.errors import BrokerError
from backend.execution.breaker import CircuitBreaker
from backend.execution.gateway import LiveOrderGateway
from backend.execution.manager import LivePositionManager
from backend.execution.readiness import LiveReadiness
from backend.execution.store import PostgresExecutionStore
from backend.risk.live_engine import LiveRiskEngine

CARTELLA = "var/execution"
FILE_CONFERMA = "var/LIVE_CONFIRMATION.json"


@dataclass
class LiveStack:
    readiness: LiveReadiness
    breaker: CircuitBreaker
    store: PostgresExecutionStore
    engine: LiveRiskEngine | None
    gateway: LiveOrderGateway | None
    manager: LivePositionManager | None
    drills_file: Path
    note: tuple[str, ...] = ()


def costruisci_stack(
    container: Any, *, paper_trades: Any, paper_critical_errors: Any, barre: Any
) -> LiveStack:
    c = container
    cartella = resolve(CARTELLA)
    breaker = CircuitBreaker(cartella / "breaker.json", clock=c.clock)
    store = PostgresExecutionStore(c.database)
    note: list[str] = []
    engine = None
    if c.broker is not None and c.valuation is not None and c.instruments is not None:

        def ora_broker():  # type: ignore[no-untyped-def]
            stima = c.broker_clock.estimate(now=c.clock.now())
            return stima.server_time if stima.reliable else None

        engine = LiveRiskEngine(
            account=c.broker,
            valuation=c.valuation,
            instruments=c.instruments,
            deals=c.deals,
            deal_reader=c.broker.get_deals,
            limits=lambda: c.settings.risk,
            kill_switch=lambda: c.kill_switch.state().engaged,
            ora_broker=ora_broker,
            clock=c.clock,
        )
    readiness = LiveReadiness(
        settings=c.settings,
        clock=c.clock,
        paper_trades=paper_trades,
        paper_critical_errors=paper_critical_errors,
        store=store,
        broker=c.broker,
        risk_engine=engine,
        breaker=breaker,
        drills_file=cartella / "drills.json",
        confirmation_file=resolve(FILE_CONFERMA),
    )
    gateway = manager = None
    if engine is not None and c.broker is not None and not c.broker.is_simulated:
        try:
            from backend.execution.mt5_terminal import MT5TradingTerminal

            terminale = MT5TradingTerminal(c.broker)
            gateway = LiveOrderGateway(
                terminal=terminale,
                store=store,
                breaker=breaker,
                kill_switch=lambda: c.kill_switch.state().engaged,
                autorizzazione=readiness.consenti,
                engine_id=engine.engine_id,
                clock=c.clock,
            )
            manager = LivePositionManager(
                gateway=gateway, terminal=terminale, store=store, barre=barre, clock=c.clock
            )
        except BrokerError as e:
            note.append(f"Terminale di trading non disponibile: {e}")
    else:
        note.append("Gateway non costruito: broker assente o simulato.")
    return LiveStack(
        readiness=readiness,
        breaker=breaker,
        store=store,
        engine=engine,
        gateway=gateway,
        manager=manager,
        drills_file=cartella / "drills.json",
        note=tuple(note),
    )
