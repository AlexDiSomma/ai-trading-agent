"""Catalogo degli eventi interni e bus minimale.

I moduli non si chiamano a catena: pubblicano eventi. Serve a tre cose:
- un modulo lento (news, macro) non blocca quelli veloci (prezzi, posizioni);
- il Decision Journal puo' registrare *tutto* iscrivendosi al bus, senza che i
  moduli sappiano della sua esistenza;
- il backtest riproduce gli stessi eventi in ordine storico e riusa gli stessi
  consumatori, senza una seconda implementazione della pipeline.

Il bus in-process di questo file e' quello della fase 1. La fase successiva
sostituisce il trasporto con Redis Streams mantenendo la stessa interfaccia
(vedi docs/adr/0002-pipeline-a-due-cicli.md).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class EventType(StrEnum):
    """Eventi del sistema, nell'ordine della pipeline concettuale."""

    # --- dati di mercato ---
    TICK_RECEIVED = "market.tick.received"
    CANDLE_CLOSED = "market.candle.closed"
    MARKET_DATA_STALE = "market.data.stale"

    # --- cosa succede nel mondo ---
    MACRO_RELEASE_PUBLISHED = "macro.release.published"
    CALENDAR_EVENT_IMMINENT = "calendar.event.imminent"
    NEWS_BATCH_INGESTED = "news.batch.ingested"
    INTERMARKET_UPDATED = "intermarket.context.updated"
    GLOBAL_CONTEXT_UPDATED = "world.context.updated"
    GLOBAL_REGIME_CHANGED = "world.regime.changed"

    # --- cosa succede sui mercati ---
    ASSET_REGIME_CHANGED = "asset.regime.changed"
    SCANNER_RANKING_UPDATED = "scanner.ranking.updated"

    # --- decisione ---
    AI_DECISION_PRODUCED = "ai.decision.produced"
    AI_PROVIDER_UNAVAILABLE = "ai.provider.unavailable"
    TRADE_PLAN_CREATED = "plan.created"
    TRADE_PLAN_EXPIRED = "plan.expired"

    # --- rischio e portafoglio ---
    PORTFOLIO_EXPOSURE_UPDATED = "portfolio.exposure.updated"
    RISK_PLAN_APPROVED = "risk.plan.approved"
    RISK_PLAN_REDUCED = "risk.plan.reduced"
    RISK_PLAN_REJECTED = "risk.plan.rejected"
    RISK_BUDGET_EXHAUSTED = "risk.budget.exhausted"
    RISK_CIRCUIT_BREAKER_TRIPPED = "risk.circuit_breaker.tripped"

    # --- esecuzione ---
    EXECUTION_INTENT_CREATED = "execution.intent.created"
    ORDER_SENT = "execution.order.sent"
    ORDER_FILLED = "execution.order.filled"
    ORDER_REJECTED = "execution.order.rejected"
    POSITION_OPENED = "position.opened"
    POSITION_MODIFIED = "position.modified"
    POSITION_PARTIALLY_CLOSED = "position.partially_closed"
    POSITION_CLOSED = "position.closed"
    POSITION_THESIS_INVALIDATED = "position.thesis_invalidated"

    # --- apprendimento ---
    TRADE_CLOSED_ANALYSED = "performance.trade.analysed"
    DAILY_PERFORMANCE_COMPUTED = "performance.daily.computed"
    STRATEGY_DEGRADATION_DETECTED = "performance.strategy.degradation"

    # --- piattaforma ---
    BROKER_CONNECTION_LOST = "broker.connection.lost"
    BROKER_CONNECTION_RESTORED = "broker.connection.restored"
    KILL_SWITCH_ENGAGED = "system.kill_switch.engaged"
    KILL_SWITCH_RELEASED = "system.kill_switch.released"
    MODE_CHANGED = "system.mode.changed"
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"


@dataclass(frozen=True, slots=True)
class Event:
    """Messaggio immutabile.

    `correlation_id` lega fra loro tutti gli eventi nati dalla stessa
    osservazione di mercato: e' cio' che permette di ricostruire un trade dalla
    candela che l'ha fatto notare fino alla chiusura.
    """

    type: EventType
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: UUID = field(default_factory=uuid4)
    event_id: UUID = field(default_factory=uuid4)
    source: str = "unknown"


Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Bus in-process.

    Un handler che solleva un'eccezione non interrompe gli altri: il journal non
    deve poter far cadere l'esecuzione, ne' viceversa. L'errore viene restituito
    al chiamante tramite `on_handler_error` per essere loggato dove c'e' il
    logger, cosi' `core` resta senza dipendenze.
    """

    def __init__(self, on_handler_error: Callable[[Event, str, Exception], None] | None = None):
        self._handlers: dict[EventType, list[tuple[str, Handler]]] = defaultdict(list)
        self._wildcard: list[tuple[str, Handler]] = []
        self._on_handler_error = on_handler_error

    def subscribe(self, event_type: EventType, handler: Handler, *, name: str = "") -> None:
        self._handlers[event_type].append((name or handler.__qualname__, handler))

    def subscribe_all(self, handler: Handler, *, name: str = "") -> None:
        """Iscrive a tutti gli eventi (journal, audit trail, metriche)."""
        self._wildcard.append((name or handler.__qualname__, handler))

    async def publish(self, event: Event) -> None:
        targets = [*self._handlers.get(event.type, []), *self._wildcard]
        if not targets:
            return
        results = await asyncio.gather(
            *(handler(event) for _, handler in targets), return_exceptions=True
        )
        for (name, _), result in zip(targets, results, strict=True):
            if isinstance(result, Exception) and self._on_handler_error is not None:
                self._on_handler_error(event, name, result)

    def handler_count(self, event_type: EventType) -> int:
        return len(self._handlers.get(event_type, [])) + len(self._wildcard)
