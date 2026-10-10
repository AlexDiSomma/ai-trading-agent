"""Struttura di mercato: successione di massimi e minimi, rotture, cambio di carattere.

Il vocabolario è quello della price action, ma il significato qui è
**probabilistico**: "UPTREND" vuol dire "la successione degli swing confermati è
compatibile con una tendenza rialzista", non "il prezzo salirà".

Tre concetti, in ordine di affidabilità decrescente:

1. **struttura** (massimi e minimi crescenti o decrescenti) — è un fatto
   osservabile sugli swing confermati;
2. **rottura di struttura** (break of structure) — la chiusura supera l'ultimo
   swing confermato opposto: un fatto, ma sul singolo evento dice poco;
3. **cambio di carattere** (change of character) — una rottura *contro* la
   struttura prevalente. È il meno affidabile dei tre e il più usato a sproposito:
   qui viene segnalato, mai trattato come conferma.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.core.enums import StructureBreak, StructureTrend, SwingKind
from backend.schemas.quant import StructureSnapshot, SwingPoint
from backend.structure.swings import alterna, solo_confermati

#: Swing confermati minimi per parlare di struttura: due massimi e due minimi.
#: Con meno, qualunque classificazione è un'illusione costruita su tre punti.
SWING_MINIMI = 4


def analizza_struttura(punti: Sequence[SwingPoint], chiusura_corrente: float) -> StructureSnapshot:
    """Stato della struttura di mercato.

    Usa **solo** gli swing confermati (vedi `swings.py`): includere quelli
    recenti non confermati significherebbe classificare la struttura sulla base
    di massimi che il mercato non ha ancora validato.
    """
    confermati = alterna(solo_confermati(punti))
    massimi = [p for p in confermati if p.kind is SwingKind.HIGH]
    minimi = [p for p in confermati if p.kind is SwingKind.LOW]

    ultimo_massimo = massimi[-1].price if massimi else None
    ultimo_minimo = minimi[-1].price if minimi else None
    massimo_precedente = massimi[-2].price if len(massimi) >= 2 else None
    minimo_precedente = minimi[-2].price if len(minimi) >= 2 else None

    tendenza = _classifica(ultimo_massimo, massimo_precedente, ultimo_minimo, minimo_precedente)
    if len(confermati) < SWING_MINIMI:
        tendenza = StructureTrend.UNDEFINED

    rottura = StructureBreak.NONE
    if ultimo_massimo is not None and chiusura_corrente > ultimo_massimo:
        rottura = StructureBreak.BREAK_UP
    elif ultimo_minimo is not None and chiusura_corrente < ultimo_minimo:
        rottura = StructureBreak.BREAK_DOWN

    # Cambio di carattere: rottura nella direzione contraria alla struttura.
    cambio = (tendenza is StructureTrend.UPTREND and rottura is StructureBreak.BREAK_DOWN) or (
        tendenza is StructureTrend.DOWNTREND and rottura is StructureBreak.BREAK_UP
    )

    return StructureSnapshot(
        trend=tendenza,
        last_swing_high=ultimo_massimo,
        last_swing_low=ultimo_minimo,
        previous_swing_high=massimo_precedente,
        previous_swing_low=minimo_precedente,
        structure_break=rottura,
        change_of_character=cambio,
        confirmed_swings=len(confermati),
    )


def _classifica(
    ultimo_massimo: float | None,
    massimo_precedente: float | None,
    ultimo_minimo: float | None,
    minimo_precedente: float | None,
) -> StructureTrend:
    """Tendenza dalla coppia di massimi e dalla coppia di minimi.

    Richiede **entrambe** le condizioni: massimi crescenti *e* minimi crescenti.
    Accontentarsi di una delle due farebbe chiamare rialzista un mercato che fa
    massimi più alti su minimi più bassi, cioè che si sta allargando.
    """
    if None in (ultimo_massimo, massimo_precedente, ultimo_minimo, minimo_precedente):
        return StructureTrend.UNDEFINED

    massimi_crescenti = ultimo_massimo > massimo_precedente  # type: ignore[operator]
    minimi_crescenti = ultimo_minimo > minimo_precedente  # type: ignore[operator]

    if massimi_crescenti and minimi_crescenti:
        return StructureTrend.UPTREND
    if not massimi_crescenti and not minimi_crescenti:
        return StructureTrend.DOWNTREND
    return StructureTrend.RANGE
