"""Compressione, rottura, falsa rottura, ritest.

Il consolidamento viene misurato sulle barre **precedenti** l'ultima chiusa, e la
rottura si valuta confrontando l'ultima chiusura con quel range. Misurare il
range includendo la barra che lo rompe è un errore circolare: il range si
allargherebbe fino a contenere sempre la rottura, e nessun breakout verrebbe mai
rilevato.

**Correzione del 2026-10-04.** La prima versione classificava `RETEST` in base alla
sola vicinanza della chiusura al bordo del range. È sbagliato: un ritest è per
definizione il *ritorno su un livello già rotto*, e senza una rottura precedente
quella condizione è vera in continuazione dentro qualunque range stretto — il
risultato era che ogni mercato compresso veniva etichettato come "ritest". Ora un
ritest richiede una rottura recente, e si distingue dalla falsa rottura per
quanto il prezzo è rientrato:

* chiusura ancora oltre il livello, o entro una tolleranza da esso → **RETEST**,
  il livello è sotto esame;
* chiusura rientrata ben dentro il range → **FALSE_BREAKOUT**.

Resta una classificazione probabilistica: quale delle due sia, lo dirà il
mercato. Tutte le soglie sono euristiche dichiarate e riportate nel risultato
(`compression_threshold_atr`, `lookback_bars`); la loro validazione è materia di
walk-forward (fase 9), non di intuizione.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.core.enums import BreakoutKind
from backend.schemas.quant import BreakoutSnapshot

#: Barre su cui misurare il consolidamento.
LOOKBACK = 20

#: Ampiezza massima del range, in ATR, per parlare di compressione. Su 20 barre
#: un mercato normale occupa 4-6 ATR; sotto 3 è stretto.
SOGLIA_COMPRESSIONE_ATR = 3.0

#: Entro quante barre si cerca una rottura precedente.
BARRE_ROTTURA_RECENTE = 3

#: Distanza dal livello rotto, in ATR, entro cui il prezzo "sta ritestando".
TOLLERANZA_RITEST_ATR = 0.25


@dataclass(frozen=True, slots=True)
class _RotturaRecente:
    direzione: int  # +1 verso l'alto, -1 verso il basso
    livello: float
    barre_fa: int


def rileva_breakout(
    massimi: Sequence[float],
    minimi: Sequence[float],
    chiusure: Sequence[float],
    atr: float | None,
    *,
    lookback: int = LOOKBACK,
    soglia_compressione_atr: float = SOGLIA_COMPRESSIONE_ATR,
) -> BreakoutSnapshot:
    """Stato di rottura sull'ultima candela chiusa.

    Serve almeno `lookback + 2` barre: `lookback` per il range, una per la barra
    che eventualmente lo rompe, una per avere un confronto precedente.
    """
    minimo_necessario = lookback + 2
    if len(chiusure) < minimo_necessario or atr is None or atr <= 0:
        return BreakoutSnapshot(
            kind=BreakoutKind.NONE,
            compression_threshold_atr=soglia_compressione_atr,
            lookback_bars=lookback,
        )

    # Range sulle barre precedenti l'ultima chiusa.
    range_alto = max(massimi[-lookback - 1 : -1])
    range_basso = min(minimi[-lookback - 1 : -1])
    ampiezza_atr = (range_alto - range_basso) / atr
    chiusura = chiusure[-1]
    tolleranza = atr * TOLLERANZA_RITEST_ATR

    kind = (
        BreakoutKind.COMPRESSION if ampiezza_atr <= soglia_compressione_atr else BreakoutKind.NONE
    )
    livello_rotto: float | None = None
    barre_dalla_rottura: int | None = None

    if chiusura > range_alto:
        kind, livello_rotto, barre_dalla_rottura = BreakoutKind.BREAKOUT_UP, range_alto, 0
    elif chiusura < range_basso:
        kind, livello_rotto, barre_dalla_rottura = BreakoutKind.BREAKOUT_DOWN, range_basso, 0
    else:
        # Nessuna rottura adesso. Una rottura recente, invece, cambia la lettura
        # della situazione attuale: la classificazione come ritest o come falsa
        # rottura dipende da quanto il prezzo è rientrato.
        recente = _rottura_recente(chiusure, range_alto, range_basso, lookback)
        if recente is not None:
            livello_rotto = recente.livello
            barre_dalla_rottura = recente.barre_fa
            vicino = abs(chiusura - recente.livello) <= tolleranza
            if vicino:
                kind = BreakoutKind.RETEST
            elif recente.direzione > 0:
                kind = BreakoutKind.FALSE_BREAKOUT_UP
            else:
                kind = BreakoutKind.FALSE_BREAKOUT_DOWN

    return BreakoutSnapshot(
        kind=kind,
        range_high=round(range_alto, 8),
        range_low=round(range_basso, 8),
        range_width_atr=round(ampiezza_atr, 3),
        bars_in_range=lookback,
        broken_level=round(livello_rotto, 8) if livello_rotto is not None else None,
        bars_since_break=barre_dalla_rottura,
        compression_threshold_atr=soglia_compressione_atr,
        lookback_bars=lookback,
    )


def _rottura_recente(
    chiusure: Sequence[float],
    range_alto: float,
    range_basso: float,
    lookback: int,
) -> _RotturaRecente | None:
    """Cerca una chiusura oltre il range nelle barre immediatamente precedenti.

    Si guarda la **chiusura** e non il massimo intraday: una barra che sfiora il
    livello con l'ombra e chiude dentro non ha rotto niente, e trattarla come
    rottura genera falsi segnali a ripetizione.
    """
    disponibili = min(BARRE_ROTTURA_RECENTE, len(chiusure) - lookback - 1)
    for distanza in range(1, max(disponibili, 0) + 1):
        valore = chiusure[-1 - distanza]
        if valore > range_alto:
            return _RotturaRecente(direzione=1, livello=range_alto, barre_fa=distanza)
        if valore < range_basso:
            return _RotturaRecente(direzione=-1, livello=range_basso, barre_fa=distanza)
    return None
