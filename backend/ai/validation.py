"""Validazione dell'output del modello, oltre lo schema (fase 5A).

Lo schema Pydantic garantisce la forma. Qui si verifica che l'analisi sia **coerente
con l'ingresso** e **non operativa**. Ogni violazione rende l'analisi non disponibile:
non si corregge, non si filtra, non si tiene "la parte buona". Fail closed.

1. il regime ripetuto dal modello coincide con quello del motore;
2. ogni riferimento citato esiste fra quelli forniti;
3. ogni strumento da osservare è nell'universo;
4. con regime MIXED, TRANSITION o UNCERTAIN ci sono evidenze contraddittorie;
5. con limitazioni note, almeno un'incertezza indica un'informazione mancante;
6. la fiducia non è HIGH con dati non GOOD, ed è LOW con regime UNCERTAIN;
7. nessun contenuto operativo nel testo (acquisti, vendite, stop, target, lotti).
"""

from __future__ import annotations

import re

from backend.ai.contracts import AnalysisInput, InvalidationCondition, MarketAnalysis
from backend.config.global_universe import VALUTE
from backend.core.enums import (
    EventPhase,
    GlobalRegime,
    RelationshipState,
    StrengthLabel,
    VolatilityLevel,
    VolatilityTrend,
)

_STATI = {
    "CURRENCY_STRENGTH": {s.value for s in StrengthLabel},
    "REGIME": {s.value for s in GlobalRegime},
    "VOLATILITY": {s.value for s in VolatilityLevel} | {s.value for s in VolatilityTrend},
    "CORRELATION": {s.value for s in RelationshipState},
    "EVENT": {s.value for s in EventPhase},
}


def verificabile(c: InvalidationCondition, ingresso: AnalysisInput) -> str | None:
    """None se la condizione è verificabile da un programma con dati futuri, altrimenti
    il motivo. Serve alla fase 9: una condizione ambigua si giudica solo con hindsight."""
    soggetto = c.subject or ""
    # "currency_strength.USD" è un riferimento non ambiguo alla valuta USD.
    if c.metric == "CURRENCY_STRENGTH" and soggetto.startswith("currency_strength."):
        soggetto = soggetto.removeprefix("currency_strength.")
    strumenti = set(ingresso.universe) - set(VALUTE)
    soglia_ok = c.direction in ("ABOVE", "BELOW") and c.threshold is not None
    stato_ok = c.direction in ("CHANGES_TO", "NOT_REACHED", "OCCURS") and (
        (c.target_state or "") in _STATI.get(c.metric, set())
    )
    match c.metric:
        case "PRICE":
            ok = soggetto in strumenti and soglia_ok
        case "CURRENCY_STRENGTH":
            ok = soggetto in VALUTE and (soglia_ok or stato_ok)
        case "REGIME":
            ok = soggetto in ("regime", "risk_sentiment") and stato_ok
        case "VOLATILITY":
            ok = soggetto in {"implied_volatility", "volatility"} | strumenti and (
                soglia_ok or stato_ok
            )
        case "CORRELATION":
            ok = (
                soggetto.startswith("intermarket.")
                and soggetto in ingresso.allowed_refs
                and ((soglia_ok and -1 <= (c.threshold or 0) <= 1) or stato_ok)
            )
        case "EVENT":
            ok = (
                soggetto.startswith("calendar.events.")
                and soggetto in ingresso.allowed_refs
                and (stato_ok)
            )
        case _:
            ok = False
    if ok:
        return None
    return (
        f"Condizione non verificabile: {c.metric} su '{soggetto or '-'}' {c.direction} "
        f"(soglia {c.threshold}, stato {c.target_state})."
    )


