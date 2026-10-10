"""Rischio da calendario economico: finestre attive, prossimi eventi, livello.

Funzioni pure sul `CalendarSnapshot`: niente rete, niente orologio.

**Le finestre di blocco sono quelle dell'utente** (`RISK_BLOCK_HIGH_IMPACT_BEFORE_
MINUTES`, `..._AFTER_MINUTES`, oggi 60 e 30): un evento ad alto impatto apre una
finestra da 60 minuti prima a 30 minuti dopo. Qui la finestra si **misura e si
dichiara**; bloccare gli ingressi sarà compito del Risk Engine (fase 6).

**Livello**, nell'ordine:

* `UNKNOWN` — calendario assente, oppure la sua copertura finisce prima di 24 ore
  da adesso: "nessun evento" non vale oltre ciò che il calendario conosce;
* `HIGH` — almeno una finestra di blocco attiva adesso;
* `MODERATE` — un evento ad alto impatto nelle prossime 24 ore;
* `LOW` — copertura completa delle prossime 24 ore e nessun evento ad alto impatto.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from backend.config.global_universe import VALUTE
from backend.core.enums import EventImportance, EventPhase, UncertaintyLevel
from backend.schemas.external import CalendarSnapshot, EconomicEvent

_FROZEN = ConfigDict(frozen=True, extra="ignore")

ORIZZONTE = timedelta(hours=24)


class EventWindow(BaseModel):
    """Una finestra di blocco attorno a un evento ad alto impatto."""

    model_config = _FROZEN

    event_id: str
    title: str
    currency: str
    importance: EventImportance
    scheduled_at: datetime
    starts_at: datetime
    ends_at: datetime


class EventView(BaseModel):
    """Un evento visto all'istante di analisi: fase, finestra, sorpresa."""

    model_config = _FROZEN

    event: EconomicEvent
    phase: EventPhase
    window_start: datetime
    window_end: datetime
    #: Minuti all'orario dell'evento (negativi se già passato).
    minutes_to_event: float
    blocks_trading: bool
    #: Valore pubblicato, solo se già noto all'istante di analisi.
    actual: str | None = None
    #: Pubblicato − atteso, se entrambi numerici con la stessa unità.
    surprise: float | None = None
    surprise_vs_previous: float | None = None
    surprise_note: str = ""


class CalendarRisk(BaseModel):
    model_config = _FROZEN

    level: UncertaintyLevel
    reason: str
    covers_next_24h: bool = False
    active_windows: tuple[EventWindow, ...] = ()
    #: Eventi di importanza media o superiore fra 24 ore fa e 24 ore da adesso.
    events: tuple[EventView, ...] = ()
    upcoming: tuple[EconomicEvent, ...] = ()
    next_high_impact: dict[str, EconomicEvent] = {}


_SUFFISSI = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def valore_numerico(testo: str | None) -> tuple[float, str] | None:
    """'9.0K' → (9000, 'K'), '6.5%' → (6.5, '%'), '-41.7K' → (-41700, 'K'). Altrimenti None."""
    if not testo:
        return None
    t = testo.strip().replace(",", "").lstrip("<>~")
    unita = ""
    if t.endswith("%"):
        unita, t = "%", t[:-1]
    elif t and t[-1].upper() in _SUFFISSI:
        unita = t[-1].upper()
        t = t[:-1]
    try:
        numero = float(t)
    except ValueError:
        return None
    return numero * _SUFFISSI.get(unita, 1.0), unita


def sorpresa(attuale: str | None, atteso: str | None) -> float | None:
    a, b = valore_numerico(attuale), valore_numerico(atteso)
    if a is None or b is None or a[1] != b[1]:
        return None
    return round(a[0] - b[0], 10)


def vista_evento(evento: EconomicEvent, *, as_of: datetime, prima: int, dopo: int) -> EventView:
    """Fase dell'evento all'istante `as_of`. Il valore pubblicato conta solo se era
    già arrivato a noi in quell'istante: niente hindsight nei replay."""
    inizio = evento.scheduled_at - timedelta(minutes=prima)
    fine = evento.scheduled_at + timedelta(minutes=dopo)
    noto = (
        evento.actual is not None
        and evento.actual_seen_at is not None
        and evento.actual_seen_at <= as_of
        and as_of >= evento.scheduled_at
    )
    if noto:
        fase = EventPhase.ACTUAL_AVAILABLE
    elif as_of < inizio:
        fase = EventPhase.PRE_EVENT
    elif as_of <= fine:
        fase = EventPhase.EVENT_WINDOW
    else:
        fase = EventPhase.POST_EVENT
    s_attese = sorpresa(evento.actual, evento.forecast) if noto else None
    s_prec = sorpresa(evento.actual, evento.previous) if noto else None
    nota = ""
    if fase is EventPhase.POST_EVENT:
        nota = f"Evento passato; valore pubblicato non disponibile dalla fonte {evento.source}."
    elif noto and s_attese is None:
        nota = "Valore pubblicato non confrontabile con l'atteso (formato o unità diversi)."
    return EventView(
        event=evento,
        phase=fase,
        window_start=inizio,
        window_end=fine,
        minutes_to_event=round((evento.scheduled_at - as_of).total_seconds() / 60, 1),
        blocks_trading=evento.importance.blocks_trading,
        actual=evento.actual if noto else None,
        surprise=s_attese,
        surprise_vs_previous=s_prec,
        surprise_note=nota,
    )


