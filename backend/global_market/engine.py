"""Motore del contesto globale: da serie di riferimento a `GlobalMarketContext`.

Funzione pura. Niente orologio, niente broker, niente casualità: riceve le serie,
gli stati e l'istante di analisi, e restituisce snapshot e contesto. È ciò che lo
rende **riproducibile** (stesso ingresso → stesso contesto, verificato da test) e
riusabile nel backtest della fase 9 senza modifiche.

Pipeline, nell'ordine richiesto:

    Market Data → Global Regime → Currency Strength → Intermarket → Volatility
    → Relative Strength → GlobalMarketContext → (Asset Analysis)

Il regime viene *deciso* per ultimo perché dipende da tutte le dimensioni, ma
nell'oggetto finale sta in cima: è la prima cosa che si legge.

**Fase 4.5 — fonti oltre XM** (ADR 0018). Oltre alle serie MT5 il motore riceve
`DatiEsterni`: VIX (CBOE), curva dei rendimenti (Tesoro USA), calendario
economico (Forex Factory), con lo stato di ogni fonte. Le osservazioni esterne
sono filtrate per **disponibilità** all'istante di analisi: nessun look-ahead
nemmeno da lì. Una fonte assente rende la sua dimensione `INSUFFICIENT` e lo
dichiara; non ferma il resto.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from backend.calendar.risk import rischio_calendario
from backend.config.global_universe import UNIVERSO, del_gruppo
from backend.core.enums import (
    GlobalDataQuality,
    MarketPhase,
    MarketStatus,
    ProviderKind,
    ProviderState,
    QuoteQualityStatus,
    ReferenceGroup,
    SeriesStatus,
    StrengthLabel,
    Timeframe,
)
from backend.global_market import parametri as par
from backend.global_market import sources as fonti
from backend.global_market.currency_strength import etichetta, forza_valute, serie_valute
from backend.global_market.external_dims import (
    DXY_PESI,
    indice_dollaro,
    tassi,
    volatilita_implicita,
)
from backend.global_market.features import (
    Misure,
    SerieDiRiferimento,
    composito,
    concordi,
    misura,
    taglia,
)
from backend.global_market.qualita import al_massimo
from backend.global_market.relative_strength import (
    classifica,
    direzione_gruppo,
    etichetta_strumento,
)
from backend.global_market.risk import conflitti, evidenze, regime, sentiment
from backend.global_market.volatility import regime_volatilita
from backend.intermarket.engine import analizza
from backend.macro.event_risk import rischio_eventi
from backend.schemas.external import (
    CalendarSnapshot,
    ExternalSeries,
    ProviderStatus,
    YieldCurveHistory,
)
from backend.schemas.global_market import (
    CrossCheck,
    GlobalMarketContext,
    GlobalMarketSnapshot,
    InstrumentReading,
    ReferenceInstrumentStatus,
    ReferenceSeriesStatus,
    SummaryLine,
)


@dataclass(frozen=True)
class IngressoStrumento:
    """Ciò che il lettore ha raccolto per uno strumento di riferimento."""

    canonical: str
    broker_symbol: str
    group: ReferenceGroup
    market_status: MarketStatus = MarketStatus.UNKNOWN
    quote_status: QuoteQualityStatus | None = None
    #: Stato di ogni serie **prima** del taglio: FRESH/UNVERIFIED/CLOSED/STALE/MISSING.
    stati: Mapping[Timeframe, SeriesStatus] = field(default_factory=dict)
    serie: Mapping[Timeframe, SerieDiRiferimento] = field(default_factory=dict)
    eta: Mapping[Timeframe, float | None] = field(default_factory=dict)
    note: Mapping[Timeframe, str] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class DatiEsterni:
    """Dati da fonti diverse da XM, con lo stato di ogni fonte (fase 4.5)."""

    vix: ExternalSeries | None = None
    curva: YieldCurveHistory | None = None
    calendario: CalendarSnapshot | None = None
    stati: tuple[ProviderStatus, ...] = ()
    #: Finestra di blocco attorno agli eventi ad alto impatto (limiti dell'utente).
    prima_minuti: int = 60
    dopo_minuti: int = 30

    def stato(self, tipo: ProviderKind) -> ProviderStatus | None:
        return next((s for s in self.stati if s.kind is tipo), None)


def _impronta(
    serie: Mapping[tuple[str, Timeframe], SerieDiRiferimento], esterni: tuple[str, ...] = ()
) -> str:
    """sha256 delle barre usate: identifica esattamente l'ingresso del contesto."""
    h = hashlib.sha256(par.VERSIONE_MOTORE.encode())
    for voce in esterni:
        h.update(f"|esterno|{voce}".encode())
    for canonico, tf in sorted(serie, key=lambda k: (k[0], str(k[1]))):
        s = serie[(canonico, tf)]
        h.update(f"|{canonico}|{tf}|{len(s)}".encode())
        for t, hi, lo, c in zip(s.tempi, s.massimi, s.minimi, s.chiusure, strict=True):
            h.update(f"{t.isoformat()},{hi!r},{lo!r},{c!r};".encode())
    return h.hexdigest()


