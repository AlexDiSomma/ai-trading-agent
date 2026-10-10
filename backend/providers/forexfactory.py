"""Calendario economico da Forex Factory (feed JSON settimanale pubblico).

Scelto per la fase 4.5 dopo il confronto in `docs/EXTERNAL_PROVIDERS.md`: è
l'unica fonte **gratuita e senza chiave** che risponde da questa rete (verificato il
2026-10-05). Trading Economics ha chiuso l'accesso ospite; FMP e Finnhub
richiedono una chiave; MT5 non espone il calendario nell'API Python.

Limiti misurati, dichiarati e gestiti:

* copre **solo la settimana corrente** (da domenica a sabato, ora di New York):
  oltre `coverage_end` il calendario non sa nulla;
* **non contiene il valore pubblicato** (`actual`), solo previsione e precedente:
  serve per sapere *quando* c'è un evento, non per leggerne la sorpresa;
* è un feed **non ufficiale**: il formato può cambiare senza preavviso, e un
  formato inatteso diventa `ProviderError`, mai un calendario vuoto;
* il server dichiara `Cache-Control: max-age=60`; noi interroghiamo al massimo una
  volta l'ora.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.core.clock import Clock, SystemClock
from backend.core.enums import (
    DataFrequency,
    EventImportance,
    IntendedUse,
    ProviderKind,
    SourceDomain,
)
from backend.providers.base import ProviderError, ProviderInfo, StatoProvider
from backend.providers.http import Cache, scarica
from backend.schemas.external import CalendarSnapshot, EconomicEvent, ProviderStatus

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEW_YORK = ZoneInfo("America/New_York")

_IMPORTANZA = {
    "High": EventImportance.HIGH,
    "Medium": EventImportance.MEDIUM,
    "Low": EventImportance.LOW,
    "Holiday": EventImportance.HOLIDAY,
}

INFO = ProviderInfo(
    name="forexfactory",
    kind=ProviderKind.ECONOMIC_CALENDAR,
    official=False,
    requires_key=False,
    free=True,
    coverage="settimana corrente; nessun valore pubblicato (actual)",
    license_note="feed pubblico non ufficiale di Forex Factory; formato non garantito",
    domain=SourceDomain.CALENDAR,
    frequency=DataFrequency.WEEKLY_SCHEDULE,
    publication_delay=(
        "programma della settimana pubblicato in anticipo; aggiornato in giornata; "
        "valore pubblicato (actual) non presente nel feed"
    ),
    intended_use=IntendedUse.EVENT_RISK,
    url=URL,
)


def analizza_feed(testo: str, *, retrieved_at: datetime) -> CalendarSnapshot:
    """Da JSON del feed a `CalendarSnapshot`. Formato inatteso → `ProviderError`."""
    try:
        grezzi = json.loads(testo)
    except json.JSONDecodeError as errore:
        raise ProviderError(f"Forex Factory: JSON non valido ({errore})") from errore
    if not isinstance(grezzi, list) or not grezzi:
        raise ProviderError("Forex Factory: feed vuoto o non è un elenco di eventi.")

    eventi: list[EconomicEvent] = []
    for voce in grezzi:
        try:
            titolo = str(voce["title"]).strip()
            valuta = str(voce["country"]).strip().upper()
            quando = datetime.fromisoformat(str(voce["date"]))
        except (KeyError, TypeError, ValueError) as errore:
            raise ProviderError(f"Forex Factory: evento con formato inatteso: {voce!r}") from errore
        if quando.tzinfo is None:
            raise ProviderError(f"Forex Factory: orario senza fuso: {voce['date']}")
        quando = quando.astimezone(UTC)
        impronta = hashlib.sha1(  # noqa: S324 - identificativo, non sicurezza
            f"{titolo}|{valuta}|{quando.isoformat()}".encode()
        ).hexdigest()[:16]
        eventi.append(
            EconomicEvent(
                event_id=impronta,
                title=titolo,
                currency="ALL" if valuta == "ALL" else valuta,
                importance=_IMPORTANZA.get(str(voce.get("impact", "")), EventImportance.UNKNOWN),
                scheduled_at=quando,
                forecast=str(voce.get("forecast") or "") or None,
                previous=str(voce.get("previous") or "") or None,
                actual=None,
                source=INFO.name,
            )
        )

    # La settimana di Forex Factory va da domenica a domenica, ora di New York.
    primo = min(e.scheduled_at for e in eventi).astimezone(NEW_YORK)
    domenica = (primo - timedelta(days=(primo.weekday() + 1) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return CalendarSnapshot(
        provider=INFO.name,
        retrieved_at=retrieved_at,
        coverage_start=domenica.astimezone(UTC),
        coverage_end=(domenica + timedelta(days=7)).astimezone(UTC),
        events=tuple(sorted(eventi, key=lambda e: (e.scheduled_at, e.currency, e.title))),
        has_actuals=False,
    )


class ForexFactoryCalendar:
    info = INFO

    def __init__(self, *, timeout: float = 20.0, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._timeout = timeout
        self._stato = StatoProvider(INFO)
        self._cache: Cache[CalendarSnapshot] = Cache(3_600, self._clock)

    def status(self) -> ProviderStatus:
        return self._stato.status()

    async def calendar(self) -> CalendarSnapshot:
        async def carica() -> CalendarSnapshot:
            testo = await scarica(URL, limite_secondi=self._timeout)
            return analizza_feed(testo, retrieved_at=self._clock.now())

        try:
            calendario = await self._cache.ottieni(carica)
        except ProviderError as errore:
            self._stato.errore(str(errore))
            raise
        adesso = self._clock.now()
        # Fuori dalla settimana coperta il calendario non vale più: è vecchio.
        self._stato.successo(
            calendario.retrieved_at,
            calendario.coverage_end,
            vecchio=adesso >= calendario.coverage_end,
        )
        return calendario
