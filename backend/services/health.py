"""Servizio di stato del sistema.

Risponde a una sola domanda, e la risponde in italiano: **il sistema può
operare, e se no perché.**

Ogni componente viene interrogato in parallelo e nessun controllo può far
fallire la risposta: un health check che va in errore è inutile proprio quando
serve. I controlli che sollevano eccezioni vengono tradotti in uno stato
`UNHEALTHY` con il motivo, non propagati.

Il campo `critical` distingue ciò che blocca l'operatività (broker, database)
da ciò che la degrada (cache, provider di news): solo i primi concorrono a
`can_trade`.
"""

from __future__ import annotations

import asyncio

from backend import __version__
from backend.core.enums import HealthStatus
from backend.schemas.health import ComponentHealth, SystemHealth
from backend.services.container import ApplicationContainer


class HealthService:
    """Raccoglie lo stato dei componenti e lo riassume."""

    def __init__(self, container: ApplicationContainer) -> None:
        self._container = container

    async def check(self) -> SystemHealth:
        components = await asyncio.gather(
            self._check_database(),
            self._check_cache(),
            self._check_broker(),
            self._check_ai(),
            self._check_journal(),
            self._check_persistence(),
        )

        settings = self._container.settings
        kill_switch = self._container.kill_switch.state()

        critical_statuses = [c.status for c in components if c.critical]
        overall = SystemHealth.worst(critical_statuses)
        if kill_switch.engaged and overall is HealthStatus.HEALTHY:
            # Il kill switch non è un guasto: il sistema è sano e volutamente
            # fermo. "DEGRADED" lo comunica senza far pensare a un errore.
            overall = HealthStatus.DEGRADED

        return SystemHealth(
            status=overall,
            version=__version__,
            environment=str(settings.app.env),
            declared_mode=settings.app.mode,
            effective_mode=settings.app.effective_mode,
            kill_switch_engaged=kill_switch.engaged,
            kill_switch_reason=kill_switch.reason,
            uptime_seconds=round(self._container.uptime_seconds, 1),
            checked_at=self._container.clock.now(),
            components=tuple(components),
            warnings=tuple(settings.startup_warnings()),
        )

    # ------------------------------------------------------------------
    # Controlli dei singoli componenti
    # ------------------------------------------------------------------
    async def _check_database(self) -> ComponentHealth:
        status, detail, latency = await self._container.database.check()
        return ComponentHealth(
            name="PostgreSQL",
            status=status,
            detail=detail,
            latency_ms=latency,
            checked_at=self._container.clock.now(),
            critical=True,
        )

    async def _check_cache(self) -> ComponentHealth:
        status, detail, latency = await self._container.cache.check()
        return ComponentHealth(
            name="Redis",
            status=status,
            detail=detail,
            latency_ms=latency,
            checked_at=self._container.clock.now(),
            # Non critico in fase 1: oggi Redis è solo cache dei contesti.
            # Diventerà critico quando i worker ne useranno le code (fase 7).
            critical=False,
        )

    async def _check_broker(self) -> ComponentHealth:
        now = self._container.clock.now()
        broker = self._container.broker
        if broker is None:
            return ComponentHealth(
                name="MetaTrader 5",
                status=HealthStatus.UNHEALTHY,
                detail=self._container.broker_error
                or "Connessione a MT5 non stabilita: nessun dato di mercato.",
                checked_at=now,
                critical=True,
            )
        health = await broker.health()
        return ComponentHealth(
            name=broker.name,
            status=health.status,
            detail=health.detail,
            latency_ms=health.latency_ms,
            checked_at=now,
            critical=True,
            metadata={
                **health.metadata,
                "ora_server_broker": (
                    health.server_time.isoformat() if health.server_time else None
                ),
                "build_terminale": health.terminal_build,
            },
        )

    async def _check_ai(self) -> ComponentHealth:
        """Stato del provider LLM.

        In fase 1 è un controllo di configurazione, non una chiamata: interrogare
        un modello a ogni health check costerebbe denaro per nessuna
        informazione in più. La verifica di raggiungibilità arriva con il modulo
        AI (fase 7), con cache e budget.
        """
        ai = self._container.settings.ai
        now = self._container.clock.now()
        if ai.provider == "null":
            return ComponentHealth(
                name="Provider AI",
                status=HealthStatus.UNHEALTHY if ai.required else HealthStatus.DEGRADED,
                detail="Nessun provider AI configurato (AI_PROVIDER=null)."
                + (" Con AI_REQUIRED=true non verranno prodotte decisioni." if ai.required else ""),
                checked_at=now,
                critical=ai.required,
            )
        if not ai.has_credentials:
            return ComponentHealth(
                name="Provider AI",
                status=HealthStatus.UNHEALTHY,
                detail=f"Chiave API mancante per il provider '{ai.provider}'.",
                checked_at=now,
                critical=ai.required,
            )
        return ComponentHealth(
            name="Provider AI",
            status=HealthStatus.HEALTHY,
            detail=f"Provider '{ai.provider}' configurato con il modello {ai.model}.",
            checked_at=now,
            critical=ai.required,
            metadata={"provider": ai.provider, "modello": ai.model, "obbligatorio": ai.required},
        )

    async def _check_persistence(self) -> ComponentHealth:
        """Persistenza dei contesti: BEST_EFFORT, quindi non critica (ADR 0019).

        L'intelligence read-only continua senza database e lo dichiara qui. Lo stato
        del rischio e l'esecuzione (fasi 6 e 11) saranno REQUIRED: per loro il
        database è già un componente critico (`PostgreSQL` qui sopra).
        """
        p = self._container.persistence
        now = self._container.clock.now()
        sana = p.write_failures == 0 and p.writes_skipped == 0
        return ComponentHealth(
            name="Persistenza contesti (best effort)",
            status=HealthStatus.HEALTHY if sana else HealthStatus.DEGRADED,
            detail=(
                "Contesti archiviati regolarmente."
                if sana
                else f"{p.write_failures} scritture fallite e {p.writes_skipped} saltate: "
                "lo storico dei contesti ha buchi. L'osservazione continua."
            ),
            checked_at=now,
            critical=False,
            metadata={
                "requisito": "BEST_EFFORT",
                "affidabile": p.reliable,
                "scritture_fallite": p.write_failures,
                "scritture_saltate": p.writes_skipped,
            },
        )

    async def _check_journal(self) -> ComponentHealth:
        failures = self._container.journal.write_failures
        now = self._container.clock.now()
        if failures == 0:
            return ComponentHealth(
                name="Diario di sistema",
                status=HealthStatus.HEALTHY,
                detail="Tutti gli eventi sono stati registrati.",
                checked_at=now,
                critical=True,
            )
        return ComponentHealth(
            name="Diario di sistema",
            status=HealthStatus.UNHEALTHY,
            detail=f"{failures} eventi non registrati: la tracciabilità è compromessa.",
            checked_at=now,
            critical=True,
            metadata={"scritture_fallite": failures},
        )
