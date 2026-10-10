"""Interfacce delle fonti esterne della Global Intelligence (fase 4.5, ADR 0018).

Cinque famiglie, ognuna dietro un `Protocol`:

| Interfaccia | Cosa fornisce | Implementazione oggi |
|---|---|---|
| `MarketReferenceProvider` | prezzi di riferimento non (bene) disponibili su XM | CBOE (VIX) |
| `EconomicCalendarProvider` | eventi macro con importanza e orario | Forex Factory |
| `RatesProvider` | curva dei rendimenti | Tesoro USA |
| `MacroDataProvider` | serie macro (inflazione, occupazione, ...) | nessuna |
| `NewsProvider` | notizie | nessuna (rinviate) |

**Nessun dato finto fuori dai test.** Una famiglia senza provider è rappresentata
da un oggetto `NotConfigured*` che dichiara `NOT_CONFIGURED` e solleva se
interrogato: il contesto globale mostra la dimensione come non disponibile, mai
come "tranquilla".

Ogni provider tiene in cache l'ultima lettura riuscita per un tempo proprio della
fonte (il calendario cambia in giornata, la chiusura del VIX una volta al giorno), e
riporta il proprio stato: ultimo successo, ultimo errore, data dell'ultima
osservazione.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.core.enums import (
    DataFrequency,
    IntendedUse,
    ProviderKind,
    ProviderState,
    SourceDomain,
)
from backend.core.errors import TradingAgentError
from backend.schemas.external import (
    CalendarSnapshot,
    ExternalSeries,
    ProviderStatus,
    YieldCurveHistory,
)


class ProviderError(TradingAgentError):
    """Una fonte esterna non ha risposto, o ha risposto in un formato inatteso.

    Non blocca l'operatività da sola: degrada la dimensione che ne dipende.
    """

    blocks_trading = False


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    kind: ProviderKind
    official: bool
    requires_key: bool
    free: bool
    coverage: str
    license_note: str
    domain: SourceDomain
    frequency: DataFrequency
    publication_delay: str
    intended_use: IntendedUse
    url: str = ""


@runtime_checkable
class ExternalProvider(Protocol):
    info: ProviderInfo

    def status(self) -> ProviderStatus: ...


@runtime_checkable
class MarketReferenceProvider(ExternalProvider, Protocol):
    async def daily_series(self, series_id: str) -> ExternalSeries: ...


@runtime_checkable
class EconomicCalendarProvider(ExternalProvider, Protocol):
    async def calendar(self) -> CalendarSnapshot: ...


@runtime_checkable
class RatesProvider(ExternalProvider, Protocol):
    async def yield_curve(self) -> YieldCurveHistory: ...


@runtime_checkable
class MacroDataProvider(ExternalProvider, Protocol):
    async def macro_series(self, series_id: str) -> ExternalSeries: ...


@runtime_checkable
class NewsProvider(ExternalProvider, Protocol):
    async def headlines(self, since: datetime) -> tuple[object, ...]: ...


# ======================================================================
# Stato condiviso
# ======================================================================
class StatoProvider:
    """Ultimo successo, ultimo errore, ultima osservazione: lo stato riportato."""

    def __init__(self, info: ProviderInfo) -> None:
        self.info = info
        self.ultimo_successo: datetime | None = None
        self.ultimo_errore: str | None = None
        self.dati_al: datetime | None = None
        self.vecchio = False

    def successo(
        self, quando: datetime, dati_al: datetime | None, *, vecchio: bool = False
    ) -> None:
        self.ultimo_successo = quando
        self.ultimo_errore = None
        self.dati_al = dati_al
        self.vecchio = vecchio

    def errore(self, motivo: str) -> None:
        self.ultimo_errore = motivo[:300]

    def status(self) -> ProviderStatus:
        if self.ultimo_errore is not None:
            stato = ProviderState.FAILED
        elif self.ultimo_successo is None:
            # Configurato ma non ancora interrogato: non è un successo.
            stato = ProviderState.FAILED
        elif self.vecchio:
            stato = ProviderState.STALE
        else:
            stato = ProviderState.AVAILABLE
        return ProviderStatus(
            name=self.info.name,
            kind=self.info.kind,
            state=stato,
            official=self.info.official,
            requires_key=self.info.requires_key,
            free=self.info.free,
            coverage=self.info.coverage,
            license_note=self.info.license_note,
            domain=self.info.domain,
            frequency=self.info.frequency,
            publication_delay=self.info.publication_delay,
            intended_use=self.info.intended_use,
            last_success=self.ultimo_successo,
            last_error=self.ultimo_errore,
            data_as_of=self.dati_al,
        )


# ======================================================================
# Famiglie senza provider: dichiarate, mai simulate
# ======================================================================
class NotConfigured:
    """Base per le famiglie senza fonte: stato NOT_CONFIGURED, interrogazioni vietate."""

    def __init__(
        self, kind: ProviderKind, motivo: str, domain: SourceDomain, uso: IntendedUse
    ) -> None:
        self.info = ProviderInfo(
            name="nessuno",
            kind=kind,
            official=False,
            requires_key=False,
            free=True,
            coverage="",
            license_note=motivo,
            domain=domain,
            frequency=DataFrequency.NONE,
            publication_delay="",
            intended_use=uso,
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.info.name,
            kind=self.info.kind,
            state=ProviderState.NOT_CONFIGURED,
            license_note=self.info.license_note,
            domain=self.info.domain,
            intended_use=self.info.intended_use,
        )

    def _rifiuta(self) -> ProviderError:
        return ProviderError(
            f"Nessun provider configurato per {self.info.kind}: {self.info.license_note}"
        )


class NotConfiguredReference(NotConfigured):
    def __init__(self, motivo: str = "fonte di riferimento non configurata") -> None:
        super().__init__(
            ProviderKind.MARKET_REFERENCE,
            motivo,
            SourceDomain.MARKET_REFERENCE,
            IntendedUse.STRUCTURAL_CONTEXT,
        )

    async def daily_series(self, series_id: str) -> ExternalSeries:
        raise self._rifiuta()


class NotConfiguredCalendar(NotConfigured):
    def __init__(self, motivo: str = "calendario economico non configurato") -> None:
        super().__init__(
            ProviderKind.ECONOMIC_CALENDAR, motivo, SourceDomain.CALENDAR, IntendedUse.EVENT_RISK
        )

    async def calendar(self) -> CalendarSnapshot:
        raise self._rifiuta()


class NotConfiguredRates(NotConfigured):
    def __init__(self, motivo: str = "curva dei rendimenti non configurata") -> None:
        super().__init__(
            ProviderKind.RATES, motivo, SourceDomain.MACRO, IntendedUse.STRUCTURAL_CONTEXT
        )

    async def yield_curve(self) -> YieldCurveHistory:
        raise self._rifiuta()


class NotConfiguredMacro(NotConfigured):
    def __init__(
        self, motivo: str = "nessun provider macro scelto (candidato: FRED, chiave gratuita)"
    ) -> None:
        super().__init__(
            ProviderKind.MACRO_DATA, motivo, SourceDomain.MACRO, IntendedUse.STRUCTURAL_CONTEXT
        )

    async def macro_series(self, series_id: str) -> ExternalSeries:
        raise self._rifiuta()


class NotConfiguredNews(NotConfigured):
    def __init__(self, motivo: str = "news e sentiment rinviati: nessun provider") -> None:
        super().__init__(ProviderKind.NEWS, motivo, SourceDomain.NEWS, IntendedUse.EVENT_RISK)

    async def headlines(self, since: datetime) -> tuple[object, ...]:
        raise self._rifiuta()
