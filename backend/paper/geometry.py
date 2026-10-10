"""Geometria del piano e risoluzione delle barre: deterministica, una sola versione.

Qui vivono le tre regole che prima dipendevano dal modello o erano sparse:

* **tipo d'ingresso** — MARKET / STOP / LIMIT si deducono da direzione, livello
  desiderato e prezzo corrente. Il modello confondeva STOP e LIMIT (fase 5B): ora
  indica solo *dove* entrare, il codice decide *come*.
* **RR** — sempre calcolato dai prezzi, mai dichiarato.
* **ambiguità dentro la barra** — quando una barra tocca sia lo stop sia il target,
  l'ordine degli eventi non è noto. Regola, nell'ordine:
  1. apertura già oltre un livello (gap): vince quel livello, al prezzo d'apertura
     per lo stop (peggiore) e al target per il target;
  2. altrimenti, se esiste una barra più fine (M1), la si usa;
  3. altrimenti vince lo **stop** e l'esito è marcato `ambiguous`.

Le barre sono bid (MT5). Per uno SHORT l'uscita avviene all'ask: lo stop si tocca
quando `high + spread >= stop`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TipoIngresso = Literal["MARKET", "STOP", "LIMIT"]

#: Entro questa distanza (in ATR H1) dal prezzo un livello d'ingresso è "a mercato".
SOGLIA_MARKET_ATR = 0.1


def tipo_ingresso(verso: int, livello: float | None, prezzo: float, atr: float) -> TipoIngresso:
    """MARKET se il livello è assente o vicino; STOP se è oltre il prezzo nella
    direzione dell'operazione; LIMIT se è dalla parte opposta (ritracciamento)."""
    if livello is None or abs(livello - prezzo) <= SOGLIA_MARKET_ATR * atr:
        return "MARKET"
    return "STOP" if verso * (livello - prezzo) > 0 else "LIMIT"


def rr(entrata: float, stop: float, target: float) -> float | None:
    rischio = abs(entrata - stop)
    return round(abs(target - entrata) / rischio, 3) if rischio > 0 else None


@dataclass(frozen=True, slots=True)
class EsitoBarra:
    evento: Literal["SL", "TP"] | None
    prezzo: float | None = None
    #: Stop e target nella stessa barra senza modo di stabilire l'ordine.
    ambiguo: bool = False


def risolvi_barra(
    verso: int,
    stop: float,
    target: float,
    *,
    open_: float,
    high: float,
    low: float,
    spread: float = 0.0,
) -> EsitoBarra:
    """Stop o target in una barra bid, con lo spread sul lato dell'uscita."""
    if verso > 0:
        # LONG: esce al bid.
        tocca_sl, tocca_tp = low <= stop, high >= target
        gap_sl, gap_tp = open_ <= stop, open_ >= target
    else:
        # SHORT: esce all'ask = bid + spread.
        tocca_sl, tocca_tp = high + spread >= stop, low + spread <= target
        gap_sl, gap_tp = open_ + spread >= stop, open_ + spread <= target
    if gap_sl:
        return EsitoBarra("SL", open_ + (spread if verso < 0 else 0.0))
    if gap_tp:
        return EsitoBarra("TP", target)
    if tocca_sl and tocca_tp:
        return EsitoBarra("SL", stop, ambiguo=True)
    if tocca_sl:
        return EsitoBarra("SL", stop)
    if tocca_tp:
        return EsitoBarra("TP", target)
    return EsitoBarra(None)
