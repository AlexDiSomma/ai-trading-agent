"""Diario di sistema: l'audit trail su database.

Si iscrive a **tutti** gli eventi del bus e li scrive in `system_events`. I
moduli non sanno che esiste: nessuno deve ricordarsi di "loggare anche nel
diario", altrimenti la riga che manca è sempre quella che serviva.

Differenza dai log: i log si ruotano, si comprimono e si perdono. Questa tabella
resta e si interroga con SQL. È il presupposto del Decision Journal della fase 8
e dell'analisi di performance della fase 9.

Un errore di scrittura **non** viene ingoiato in silenzio: se il diario non
scrive, il sistema ha perso la tracciabilità e questa è a sua volta una
condizione che il circuit breaker deve conoscere (`journal_failures`).
"""

from __future__ import annotations

from backend.core.clock import Clock, SystemClock
from backend.core.enums import TradingMode
from backend.core.events import Event, EventType
from backend.core.logging import get_logger
from backend.database.session import Database
from backend.models.system import SystemEvent

logger = get_logger(__name__)

#: Eventi che vanno registrati con severità alta: sono quelli che cambiano la
#: capacità operativa del sistema, non il suo stato di analisi.
_CRITICAL_EVENTS: frozenset[EventType] = frozenset(
    {
        EventType.KILL_SWITCH_ENGAGED,
        EventType.RISK_BUDGET_EXHAUSTED,
        EventType.RISK_CIRCUIT_BREAKER_TRIPPED,
        EventType.BROKER_CONNECTION_LOST,
        EventType.AI_PROVIDER_UNAVAILABLE,
        EventType.MARKET_DATA_STALE,
    }
)

_WARNING_EVENTS: frozenset[EventType] = frozenset(
    {
        EventType.RISK_PLAN_REJECTED,
        EventType.RISK_PLAN_REDUCED,
        EventType.POSITION_THESIS_INVALIDATED,
        EventType.STRATEGY_DEGRADATION_DETECTED,
        EventType.ORDER_REJECTED,
        EventType.TRADE_PLAN_EXPIRED,
    }
)


class SystemJournal:
    """Scrive su database ogni evento che passa dal bus."""

    def __init__(
        self, database: Database, *, mode: TradingMode, clock: Clock | None = None
    ) -> None:
        self._database = database
        self._mode = mode
        self._clock = clock or SystemClock()
        #: Scritture fallite dall'avvio: lo legge System Health.
        self.write_failures = 0

    @staticmethod
    def _severity(event_type: EventType) -> str:
        if event_type in _CRITICAL_EVENTS:
            return "CRITICAL"
        if event_type in _WARNING_EVENTS:
            return "WARNING"
        return "INFO"

    async def handle(self, event: Event) -> None:
        """Handler da iscrivere al bus con `subscribe_all`."""
        row = SystemEvent(
            event_type=str(event.type),
            occurred_at=event.occurred_at,
            severity=self._severity(event.type),
            source=event.source,
            mode=str(self._mode),
            correlation_id=event.correlation_id,
            message=str(event.payload.get("message", "")),
            # Il payload passa da `_jsonable`: contiene datetime, UUID e DTO
            # che la colonna JSON non saprebbe serializzare.
            payload=_jsonable(event.payload),
        )
        try:
            async with self._database.session() as session:
                session.add(row)
        except Exception as exc:
            self.write_failures += 1
            # Volutamente ERROR e non WARNING: senza diario il sistema non è
            # ricostruibile, ed è una condizione che va vista.
            #
            # Senza traceback, invece, di proposito: con il database spento
            # ogni evento ne produrrebbe uno, e un log di centinaia di
            # tracciati identici nasconde gli errori veri. La causa sta nel
            # messaggio, e lo stato complessivo in System Health.
            logger.error(
                "journal.scrittura_fallita",
                event_type=str(event.type),
                failures=self.write_failures,
                causa=f"{exc.__class__.__name__}: {exc}"[:200],
            )

    async def record(
        self,
        event_type: EventType,
        *,
        message: str = "",
        source: str = "system",
        **payload: object,
    ) -> None:
        """Scorciatoia per registrare un fatto senza passare dal bus.

        Usata allo startup e allo spegnimento, quando il bus non e' ancora (o
        non e' piu') il canale naturale.
        """
        await self.handle(
            Event(
                type=event_type,
                occurred_at=self._clock.now(),
                payload={"message": message, **payload},
                source=source,
            )
        )


def _jsonable(payload: dict[str, object]) -> dict[str, object]:
    """Rende il payload serializzabile in JSON senza perdere informazione."""
    result: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool, type(None))):
            result[key] = value
        else:
            result[key] = str(value)
    return result