#: Istruzioni operative. Il modello può descrivere un "sell-off" o un trend di "lungo
#: periodo": si cercano le forme imperative o tecniche di un ordine, non le parole.
_OPERATIVO = re.compile(
    r"\b(buy|sell|go long|go short|open (a )?(long|short)|enter (a )?(long|short))\b(?![-\w])"
    r"|\bstop[- ]?loss\b|\btake[- ]?profit\b|\bposition siz"
    r"|\b\d+(\.\d+)?\s*(lots?|lotti)\b"
    r"|\b(compra(re)?|acquista(re)?|vendi|aprire (una )?posizione|entrare (long|short)"
    r"|aprire (un )?(long|short)|dimensione della posizione|piazzare un ordine)\b",
    re.IGNORECASE,
)


def _testi(a: MarketAnalysis) -> list[str]:
    testi = [a.market_summary, a.analyst_view_on_regime]
    testi += [t.title + " " + t.description for t in a.dominant_themes]
    testi += [
        s.name + " " + s.narrative + " " + " ".join(s.confirming_signals) for s in a.scenarios
    ]
    testi += [e.statement for e in (*a.supporting_evidence, *a.contradictory_evidence)]
    testi += [u.description + " " + (u.missing_information or "") for u in a.uncertainties]
    testi += [w.reason + " " + w.watch_for for w in a.assets_to_watch]
    testi += [c.description for c in a.invalidation_conditions]
    testi += [c.description for s in a.scenarios for c in s.invalidation]
    return testi


def problemi(a: MarketAnalysis, ingresso: AnalysisInput) -> list[str]:
    """Elenco delle violazioni; vuoto se l'analisi è accettabile."""
    errori: list[str] = []
    if a.engine_regime_acknowledged is not ingresso.engine_regime:
        errori.append(
            f"Regime riportato {a.engine_regime_acknowledged} diverso da quello del motore "
            f"{ingresso.engine_regime}."
        )
    ammessi = set(ingresso.allowed_refs)
    citati = [
        r
        for gruppo in (
            *(e.refs for e in a.supporting_evidence),
            *(e.refs for e in a.contradictory_evidence),
            *(t.refs for t in a.dominant_themes),
        )
        for r in gruppo
    ]
    inventati = sorted({r for r in citati if r not in ammessi})
    if inventati:
        errori.append("Riferimenti inesistenti nel contesto: " + ", ".join(inventati[:8]) + ".")
    universo = set(ingresso.universe)
    fuori = sorted({w.canonical for w in a.assets_to_watch if w.canonical not in universo})
    if fuori:
        errori.append("Strumenti fuori dall'universo: " + ", ".join(fuori) + ".")
    soggetti = {
        c.subject
        for c in (*a.invalidation_conditions, *(c for s in a.scenarios for c in s.invalidation))
        if c.subject
    }
    soggetti_fuori = sorted(s for s in soggetti if s not in universo and s not in ammessi)
    if soggetti_fuori:
        errori.append(
            "Condizioni su soggetti fuori dall'universo: " + ", ".join(soggetti_fuori) + "."
        )
    if (
        ingresso.engine_regime
        in (GlobalRegime.MIXED, GlobalRegime.TRANSITION, GlobalRegime.UNCERTAIN)
        and not a.contradictory_evidence
    ):
        errori.append(f"Regime {ingresso.engine_regime} senza evidenze contraddittorie.")
    if ingresso.known_limitations and not any(u.missing_information for u in a.uncertainties):
        errori.append(
            "Limitazioni note ignorate: nessuna incertezza indica l'informazione mancante."
        )
    if ingresso.data_quality != "GOOD" and a.confidence == "HIGH":
        errori.append(f"Fiducia HIGH con qualità dei dati {ingresso.data_quality}.")
    if ingresso.engine_regime is GlobalRegime.UNCERTAIN and a.confidence != "LOW":
        errori.append("Regime UNCERTAIN con fiducia diversa da LOW.")
    condizioni = [*a.invalidation_conditions, *(c for s in a.scenarios for c in s.invalidation)]
    non_verificabili = [m for c in condizioni if (m := verificabile(c, ingresso))]
    errori.extend(non_verificabili[:6])
    operativi = sorted({m.group(0).lower() for t in _testi(a) for m in _OPERATIVO.finditer(t)})
    if operativi:
        errori.append("Contenuto operativo vietato: " + ", ".join(operativi) + ".")
    return errori