def finestra(evento: EconomicEvent, prima: int, dopo: int) -> EventWindow:
    return EventWindow(
        event_id=evento.event_id,
        title=evento.title,
        currency=evento.currency,
        importance=evento.importance,
        scheduled_at=evento.scheduled_at,
        starts_at=evento.scheduled_at - timedelta(minutes=prima),
        ends_at=evento.scheduled_at + timedelta(minutes=dopo),
    )


def valute_interessate(evento: EconomicEvent) -> tuple[str, ...]:
    """Valute toccate da un evento: la sua, o tutte le otto per un evento globale."""
    if evento.currency == "ALL":
        return VALUTE
    return (evento.currency,) if evento.currency in VALUTE else ()


def rischio_calendario(
    calendario: CalendarSnapshot | None,
    *,
    as_of: datetime,
    prima_minuti: int,
    dopo_minuti: int,
    motivo_assenza: str = "calendario economico non disponibile",
) -> CalendarRisk:
    if calendario is None:
        return CalendarRisk(
            level=UncertaintyLevel.UNKNOWN, reason=motivo_assenza.capitalize() + "."
        )

    fine_orizzonte = as_of + ORIZZONTE
    copre = calendario.coverage_start <= as_of and calendario.coverage_end >= fine_orizzonte
    bloccanti = [
        e for e in calendario.events if e.importance.blocks_trading and valute_interessate(e)
    ]

    attive = tuple(
        finestra(e, prima_minuti, dopo_minuti)
        for e in bloccanti
        if e.scheduled_at - timedelta(minutes=prima_minuti)
        <= as_of
        <= e.scheduled_at + timedelta(minutes=dopo_minuti)
    )
    prossimi_bloccanti = [e for e in bloccanti if as_of < e.scheduled_at <= fine_orizzonte]
    prossimi = tuple(
        e
        for e in calendario.events
        if as_of <= e.scheduled_at <= fine_orizzonte
        and e.importance in (EventImportance.MEDIUM, EventImportance.HIGH, EventImportance.CRITICAL)
    )
    per_valuta: dict[str, EconomicEvent] = {}
    for e in bloccanti:
        if e.scheduled_at <= as_of:
            continue
        for v in valute_interessate(e):
            if v not in per_valuta or e.scheduled_at < per_valuta[v].scheduled_at:
                per_valuta[v] = e

    if attive:
        livello = UncertaintyLevel.HIGH
        motivo = "Finestra di blocco attiva: " + "; ".join(
            f"{w.currency} {w.title} ({w.scheduled_at:%d/%m %H:%M} UTC)" for w in attive
        )
    elif not copre:
        livello = UncertaintyLevel.UNKNOWN
        motivo = (
            f"Il calendario copre fino a {calendario.coverage_end:%d/%m %H:%M} UTC: le prossime "
            "24 ore non sono interamente note."
        )
        if prossimi_bloccanti:
            livello = UncertaintyLevel.MODERATE
            motivo += " Evento ad alto impatto entro la parte coperta."
    elif prossimi_bloccanti:
        livello = UncertaintyLevel.MODERATE
        primo = min(prossimi_bloccanti, key=lambda e: e.scheduled_at)
        motivo = (
            f"Prossimo evento ad alto impatto: {primo.currency} {primo.title} alle "
            f"{primo.scheduled_at:%d/%m %H:%M} UTC."
        )
    else:
        livello = UncertaintyLevel.LOW
        motivo = "Nessun evento ad alto impatto nelle prossime 24 ore (calendario completo)."

    vicini = tuple(
        vista_evento(e, as_of=as_of, prima=prima_minuti, dopo=dopo_minuti)
        for e in calendario.events
        if as_of - ORIZZONTE <= e.scheduled_at <= fine_orizzonte
        and e.importance in (EventImportance.MEDIUM, EventImportance.HIGH, EventImportance.CRITICAL)
    )
    return CalendarRisk(
        level=livello,
        reason=motivo,
        covers_next_24h=copre,
        active_windows=attive,
        events=vicini,
        upcoming=prossimi,
        next_high_impact=dict(sorted(per_valuta.items())),
    )


def finestre_per_valute(rischio: CalendarRisk, valute: Sequence[str]) -> tuple[EventWindow, ...]:
    """Le finestre attive che toccano una delle valute indicate."""
    return tuple(w for w in rischio.active_windows if w.currency in valute or w.currency == "ALL")
