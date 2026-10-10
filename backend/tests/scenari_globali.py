"""Scenari sintetici per i test dell'intelligence globale. **Solo per i test.**

Costruisce un universo di riferimento coerente a partire da poche leve:

* `valute` — deriva per barra del valore di ogni valuta (log). I 28 cross sono
  derivati da questi valori, quindi la forza "vera" di ogni valuta è nota e il
  test può verificare che il motore la ritrovi;
* `azioni`, `oro`, `argento`, `petrolio` — deriva per barra di ogni gruppo;
* `per_timeframe` — derive diverse per timeframe (per simulare una transizione);
* `rumore` — ampiezza del rumore, generato da un seme fisso (deterministico).

Le barre sono in "ora broker" fittizia e finiscono a `FINE`.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.config.global_universe import UNIVERSO, StrumentoDiRiferimento
from backend.core.enums import MarketStatus, ReferenceGroup, SeriesStatus, Timeframe
from backend.global_market import parametri as par
from backend.global_market.engine import IngressoStrumento
from backend.global_market.features import SerieDiRiferimento

#: Ultimo istante coperto dalle barre (ora broker).
FINE = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
AS_OF = FINE - timedelta(hours=3)  # stesso istante in UTC con scarto +3h

PREZZI = {
    "XAUUSD": 4140.0,
    "XAGUSD": 60.0,
    "US500": 7700.0,
    "US100": 30800.0,
    "US30": 51200.0,
    "GER40": 24000.0,
    "UK100": 9500.0,
    "JP225": 45000.0,
    "OIL": 62.0,
}


def _derive(
    s: StrumentoDiRiferimento, valute: Mapping[str, float], gruppi: Mapping[str, float]
) -> float:
    if s.group is ReferenceGroup.FX:
        return valute.get(s.base, 0.0) - valute.get(s.quote_currency, 0.0)
    if s.group is ReferenceGroup.EQUITY_INDEX:
        return gruppi.get(s.canonical, gruppi.get("azioni", 0.0))
    if s.canonical == "XAUUSD":
        return gruppi.get("oro", 0.0)
    if s.canonical == "XAGUSD":
        return gruppi.get("argento", gruppi.get("oro", 0.0))
    return gruppi.get("petrolio", 0.0)


def serie_sintetica(
    s: StrumentoDiRiferimento,
    tf: Timeframe,
    deriva: float,
    *,
    barre: int = par.BARRE_RICHIESTE,
    rumore: float = 0.001,
    seme: int = 7,
    fine: datetime = FINE,
    shock_finale: int = 0,
    profilo: tuple[tuple[int, int, float], ...] = (),
) -> SerieDiRiferimento:
    rng = random.Random(f"{seme}:{s.canonical}:{tf}")  # noqa: S311 - rumore di test
    durata = timedelta(minutes=tf.minutes)
    inizio = fine - durata * barre
    prezzo = PREZZI.get(s.canonical, 1.0 + (sum(map(ord, s.canonical)) % 50) / 100)
    tempi, massimi, minimi, chiusure = [], [], [], []
    for i in range(barre):
        ampiezza = rumore * (25 if shock_finale and i >= barre - shock_finale else 1)
        # Profilo di volatilità: (da, a, fattore) con indici contati dalla fine.
        for da, a, fattore in profilo:
            if barre - da <= i < barre - a:
                ampiezza *= fattore
        prezzo *= math.exp(deriva + rng.gauss(0, ampiezza))
        escursione = prezzo * ampiezza * (0.5 + rng.random())
        tempi.append(inizio + durata * i)
        massimi.append(prezzo + escursione)
        minimi.append(prezzo - escursione)
        chiusure.append(prezzo)
    return SerieDiRiferimento(
        s.canonical, s.group, tf, tuple(tempi), tuple(massimi), tuple(minimi), tuple(chiusure)
    )


def scenario(
    *,
    valute: Mapping[str, float] | None = None,
    gruppi: Mapping[str, float] | None = None,
    per_timeframe: Mapping[Timeframe, tuple[Mapping[str, float], Mapping[str, float]]]
    | None = None,
    stato: SeriesStatus = SeriesStatus.FRESH,
    mercato: MarketStatus = MarketStatus.OPEN_CONFIRMED,
    mancanti: tuple[str, ...] = (),
    stati_speciali: Mapping[str, SeriesStatus] | None = None,
    barre: int = par.BARRE_RICHIESTE,
    rumore: float = 0.001,
    shock: tuple[str, ...] = (),
    seme: int = 7,
    profilo: tuple[tuple[int, int, float], ...] = (),
) -> list[IngressoStrumento]:
    valute = valute or {}
    gruppi = gruppi or {}
    risultato: list[IngressoStrumento] = []
    for s in UNIVERSO:
        if s.canonical in mancanti:
            risultato.append(
                IngressoStrumento(
                    canonical=s.canonical,
                    broker_symbol=s.broker_symbol,
                    group=s.group,
                    stati=dict.fromkeys(par.TIMEFRAMES, SeriesStatus.MISSING),
                    reason="Simbolo non disponibile (scenario).",
                )
            )
            continue
        stato_s = (stati_speciali or {}).get(s.canonical, stato)
        serie = {}
        for tf in par.TIMEFRAMES:
            v, g = per_timeframe[tf] if per_timeframe and tf in per_timeframe else (valute, gruppi)
            serie[tf] = serie_sintetica(
                s,
                tf,
                _derive(s, v, g),
                barre=barre,
                rumore=rumore,
                seme=seme,
                shock_finale=4 if s.canonical in shock or "*" in shock else 0,
                profilo=profilo,
            )
        risultato.append(
            IngressoStrumento(
                canonical=s.canonical,
                broker_symbol=s.broker_symbol,
                group=s.group,
                market_status=mercato,
                stati=dict.fromkeys(par.TIMEFRAMES, stato_s),
                serie=serie,
            )
        )
    return risultato


def con_barre_future(
    ingressi: list[IngressoStrumento], quante: int = 30, seme: int = 99
) -> list[IngressoStrumento]:
    """Aggiunge barre **dopo** `FINE`, con prezzi diversi: per i test di look-ahead."""
    rng = random.Random(seme)  # noqa: S311 - rumore di test
    nuovi = []
    for ing in ingressi:
        serie = {}
        for tf, s in ing.serie.items():
            durata = timedelta(minutes=tf.minutes)
            tempi, massimi, minimi, chiusure = (
                list(x) for x in (s.tempi, s.massimi, s.minimi, s.chiusure)
            )
            prezzo = chiusure[-1]
            for i in range(quante):
                prezzo *= math.exp(rng.gauss(0.02, 0.05))  # futuro molto diverso
                tempi.append(FINE + durata * i)
                massimi.append(prezzo * 1.01)
                minimi.append(prezzo * 0.99)
                chiusure.append(prezzo)
            serie[tf] = SerieDiRiferimento(
                s.canonical,
                s.group,
                tf,
                tuple(tempi),
                tuple(massimi),
                tuple(minimi),
                tuple(chiusure),
            )
        nuovi.append(replace(ing, serie=serie))
    return nuovi
