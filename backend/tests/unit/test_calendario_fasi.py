"""Calendario economico: fasi degli eventi, valore pubblicato, sorpresa, intercambiabilità.

Due fonti:

* il feed **reale** di Forex Factory registrato il 2026-10-05, che non contiene il
  valore pubblicato: dopo l'evento la fase è POST_EVENT, mai ACTUAL_AVAILABLE;
* un provider **di prova** (solo test) che pubblica i valori, per verificare che la
  macchina a stati e la sorpresa funzionino con un provider capace di fornirli, e che
  il provider sia intercambiabile senza toccare il resto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.calendar.risk import rischio_calendario, valore_numerico, vista_evento
from backend.core.enums import (
    DataFrequency,
    EventImportance,
    EventPhase,
    IntendedUse,
    ProviderKind,
    SourceDomain,
)
from backend.providers.base import EconomicCalendarProvider, ProviderInfo, StatoProvider
from backend.providers.forexfactory import analizza_feed
from backend.schemas.external import CalendarSnapshot, EconomicEvent, ProviderStatus

DATI = Path(__file__).parents[1] / "dati"
FOMC = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def reale() -> CalendarSnapshot:
    return analizza_feed(
        (DATI / "forexfactory_2026-10-05.json").read_text(encoding="utf-8"),
        retrieved_at=datetime(2026, 10, 5, 8, 0, tzinfo=UTC),
    )


def _fomc(cal: CalendarSnapshot) -> EconomicEvent:
    return next(e for e in cal.events if e.title == "FOMC Meeting Minutes")


@pytest.mark.parametrize(
    ("minuti", "fase"),
    [
        (-120, EventPhase.PRE_EVENT),
        (-61, EventPhase.PRE_EVENT),
        (-60, EventPhase.EVENT_WINDOW),
        (0, EventPhase.EVENT_WINDOW),
        (30, EventPhase.EVENT_WINDOW),
        (31, EventPhase.POST_EVENT),
        (600, EventPhase.POST_EVENT),
    ],
)
def test_fasi_sul_calendario_reale(reale: CalendarSnapshot, minuti: int, fase: EventPhase) -> None:
    """Finestra dell'utente: 60' prima, 30' dopo. Forex Factory non dà il valore
    pubblicato: dopo la finestra si resta POST_EVENT."""
    v = vista_evento(_fomc(reale), as_of=FOMC + timedelta(minutes=minuti), prima=60, dopo=30)
    assert v.phase is fase
    assert v.actual is None and v.surprise is None
    assert v.blocks_trading is True


def test_post_event_dichiara_il_valore_mancante(reale: CalendarSnapshot) -> None:
    v = vista_evento(_fomc(reale), as_of=FOMC + timedelta(hours=2), prima=60, dopo=30)
    assert "non disponibile" in v.surprise_note


# ======================================================================
# Provider di prova con valori pubblicati
# ======================================================================
class CalendarioConValori:
    """Solo per i test: un provider che, come FMP o Trading Economics, pubblica `actual`."""

    info = ProviderInfo(
        name="prova_con_actual",
        kind=ProviderKind.ECONOMIC_CALENDAR,
        official=False,
        requires_key=False,
        free=True,
        coverage="test",
        license_note="solo test",
        domain=SourceDomain.CALENDAR,
        frequency=DataFrequency.WEEKLY_SCHEDULE,
        publication_delay="actual pubblicato all'orario dell'evento",
        intended_use=IntendedUse.EVENT_RISK,
    )

    def __init__(self, letto_alle: datetime) -> None:
        self._stato = StatoProvider(self.info)
        self._letto = letto_alle

    def status(self) -> ProviderStatus:
        return self._stato.status()

    async def calendar(self) -> CalendarSnapshot:
        evento = EconomicEvent(
            event_id="nfp",
            title="Non-Farm Employment Change",
            currency="USD",
            importance=EventImportance.HIGH,
            scheduled_at=datetime(2026, 10, 9, 12, 30, tzinfo=UTC),
            forecast="120K",
            previous="95K",
            actual="168K",
            actual_seen_at=datetime(2026, 10, 9, 12, 30, 40, tzinfo=UTC),
            source=self.info.name,
        )
        self._stato.successo(self._letto, None)
        return CalendarSnapshot(
            provider=self.info.name,
            retrieved_at=self._letto,
            coverage_start=datetime(2026, 10, 4, 4, tzinfo=UTC),
            coverage_end=datetime(2026, 10, 11, 4, tzinfo=UTC),
            events=(evento,),
            has_actuals=True,
        )


async def test_provider_intercambiabile_rispetta_l_interfaccia() -> None:
    p = CalendarioConValori(datetime(2026, 10, 9, 13, 0, tzinfo=UTC))
    assert isinstance(p, EconomicCalendarProvider)
    cal = await p.calendar()
    assert cal.has_actuals is True


async def test_actual_available_e_sorpresa() -> None:
    cal = await CalendarioConValori(datetime(2026, 10, 9, 13, 0, tzinfo=UTC)).calendar()
    v = vista_evento(
        cal.events[0], as_of=datetime(2026, 10, 9, 12, 45, tzinfo=UTC), prima=60, dopo=30
    )
    assert v.phase is EventPhase.ACTUAL_AVAILABLE
    assert v.actual == "168K"
    assert v.surprise == pytest.approx(48_000)
    assert v.surprise_vs_previous == pytest.approx(73_000)


async def test_nessun_hindsight_sul_valore_pubblicato() -> None:
    """Nel replay a un istante prima dell'arrivo del valore, il valore non esiste."""
    cal = await CalendarioConValori(datetime(2026, 10, 9, 13, 0, tzinfo=UTC)).calendar()
    evento = cal.events[0]
    # Dentro la finestra ma 10 secondi prima che il valore arrivasse.
    prima = vista_evento(
        evento, as_of=datetime(2026, 10, 9, 12, 30, 30, tzinfo=UTC), prima=60, dopo=30
    )
    assert prima.phase is EventPhase.EVENT_WINDOW
    assert prima.actual is None and prima.surprise is None
    # Il giorno prima: solo programma, nessun valore.
    giorno_prima = vista_evento(
        evento, as_of=datetime(2026, 10, 8, 12, 0, tzinfo=UTC), prima=60, dopo=30
    )
    assert giorno_prima.phase is EventPhase.PRE_EVENT


