"""Forza relativa e direzione dei gruppi: valute, metalli, indici.

Due letture diverse degli stessi momentum normalizzati:

* **classifica** — chi è più forte e chi più debole, dentro il gruppo e in
  assoluto. Il confronto fra gruppi è indicativo, e la classifica lo dichiara: le
  valute sono misurate contro il paniere, metalli e indici contro la propria
  valuta di quotazione;
* **direzione di gruppo** — dove vanno le azioni nel complesso, dove vanno i
  metalli, con l'**ampiezza** (quanti componenti concordano). Un Nasdaq forte con
  gli altri cinque indici deboli non è "azioni forti": è `MIXED`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from backend.config.global_universe import UNIVERSO
from backend.core.enums import ReferenceGroup, StrengthLabel, Timeframe
from backend.global_market import parametri as par
from backend.global_market.currency_strength import Misurazioni, etichetta
from backend.global_market.features import composito, concordi
from backend.global_market.qualita import Stati, qualita_dimensione
from backend.schemas.global_market import (
    CurrencyStrengthView,
    GroupDirection,
    RelativeStrengthEntry,
    RelativeStrengthView,
)


def momentum_per_tf(misure: Misurazioni, canonico: str) -> dict[Timeframe, float | None]:
    per = misure.get(canonico, {})
    return {tf: (per[tf].momentum_z if tf in per else None) for tf in par.TIMEFRAMES}


def classifica(
    misure: Misurazioni, valute: CurrencyStrengthView, stati: Stati
) -> RelativeStrengthView:
    voci: list[RelativeStrengthEntry] = [
        RelativeStrengthEntry(
            name=c.currency,
            group="CURRENCY",
            score=c.score,
            measured_against="paniere delle altre sette valute",
            by_timeframe=c.by_timeframe,
        )
        for c in valute.currencies
    ]
    richiesti: list[str] = []
    for s in UNIVERSO:
        if s.group is ReferenceGroup.FX:
            continue
        richiesti.append(s.canonical)
        per_tf = momentum_per_tf(misure, s.canonical)
        voci.append(
            RelativeStrengthEntry(
                name=s.canonical,
                group=str(s.group),
                score=composito(per_tf),
                measured_against=s.quote_currency,
                by_timeframe=per_tf,
            )
        )

    def chiave(v: RelativeStrengthEntry) -> tuple[bool, float, str]:
        return (v.score is None, -(v.score or 0.0), v.name)

    ordinate = sorted(voci, key=chiave)
    rango_gruppo: dict[str, int] = {}
    risultato: list[RelativeStrengthEntry] = []
    posizione = 0
    for voce in ordinate:
        if voce.score is None:
            risultato.append(voce)
            continue
        posizione += 1
        rango_gruppo[voce.group] = rango_gruppo.get(voce.group, 0) + 1
        risultato.append(
            voce.model_copy(
                update={"rank_overall": posizione, "rank_in_group": rango_gruppo[voce.group]}
            )
        )
    qualita = qualita_dimensione("relative_strength", richiesti, stati)
    # La parte valutaria ha la sua qualità: vale la peggiore delle due.
    if valute.quality.quality.rank > qualita.quality.rank:
        qualita = qualita.model_copy(
            update={
                "quality": valute.quality.quality,
                "reasons": (*qualita.reasons, *valute.quality.reasons),
            }
        )
    return RelativeStrengthView(entries=tuple(risultato), quality=qualita)


def direzione_gruppo(
    nome: str, membri: Sequence[str], misure: Misurazioni, stati: Stati
) -> GroupDirection:
    """Direzione composita di un gruppo, con ampiezza e coerenza fra timeframe."""
    per_membro: dict[str, float | None] = {}
    per_tf: dict[Timeframe, float | None] = {}
    for tf in par.TIMEFRAMES:
        valori = [
            m.momentum_z
            for c in membri
            if (m := misure.get(c, {}).get(tf)) is not None and m.momentum_z is not None
        ]
        per_tf[tf] = round(math.fsum(valori) / len(valori), 6) if valori else None
    for c in sorted(membri):
        per_membro[c] = composito(momentum_per_tf(misure, c))

    score = composito(per_tf)
    qualita = qualita_dimensione(nome, membri, stati)
    validi = [v for v in per_membro.values() if v is not None]
    ampiezza: float | None = None
    if score is not None and validi:
        segno = 1 if score >= 0 else -1
        ampiezza = round(sum(1 for v in validi if v * segno > 0) / len(validi), 4)

    label = etichetta(score, per_tf)
    note = ""
    if (
        label in (StrengthLabel.STRONG, StrengthLabel.WEAK)
        and ampiezza is not None
        and len(validi) >= 3
        and ampiezza < par.SOGLIA_AMPIEZZA
    ):
        label = StrengthLabel.MIXED
        note = (
            f"Composito {score:+.2f} ma solo il {ampiezza:.0%} dei componenti concorda: "
            "movimento non condiviso dal gruppo."
        )
    return GroupDirection(
        group=nome,
        label=label,
        score=score,
        breadth=ampiezza,
        by_timeframe=per_tf,
        members=per_membro,
        timeframes_agree=concordi(per_tf),
        quality=qualita,
        note=note,
    )


def etichetta_strumento(
    misure: Mapping[str, Mapping[Timeframe, object]], canonico: str
) -> StrengthLabel:
    per_tf = momentum_per_tf(misure, canonico)  # type: ignore[arg-type]
    return etichetta(composito(per_tf), per_tf)
