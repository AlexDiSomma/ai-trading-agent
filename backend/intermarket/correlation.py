"""Correlazioni fra strumenti: funzioni pure, senza NaN.

Tre garanzie, verificate da test di proprietà:

1. **Il risultato è in [-1, 1] oppure `None`**, mai NaN né infinito. Una serie
   costante ha varianza zero e la correlazione non è definita: si restituisce
   `None`, non 0 (che vorrebbe dire "nessuna relazione", un'affermazione).
2. **I rendimenti si allineano per orario**, non per posizione. Indici e forex
   hanno sessioni diverse: la barra 300 dell'uno non è la barra 300 dell'altro.
   Si usano solo gli istanti presenti in entrambe le serie.
3. **Nessun dato dal futuro**: le funzioni lavorano solo sulle barre ricevute, e
   chi le chiama passa barre già tagliate all'istante di analisi.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Correlazione di Pearson; `None` se non definita o con dati non finiti."""
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    if not all(math.isfinite(v) for v in xs) or not all(math.isfinite(v) for v in ys):
        return None
    try:
        media_x = math.fsum(xs) / n
        media_y = math.fsum(ys) / n
        sxx = math.fsum((x - media_x) ** 2 for x in xs)
        syy = math.fsum((y - media_y) ** 2 for y in ys)
        if not (math.isfinite(sxx) and math.isfinite(syy)) or sxx <= 0 or syy <= 0:
            return None
        sxy = math.fsum((x - media_x) * (y - media_y) for x, y in zip(xs, ys, strict=True))
        r = sxy / math.sqrt(sxx) / math.sqrt(syy)
    except (OverflowError, ZeroDivisionError):
        # Trovato dal test di proprietà: valori finiti ma enormi (1e308) fanno
        # traboccare le somme. Non è una correlazione calcolabile: None.
        return None
    if not math.isfinite(r):
        return None
    # Gli arrotondamenti possono portare a 1.0000000000000002.
    return max(-1.0, min(1.0, r))


def rendimenti_per_orario(
    tempi: Sequence[datetime], chiusure: Sequence[float]
) -> dict[datetime, float]:
    """Rendimenti logaritmici indicizzati per l'orario della barra che li chiude.

    Una chiusura non positiva o non finita interrompe la catena: il rendimento di
    quella barra e della successiva non esistono, invece di diventare ±infinito.
    """
    risultato: dict[datetime, float] = {}
    for i in range(1, len(chiusure)):
        a, b = chiusure[i - 1], chiusure[i]
        if a > 0 and b > 0 and math.isfinite(a) and math.isfinite(b):
            risultato[tempi[i]] = math.log(b / a)
    return risultato


def allinea(
    a: dict[datetime, float], b: dict[datetime, float]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Coppie di rendimenti agli istanti comuni, in ordine di tempo."""
    comuni = sorted(a.keys() & b.keys())
    return tuple(a[t] for t in comuni), tuple(b[t] for t in comuni)


def correlazioni_mobili(
    xs: Sequence[float], ys: Sequence[float], finestra: int
) -> tuple[float | None, ...]:
    """Correlazione su ogni finestra di `finestra` coppie consecutive."""
    if finestra < 3 or len(xs) < finestra:
        return ()
    return tuple(
        pearson(xs[i - finestra : i], ys[i - finestra : i]) for i in range(finestra, len(xs) + 1)
    )


def stabilita(mobili: Sequence[float | None], riferimento: float | None) -> float | None:
    """Quota delle finestre con lo stesso segno della correlazione di riferimento.

    Una relazione che cambia segno metà delle volte non è una relazione: è rumore
    che a tratti sembra una regola.
    """
    if riferimento is None or riferimento == 0:
        return None
    valide = [m for m in mobili if m is not None]
    if not valide:
        return None
    segno = 1 if riferimento > 0 else -1
    concordi = sum(1 for m in valide if m * segno > 0)
    return concordi / len(valide)