def _fase(stati: Sequence[MarketStatus]) -> tuple[MarketPhase, str]:
    if not stati:
        return MarketPhase.UNKNOWN, "Nessuno strumento di riferimento letto."
    aperti = sum(1 for s in stati if s is MarketStatus.OPEN_CONFIRMED)
    chiusi = sum(1 for s in stati if s is MarketStatus.CLOSED_INFERRED)
    totale = len(stati)
    if aperti / totale >= 0.8:
        return MarketPhase.OPEN_CONFIRMED, f"{aperti} strumenti su {totale} con quotazione fresca."
    if chiusi == totale:
        return (
            MarketPhase.CLOSED_INFERRED,
            f"MERCATO CHIUSO (dedotto): tutti i {totale} strumenti hanno quotazioni ferme. "
            "Il contesto descrive l'ultima sessione.",
        )
    if aperti:
        return (
            MarketPhase.PARTIALLY_OPEN,
            f"Mercato parzialmente aperto: {aperti} strumenti su {totale} quotano, "
            f"{chiusi} dedotti chiusi, {totale - aperti - chiusi} senza quotazione recente "
            "(stato ignoto).",
        )
    return (
        MarketPhase.UNKNOWN,
        "Nessuna quotazione fresca confermata: stato del mercato non determinabile "
        "(ora broker non misurata o flusso prezzi fermo).",
    )


