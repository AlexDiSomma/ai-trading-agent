"""Metriche per decidere, più avanti, se l'AI aggiunge valore alla baseline quant.

Domande e misure:

1. **Ogni gruppo da solo** (A, B, C): operazioni, tasso d'innesco, esiti TP/SL/
   invalidazione/tempo, R medio e mediano, R totale, profit factor, drawdown massimo in
   R, MAE/MFE medi, tempo medio all'esito.
2. **Il filtro (B contro A)**: R medio delle operazioni di A **approvate** contro
   quello delle **respinte** (l'esito controfattuale delle respinte è noto, perché A
   viene simulato comunque). Il filtro vale se respinge le peggiori: differenza
   positiva, con intervallo bootstrap.
3. **La tesi (C contro A)**: confronto **appaiato** per decisione (stesso strumento,
   stesso istante); un WAIT vale 0 R. Differenza media con intervallo bootstrap e
   conteggio di vittorie/sconfitte.
4. **Affidabilità dell'AI**: quote di output VALID / REJECTED / UNAVAILABLE e di
   affermazioni non sostenute.
5. **Calibrazione dell'etichetta di confidence**: per LOW/MEDIUM/HIGH, numero,
   frequenza di R > 0 e R medio. È una tabella da osservare, non una probabilità.

Soglie di campione dichiarate: sotto `MINIMO_OPERAZIONI` operazioni chiuse per
gruppo, o `MINIMO_COPPIE` decisioni appaiate, il verdetto è `INSUFFICIENT_SAMPLE`
qualunque sia il numero. Il bootstrap usa un seme fisso: lo stesso diario dà sempre
lo stesso report.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.shadow.evaluator import ShadowOutcome
from backend.shadow.records import DecisionSnapshot, OutcomeSnapshot

MINIMO_OPERAZIONI = 30
MINIMO_COPPIE = 50
SEME = 20261005
CAMPIONI_BOOTSTRAP = 2000
GRUPPI = ("A_QUANT_ONLY", "B_QUANT_AI_FILTER", "C_AI_THESIS")

_LETTURA = ConfigDict(frozen=True, extra="ignore")


class GroupStats(BaseModel):
    model_config = _LETTURA

    group: str
    decisions: int
    active_plans: int
    wait: int
    filtered: int
    rejected: int
    triggered: int
    closed: int
    pending: int
    not_triggered: int
    trigger_rate: float | None
    tp: int
    sl: int
    invalidation: int
    time_exit: int
    ambiguous_bars: int
    win_rate: float | None
    mean_r: float | None
    median_r: float | None
    total_r: float
    profit_factor: float | None
    max_drawdown_r: float
    mean_mae_r: float | None
    mean_mfe_r: float | None
    mean_hours_to_outcome: float | None


class Interval(BaseModel):
    model_config = _LETTURA

    mean: float | None
    low_95: float | None
    high_95: float | None
    n: int


class ShadowReport(BaseModel):
    model_config = _LETTURA

    groups: tuple[GroupStats, ...]
    filter_approved_r: Interval
    filter_rejected_r: Interval
    filter_value: Interval
    filter_verdict: str
    thesis_vs_quant: Interval
    thesis_wins: int
    thesis_losses: int
    thesis_ties: int
    thesis_verdict: str
    ai_records: dict[str, dict[str, int]]
    unsupported_claims: int
    confidence_calibration: dict[str, dict[str, Any]]
    notes: tuple[str, ...]


def _media(v: Sequence[float]) -> float | None:
    return round(statistics.fmean(v), 3) if v else None


def _bootstrap(valori: Sequence[float], rng: random.Random) -> Interval:
    if len(valori) < 2:
        return Interval(mean=_media(valori), low_95=None, high_95=None, n=len(valori))
    medie = sorted(
        statistics.fmean(rng.choices(valori, k=len(valori))) for _ in range(CAMPIONI_BOOTSTRAP)
    )
    return Interval(
        mean=_media(valori),
        low_95=round(medie[int(0.025 * len(medie))], 3),
        high_95=round(medie[int(0.975 * len(medie)) - 1], 3),
        n=len(valori),
    )


def _differenza(a: Sequence[float], b: Sequence[float], rng: random.Random) -> Interval:
    """media(a) − media(b), campioni indipendenti."""
    if len(a) < 2 or len(b) < 2:
        m = (_media(a) or 0) - (_media(b) or 0) if a and b else None
        return Interval(
            mean=None if m is None else round(m, 3),
            low_95=None,
            high_95=None,
            n=min(len(a), len(b)),
        )
    diff = sorted(
        statistics.fmean(rng.choices(a, k=len(a))) - statistics.fmean(rng.choices(b, k=len(b)))
        for _ in range(CAMPIONI_BOOTSTRAP)
    )
    return Interval(
        mean=round(statistics.fmean(a) - statistics.fmean(b), 3),
        low_95=round(diff[int(0.025 * len(diff))], 3),
        high_95=round(diff[int(0.975 * len(diff)) - 1], 3),
        n=min(len(a), len(b)),
    )


def _drawdown(r: Sequence[float]) -> float:
    picco = cumulato = peggiore = 0.0
    for x in r:
        cumulato += x
        picco = max(picco, cumulato)
        peggiore = max(peggiore, picco - cumulato)
    return round(peggiore, 3)


def statistiche_gruppo(
    gruppo: str, coppie: list[tuple[DecisionSnapshot, ShadowOutcome | None]]
) -> GroupStats:
    piani = [d.piano(gruppo) for d, _ in coppie]
    esiti = [o for _, o in coppie if o is not None]
    chiusi = sorted((o for o in esiti if o.status == "CLOSED"), key=lambda o: o.exit_at)  # type: ignore[arg-type,return-value]
    r = [o.simulated_r for o in chiusi if o.simulated_r is not None]
    attivi = [p for p in piani if p.status == "ACTIVE"]
    innescati = [o for o in esiti if o.triggered]
    giudicabili = [o for o in esiti if o.plan_status == "ACTIVE" and o.status != "PENDING"]
    guadagni = sum(x for x in r if x > 0)
    perdite = -sum(x for x in r if x < 0)
    return GroupStats(
        group=gruppo,
        decisions=len(piani),
        active_plans=len(attivi),
        wait=sum(p.status == "WAIT" for p in piani),
        filtered=sum(p.status == "FILTERED" for p in piani),
        rejected=sum(p.status == "REJECTED" for p in piani),
        triggered=len(innescati),
        closed=len(chiusi),
        pending=sum(o.status == "PENDING" for o in esiti),
        not_triggered=sum(o.status == "NOT_TRIGGERED" for o in esiti),
        trigger_rate=round(len([o for o in giudicabili if o.triggered]) / len(giudicabili), 3)
        if giudicabili
        else None,
        tp=sum(o.exit_reason == "TP" for o in chiusi),
        sl=sum(o.exit_reason == "SL" for o in chiusi),
        invalidation=sum(o.exit_reason == "INVALIDATION" for o in chiusi),
        time_exit=sum(o.exit_reason == "TIME" for o in chiusi),
        ambiguous_bars=sum(o.ambiguous_bar for o in chiusi),
        win_rate=round(sum(x > 0 for x in r) / len(r), 3) if r else None,
        mean_r=_media(r),
        median_r=round(statistics.median(r), 3) if r else None,
        total_r=round(sum(r), 3),
        profit_factor=round(guadagni / perdite, 3) if perdite > 0 else None,
        max_drawdown_r=_drawdown(r),
        mean_mae_r=_media([o.mae_r for o in chiusi if o.mae_r is not None]),
        mean_mfe_r=_media([o.mfe_r for o in chiusi if o.mfe_r is not None]),
        mean_hours_to_outcome=_media(
            [o.time_to_outcome_hours for o in chiusi if o.time_to_outcome_hours is not None]
        ),
    )


def _r_decisione(o: ShadowOutcome | None) -> float | None:
    """R di una decisione per il confronto appaiato: WAIT/REJECTED/non innescato = 0.

    None se l'esito non è ancora definitivo (PENDING o non valutato).
    """
    if o is None or o.status == "PENDING":
        return None
    if o.status == "CLOSED":
        return o.simulated_r
    return 0.0


def report(
    decisioni: Sequence[DecisionSnapshot], esiti: dict[str, OutcomeSnapshot]
) -> ShadowReport:
    # Seme fisso: riproducibilità del report, non crittografia.
    rng = random.Random(SEME)  # noqa: S311
    per_gruppo: dict[str, list[tuple[DecisionSnapshot, ShadowOutcome | None]]] = {
        g: [] for g in GRUPPI
    }
    for d in decisioni:
        snap = esiti.get(d.decision_id)
        for g in GRUPPI:
            o = next((x for x in snap.outcomes if x.group == g), None) if snap else None
            per_gruppo[g].append((d, o))
    gruppi = tuple(statistiche_gruppo(g, per_gruppo[g]) for g in GRUPPI)

    # --- filtro: esiti di A divisi per verdetto di B ---
    approvate: list[float] = []
    respinte: list[float] = []
    for (d, oa), (_, _ob) in zip(
        per_gruppo["A_QUANT_ONLY"], per_gruppo["B_QUANT_AI_FILTER"], strict=True
    ):
        b = d.piano("B_QUANT_AI_FILTER")
        if oa is None or oa.status != "CLOSED" or oa.simulated_r is None:
            continue
        if b.status == "ACTIVE":
            approvate.append(oa.simulated_r)
        elif b.status == "FILTERED":
            respinte.append(oa.simulated_r)
    valore_filtro = _differenza(approvate, respinte, rng)
    if len(approvate) + len(respinte) < MINIMO_OPERAZIONI:
        verdetto_filtro = "INSUFFICIENT_SAMPLE"
    elif valore_filtro.low_95 is not None and valore_filtro.low_95 > 0:
        verdetto_filtro = "FILTER_ADDS_VALUE"
    elif valore_filtro.high_95 is not None and valore_filtro.high_95 < 0:
        verdetto_filtro = "FILTER_DESTROYS_VALUE"
    else:
        verdetto_filtro = "NO_SIGNIFICANT_DIFFERENCE"

    # --- tesi contro quant, appaiate per decisione ---
    differenze: list[float] = []
    for (_, oa), (_, oc) in zip(per_gruppo["A_QUANT_ONLY"], per_gruppo["C_AI_THESIS"], strict=True):
        ra, rc = _r_decisione(oa), _r_decisione(oc)
        if ra is not None and rc is not None:
            differenze.append(rc - ra)
    confronto = _bootstrap(differenze, rng)
    if len(differenze) < MINIMO_COPPIE:
        verdetto_tesi = "INSUFFICIENT_SAMPLE"
    elif confronto.low_95 is not None and confronto.low_95 > 0:
        verdetto_tesi = "AI_THESIS_ADDS_VALUE"
    elif confronto.high_95 is not None and confronto.high_95 < 0:
        verdetto_tesi = "AI_THESIS_WORSE"
    else:
        verdetto_tesi = "NO_SIGNIFICANT_DIFFERENCE"

    # --- affidabilità e calibrazione ---
    stati: dict[str, dict[str, int]] = {"thesis": {}, "filter": {}}
    non_sostenute = 0
    calibrazione: dict[str, list[float]] = {}
    for d in decisioni:
        for nome, rec in (("thesis", d.thesis_record), ("filter", d.filter_record)):
            if rec is not None:
                stati[nome][rec.status] = stati[nome].get(rec.status, 0) + 1
                non_sostenute += len(rec.unsupported_claims)
        c = d.piano("C_AI_THESIS")
        snap = esiti.get(d.decision_id)
        oc = next((x for x in snap.outcomes if x.group == "C_AI_THESIS"), None) if snap else None
        if c.ai_confidence_label and c.status == "ACTIVE" and oc and oc.status == "CLOSED":
            calibrazione.setdefault(c.ai_confidence_label, []).append(oc.simulated_r or 0.0)
    tabella = {
        k: {
            "n": len(v),
            "share_r_positive": round(sum(x > 0 for x in v) / len(v), 3),
            "mean_r": _media(v),
        }
        for k, v in sorted(calibrazione.items())
    }
    return ShadowReport(
        groups=gruppi,
        filter_approved_r=_bootstrap(approvate, rng),
        filter_rejected_r=_bootstrap(respinte, rng),
        filter_value=valore_filtro,
        filter_verdict=verdetto_filtro,
        thesis_vs_quant=confronto,
        thesis_wins=sum(x > 0 for x in differenze),
        thesis_losses=sum(x < 0 for x in differenze),
        thesis_ties=sum(x == 0 for x in differenze),
        thesis_verdict=verdetto_tesi,
        ai_records=stati,
        unsupported_claims=non_sostenute,
        confidence_calibration=tabella,
        notes=(
            f"Verdetti solo con almeno {MINIMO_OPERAZIONI} operazioni chiuse (filtro) e "
            f"{MINIMO_COPPIE} decisioni appaiate (tesi).",
            "R al netto dello spread; SL e TP nella stessa barra contano come SL.",
            "La confidence dell'AI è un'etichetta: la tabella serve a calibrarla, non a usarla.",
        ),
    )
