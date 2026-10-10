"""Riconoscimento di massimi e minimi locali (swing).

Il punto che conta più di tutto il resto di questo file: **uno swing richiede
barre alla propria destra per essere riconosciuto.** Un massimo è un massimo solo
se le `forza` barre successive non lo superano, quindi le ultime `forza` barre
della serie non sono classificabili.

Chi ignora questo dettaglio costruisce un sistema che "vede" i massimi nel
momento in cui si formano. Funziona magnificamente sullo storico e perde denaro
in tempo reale. Per questo ogni swing porta `confirmed`, la struttura di mercato
usa solo quelli confermati, e i non confermati esistono solo per essere mostrati.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from backend.core.enums import SwingKind
from backend.schemas.quant import SwingPoint

#: Barre esaminate per lato. Tre è un compromesso: con 1-2 si riconoscono troppi
#: swing e la struttura diventa rumore, con 5+ si perdono i movimenti operativi
#: sui timeframe bassi. Valore da validare in walk-forward (fase 9).
FORZA_PREDEFINITA = 3


def trova_swing(
    massimi: Sequence[float],
    minimi: Sequence[float],
    tempi: Sequence[datetime],
    forza: int = FORZA_PREDEFINITA,
) -> tuple[SwingPoint, ...]:
    """Tutti gli swing della serie, confermati e non, in ordine di tempo.

    Un massimo in `i` richiede `massimi[i]` maggiore di tutte le `forza` barre a
    sinistra e maggiore o uguale a quelle a destra. L'asimmetria fra `>` e `>=`
    evita di contare due volte i plateau: con due barre di pari massimo, solo la
    prima diventa swing.
    """
    if forza < 1:
        raise ValueError("La forza deve essere almeno 1")
    if not (len(massimi) == len(minimi) == len(tempi)):
        raise ValueError("Massimi, minimi e tempi devono avere pari lunghezza")

    ultimo_indice = len(massimi) - 1
    punti: list[SwingPoint] = []

    for i in range(forza, len(massimi) - 1 + 1):
        sinistra = range(max(0, i - forza), i)
        destra = range(i + 1, min(len(massimi), i + forza + 1))
        if not sinistra:
            continue

        confermato = i + forza <= ultimo_indice

        if all(massimi[i] > massimi[j] for j in sinistra) and all(
            massimi[i] >= massimi[j] for j in destra
        ):
            punti.append(
                SwingPoint(
                    index=i,
                    time=tempi[i],
                    price=massimi[i],
                    kind=SwingKind.HIGH,
                    confirmed=confermato,
                    strength=forza,
                )
            )

        if all(minimi[i] < minimi[j] for j in sinistra) and all(
            minimi[i] <= minimi[j] for j in destra
        ):
            punti.append(
                SwingPoint(
                    index=i,
                    time=tempi[i],
                    price=minimi[i],
                    kind=SwingKind.LOW,
                    confirmed=confermato,
                    strength=forza,
                )
            )

    punti.sort(key=lambda p: (p.index, p.kind.value))
    return tuple(punti)


def alterna(punti: Sequence[SwingPoint]) -> tuple[SwingPoint, ...]:
    """Riduce la sequenza a massimi e minimi alternati.

    Serve perché il riconoscimento per fratti può produrre due massimi di seguito
    senza un minimo in mezzo. Senza alternanza la struttura di mercato
    confronterebbe un massimo con un altro massimo poco più avanti e vedrebbe
    tendenze che non ci sono.

    Fra due massimi consecutivi resta il più alto, fra due minimi il più basso:
    è quello che conta per la struttura.
    """
    risultato: list[SwingPoint] = []
    for punto in punti:
        if not risultato:
            risultato.append(punto)
            continue
        precedente = risultato[-1]
        if precedente.kind is not punto.kind:
            risultato.append(punto)
            continue
        if punto.kind is SwingKind.HIGH:
            if punto.price > precedente.price:
                risultato[-1] = punto
        elif punto.price < precedente.price:
            risultato[-1] = punto
    return tuple(risultato)


def solo_confermati(punti: Sequence[SwingPoint]) -> tuple[SwingPoint, ...]:
    """Gli swing utilizzabili per decidere."""
    return tuple(p for p in punti if p.confirmed)
