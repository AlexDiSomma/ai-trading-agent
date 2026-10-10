"""Fonti del contesto globale: catalogo, qualità per dominio, qualità complessiva (ADR 0021).

**Niente più una sola qualità globale.** Ogni dominio di provenienza ha la sua:

| Dominio | Cosa contiene | Da dove |
|---|---|---|
| `BROKER_DATA` | 38 strumenti di riferimento, candele e quotazioni | XM/MT5 |
| `MARKET_REFERENCE` | VIX; DXY ricostruito (solo verifica incrociata) | CBOE; derivato |
| `CALENDAR` | eventi macro, finestre di blocco | Forex Factory |
| `MACRO` | curva dei rendimenti; serie macro | Tesoro USA; nessuno |
| `NEWS` | notizie | nessuno (rinviate) |

**Qualità complessiva del contesto**, regola esplicita:

1. parte dalla qualità di `BROKER_DATA` (il nucleo: senza i prezzi non c'è contesto);
2. `CALENDAR` diversa da GOOD la porta al massimo a DEGRADED: senza sapere quando
   arrivano gli eventi, il contesto non è completo per chi deciderà;
3. una fonte **configurata** che fallisce o è vecchia (`FAILED`, `STALE`) la porta al
   massimo a DEGRADED: qualcosa che doveva esserci non c'è;
4. una fonte **non configurata per scelta** (macro, news) **non** la peggiora: è una
   limitazione nota, dichiarata in `known_limitations`, non un guasto.

Ogni fonte dichiara frequenza, ultima osservazione, ritardo di pubblicazione e uso
previsto. I dati giornalieri (VIX, Tesoro) sono `STRUCTURAL_CONTEXT`: descrivono lo
sfondo dei giorni, non quello delle ore.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.core.enums import (
    DataCategory,
    DataFrequency,
    GlobalDataQuality,
    IntendedUse,
    ProviderKind,
    ProviderState,
    SourceDomain,
)
from backend.global_market.qualita import peggiore
from backend.schemas.external import ProviderStatus, SourceDescriptor
from backend.schemas.global_market import (
    DimensionQuality,
    DollarIndexView,
    EventRisk,
    ImpliedVolatility,
    RatesView,
    ReferenceInstrumentStatus,
    SourceQuality,
)

DXY_COMPONENTI = ("EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF")


def _stato(stati: Sequence[ProviderStatus], tipo: ProviderKind) -> ProviderStatus | None:
    return next((s for s in stati if s.kind is tipo), None)


def catalogo(
    strumenti: Sequence[ReferenceInstrumentStatus],
    stati: Sequence[ProviderStatus],
    *,
    vix_osservazioni: int,
    vix_ultima: object,
    curva_osservazioni: int,
    curva_ultima: object,
    eventi: int,
) -> tuple[SourceDescriptor, ...]:
    """Ogni dato usato, con categoria, fonte, dominio, frequenza e uso previsto."""
    voci = [
        SourceDescriptor(
            id=s.canonical,
            name=s.broker_symbol,
            category=s.category,
            source=s.source,
            domain=SourceDomain.BROKER_DATA,
            frequency=DataFrequency.INTRADAY_BARS,
            intended_use=IntendedUse.INTRADAY_CONTEXT,
            publication_delay="in tempo reale (barre chiuse M15, H1, H4, D1)",
            tradable_on_broker=True,
            state=(
                ProviderState.AVAILABLE
                if any(x.status.usable for x in s.series)
                else ProviderState.FAILED
            ),
            observations=sum(x.bars for x in s.series),
            last_observation=max(
                (x.last_bar_time for x in s.series if x.last_bar_time), default=None
            ),
        )
        for s in strumenti
    ]

    def voce(
        tipo: ProviderKind,
        *,
        id: str,  # noqa: A002 - nome del campo nel modello
        name: str,
        category: DataCategory,
        observations: int,
        last: object,
        note: str = "",
    ) -> SourceDescriptor:
        st = _stato(stati, tipo)
        return SourceDescriptor(
            id=id,
            name=name,
            category=category,
            source=st.name if st else "nessuno",
            domain=st.domain if st else _DOMINIO[tipo],
            frequency=st.frequency if st else DataFrequency.NONE,
            intended_use=st.intended_use if st else _USO[tipo],
            publication_delay=st.publication_delay if st else "",
            state=st.state if st else ProviderState.NOT_CONFIGURED,
            observations=observations,
            last_observation=last,  # type: ignore[arg-type]
            note=note,
        )

    voci.append(
        voce(
            ProviderKind.MARKET_REFERENCE,
            id="VIX",
            name="CBOE Volatility Index",
            category=DataCategory.REFERENCE_INSTRUMENT,
            observations=vix_osservazioni,
            last=vix_ultima,
            note="Su XM solo come future con scadenza. Giornaliero: contesto strutturale.",
        )
    )
    for scadenza in ("3M", "2Y", "10Y"):
        voci.append(
            voce(
                ProviderKind.RATES,
                id=f"UST{scadenza}",
                name=f"Rendimento Titoli di Stato USA {scadenza}",
                category=DataCategory.MACRO_SERIES,
                observations=curva_osservazioni,
                last=curva_ultima,
            )
        )
    componenti_ok = all(
        any(v.id == c and v.state is ProviderState.AVAILABLE for v in voci) for c in DXY_COMPONENTI
    )
    voci.append(
        SourceDescriptor(
            id="DXY",
            name="US Dollar Index (ricostruito)",
            category=DataCategory.REFERENCE_INSTRUMENT,
            source="derivato da XM (formula ICE)",
            domain=SourceDomain.MARKET_REFERENCE,
            frequency=DataFrequency.INTRADAY_BARS,
            # Correzione pre-fase 5: verifica incrociata, non fonte primaria.
            intended_use=IntendedUse.CROSS_CHECK,
            publication_delay="come i sei cambi XM da cui è calcolato",
            observations=min((v.observations for v in voci if v.id in DXY_COMPONENTI), default=0),
            state=ProviderState.AVAILABLE if componenti_ok else ProviderState.FAILED,
            note="Verifica la forza USD del grafo valutario; non entra nel regime.",
        )
    )
    voci.append(
        voce(
            ProviderKind.ECONOMIC_CALENDAR,
            id="CALENDAR",
            name="Calendario economico",
            category=DataCategory.MACRO_SERIES,
            observations=eventi,
            last=None,
        )
    )
    return tuple(voci)


_USO = {
    ProviderKind.MARKET_REFERENCE: IntendedUse.STRUCTURAL_CONTEXT,
    ProviderKind.ECONOMIC_CALENDAR: IntendedUse.EVENT_RISK,
    ProviderKind.RATES: IntendedUse.STRUCTURAL_CONTEXT,
    ProviderKind.MACRO_DATA: IntendedUse.STRUCTURAL_CONTEXT,
    ProviderKind.NEWS: IntendedUse.EVENT_RISK,
}

_DOMINIO = {
    ProviderKind.MARKET_REFERENCE: SourceDomain.MARKET_REFERENCE,
    ProviderKind.ECONOMIC_CALENDAR: SourceDomain.CALENDAR,
    ProviderKind.RATES: SourceDomain.MACRO,
    ProviderKind.MACRO_DATA: SourceDomain.MACRO,
    ProviderKind.NEWS: SourceDomain.NEWS,
}


def _cattivi(stati: Sequence[ProviderStatus], dominio: SourceDomain) -> list[ProviderStatus]:
    return [
        s
        for s in stati
        if s.domain is dominio and s.state in (ProviderState.FAILED, ProviderState.STALE)
    ]


def qualita_per_dominio(
    *,
    broker: Sequence[DimensionQuality],
    stati: Sequence[ProviderStatus],
    implicita: ImpliedVolatility,
    dollaro: DollarIndexView,
    eventi: EventRisk,
    tassi: RatesView,
) -> tuple[SourceQuality, ...]:
    centrali = {"currency_strength", "volatility"}
    q_broker = peggiore(*(d.quality for d in broker if d.dimension in centrali))
    secondarie = peggiore(*(d.quality for d in broker if d.dimension not in centrali))
    if q_broker is GlobalDataQuality.GOOD and secondarie is not GlobalDataQuality.GOOD:
        q_broker = GlobalDataQuality.DEGRADED
    motivi_broker = tuple(dict.fromkeys(r for d in broker for r in d.reasons))

    def configurato(tipo: ProviderKind) -> bool:
        st = _stato(stati, tipo)
        return st is not None and st.state is not ProviderState.NOT_CONFIGURED

    rif_motivi = list(implicita.quality.reasons)
    if dollaro.quality.quality is not GlobalDataQuality.GOOD:
        rif_motivi.append("DXY ricostruito non disponibile (solo verifica incrociata).")
    macro_conf = configurato(ProviderKind.RATES) or configurato(ProviderKind.MACRO_DATA)
    macro_motivi = list(tassi.quality.reasons)
    q_macro = tassi.quality.quality
    if not configurato(ProviderKind.MACRO_DATA):
        macro_motivi.append(
            "Nessuna serie macro (inflazione, occupazione, PIL): provider non configurato."
        )
        if q_macro is GlobalDataQuality.GOOD:
            q_macro = GlobalDataQuality.DEGRADED

    return (
        SourceQuality(
            domain=SourceDomain.BROKER_DATA,
            quality=q_broker,
            configured=True,
            providers=("xm/mt5",),
            intended_use=IntendedUse.INTRADAY_CONTEXT,
            reasons=motivi_broker,
        ),
        SourceQuality(
            domain=SourceDomain.MARKET_REFERENCE,
            quality=implicita.quality.quality,
            configured=configurato(ProviderKind.MARKET_REFERENCE),
            providers=(
                *(s.name for s in stati if s.domain is SourceDomain.MARKET_REFERENCE),
                "derivato:DXY",
            ),
            intended_use=IntendedUse.STRUCTURAL_CONTEXT,
            reasons=tuple(rif_motivi),
        ),
        SourceQuality(
            domain=SourceDomain.CALENDAR,
            quality=eventi.quality.quality,
            configured=configurato(ProviderKind.ECONOMIC_CALENDAR),
            providers=(eventi.calendar_provider,),
            intended_use=IntendedUse.EVENT_RISK,
            reasons=eventi.quality.reasons,
        ),
        SourceQuality(
            domain=SourceDomain.MACRO,
            quality=q_macro,
            configured=macro_conf,
            providers=tuple(s.name for s in stati if s.domain is SourceDomain.MACRO),
            intended_use=IntendedUse.STRUCTURAL_CONTEXT,
            reasons=tuple(macro_motivi),
        ),
        _qualita_notizie(stati, configurato(ProviderKind.NEWS)),
    )


def _qualita_notizie(stati: Sequence[ProviderStatus], configurata: bool) -> SourceQuality:
    """Dallo stato reale del servizio notizie (feed RSS), non da un valore fisso."""
    fonti = [
        s
        for s in stati
        if s.domain is SourceDomain.NEWS and s.state is not ProviderState.NOT_CONFIGURED
    ]
    if not configurata or not fonti:
        return SourceQuality(
            domain=SourceDomain.NEWS,
            quality=GlobalDataQuality.INSUFFICIENT,
            configured=False,
            providers=("nessuno",),
            intended_use=IntendedUse.EVENT_RISK,
            reasons=("Nessuna fonte di notizie configurata.",),
        )
    f = fonti[0]
    qualita = {
        ProviderState.AVAILABLE: GlobalDataQuality.GOOD,
        ProviderState.STALE: GlobalDataQuality.DEGRADED,
    }.get(f.state, GlobalDataQuality.INSUFFICIENT)
    motivo = f"{f.name}: {f.state}" + (f" — {f.last_error}" if f.last_error else "")
    return SourceQuality(
        domain=SourceDomain.NEWS,
        quality=qualita,
        configured=True,
        providers=(f.name,),
        intended_use=IntendedUse.EVENT_RISK,
        reasons=(motivo,),
    )


def qualita_complessiva(
    domini: Sequence[SourceQuality], stati: Sequence[ProviderStatus]
) -> tuple[GlobalDataQuality, tuple[str, ...]]:
    per = {d.domain: d for d in domini}
    complessiva = per[SourceDomain.BROKER_DATA].quality
    motivi: list[str] = [f"Dati del broker: {complessiva}."]
    calendario = per[SourceDomain.CALENDAR]
    if calendario.quality is not GlobalDataQuality.GOOD and complessiva is GlobalDataQuality.GOOD:
        complessiva = GlobalDataQuality.DEGRADED
        motivi.append("Calendario non completo per le prossime 24 ore: al massimo DEGRADED.")
    guasti = [s for s in stati if s.state in (ProviderState.FAILED, ProviderState.STALE)]
    if guasti and complessiva is GlobalDataQuality.GOOD:
        complessiva = GlobalDataQuality.DEGRADED
    for s in guasti:
        motivi.append(f"Fonte configurata {s.name} in stato {s.state}: al massimo DEGRADED.")
    return complessiva, tuple(motivi)


def limitazioni(
    domini: Sequence[SourceQuality],
    eventi: EventRisk,
    stati: Sequence[ProviderStatus],
) -> tuple[str, ...]:
    """Ciò che il contesto **non può** sapere, in frasi esplicite (servono all'AI)."""
    voci: list[str] = []
    per = {d.domain: d for d in domini}
    if not per[SourceDomain.CALENDAR].configured:
        voci.append("Nessun calendario economico: rischio da eventi sconosciuto.")
    else:
        if not eventi.calendar_has_actuals:
            voci.append(
                "Il calendario non fornisce i valori pubblicati (actual): la sorpresa dei dati "
                "macro rispetto alle attese non è misurabile."
            )
        if eventi.calendar_coverage_end is not None:
            voci.append(
                f"Il calendario copre solo fino a {eventi.calendar_coverage_end:%d/%m %H:%M} UTC."
            )
    if not any(
        s.kind is ProviderKind.MACRO_DATA and s.state is not ProviderState.NOT_CONFIGURED
        for s in stati
    ):
        voci.append(
            "Nessuna serie macro (inflazione, occupazione, PIL, politica monetaria): il regime "
            "macro non è osservato, solo i prezzi."
        )
    voci.append("Rendimenti solo USA: nessun differenziale di tasso con EUR, GBP, JPY, CHF, AUD.")
    voci.append(
        "VIX e rendimenti sono giornalieri (contesto strutturale): non descrivono le ultime ore."
    )
    if not per[SourceDomain.NEWS].configured:
        voci.append(
            "Nessuna fonte di notizie: eventi non in calendario (dichiarazioni, geopolitica) "
            "sono invisibili."
        )
    else:
        voci.append(
            "Notizie solo da feed RSS gratuiti (titoli ed estratti, ritardo di minuti): nessuna "
            "agenzia in tempo reale."
        )
    voci.append(
        "Nessun dato di posizionamento (COT, flussi) né di volumi reali: il forex su MT5 ha "
        "solo volumi di tick."
    )
    return tuple(voci)
