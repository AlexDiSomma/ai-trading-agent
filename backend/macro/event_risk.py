"""Rischio macro ed eventi (rivisto in fase 4.5).

Due misure tenute separate (vedi `schemas.global_market.EventRisk`):

* **dal calendario** — finestre di blocco attive e eventi ad alto impatto nelle
  prossime 24 ore (`calendar/risk.py`). Senza calendario, o oltre la sua copertura,
  vale `UNKNOWN`: l'assenza di informazione non è rassicurante;
* **implicita nei prezzi** — punteggio semplice e leggibile:

  | indizio | punti |
  |---|---|
  | volatilità (realizzata o VIX, la più alta) ELEVATED / EXTREME | 1 / 2 |
  | volatilità realizzata o VIX in espansione | 1 |
  | shock di volatilità | 2 |
  | intermarket DIVERGENT, o maggioranza instabile | 1 |
  | almeno un conflitto HIGH, o due MEDIUM | 1 |

  0 → LOW, 1-2 → MODERATE, ≥3 → HIGH. Nessuna misura di volatilità → UNKNOWN.

Qualità: `GOOD` solo con un calendario disponibile che copre le prossime 24 ore,
altrimenti `DEGRADED`.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.calendar.risk import CalendarRisk
from backend.core.enums import (
    ConflictSeverity,
    GlobalDataQuality,
    IntermarketState,
    ProviderState,
    UncertaintyLevel,
    VolatilityLevel,
    VolatilityTrend,
)
from backend.schemas.external import CalendarSnapshot
from backend.schemas.global_market import (
    Conflict,
    DimensionQuality,
    EventRisk,
    ImpliedVolatility,
    IntermarketView,
    VolatilityView,
)

_ORDINE = [
    VolatilityLevel.UNKNOWN,
    VolatilityLevel.LOW,
    VolatilityLevel.NORMAL,
    VolatilityLevel.ELEVATED,
    VolatilityLevel.EXTREME,
]


def rischio_eventi(
    *,
    calendario: CalendarSnapshot | None,
    stato_calendario: ProviderState,
    nome_calendario: str,
    rischio_calendario: CalendarRisk,
    volatilita: VolatilityView,
    implicita: ImpliedVolatility | None,
    intermarket: IntermarketView,
    conflitti: Sequence[Conflict],
) -> EventRisk:
    indizi: list[str] = []
    punti = 0
    livelli = [volatilita.level] + ([implicita.level] if implicita else [])
    massimo = max(livelli, key=_ORDINE.index)
    if massimo is VolatilityLevel.EXTREME:
        punti += 2
        indizi.append("volatilità estrema")
    elif massimo is VolatilityLevel.ELEVATED:
        punti += 1
        indizi.append("volatilità elevata")
    if volatilita.trend is VolatilityTrend.EXPANDING or (
        implicita is not None and implicita.trend is VolatilityTrend.EXPANDING
    ):
        punti += 1
        indizi.append("volatilità in espansione")
    if volatilita.shock:
        punti += 2
        indizi.append("shock di volatilità")
    if intermarket.state is IntermarketState.DIVERGENT:
        punti += 1
        indizi.append("relazioni intermarket divergenti dalla loro storia")
    elif intermarket.unstable * 2 > max(1, len(intermarket.relationships)):
        punti += 1
        indizi.append("relazioni intermarket instabili")
    gravi = sum(1 for c in conflitti if c.severity is ConflictSeverity.HIGH)
    medi = sum(1 for c in conflitti if c.severity is ConflictSeverity.MEDIUM)
    if gravi or medi >= 2:
        punti += 1
        indizi.append("evidenze in conflitto fra loro")

    if massimo is VolatilityLevel.UNKNOWN:
        implicita_livello = UncertaintyLevel.UNKNOWN
    elif punti >= 3:
        implicita_livello = UncertaintyLevel.HIGH
    elif punti >= 1:
        implicita_livello = UncertaintyLevel.MODERATE
    else:
        implicita_livello = UncertaintyLevel.LOW

    disponibile = calendario is not None
    if disponibile and rischio_calendario.covers_next_24h:
        qualita = DimensionQuality(
            dimension="macro_event_risk", quality=GlobalDataQuality.GOOD, coverage=1.0
        )
    else:
        qualita = DimensionQuality(
            dimension="macro_event_risk",
            quality=GlobalDataQuality.DEGRADED,
            coverage=0.5 if disponibile else 0.0,
            reasons=(rischio_calendario.reason,),
        )
    return EventRisk(
        calendar_provider=nome_calendario,
        calendar_state=stato_calendario,
        calendar_available=disponibile,
        calendar_coverage_end=calendario.coverage_end if calendario else None,
        calendar_has_actuals=calendario.has_actuals if calendario else False,
        calendar_level=rischio_calendario.level,
        calendar_reason=rischio_calendario.reason,
        active_windows=rischio_calendario.active_windows,
        upcoming_events=rischio_calendario.upcoming,
        events=rischio_calendario.events,
        next_high_impact=rischio_calendario.next_high_impact,
        market_implied_uncertainty=implicita_livello,
        drivers=tuple(indizi),
        quality=qualita,
    )
