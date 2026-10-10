"""Indicatori tecnici.

Funzioni pure su serie di numeri: nessuno stato, nessun accesso al broker,
nessuna conoscenza di quale strumento si stia guardando. È la proprietà che
permette di usare esattamente questo codice nel backtest — un motore di backtest
con indicatori propri misura un sistema che non esiste.

Ogni funzione restituisce la serie completa (vedi `series.py` per il perché) più
un oggetto riassuntivo con i soli valori correnti, che è quello che finisce nel
contesto da passare all'analisi.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.quant.series import (
    Serie,
    deviazione_standard,
    ema,
    rango_percentile,
    rendimenti_logaritmici,
    sma,
    true_range,
    ultimo,
    wilder,
)

# ----------------------------------------------------------------------
# Medie
# ----------------------------------------------------------------------
__all__ = [
    "Adx",
    "Bollinger",
    "Macd",
    "adx",
    "ampiezza_bollinger",
    "atr",
    "bollinger",
    "ema",
    "momentum",
    "rsi",
    "sma",
    "volatilita_realizzata",
]


# ----------------------------------------------------------------------
# RSI
# ----------------------------------------------------------------------
def rsi(chiusure: Sequence[float], periodo: int = 14) -> Serie:
    """Indice di forza relativa, con smorzamento di Wilder.

    Casi limite trattati esplicitamente: senza perdite nella finestra l'RSI è
    100, senza guadagni è 0. Lasciare che una divisione per zero propaghi un
    `inf` darebbe un RSI tecnicamente corretto e inutilizzabile a valle.
    """
    guadagni: list[float | None] = [None] * len(chiusure)
    perdite: list[float | None] = [None] * len(chiusure)
    for i in range(1, len(chiusure)):
        variazione = chiusure[i] - chiusure[i - 1]
        guadagni[i] = max(variazione, 0.0)
        perdite[i] = max(-variazione, 0.0)

    media_guadagni = wilder(tuple(guadagni), periodo)
    media_perdite = wilder(tuple(perdite), periodo)

    risultato: list[float | None] = [None] * len(chiusure)
    for i in range(len(chiusure)):
        g, p = media_guadagni[i], media_perdite[i]
        if g is None or p is None:
            continue
        if p == 0:
            risultato[i] = 100.0 if g > 0 else 50.0
        else:
            rs = g / p
            risultato[i] = 100.0 - 100.0 / (1.0 + rs)
    return tuple(risultato)


# ----------------------------------------------------------------------
# MACD
# ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Macd:
    linea: Serie
    segnale: Serie
    istogramma: Serie


def macd(chiusure: Sequence[float], veloce: int = 12, lenta: int = 26, segnale: int = 9) -> Macd:
    """MACD classico 12/26/9.

    La linea del segnale è la EMA della linea MACD, calcolata **sulla serie
    completa** e non sugli ultimi valori: ricalcolarla su una finestra corta
    darebbe un segnale che dipende da quante barre si sono chieste al broker.
    """
    ema_veloce = ema(chiusure, veloce)
    ema_lenta = ema(chiusure, lenta)

    linea: list[float | None] = [None] * len(chiusure)
    for i in range(len(chiusure)):
        if ema_veloce[i] is None or ema_lenta[i] is None:
            continue
        linea[i] = ema_veloce[i] - ema_lenta[i]  # type: ignore[operator]

    # La EMA del segnale parte dal primo valore definito della linea MACD.
    primo = next((i for i, v in enumerate(linea) if v is not None), None)
    serie_segnale: list[float | None] = [None] * len(chiusure)
    istogramma: list[float | None] = [None] * len(chiusure)
    if primo is not None:
        valori_linea = [v for v in linea[primo:] if v is not None]
        calcolata = ema(valori_linea, segnale)
        for offset, valore in enumerate(calcolata):
            serie_segnale[primo + offset] = valore
        for i in range(len(chiusure)):
            if linea[i] is not None and serie_segnale[i] is not None:
                istogramma[i] = linea[i] - serie_segnale[i]  # type: ignore[operator]

    return Macd(tuple(linea), tuple(serie_segnale), tuple(istogramma))


# ----------------------------------------------------------------------
# ATR
# ----------------------------------------------------------------------
def atr(
    massimi: Sequence[float],
    minimi: Sequence[float],
    chiusure: Sequence[float],
    periodo: int = 14,
) -> Serie:
    """Average True Range con smorzamento di Wilder."""
    return wilder(true_range(massimi, minimi, chiusure), periodo)


# ----------------------------------------------------------------------
# ADX
# ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Adx:
    adx: Serie
    di_positivo: Serie
    di_negativo: Serie


def adx(
    massimi: Sequence[float],
    minimi: Sequence[float],
    chiusure: Sequence[float],
    periodo: int = 14,
) -> Adx:
    """ADX e Directional Indicator secondo Wilder.

    Il punto delicato è la regola del movimento direzionale: in una barra solo
    uno fra +DM e -DM può essere diverso da zero, e vince quello maggiore. Una
    implementazione che li somma entrambi produce un ADX sistematicamente basso
    e fa sembrare laterale ogni mercato in tendenza.
    """
    lunghezza = len(massimi)
    dm_positivo: list[float | None] = [None] * lunghezza
    dm_negativo: list[float | None] = [None] * lunghezza
    for i in range(1, lunghezza):
        su = massimi[i] - massimi[i - 1]
        giu = minimi[i - 1] - minimi[i]
        dm_positivo[i] = su if (su > giu and su > 0) else 0.0
        dm_negativo[i] = giu if (giu > su and giu > 0) else 0.0

    tr_smorzato = wilder(true_range(massimi, minimi, chiusure), periodo)
    dmp_smorzato = wilder(tuple(dm_positivo), periodo)
    dmn_smorzato = wilder(tuple(dm_negativo), periodo)

    di_positivo: list[float | None] = [None] * lunghezza
    di_negativo: list[float | None] = [None] * lunghezza
    dx: list[float | None] = [None] * lunghezza
    for i in range(lunghezza):
        tr_valore = tr_smorzato[i]
        if tr_valore is None or tr_valore == 0 or dmp_smorzato[i] is None:
            continue
        dip = 100.0 * dmp_smorzato[i] / tr_valore  # type: ignore[operator]
        din = 100.0 * dmn_smorzato[i] / tr_valore  # type: ignore[operator]
        di_positivo[i] = dip
        di_negativo[i] = din
        somma = dip + din
        dx[i] = 100.0 * abs(dip - din) / somma if somma > 0 else 0.0

    return Adx(wilder(tuple(dx), periodo), tuple(di_positivo), tuple(di_negativo))


# ----------------------------------------------------------------------
# Bande di Bollinger
# ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Bollinger:
    superiore: Serie
    centrale: Serie
    inferiore: Serie
    #: Ampiezza relativa: (superiore - inferiore) / centrale. Adimensionale,
    #: quindi confrontabile fra strumenti con prezzi di ordini di grandezza
    #: diversi.
    ampiezza: Serie


def bollinger(
    chiusure: Sequence[float], periodo: int = 20, moltiplicatore: float = 2.0
) -> Bollinger:
    centrale = sma(chiusure, periodo)
    scarto = deviazione_standard(chiusure, periodo)
    superiore: list[float | None] = [None] * len(chiusure)
    inferiore: list[float | None] = [None] * len(chiusure)
    ampiezza: list[float | None] = [None] * len(chiusure)
    for i in range(len(chiusure)):
        if centrale[i] is None or scarto[i] is None:
            continue
        superiore[i] = centrale[i] + moltiplicatore * scarto[i]  # type: ignore[operator]
        inferiore[i] = centrale[i] - moltiplicatore * scarto[i]  # type: ignore[operator]
        if centrale[i]:
            ampiezza[i] = (superiore[i] - inferiore[i]) / centrale[i]  # type: ignore[operator]
    return Bollinger(tuple(superiore), tuple(centrale), tuple(inferiore), tuple(ampiezza))


def ampiezza_bollinger(bande: Bollinger, finestra: int = 120) -> tuple[float | None, float | None]:
    """Ampiezza corrente e sua posizione percentile nella finestra storica.

    Il secondo numero è quello che conta: un'ampiezza dello 0,8% non dice nulla
    da sola, ma se è il valore più basso degli ultimi sei mesi è una
    compressione.
    """
    return ultimo(bande.ampiezza), rango_percentile(bande.ampiezza, finestra)


# ----------------------------------------------------------------------
# Volatilità e momentum
# ----------------------------------------------------------------------
def volatilita_realizzata(
    chiusure: Sequence[float], periodo: int = 20, barre_per_anno: int | None = None
) -> tuple[float | None, float | None]:
    """Volatilità dei rendimenti logaritmici: per barra e, se possibile, annua.

    L'annualizzazione richiede di sapere quante barre ci sono in un anno, che
    dipende dal timeframe **e** dalle sessioni del broker. Il parametro è
    esplicito e opzionale: un numero annualizzato con un fattore inventato
    sembra autorevole e non lo è.
    """
    rendimenti = [r for r in rendimenti_logaritmici(chiusure)[-periodo:] if r is not None]
    if len(rendimenti) < max(5, periodo // 2):
        return None, None
    media = sum(rendimenti) / len(rendimenti)
    varianza = sum((r - media) ** 2 for r in rendimenti) / len(rendimenti)
    per_barra = varianza**0.5
    annua = per_barra * (barre_per_anno**0.5) if barre_per_anno else None
    return per_barra, annua


def momentum(
    chiusure: Sequence[float], periodi: Sequence[int] = (5, 20, 60)
) -> dict[int, float | None]:
    """Variazione percentuale su più orizzonti.

    Più orizzonti e non uno: un momentum positivo a 5 barre dentro un momentum
    negativo a 60 è un rimbalzo, non una tendenza, e la differenza conta.
    """
    risultato: dict[int, float | None] = {}
    for periodo in periodi:
        if len(chiusure) <= periodo or chiusure[-periodo - 1] == 0:
            risultato[periodo] = None
            continue
        risultato[periodo] = (chiusure[-1] / chiusure[-periodo - 1] - 1.0) * 100.0
    return risultato
