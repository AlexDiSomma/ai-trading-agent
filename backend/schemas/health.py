"""DTO dello stato del sistema.

System Health non è una pagina di cortesia: è il posto dove il sistema dichiara
*perché* non sta operando. Il progetto precedente dava l'impressione di essere
rotto solo perché nessun componente diceva ad alta voce cosa lo bloccava.

Per questo ogni componente riporta tre cose: stato, motivo in italiano e
latenza. E il riepilogo dichiara esplicitamente `can_trade`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import HealthStatus, TradingMode


class ComponentHealth(BaseModel):
    """Stato di un singolo componente (database, Redis, broker, AI...)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: HealthStatus
    # Sempre in italiano: lo legge l'utente, non solo lo sviluppatore.
    detail: str = ""
    latency_ms: float | None = None
    checked_at: datetime
    # Un componente non critico degradato non ferma il sistema (es. news).
    critical: bool = True
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class SystemHealth(BaseModel):
    """Riepilogo complessivo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: HealthStatus
    version: str
    environment: str
    declared_mode: TradingMode
    effective_mode: TradingMode
    kill_switch_engaged: bool
    kill_switch_reason: str | None = None
    uptime_seconds: float = Field(ge=0)
    checked_at: datetime
    components: tuple[ComponentHealth, ...] = ()
    # Condizioni in cui il sistema funziona ma non fa ciò che si potrebbe
    # credere (mock attivo, LIVE declassato, chiave AI mancante...).
    warnings: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def can_trade(self) -> bool:
        """Se false, nessun ordine partirà: `blocking_reasons` spiega perché."""
        return not self.blocking_reasons

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.kill_switch_engaged:
            reasons.append(self.kill_switch_reason or "Kill switch attivo")
        if self.effective_mode is TradingMode.OBSERVE:
            reasons.append("Modalità OBSERVE: analisi senza operatività")
        for component in self.components:
            if component.critical and not component.status.can_trade:
                reasons.append(f"{component.name}: {component.detail or component.status.value}")
        return tuple(reasons)

    @staticmethod
    def worst(statuses: list[HealthStatus]) -> HealthStatus:
        """Lo stato complessivo è il peggiore fra i componenti critici."""
        order = [
            HealthStatus.UNHEALTHY,
            HealthStatus.UNKNOWN,
            HealthStatus.DEGRADED,
            HealthStatus.HEALTHY,
        ]
        for candidate in order:
            if candidate in statuses:
                return candidate
        return HealthStatus.UNKNOWN