def costruisci(
    ingressi: Sequence[IngressoStrumento],
    *,
    as_of: datetime,
    limite_broker: datetime | None,
    broker_offset_seconds: float | None = None,
    broker_time_source: str = "UNAVAILABLE",
    simulated: bool = False,
    source: str = "mt5",
    esterni: DatiEsterni | None = None,
) -> tuple[GlobalMarketSnapshot, GlobalMarketContext]:
    """Snapshot e contesto globale all'istante `as_of`.

    `limite_broker` è lo stesso istante in ora broker: ogni barra che finisce dopo
    viene scartata (nessun look-ahead). `None` solo se l'ora broker è ignota: in
    quel caso si usano le barre già marcate chiuse dal connettore.
    """
    ordinati = sorted(ingressi, key=lambda i: i.canonical)
    esterni = esterni or DatiEsterni()
    # Solo osservazioni esterne già pubbliche ad `as_of`: nessun look-ahead.
    vix_obs = esterni.vix.available(as_of) if esterni.vix else ()
    curva_obs = esterni.curva.available(as_of) if esterni.curva else ()

    # --- 1. taglio temporale e stato definitivo di ogni serie ---
    usabili: dict[tuple[str, Timeframe], SerieDiRiferimento] = {}
    stati: dict[str, dict[Timeframe, SeriesStatus]] = {}
    stati_strumenti: list[ReferenceInstrumentStatus] = []
    for ing in ordinati:
        stati[ing.canonical] = {}
        righe: list[ReferenceSeriesStatus] = []
        for tf in par.TIMEFRAMES:
            stato = ing.stati.get(tf, SeriesStatus.MISSING)
            serie = ing.serie.get(tf)
            nota = ing.note.get(tf, "")
            tagliata = taglia(serie, limite_broker) if serie is not None else None
            barre = len(tagliata) if tagliata is not None else 0
            if stato.usable and barre < par.BARRE_MINIME:
                stato = SeriesStatus.INSUFFICIENT_HISTORY
                nota = f"{barre} barre chiuse, minimo {par.BARRE_MINIME}."
            if stato.usable and tagliata is not None:
                usabili[(ing.canonical, tf)] = tagliata
            stati[ing.canonical][tf] = stato
            righe.append(
                ReferenceSeriesStatus(
                    timeframe=tf,
                    status=stato,
                    bars=barre,
                    last_bar_time=tagliata.tempi[-1] if tagliata and barre else None,
                    last_bar_age_seconds=ing.eta.get(tf),
                    note=nota,
                )
            )
        stati_strumenti.append(
            ReferenceInstrumentStatus(
                canonical=ing.canonical,
                broker_symbol=ing.broker_symbol,
                group=ing.group,
                market_status=ing.market_status,
                quote_status=ing.quote_status,
                series=tuple(righe),
                reason=ing.reason,
            )
        )

    fase, motivo_fase = _fase([i.market_status for i in ordinati])
    impronta_esterni = (
        *(f"VIX|{o.day}|{o.close!r}" for o in vix_obs),
        *(f"UST|{q.day}|{sorted(q.yields.items())!r}" for q in curva_obs),
        *(
            f"CAL|{e.event_id}|{e.scheduled_at.isoformat()}|{e.importance}"
            for e in (esterni.calendario.events if esterni.calendario else ())
        ),
    )
    snapshot = GlobalMarketSnapshot(
        as_of=as_of,
        broker_time_offset_seconds=broker_offset_seconds,
        broker_time_source=broker_time_source,
        market_phase=fase,
        market_phase_reason=motivo_fase,
        instruments=tuple(stati_strumenti),
        input_fingerprint=_impronta(usabili, impronta_esterni),
        simulated=simulated,
        source=source,
        sources=fonti.catalogo(
            stati_strumenti,
            esterni.stati,
            vix_osservazioni=len(vix_obs),
            vix_ultima=vix_obs[-1].day if vix_obs else None,
            curva_osservazioni=len(curva_obs),
            curva_ultima=curva_obs[-1].day if curva_obs else None,
            eventi=len(esterni.calendario.events) if esterni.calendario else 0,
        ),
        providers=esterni.stati,
    )

    # --- 2. misure per serie ---
    misure: dict[str, dict[Timeframe, Misure]] = {}
    for (canonico, tf), serie in sorted(usabili.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        misure.setdefault(canonico, {})[tf] = misura(serie)

    # --- 3. dimensioni ---
    stimate_valute = {tf: serie_valute(usabili, tf) for tf in par.TIMEFRAMES}
    valute = forza_valute(usabili, stati, stimate_valute)
    intermarket = analizza(usabili, stati)
    volatilita = regime_volatilita(misure, stati)
    forza_relativa = classifica(misure, valute, stati)
    azioni = direzione_gruppo(
        "equities", [s.canonical for s in del_gruppo(ReferenceGroup.EQUITY_INDEX)], misure, stati
    )
    metalli = direzione_gruppo(
        "metals", [s.canonical for s in del_gruppo(ReferenceGroup.METAL)], misure, stati
    )
    oro = etichetta_strumento(misure, "XAUUSD")
    letture = []
    for ing in ordinati:
        per_tf = {
            tf: (m.momentum_z if (m := misure.get(ing.canonical, {}).get(tf)) else None)
            for tf in par.TIMEFRAMES
        }
        punteggio = composito(per_tf)
        letture.append(
            InstrumentReading(
                canonical=ing.canonical,
                group=ing.group,
                market_status=ing.market_status,
                by_timeframe=per_tf,
                score=punteggio,
                label=etichetta(punteggio, per_tf),
                timeframes_agree=concordi(per_tf),
                usable_timeframes=tuple(
                    tf for tf in par.TIMEFRAMES if stati[ing.canonical][tf].usable
                ),
            )
        )

    # --- fonti esterne e derivate ---
    stato_vix = esterni.stato(ProviderKind.MARKET_REFERENCE)
    stato_tassi = esterni.stato(ProviderKind.RATES)
    stato_cal = esterni.stato(ProviderKind.ECONOMIC_CALENDAR)
    implicita = volatilita_implicita(
        vix_obs,
        as_of=as_of,
        fonte=stato_vix.name if stato_vix else "nessuna",
        assente=None if esterni.vix else _assente(stato_vix, "VIX"),
    )
    curva_tassi = tassi(
        curva_obs,
        as_of=as_of,
        fonte=stato_tassi.name if stato_tassi else "nessuna",
        assente=None if esterni.curva else _assente(stato_tassi, "curva dei rendimenti"),
    )
    usd_stimato = stimate_valute[Timeframe.H1].valute
    usd = valute.of("USD")
    dollaro = indice_dollaro(
        usabili,
        usd_h1=usd_stimato["USD"],
        usd_label=usd.label if usd else StrengthLabel.UNKNOWN,
        stati_componenti={c: any(st.usable for st in stati.get(c, {}).values()) for c in DXY_PESI},
    )
    rischio_cal = rischio_calendario(
        esterni.calendario,
        as_of=as_of,
        prima_minuti=esterni.prima_minuti,
        dopo_minuti=esterni.dopo_minuti,
        motivo_assenza=_assente(stato_cal, "calendario economico"),
    )

    prove = evidenze(valute, azioni, volatilita, implicita)
    rischio = sentiment(prove, (valute.quality, azioni.quality, volatilita.quality))
    lista_conflitti = conflitti(
        rischio=rischio,
        valute=valute,
        azioni=azioni,
        oro=oro,
        volatilita=volatilita,
        intermarket=intermarket,
        implicita=implicita,
    )
    # EVENT_DRIVEN per il mondo intero solo se l'evento tocca il dollaro, più valute
    # G8 o tutte: l'occupazione canadese non cambia il regime globale.
    toccate = {
        v for w in rischio_cal.active_windows for v in valute_interessate_finestra(w.currency)
    }
    finestre_globali = (
        tuple(f"{w.currency} {w.title}" for w in rischio_cal.active_windows)
        if "USD" in toccate or len(toccate) >= 2
        else ()
    )
    stato_regime, motivi_regime = regime(rischio, volatilita, lista_conflitti, finestre_globali)
    macro = rischio_eventi(
        calendario=esterni.calendario,
        stato_calendario=stato_cal.state if stato_cal else ProviderState.NOT_CONFIGURED,
        nome_calendario=stato_cal.name if stato_cal else "nessuno",
        rischio_calendario=rischio_cal,
        volatilita=volatilita,
        implicita=implicita,
        intermarket=intermarket,
        conflitti=lista_conflitti,
    )

    # --- 4. qualità ---
    dimensioni = [
        rischio.quality,
        valute.quality,
        volatilita.quality,
        intermarket.quality,
        forza_relativa.quality,
        azioni.quality,
        metalli.quality,
        macro.quality,
        implicita.quality,
        curva_tassi.quality,
        dollaro.quality,
    ]
    avvisi: list[str] = []
    if fase is not MarketPhase.OPEN_CONFIRMED:
        dimensioni = [
            al_massimo(d, GlobalDataQuality.DEGRADED, f"Fase di mercato {fase}.")
            for d in dimensioni
        ]
        avvisi.append(motivo_fase)
    if simulated:
        dimensioni = [
            al_massimo(d, GlobalDataQuality.DEGRADED, "Dati simulati.") for d in dimensioni
        ]
        avvisi.append(
            "Prezzi SIMULATI: nessun valore di questo contesto ha significato di mercato."
        )
    if limite_broker is None:
        avvisi.append(
            "Ora del broker non misurata: le barre non sono tagliate all'istante di analisi "
            "e la loro freschezza non è verificabile (ADR 0015)."
        )
    mancanti = snapshot.missing
    if mancanti:
        avvisi.append("Strumenti di riferimento non disponibili: " + ", ".join(mancanti) + ".")

    # Verifica incrociata DXY ricostruito / USD del grafo: non tocca il regime; se
    # fallisce, la forza delle valute è al massimo DEGRADED (correzione pre-fase 5).
    if dollaro.agrees_with_usd_strength is None:
        verifica = CrossCheck(
            name="DXY_RICOSTRUITO_VS_USD_GRAFO",
            passed=None,
            detail="Non verificabile: DXY o forza USD non disponibili.",
            effect="nessuno",
        )
    else:
        verifica = CrossCheck(
            name="DXY_RICOSTRUITO_VS_USD_GRAFO",
            passed=dollaro.agrees_with_usd_strength,
            detail=(
                f"DXY {dollaro.label}, USD dal grafo "
                f"{usd.label if usd else StrengthLabel.UNKNOWN}; correlazione H1 "
                f"{dollaro.correlation_with_usd_strength}."
            ),
            effect="se fallisce: qualità della forza valutaria al massimo DEGRADED",
        )
    if verifica.passed is False:
        dimensioni = [
            al_massimo(
                d,
                GlobalDataQuality.DEGRADED,
                "Verifica incrociata fallita: DXY ricostruito e USD del grafo discordano.",
            )
            if d.dimension == "currency_strength"
            else d
            for d in dimensioni
        ]

    # Qualità per dominio di fonte e qualità complessiva (ADR 0021).
    esterne = {"macro_event_risk", "implied_volatility", "rates", "dollar_index"}

    def qualita_di(nome: str):  # type: ignore[no-untyped-def]
        return next(d for d in dimensioni if d.dimension == nome)

    domini = fonti.qualita_per_dominio(
        broker=[d for d in dimensioni if d.dimension not in esterne],
        stati=esterni.stati,
        implicita=implicita.model_copy(update={"quality": qualita_di("implied_volatility")}),
        dollaro=dollaro,
        eventi=macro.model_copy(update={"quality": qualita_di("macro_event_risk")}),
        tassi=curva_tassi.model_copy(update={"quality": qualita_di("rates")}),
    )
    complessiva, motivi_qualita = fonti.qualita_complessiva(domini, esterni.stati)
    limiti = fonti.limitazioni(domini, macro, esterni.stati)

    contesto = GlobalMarketContext(
        as_of=as_of,
        engine_version=par.VERSIONE_MOTORE,
        input_fingerprint=snapshot.input_fingerprint,
        market_phase=fase,
        market_phase_reason=motivo_fase,
        regime=stato_regime,
        regime_reasons=motivi_regime,
        risk_sentiment=rischio.model_copy(update={"quality": qualita_di("risk_sentiment")}),
        volatility=volatilita.model_copy(update={"quality": qualita_di("volatility")}),
        currency_strength=valute.model_copy(update={"quality": qualita_di("currency_strength")}),
        relative_strength=forza_relativa.model_copy(
            update={"quality": qualita_di("relative_strength")}
        ),
        equities=azioni.model_copy(update={"quality": qualita_di("equities")}),
        metals=metalli.model_copy(update={"quality": qualita_di("metals")}),
        intermarket=intermarket.model_copy(update={"quality": qualita_di("intermarket")}),
        macro_event_risk=macro.model_copy(update={"quality": qualita_di("macro_event_risk")}),
        implied_volatility=implicita.model_copy(
            update={"quality": qualita_di("implied_volatility")}
        ),
        rates=curva_tassi.model_copy(update={"quality": qualita_di("rates")}),
        dollar_index=dollaro.model_copy(update={"quality": qualita_di("dollar_index")}),
        providers=esterni.stati,
        conflicts=lista_conflitti,
        instruments=tuple(letture),
        data_quality=complessiva,
        data_quality_reasons=motivi_qualita,
        source_quality=domini,
        dimension_quality=tuple(dimensioni),
        cross_checks=(verifica,),
        known_limitations=limiti,
        simulated=simulated,
        warnings=tuple(avvisi),
        summary=(),
    )
    return snapshot, contesto.model_copy(update={"summary": sintesi(contesto, oro)})


def sintesi(c: GlobalMarketContext, oro: StrengthLabel) -> tuple[SummaryLine, ...]:
    """Le righe che si leggono per prime. Una per dimensione, nessuna media."""
    usd = c.currency_strength.of("USD")
    classifica_valute = " > ".join(
        x.currency for x in c.currency_strength.currencies if x.score is not None
    )
    macro = c.macro_event_risk
    incertezza = f"{macro.market_implied_uncertainty} (implicita nei prezzi)"
    calendario = (
        f"{macro.calendar_level} — {macro.calendar_reason}"
        if macro.calendar_available
        else f"UNKNOWN — {macro.calendar_reason}"
    )
    vix = c.implied_volatility
    tassi_v = c.rates
    dxy = c.dollar_index
    righe = [
        ("Mercato", str(c.market_phase)),
        ("Regime", str(c.regime)),
        ("Risk", str(c.risk_sentiment.label)),
        ("USD", str(usd.label) if usd else str(StrengthLabel.UNKNOWN)),
        ("Valute (forte → debole)", classifica_valute or "n/d"),
        ("Gold", str(oro)),
        ("Metals", str(c.metals.label)),
        ("Equities", str(c.equities.label)),
        ("Volatility", f"{c.volatility.trend} ({c.volatility.level})"),
        (
            "VIX",
            f"{vix.value} ({vix.level}, {vix.trend}, al {vix.percentile_1y:.0%} dell'anno)"
            if vix and vix.value is not None and vix.percentile_1y is not None
            else "non disponibile",
        ),
        (
            "US 10Y",
            f"{tassi_v.yields.get('10Y')}% ({tassi_v.ten_year_trend}, 2s10s "
            f"{tassi_v.slope_2s10s_bp:+.0f} pb)"
            if tassi_v and tassi_v.yields and tassi_v.slope_2s10s_bp is not None
            else "non disponibile",
        ),
        (
            "DXY ricostruito (solo verifica)",
            f"{dxy.value} {dxy.label}" if dxy and dxy.value is not None else "non disponibile",
        ),
        ("Intermarket", str(c.intermarket.state)),
        ("Calendario", calendario),
        ("Macro uncertainty", incertezza),
        ("Conflitti", str(len(c.conflicts))),
        ("Data quality", str(c.data_quality)),
        (
            "Qualità per fonte",
            ", ".join(
                f"{q.domain}={q.quality if q.configured else 'NOT_CONFIGURED'}"
                for q in c.source_quality
            ),
        ),
    ]
    return tuple(SummaryLine(label=a, value=b) for a, b in righe)


def valute_interessate_finestra(valuta: str) -> tuple[str, ...]:
    from backend.config.global_universe import VALUTE

    return VALUTE if valuta == "ALL" else ((valuta,) if valuta in VALUTE else ())


def _assente(stato: ProviderStatus | None, cosa: str) -> str:
    if stato is None or stato.state is ProviderState.NOT_CONFIGURED:
        return f"{cosa}: nessuna fonte configurata"
    if stato.last_error:
        return f"{cosa}: fonte {stato.name} non raggiungibile ({stato.last_error[:120]})"
    return f"{cosa}: dati della fonte {stato.name} non disponibili"


#: Strumenti dell'universo, per i chiamanti che non vogliono importare la config.
CANONICI = tuple(s.canonical for s in UNIVERSO)
