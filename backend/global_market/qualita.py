"""Qualità dei dati per dimensione del contesto globale.

Regola unica per tutte le dimensioni, così la stessa situazione dà lo stesso
giudizio ovunque:

* uno strumento "conta" se ha almeno due timeframe utilizzabili (il composito ne
  richiede due);
* `INSUFFICIENT` se conta meno della metà degli strumenti richiesti;
* `GOOD` solo se ne conta almeno il 90% **e** tutte le serie usate sono fresche
  con età verificata;
* `DEGRADED` in tutti gli altri casi — in particolare a mercato chiuso (dati
  dell'ultima sessione) e con l'ora broker ignota (freschezza non verificabile).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.core.enums import GlobalDataQuality, SeriesStatus, Timeframe
from backend.global_market import parametri as par
from backend.schemas.global_market import DimensionQuality

#: Stato di ogni serie: canonico → timeframe → stato.
Stati = Mapping[str, Mapping[Timeframe, SeriesStatus]]


def qualita_dimensione(
    nome: str, richiesti: Sequence[str], stati: Stati, *, minimo_timeframe: int = 2
) -> DimensionQuality:
    if not richiesti:
        return DimensionQuality(
            dimension=nome,
            quality=GlobalDataQuality.INSUFFICIENT,
            coverage=0.0,
            reasons=("Nessuno strumento richiesto.",),
        )
    usati = 0
    non_freschi: set[SeriesStatus] = set()
    esclusi: list[str] = []
    for canonico in sorted(richiesti):
        per_tf = stati.get(canonico, {})
        utilizzabili = [s for s in per_tf.values() if s.usable]
        if len(utilizzabili) >= minimo_timeframe:
            usati += 1
            non_freschi.update(s for s in utilizzabili if s is not SeriesStatus.FRESH)
        else:
            motivo = ", ".join(sorted({str(s) for s in per_tf.values()})) or "MISSING"
            esclusi.append(f"{canonico} ({motivo})")

    copertura = round(usati / len(richiesti), 4)
    motivi: list[str] = []
    if esclusi:
        motivi.append("Esclusi: " + "; ".join(esclusi) + ".")
    if SeriesStatus.CLOSED in non_freschi:
        motivi.append("Mercato dedotto chiuso: dati dell'ultima sessione.")
    if SeriesStatus.UNVERIFIED in non_freschi:
        motivi.append("Freschezza non verificabile: ora del broker non misurata (ADR 0015).")

    if copertura < par.COPERTURA_MINIMA:
        qualita = GlobalDataQuality.INSUFFICIENT
        motivi.insert(0, f"Copertura {copertura:.0%}, sotto il minimo del 50%.")
    elif copertura >= par.COPERTURA_BUONA and not non_freschi:
        qualita = GlobalDataQuality.GOOD
    else:
        qualita = GlobalDataQuality.DEGRADED
    return DimensionQuality(
        dimension=nome, quality=qualita, coverage=copertura, reasons=tuple(motivi)
    )


def peggiore(*qualita: GlobalDataQuality) -> GlobalDataQuality:
    return max(qualita, key=lambda q: q.rank) if qualita else GlobalDataQuality.INSUFFICIENT


def al_massimo(
    qualita: DimensionQuality, tetto: GlobalDataQuality, motivo: str
) -> DimensionQuality:
    """Peggiora una qualità fino a `tetto` se è migliore, aggiungendo il motivo."""
    if qualita.quality.rank >= tetto.rank:
        return qualita
    return qualita.model_copy(update={"quality": tetto, "reasons": (*qualita.reasons, motivo)})
