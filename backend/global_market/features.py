"""Serie di riferimento e misure per singola serie: taglio temporale, momentum, volatilità.

**Il taglio temporale è il punto in cui si impedisce il look-ahead.** Ogni serie
entra nel motore già tagliata con `taglia(serie, limite)`: restano solo le barre
la cui **fine** (apertura + durata) non supera il limite. Una barra che si chiude
dopo l'istante di analisi non esiste per il motore, anche se il broker la fornisce.
Un test aggiunge barre future e verifica che il contesto non cambi.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from backend.core.enums import ReferenceGroup, Timeframe
from backend.global_market import parametri as par
from backend.quant.indicators import atr, bollinger
from backend.quant.series import rango_percentile


@dataclass(frozen=True, slots=True)
class SerieDiRiferimento:
    """Barre chiuse di uno strumento di riferimento su un timeframe."""

    canonical: str
    group: ReferenceGroup
    timeframe: Timeframe
    #: Apertura di ogni barra, in ora broker (come MT5).
    tempi: tuple[datetime, ...]
    massimi: tuple[float, ...]
    minimi: tuple[float, ...]
    chiusure: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.chiusure)


def taglia(serie: SerieDiRiferimento, limite: datetime | None) -> SerieDiRiferimento:
    """Solo le barre chiuse entro `limite` (ora broker). `None`: nessun taglio."""
    if limite is None:
        return serie
    durata = timedelta(minutes=serie.timeframe.minutes)
    n = sum(1 for t in serie.tempi if t + durata <= limite)
    # Le barre sono ordinate: basta contare quante stanno prima del limite.
    return SerieDiRiferimento(
        serie.canonical,
        serie.group,
        serie.timeframe,
        serie.tempi[:n],
        serie.massimi[:n],
        serie.minimi[:n],
        serie.chiusure[:n],
    )


def _std(valori: Sequence[float]) -> float | None:
    if len(valori) < 5:
        return None
    media = math.fsum(valori) / len(valori)
    varianza = math.fsum((v - media) ** 2 for v in valori) / len(valori)
    return math.sqrt(varianza) if varianza > 0 else None


def _log_rendimenti(chiusure: Sequence[float]) -> list[float]:
    return [
        math.log(b / a)
        for a, b in pairwise(chiusure)
        if a > 0 and b > 0 and math.isfinite(a) and math.isfinite(b)
    ]


@dataclass(frozen=True, slots=True)
class Misure:
    """Misure di una serie all'ultima barra chiusa. `None` dove non definite."""

    #: Rendimento su `ORIZZONTE` barre diviso per la volatilità attesa sullo stesso
    #: orizzonte: un numero confrontabile fra EURUSD e US100.
    momentum_z: float | None
    volatilita_barra: float | None
    atr_percentile: float | None
    bollinger_percentile: float | None
    rapporto_volatilita: float | None


def misura(serie: SerieDiRiferimento) -> Misure:
    chiusure = serie.chiusure
    if len(chiusure) < par.BARRE_MINIME:
        return Misure(None, None, None, None, None)

    rendimenti = _log_rendimenti(chiusure)
    sigma = _std(rendimenti[-par.FINESTRA_VOLATILITA :])
    momentum_z: float | None = None
    if sigma and chiusure[-par.ORIZZONTE - 1] > 0 and chiusure[-1] > 0:
        r = math.log(chiusure[-1] / chiusure[-par.ORIZZONTE - 1])
        z = r / (sigma * math.sqrt(par.ORIZZONTE))
        momentum_z = max(-par.LIMITE_Z, min(par.LIMITE_Z, z)) if math.isfinite(z) else None

    breve = _std(rendimenti[-par.VOL_BREVE :])
    lunga = _std(rendimenti[-par.VOL_LUNGA :])
    rapporto = breve / lunga if breve is not None and lunga else None

    atr_pct: float | None = None
    bb_pct: float | None = None
    if len(chiusure) >= par.BARRE_PER_PERCENTILI:
        serie_atr = atr(serie.massimi, serie.minimi, chiusure, 14)
        atr_pct = rango_percentile(serie_atr, par.FINESTRA_PERCENTILE_ATR)
        ampiezza = bollinger(chiusure, 20).ampiezza
        bb_pct = rango_percentile(ampiezza, par.FINESTRA_PERCENTILE_BOLLINGER)

    return Misure(
        momentum_z=_r(momentum_z),
        volatilita_barra=_r(sigma, 8),
        atr_percentile=_r(atr_pct),
        bollinger_percentile=_r(bb_pct),
        rapporto_volatilita=_r(rapporto),
    )


def _r(valore: float | None, cifre: int = 6) -> float | None:
    """Arrotondamento fisso: rende l'uscita identica fra esecuzioni e macchine."""
    if valore is None or not math.isfinite(valore):
        return None
    return round(valore, cifre)


def composito(per_tf: dict[Timeframe, float | None], minimo: int = 2) -> float | None:
    """Media pesata sui timeframe disponibili; `None` con meno di `minimo` valori."""
    validi = [(tf, v) for tf, v in per_tf.items() if v is not None]
    if len(validi) < minimo:
        return None
    peso = math.fsum(par.PESI[tf] for tf, _ in validi)
    return _r(math.fsum(par.PESI[tf] * v for tf, v in validi) / peso)


def concordi(per_tf: dict[Timeframe, float | None]) -> bool | None:
    """Vero se tutti i valori disponibili e non trascurabili hanno lo stesso segno."""
    segni = {1 if v > 0 else -1 for v in per_tf.values() if v is not None and abs(v) >= 0.1}
    if not segni:
        return None
    return len(segni) == 1


def mediana(valori: Sequence[float | None]) -> float | None:
    validi = sorted(v for v in valori if v is not None)
    if not validi:
        return None
    n = len(validi)
    centro = n // 2
    return _r(validi[centro] if n % 2 else (validi[centro - 1] + validi[centro]) / 2)
