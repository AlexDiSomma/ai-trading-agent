"""Il contesto globale visto da un singolo strumento.

È il passaggio `GlobalMarketContext → Asset Analysis` della pipeline: prima di
leggere il grafico di EURUSD si sa già se l'euro è forte, se il dollaro è forte e
in quale regime si trova il mercato. Non è un segnale: è lo sfondo su cui il
segnale della fase 7 dovrà essere coerente.
"""

from __future__ import annotations

from backend.calendar.risk import EventWindow
from backend.config.global_universe import VALUTE
from backend.core.enums import AssetClass, StrengthLabel
from backend.schemas.global_market import AssetGlobalBackdrop, GlobalMarketContext
from backend.schemas.instrument import Instrument


def sfondo(strumento: Instrument, contesto: GlobalMarketContext) -> AssetGlobalBackdrop:
    valute = contesto.currency_strength
    base = valute.of(strumento.base_currency)
    quotazione = valute.of(strumento.quote_currency)
    differenziale = (
        round(base.score - quotazione.score, 6)
        if base is not None
        and quotazione is not None
        and base.score is not None
        and quotazione.score is not None
        else None
    )

    gruppo: StrengthLabel | None = None
    note: list[str] = []
    if strumento.asset_class is AssetClass.INDEX:
        gruppo = contesto.equities.label
        note.append(f"Indici azionari nel complesso: {gruppo}.")
    elif strumento.asset_class is AssetClass.METAL:
        gruppo = contesto.metals.label
        note.append(f"Metalli nel complesso: {gruppo}.")
    if differenziale is not None:
        note.append(
            f"Forza {strumento.base_currency} {base.label if base else '?'} contro "  # type: ignore[union-attr]
            f"{strumento.quote_currency} {quotazione.label if quotazione else '?'} "
            f"(differenziale {differenziale:+.2f})."
        )
    # Valute dello strumento toccate dal calendario: le due del cambio, oppure la
    # valuta di quotazione per metalli e indici (l'oro reagisce ai dati americani).
    valute = tuple(
        dict.fromkeys(v for v in (strumento.base_currency, strumento.quote_currency) if v in VALUTE)
    )
    macro = contesto.macro_event_risk
    finestre: tuple[EventWindow, ...] = tuple(
        w for w in macro.active_windows if w.currency in valute or w.currency == "ALL"
    )
    prossimi = tuple(e for v, e in sorted(macro.next_high_impact.items()) if v in valute)
    if finestre:
        note.append(
            "Finestra di evento attiva: "
            + "; ".join(f"{w.currency} {w.title}" for w in finestre)
            + "."
        )
    elif not macro.calendar_available:
        note.append("Calendario economico non disponibile: rischio da eventi sconosciuto.")
    if not contesto.usable_for_decisions:
        note.append(
            "Contesto globale non utilizzabile per decidere: " + contesto.market_phase_reason
        )

    return AssetGlobalBackdrop(
        canonical=strumento.canonical,
        regime=contesto.regime,
        volatility_level=contesto.volatility.level,
        volatility_trend=contesto.volatility.trend,
        base_strength=base,
        quote_strength=quotazione,
        strength_differential=differenziale,
        group_direction=gruppo,
        event_windows=finestre,
        next_high_impact=prossimi,
        notes=tuple(note),
        global_data_quality=contesto.data_quality,
        global_usable_for_decisions=contesto.usable_for_decisions,
    )
