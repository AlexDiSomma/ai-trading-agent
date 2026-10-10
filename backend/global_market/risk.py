"""Sentiment di rischio, regime globale e conflitti fra evidenze.

**Evidenze, non un punteggio.** Il sentiment nasce da indizi separati, ognuno con
il suo segno (+ risk-on) e il suo peso, tutti visibili nel contesto:

| indizio | segno risk-on | peso |
|---|---|---|
| direzione degli indici azionari | azioni in rialzo | 1.00 |
| forza dello yen | yen debole | 0.75 |
| forza delle valute cicliche (AUD, NZD) | AUD/NZD forti | 0.75 |
| forza del franco svizzero | franco debole | 0.50 |
| volatilità azionaria (percentile ATR) | volatilità bassa | 0.50 |

L'oro **non** è un indizio di rischio: sale con la paura, ma anche con il dollaro
debole e l'inflazione. Ha la sua dimensione (`metals`), e la sua relazione con le
azioni si misura nell'intermarket.

**Regime, in ordine di precedenza:**

1. `UNCERTAIN` — qualità insufficiente, meno di tre indizi, o segnali troppo deboli
   per una direzione;
2. `EVENT_DRIVEN` — shock di volatilità (o, quando ci sarà, evento ad alto impatto
   in calendario);
3. `TRANSITION` — timeframe lenti (D1, H4) e veloci (H1, M15) oltre la soglia in
   direzioni opposte;
4. `RISK_ON` / `RISK_OFF` — composito oltre ±0.35 **e** accordo pesato ≥ 70%;
5. `MIXED` — direzione presente ma indizi in disaccordo.

Infine, un conflitto grave (o due medi) fra le dimensioni trasforma RISK_ON /
RISK_OFF in `MIXED`: se le evidenze non concordano, il contesto lo dice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from backend.core.enums import (
    ConflictSeverity,
    GlobalDataQuality,
    GlobalRegime,
    IntermarketState,
    ReferenceGroup,
    StrengthLabel,
    Timeframe,
    VolatilityLevel,
    VolatilityTrend,
)
from backend.global_market import parametri as par
from backend.global_market.features import composito
from backend.global_market.qualita import peggiore
from backend.schemas.global_market import (
    Conflict,
    CurrencyStrengthView,
    DimensionQuality,
    DollarIndexView,
    GroupDirection,
    ImpliedVolatility,
    IntermarketView,
    RiskEvidence,
    RiskSentiment,
    VolatilityView,
)


def _neg(per_tf: dict[Timeframe, float | None]) -> dict[Timeframe, float | None]:
    return {tf: (None if v is None else round(-v, 6)) for tf, v in per_tf.items()}


def _media(*serie: dict[Timeframe, float | None]) -> dict[Timeframe, float | None]:
    risultato: dict[Timeframe, float | None] = {}
    for tf in par.TIMEFRAMES:
        valori = [s.get(tf) for s in serie if s.get(tf) is not None]
        risultato[tf] = round(math.fsum(valori) / len(valori), 6) if valori else None  # type: ignore[arg-type]
    return risultato


def _segno(v: float) -> int:
    return 1 if v > 0 else -1 if v < 0 else 0


def evidenze(
    valute: CurrencyStrengthView,
    azioni: GroupDirection,
    volatilita: VolatilityView,
    implicita: ImpliedVolatility | None = None,
) -> tuple[RiskEvidence, ...]:
    def tf_di(valuta: str) -> dict[Timeframe, float | None]:
        c = valute.of(valuta)
        return dict(c.by_timeframe) if c else dict.fromkeys(par.TIMEFRAMES)

    # Volatilità azionaria: percentile ATR mediano degli indici, riportato in
    # [-1, 1] con segno risk-on (bassa = +1, alta = -1).
    indici = [a for a in volatilita.assets if a.group is ReferenceGroup.EQUITY_INDEX]
    vol_tf: dict[Timeframe, float | None] = {}
    for tf in par.TIMEFRAMES:
        valori = sorted(v for a in indici if (v := a.atr_percentile.get(tf)) is not None)
        if valori:
            mediano = valori[len(valori) // 2]
            vol_tf[tf] = round(-(mediano - 0.5) * 2, 6)
        else:
            vol_tf[tf] = None

    grezze = (
        ("azioni", dict(azioni.by_timeframe), 1.0, "Direzione media degli indici azionari."),
        ("yen", _neg(tf_di("JPY")), 0.75, "Yen forte = domanda di rifugio (segno invertito)."),
        (
            "valute_cicliche",
            _media(tf_di("AUD"), tf_di("NZD")),
            0.75,
            "AUD e NZD forti = appetito per il rischio.",
        ),
        ("franco", _neg(tf_di("CHF")), 0.5, "Franco forte = domanda di rifugio (segno invertito)."),
        (
            "volatilita_azionaria",
            vol_tf,
            0.5,
            "Percentile ATR mediano degli indici: alto = avversione al rischio.",
        ),
    )
    prove = [
        RiskEvidence(name=n, score=composito(tf), by_timeframe=tf, weight=w, description=d)
        for n, tf, w, d in grezze
    ]
    # VIX (fonte esterna, giornaliero): entra solo se la sua qualità lo consente.
    # Percentile annuo riportato in [-1, 1] con segno risk-on (basso = +1).
    if (
        implicita is not None
        and implicita.percentile_1y is not None
        and implicita.quality.quality is not GlobalDataQuality.INSUFFICIENT
    ):
        valore = round(-(implicita.percentile_1y - 0.5) * 2, 6)
        per_tf = {tf: (valore if tf is Timeframe.D1 else None) for tf in par.TIMEFRAMES}
        prove.append(
            RiskEvidence(
                name="vix",
                score=valore,
                by_timeframe=per_tf,
                weight=0.75,
                description=(
                    f"VIX {implicita.value} al {implicita.percentile_1y:.0%} dell'ultimo anno "
                    f"({implicita.source}): alto = avversione al rischio."
                ),
            )
        )
    return tuple(prove)


def sentiment(
    prove: Sequence[RiskEvidence], qualita_componenti: Sequence[DimensionQuality]
) -> RiskSentiment:
    per_tf: dict[Timeframe, float | None] = {}
    for tf in par.TIMEFRAMES:
        disponibili = [(e.weight, e.by_timeframe.get(tf)) for e in prove]
        validi = [(w, v) for w, v in disponibili if v is not None]
        if len(validi) < 3:
            per_tf[tf] = None
            continue
        per_tf[tf] = round(math.fsum(w * v for w, v in validi) / math.fsum(w for w, _ in validi), 6)  # type: ignore[operator]
    score = composito(per_tf)

    accordo: float | None = None
    con_valore = [e for e in prove if e.score is not None]
    if score is not None and con_valore:
        s = _segno(score)
        peso = math.fsum(e.weight for e in con_valore)
        concordi = math.fsum(
            e.weight
            for e in con_valore
            if _segno(e.score) == s and abs(e.score) >= 0.1  # type: ignore[arg-type]
        )
        accordo = round(concordi / peso, 4) if peso else None

    qualita = peggiore(*(q.quality for q in qualita_componenti))
    motivi = tuple(dict.fromkeys(r for q in qualita_componenti for r in q.reasons))
    copertura = round(len(con_valore) / len(prove), 4) if prove else 0.0
    if len(con_valore) < 3:
        qualita = GlobalDataQuality.INSUFFICIENT
        motivi = (f"Solo {len(con_valore)} indizi di rischio su {len(prove)}.", *motivi)

    forti_on = tuple(e.name for e in con_valore if (e.score or 0) >= par.SOGLIA_FORZA)
    forti_off = tuple(e.name for e in con_valore if (e.score or 0) <= -par.SOGLIA_FORZA)
    label = _etichetta(score, accordo, per_tf, qualita, discordi=bool(forti_on and forti_off))
    return RiskSentiment(
        score=score,
        agreement=accordo,
        by_timeframe=per_tf,
        evidence=tuple(prove),
        strong_risk_on=forti_on,
        strong_risk_off=forti_off,
        label=label,
        quality=DimensionQuality(
            dimension="risk_sentiment", quality=qualita, coverage=copertura, reasons=motivi
        ),
    )


def _lento_veloce(per_tf: dict[Timeframe, float | None]) -> tuple[float | None, float | None]:
    lento = composito({tf: per_tf.get(tf) for tf in par.LENTI}, minimo=1)
    veloce = composito({tf: per_tf.get(tf) for tf in par.VELOCI}, minimo=1)
    return lento, veloce


def _etichetta(
    score: float | None,
    accordo: float | None,
    per_tf: dict[Timeframe, float | None],
    qualita: GlobalDataQuality,
    *,
    discordi: bool = False,
) -> GlobalRegime:
    if qualita is GlobalDataQuality.INSUFFICIENT or score is None:
        return GlobalRegime.UNCERTAIN
    lento, veloce = _lento_veloce(per_tf)
    if (
        lento is not None
        and veloce is not None
        and abs(lento) >= par.SOGLIA_RISCHIO
        and abs(veloce) >= par.SOGLIA_RISCHIO
        and lento * veloce < 0
    ):
        return GlobalRegime.TRANSITION
    # Fase 4.5: dimensioni che indicano con forza direzioni opposte danno MIXED
    # anche se il composito supera la soglia. La discordanza non si media.
    if discordi:
        return GlobalRegime.MIXED
    if abs(score) < par.SOGLIA_RISCHIO:
        # Indizi deboli: o concordi su "niente" (UNCERTAIN), o discordi (MIXED).
        return GlobalRegime.MIXED if (accordo or 0) < 0.5 else GlobalRegime.UNCERTAIN
    if (accordo or 0) >= par.SOGLIA_ACCORDO:
        return GlobalRegime.RISK_ON if score > 0 else GlobalRegime.RISK_OFF
    return GlobalRegime.MIXED


# ======================================================================
# Conflitti
# ======================================================================
def conflitti(
    *,
    rischio: RiskSentiment,
    valute: CurrencyStrengthView,
    azioni: GroupDirection,
    oro: StrengthLabel,
    volatilita: VolatilityView,
    intermarket: IntermarketView,
    implicita: ImpliedVolatility | None = None,
    dollaro: DollarIndexView | None = None,
) -> tuple[Conflict, ...]:
    trovati: list[Conflict] = []

    def aggiungi(
        codice: str, dimensioni: tuple[str, ...], gravita: ConflictSeverity, testo: str
    ) -> None:
        trovati.append(
            Conflict(code=codice, dimensions=dimensioni, severity=gravita, description=testo)
        )

    con_valore = [e for e in rischio.evidence if e.score is not None]
    if rischio.agreement is not None and len(con_valore) >= 3 and rischio.agreement < 0.6:
        aggiungi(
            "INDIZI_RISCHIO_DISCORDI",
            ("risk_sentiment",),
            ConflictSeverity.MEDIUM,
            f"Solo il {rischio.agreement:.0%} (pesato) degli indizi di rischio concorda.",
        )

    def etichetta_valuta(v: str) -> StrengthLabel:
        c = valute.of(v)
        return c.label if c else StrengthLabel.UNKNOWN

    rifugi_forti = [v for v in ("JPY", "CHF") if etichetta_valuta(v) is StrengthLabel.STRONG]
    if azioni.label is StrengthLabel.STRONG and rifugi_forti:
        aggiungi(
            "AZIONI_E_RIFUGI_FORTI",
            ("equities", "currency_strength"),
            ConflictSeverity.MEDIUM,
            "Indici in rialzo mentre " + " e ".join(rifugi_forti) + " sono forti: "
            "appetito per il rischio e domanda di rifugio insieme.",
        )
    if azioni.label is StrengthLabel.WEAK and etichetta_valuta("JPY") is StrengthLabel.WEAK:
        aggiungi(
            "AZIONI_DEBOLI_YEN_DEBOLE",
            ("equities", "currency_strength"),
            ConflictSeverity.MEDIUM,
            "Indici in calo senza domanda di yen: il ribasso non ha il profilo di una fuga "
            "dal rischio.",
        )
    if oro is StrengthLabel.STRONG and etichetta_valuta("USD") is StrengthLabel.STRONG:
        aggiungi(
            "ORO_E_DOLLARO_FORTI",
            ("metals", "currency_strength"),
            ConflictSeverity.MEDIUM,
            "Oro e dollaro forti insieme: di solito si muovono in senso opposto. "
            "Spesso è domanda di rifugio su entrambi.",
        )
    lento, veloce = _lento_veloce(dict(azioni.by_timeframe))
    if (
        lento is not None
        and veloce is not None
        and abs(lento) >= par.SOGLIA_FORZA
        and abs(veloce) >= par.SOGLIA_FORZA
        and lento * veloce < 0
    ):
        aggiungi(
            "AZIONI_TIMEFRAME_OPPOSTI",
            ("equities",),
            ConflictSeverity.MEDIUM,
            f"Indici {lento:+.2f} su D1/H4 e {veloce:+.2f} su H1/M15.",
        )
    if intermarket.state is IntermarketState.DIVERGENT:
        aggiungi(
            "INTERMARKET_DIVERGENTE",
            ("intermarket",),
            ConflictSeverity.HIGH,
            f"{intermarket.diverging} relazioni divergenti dalla loro base storica contro "
            f"{intermarket.consistent} coerenti.",
        )
    elif intermarket.state is IntermarketState.MIXED and intermarket.diverging:
        aggiungi(
            "INTERMARKET_PARZIALE",
            ("intermarket",),
            ConflictSeverity.LOW,
            f"{intermarket.diverging} relazioni divergenti su {len(intermarket.relationships)}.",
        )
    tendenza_azioni = volatilita.trend_by_group.get(str(ReferenceGroup.EQUITY_INDEX))
    if azioni.label is StrengthLabel.STRONG and tendenza_azioni is VolatilityTrend.EXPANDING:
        aggiungi(
            "RIALZO_CON_VOLATILITA_CRESCENTE",
            ("equities", "volatility"),
            ConflictSeverity.MEDIUM,
            "Indici in rialzo con volatilità in espansione: rialzo nervoso.",
        )
    if (rischio.label is GlobalRegime.RISK_ON and azioni.label is StrengthLabel.WEAK) or (
        rischio.label is GlobalRegime.RISK_OFF and azioni.label is StrengthLabel.STRONG
    ):
        aggiungi(
            "RISCHIO_CONTRO_AZIONI",
            ("risk_sentiment", "equities"),
            ConflictSeverity.HIGH,
            f"Sentiment {rischio.label} con azioni {azioni.label}.",
        )
    if implicita is not None and implicita.quality.quality is not GlobalDataQuality.INSUFFICIENT:
        teso = implicita.level in (VolatilityLevel.ELEVATED, VolatilityLevel.EXTREME) or (
            implicita.trend is VolatilityTrend.EXPANDING
        )
        calmo = implicita.level is VolatilityLevel.LOW and (
            implicita.trend is not VolatilityTrend.EXPANDING
        )
        if rischio.label is GlobalRegime.RISK_ON and teso:
            aggiungi(
                "RISK_ON_CON_VIX_TESO",
                ("risk_sentiment", "implied_volatility"),
                ConflictSeverity.MEDIUM,
                f"Sentiment risk-on con VIX {implicita.value} ({implicita.level}, "
                f"{implicita.trend}).",
            )
        if rischio.label is GlobalRegime.RISK_OFF and calmo:
            aggiungi(
                "RISK_OFF_CON_VIX_CALMO",
                ("risk_sentiment", "implied_volatility"),
                ConflictSeverity.MEDIUM,
                f"Sentiment risk-off con VIX basso ({implicita.value}).",
            )
    # Il DXY ricostruito non è più un conflitto di regime (correzione pre-fase 5): è
    # una verifica incrociata, che se fallisce degrada la qualità della forza valutaria.
    usd = valute.of("USD")
    if usd is not None and usd.label is StrengthLabel.MIXED:
        aggiungi(
            "DOLLARO_TIMEFRAME_OPPOSTI",
            ("currency_strength",),
            ConflictSeverity.LOW,
            "Dollaro forte su un orizzonte e debole su un altro.",
        )
    return tuple(sorted(trovati, key=lambda c: (c.severity != ConflictSeverity.HIGH, c.code)))


def regime(
    rischio: RiskSentiment,
    volatilita: VolatilityView,
    lista: Sequence[Conflict],
    finestre_globali: Sequence[str] = (),
) -> tuple[GlobalRegime, tuple[str, ...]]:
    """Regime finale.

    `finestre_globali`: eventi in calendario con finestra di blocco attiva su USD,
    su almeno due valute G8, o su tutte (vedi `engine`).
    """
    motivi: list[str] = []
    base = rischio.label
    if rischio.quality.quality is GlobalDataQuality.INSUFFICIENT or rischio.score is None:
        motivi.append("Qualità dei dati insufficiente per il sentiment di rischio.")
        return GlobalRegime.UNCERTAIN, tuple(motivi)
    if volatilita.shock or finestre_globali:
        if volatilita.shock:
            motivi.append(
                "Shock di volatilità su molti strumenti insieme: il mercato reagisce a un "
                "evento, le relazioni abituali non valgono."
            )
        if finestre_globali:
            motivi.append(
                "Finestra di evento ad alto impatto attiva: " + "; ".join(finestre_globali)
            )
        return GlobalRegime.EVENT_DRIVEN, tuple(motivi)
    if base is GlobalRegime.UNCERTAIN:
        motivi.append("Evidenze troppo deboli per una direzione di rischio.")
        return base, tuple(motivi)
    if base is GlobalRegime.MIXED and rischio.strong_risk_on and rischio.strong_risk_off:
        motivi.append(
            "Dimensioni in disaccordo: risk-on da "
            + ", ".join(rischio.strong_risk_on)
            + "; risk-off da "
            + ", ".join(rischio.strong_risk_off)
            + "."
        )
    gravi = [c for c in lista if c.severity is ConflictSeverity.HIGH]
    medi = [c for c in lista if c.severity is ConflictSeverity.MEDIUM]
    if base in (GlobalRegime.RISK_ON, GlobalRegime.RISK_OFF) and (gravi or len(medi) >= 2):
        motivi.append(
            f"Sentiment {base}, ma le dimensioni globali si contraddicono: "
            + ", ".join(c.code for c in (*gravi, *medi))
            + "."
        )
        return GlobalRegime.MIXED, tuple(motivi)
    descrizione = {
        GlobalRegime.RISK_ON: "Indizi concordi verso l'appetito per il rischio.",
        GlobalRegime.RISK_OFF: "Indizi concordi verso l'avversione al rischio.",
        GlobalRegime.MIXED: "Indizi di rischio in disaccordo fra loro.",
        GlobalRegime.TRANSITION: "Timeframe lenti e veloci in direzioni di rischio opposte.",
    }
    if not motivi:
        motivi.append(descrizione.get(base, str(base)))
    motivi.append(
        f"Composito {rischio.score:+.2f}, accordo "
        + (f"{rischio.agreement:.0%}." if rischio.agreement is not None else "n/d.")
    )
    return base, tuple(motivi)
