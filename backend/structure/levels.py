"""Supporti e resistenze dagli swing confermati.

Un livello non è un prezzo esatto, è una **zona**: il mercato non torna sullo
stesso decimale. Qui gli swing vicini vengono raggruppati entro una tolleranza
espressa in ATR, perché mezzo ATR su EURUSD e mezzo ATR su US100Cash sono
distanze diverse in punti ma equivalenti in significato.

La forza di un livello è un punteggio **euristico** su numero di contatti e
attualità. Non c'è validazione statistica dietro, e il campo lo dichiara: serve a
ordinare i livelli fra loro, non a stimare la probabilità che tengano.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.core.enums import LevelKind, SwingKind
from backend.schemas.quant import PriceLevel, SwingPoint
from backend.structure.swings import solo_confermati

#: Tolleranza di raggruppamento, in ATR. Due swing entro mezzo ATR sono lo
#: stesso livello.
TOLLERANZA_ATR = 0.5

#: Livelli restituiti per lato. Oltre, la lista diventa illeggibile e ogni
#: prezzo risulta "vicino a un livello".
MASSIMI_PER_LATO = 3


def trova_livelli(
    punti: Sequence[SwingPoint],
    prezzo_corrente: float,
    atr: float | None,
    *,
    ultimo_indice: int,
    tolleranza_atr: float = TOLLERANZA_ATR,
    massimi_per_lato: int = MASSIMI_PER_LATO,
) -> tuple[PriceLevel, ...]:
    """Supporti sotto il prezzo e resistenze sopra, i più rilevanti.

    Senza un ATR valido la funzione restituisce una tupla vuota invece di
    inventare una tolleranza: raggruppare livelli con una soglia arbitraria
    produrrebbe zone che non significano nulla.
    """
    confermati = solo_confermati(punti)
    if not confermati or atr is None or atr <= 0:
        return ()

    tolleranza = atr * tolleranza_atr
    gruppi = _raggruppa(confermati, tolleranza)

    livelli: list[PriceLevel] = []
    for prezzo, contatti, ultimo_contatto in gruppi:
        kind = LevelKind.SUPPORT if prezzo < prezzo_corrente else LevelKind.RESISTANCE
        # Attualità: 1 se l'ultimo contatto è sulla barra più recente, decresce
        # linearmente sulle barre disponibili.
        attualita = ultimo_contatto / ultimo_indice if ultimo_indice > 0 else 0.0
        # Contatti: saturati a 4, oltre i quali un livello non è "più forte" ma
        # semplicemente un intervallo molto frequentato.
        peso_contatti = min(contatti, 4) / 4
        forza = round(0.6 * peso_contatti + 0.4 * attualita, 4)
        livelli.append(
            PriceLevel(
                price=round(prezzo, 8),
                kind=kind,
                touches=contatti,
                last_touch_index=ultimo_contatto,
                distance_atr=round(abs(prezzo_corrente - prezzo) / atr, 3),
                strength=min(max(forza, 0.0), 1.0),
            )
        )

    supporti = sorted(
        (lv for lv in livelli if lv.kind is LevelKind.SUPPORT),
        key=lambda lv: lv.distance_atr or 0.0,
    )[:massimi_per_lato]
    resistenze = sorted(
        (lv for lv in livelli if lv.kind is LevelKind.RESISTANCE),
        key=lambda lv: lv.distance_atr or 0.0,
    )[:massimi_per_lato]

    return tuple(sorted([*supporti, *resistenze], key=lambda lv: lv.price))


def _raggruppa(punti: Sequence[SwingPoint], tolleranza: float) -> list[tuple[float, int, int]]:
    """Raggruppa gli swing in zone. Restituisce (prezzo medio, contatti, ultimo indice).

    I massimi e i minimi vengono raggruppati **insieme**: un livello che ha fatto
    da resistenza e poi da supporto è più significativo, non meno, e tenerli
    separati lo spezzerebbe in due zone deboli.
    """
    ordinati = sorted(punti, key=lambda p: p.price)
    gruppi: list[list[SwingPoint]] = []
    for punto in ordinati:
        if gruppi and abs(punto.price - gruppi[-1][-1].price) <= tolleranza:
            gruppi[-1].append(punto)
        else:
            gruppi.append([punto])

    risultato: list[tuple[float, int, int]] = []
    for gruppo in gruppi:
        prezzo = sum(p.price for p in gruppo) / len(gruppo)
        risultato.append((prezzo, len(gruppo), max(p.index for p in gruppo)))
    return risultato


def conta_tipi(punti: Sequence[SwingPoint]) -> tuple[int, int]:
    """Quanti massimi e quanti minimi confermati. Utile per la diagnostica."""
    confermati = solo_confermati(punti)
    massimi = sum(1 for p in confermati if p.kind is SwingKind.HIGH)
    return massimi, len(confermati) - massimi
