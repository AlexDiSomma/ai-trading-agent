"""Stop mobile: regole, misura in ombra e (se l'utente la accende) applicazione reale.

Una regola dice dove portare lo stop in funzione del **picco** raggiunto dal trade, in
multipli di R (R = distanza fra ingresso e stop iniziale). Il picco si aggiorna solo
sulle barre M1 **chiuse**: aggiornarlo sul tick farebbe confrontare lo stop nuovo con
minimi della stessa barra avvenuti *prima* del picco (ordine ignoto, uscita falsa).

Ogni posizione porta con sé, per **tutte** le regole, uno stop "ombra": quale uscita
avrebbe avuto con quella regola. Non tocca il trade vero. Alla chiusura il trade
registra il risultato in R di prezzo di ogni regola accanto al proprio, così il
confronto è sugli stessi trade, senza ipotesi a posteriori (ADR 0027).

Lo stop non arretra mai: ogni regola può solo avvicinarlo al prezzo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.paper.geometry import risolvi_barra


@dataclass(frozen=True, slots=True)
class Regola:
    nome: str
    descrizione: str
    #: Picco (in R) da cui la regola comincia a spostare lo stop.
    attiva_da_r: float
    #: FISSO: stop a `valore_r` dall'ingresso. SEGUE: stop a `valore_r` sotto il picco.
    tipo: Literal["FISSO", "SEGUE"]
    valore_r: float

    def stop_r(self, picco_r: float) -> float | None:
        """Livello dello stop in R dall'ingresso (positivo = in guadagno), o None."""
        if picco_r < self.attiva_da_r:
            return None
        return self.valore_r if self.tipo == "FISSO" else picco_r - self.valore_r


REGOLE: dict[str, Regola] = {
    "pareggio_1r": Regola(
        "Pareggio a +1R", "A +1R lo stop va al prezzo d'ingresso.", 1.0, "FISSO", 0.0
    ),
    "blocca_05r": Regola(
        "Blocca +0,5R a +1R",
        "A +1R lo stop va a +0,5R: metà del rischio resta in tasca.",
        1.0,
        "FISSO",
        0.5,
    ),
    "trailing_1r": Regola(
        "Trailing 1R da +1R",
        "Da +1R lo stop segue il massimo a 1R di distanza.",
        1.0,
        "SEGUE",
        1.0,
    ),
}


class Ombra(BaseModel):
    """Stato di una regola applicata in ombra a una posizione."""

    model_config = ConfigDict(extra="ignore")

    #: Stop corrente della regola, in R dall'ingresso; None = ancora quello iniziale.
    stop_r: float | None = None
    #: Uscita della regola (R di prezzo), se già avvenuta.
    esito_r: float | None = None


def nuovo_stop_r(regola: Regola, picco_r: float, attuale: float | None) -> float | None:
    """Stop dopo un nuovo picco: mai più lontano di quello attuale."""
    proposto = regola.stop_r(picco_r)
    if proposto is None:
        return attuale
    return proposto if attuale is None else max(attuale, proposto)


def prezzo_stop(entrata: float, verso: int, distanza: float, stop_r: float) -> float:
    return entrata + verso * stop_r * distanza


def esito_barra(
    verso: int,
    entrata: float,
    distanza: float,
    stop: float,
    target: float,
    *,
    open_: float,
    high: float,
    low: float,
    spread: float,
    slittamento: float,
) -> float | None:
    """R di prezzo dell'uscita in questa barra con questo stop, o None se non esce.

    Stessa regola del trade vero (`risolvi_barra`: gap, poi stop prudente) e stesso
    slittamento sull'uscita allo stop: il confronto non favorisce nessuno.
    """
    e = risolvi_barra(verso, stop, target, open_=open_, high=high, low=low, spread=spread)
    if e.evento is None:
        return None
    prezzo = (e.prezzo or stop) - verso * slittamento if e.evento == "SL" else target
    return round(verso * (prezzo - entrata) / distanza, 3)