async def test_eventi_nel_rischio_calendario_con_le_fasi() -> None:
    cal = await CalendarioConValori(datetime(2026, 10, 9, 13, 0, tzinfo=UTC)).calendar()
    r = rischio_calendario(
        cal, as_of=datetime(2026, 10, 9, 12, 50, tzinfo=UTC), prima_minuti=60, dopo_minuti=30
    )
    assert [e.phase for e in r.events] == [EventPhase.ACTUAL_AVAILABLE]
    assert r.active_windows  # la finestra di blocco resta attiva anche col valore noto


@pytest.mark.parametrize(
    ("testo", "atteso"),
    [
        ("9.0K", (9_000.0, "K")),
        ("-41.7K", (-41_700.0, "K")),
        ("6.5%", (6.5, "%")),
        ("1.2M", (1_200_000.0, "M")),
        ("<0.1%", (0.1, "%")),
        ("52.3", (52.3, "")),
        ("", None),
        (None, None),
        ("n/a", None),
    ],
)
def test_lettura_dei_valori(testo: str | None, atteso: tuple[float, str] | None) -> None:
    risultato = valore_numerico(testo)
    if atteso is None:
        assert risultato is None
    else:
        assert risultato is not None
        assert risultato[0] == pytest.approx(atteso[0]) and risultato[1] == atteso[1]


def test_sorpresa_solo_con_unita_coerenti() -> None:
    from backend.calendar.risk import sorpresa

    assert sorpresa("6.6%", "6.5%") == pytest.approx(0.1)
    assert sorpresa("9K", "6.5%") is None
