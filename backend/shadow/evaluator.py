"""OutcomeEvaluator: cosa è successo **dopo** la decisione, misurato sulle barre reali.

Separazione netta fra i due tempi:

* **alla decisione** il piano è fissato e registrato con il suo hash; non contiene
  nulla di successivo (`decided_at`);
* **dopo**, questo modulo legge solo barre **iniziate dopo** `decided_at` e misura:
  setup innescato o no, TP, SL, invalidazione, MAE, MFE, R simulato, tempo all'esito.

Regole di simulazione, conservative e dichiarate:

* percorso del prezzo su barre M5 (bid, come le fornisce MT5); lo spread si sottrae
  al risultato come costo in R;
* MARKET: ingresso all'apertura della prima barra M5 successiva; STOP/LIMIT: al
  livello, o all'apertura se il prezzo ci salta oltre;
* SL e TP nella stessa barra: vince lo **SL** (`ambiguous_bar=True`);
* un'invalidazione prima dell'ingresso annulla il setup; dopo l'ingresso chiude alla
  chiusura della barra in cui si verifica;
* all'orizzonte: chiusura alla chiusura dell'ultima barra (`TIME`);
* orizzonte non ancora trascorso e nessun esito: `PENDING`, da rivalutare.

L'orizzonte è in ore di calendario: un piano del venerdì sera può finire a mercato
chiuso, e allora l'ultima barra disponibile è quella del venerdì.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.ai.contracts import impronta
from backend.paper.geometry import risolvi_barra
from backend.shadow.conditions import Barra, PriceCondition, SorgenteBarre, prima_volta
from backend.shadow.plans import ShadowTradePlan

OUTCOME_VERSION = "shadow-outcome-1.0.0"
_LETTURA = ConfigDict(frozen=True, extra="ignore")


class ConditionHit(BaseModel):
    model_config = _LETTURA

    role: Literal["ENTRY", "INVALIDATION", "TARGET"]
    label: str
    #: False se mancano le barre dello strumento/timeframe: la condizione non è
    #: giudicabile, e lo si dichiara invece di considerarla "mai vera".
    verifiable: bool
    first_true_at: datetime | None = None


class MarketMove(BaseModel):
    """Cosa ha fatto il mercato nell'orizzonte, dal prezzo della decisione, in ATR H1.

    Si calcola per **ogni** piano, compresi WAIT e FILTERED: è ciò che permette di
    dire se un'attesa ha evitato una perdita o mancato un movimento.
    """

    model_config = _LETTURA

    max_up_atr: float
    max_down_atr: float
    close_change_atr: float


class ShadowOutcome(BaseModel):
    model_config = _LETTURA

    outcome_version: str = OUTCOME_VERSION
    plan_id: str
    plan_integrity_sha256: str
    group: str
    canonical: str
    decision: str
    plan_status: str
    decided_at: datetime
    evaluated_at: datetime
    #: Chiusura dell'ultima barra usata.
    data_through: datetime | None = None
    #: L'orizzonte è trascorso: l'esito non cambierà più.
    horizon_elapsed: bool

    status: Literal["NOT_APPLICABLE", "PENDING", "NOT_TRIGGERED", "CLOSED"]
    triggered: bool = False
    triggered_at: datetime | None = None
    entry_fill: float | None = None
    not_triggered_reason: str | None = None

    exit_reason: Literal["TP", "SL", "INVALIDATION", "TIME"] | None = None
    exit_at: datetime | None = None
    exit_price: float | None = None
    tp_hit: bool = False
    sl_hit: bool = False
    invalidation_hit: bool = False
    invalidation_label: str | None = None
    ambiguous_bar: bool = False

    mae_r: float | None = None
    mfe_r: float | None = None
    r_gross: float | None = None
    cost_r: float | None = None
    simulated_r: float | None = None
    time_to_outcome_hours: float | None = None

    conditions: tuple[ConditionHit, ...] = ()
    market_move: MarketMove | None = None
    bars_used: int = 0
    notes: tuple[str, ...] = ()


def _percorso(piano: ShadowTradePlan, sorgente: SorgenteBarre) -> list[Barra]:
    return [
        b
        for b in sorgente.get((piano.canonical, "M5"), ())
        if piano.decided_at <= b.inizio < piano.scadenza
    ]


def _movimento(piano: ShadowTradePlan, percorso: list[Barra]) -> MarketMove | None:
    if not percorso or not piano.atr_h1:
        return None
    rif, atr = piano.reference_price, piano.atr_h1
    return MarketMove(
        max_up_atr=round((max(b.high for b in percorso) - rif) / atr, 3),
        max_down_atr=round((rif - min(b.low for b in percorso)) / atr, 3),
        close_change_atr=round((percorso[-1].close - rif) / atr, 3),
    )


def _colpi(
    piano: ShadowTradePlan, sorgente: SorgenteBarre
) -> tuple[list[ConditionHit], dict[str, list[tuple[PriceCondition, datetime | None, bool]]]]:
    colpi: list[ConditionHit] = []
    per_ruolo: dict[str, list[tuple[PriceCondition, datetime | None, bool]]] = {
        "ENTRY": [],
        "INVALIDATION": [],
        "TARGET": [],
    }
    for ruolo, condizioni in (
        ("ENTRY", piano.entry_conditions),
        ("INVALIDATION", piano.invalidation_conditions),
        ("TARGET", piano.target_conditions),
    ):
        for c in condizioni:
            barre = sorgente.get((c.subject, c.timeframe))
            verificabile = barre is not None
            quando = (
                prima_volta(c, barre, dopo=piano.decided_at, entro=piano.scadenza)
                if barre is not None
                else None
            )
            per_ruolo[ruolo].append((c, quando, verificabile))
            colpi.append(
                ConditionHit(
                    role=ruolo,  # type: ignore[arg-type]
                    label=c.etichetta(),
                    verifiable=verificabile,
                    first_true_at=quando,
                )
            )
    return colpi, per_ruolo


def valuta(piano: ShadowTradePlan, sorgente: SorgenteBarre, *, adesso: datetime) -> ShadowOutcome:
    percorso = _percorso(piano, sorgente)
    trascorso = adesso >= piano.scadenza
    colpi, per_ruolo = _colpi(piano, sorgente)
    base = {
        "plan_id": piano.plan_id,
        "plan_integrity_sha256": piano.integrity_sha256,
        "group": piano.group,
        "canonical": piano.canonical,
        "decision": piano.decision,
        "plan_status": piano.status,
        "decided_at": piano.decided_at,
        "evaluated_at": adesso,
        "data_through": percorso[-1].fine if percorso else None,
        "horizon_elapsed": trascorso,
        "conditions": tuple(colpi),
        "market_move": _movimento(piano, percorso),
        "bars_used": len(percorso),
    }
    if piano.status != "ACTIVE" or piano.verso == 0:
        return ShadowOutcome(**base, status="NOT_APPLICABLE")  # type: ignore[arg-type]

    note: list[str] = [
        f"Condizione non verificabile (barre mancanti): {c.etichetta()}"
        for ruolo in per_ruolo.values()
        for c, _, ok in ruolo
        if not ok
    ]
    s = piano.verso
    assert piano.stop_loss is not None and piano.take_profit is not None
    sl, tp = piano.stop_loss, piano.take_profit

    # --- armamento: tutte le condizioni d'ingresso devono essersi verificate ---
    ingressi = per_ruolo["ENTRY"]
    armato: datetime | None = piano.decided_at
    if ingressi:
        tempi = [q for _, q, _ in ingressi]
        armato = None if any(q is None for q in tempi) else max(tempi)  # type: ignore[type-var]
    invalidazioni = [(c, q) for c, q, _ in per_ruolo["INVALIDATION"] if q is not None]
    prima_invalidazione = min(invalidazioni, key=lambda x: x[1]) if invalidazioni else None
    valido_fino = min(
        piano.decided_at + timedelta(hours=piano.entry_valid_hours or piano.horizon_hours),
        piano.scadenza,
    )

    # --- ingresso ---
    riempimento: tuple[int, float] | None = None
    invalidato_prima = False
    if armato is not None:
        for i, b in enumerate(percorso):
            if b.inizio < armato:
                continue
            if b.inizio >= valido_fino:
                break
            if prima_invalidazione and prima_invalidazione[1] <= b.inizio:
                invalidato_prima = True
                break
            prezzo = piano.entry_price or b.open
            if piano.entry_type == "MARKET":
                riempimento = (i, b.open)
            elif piano.entry_type == "STOP":
                if s > 0 and b.high >= prezzo:
                    riempimento = (i, max(prezzo, b.open))
                elif s < 0 and b.low <= prezzo:
                    riempimento = (i, min(prezzo, b.open))
            elif piano.entry_type == "LIMIT":
                if s > 0 and b.low <= prezzo:
                    riempimento = (i, min(prezzo, b.open))
                elif s < 0 and b.high >= prezzo:
                    riempimento = (i, max(prezzo, b.open))
            if riempimento:
                break

    if riempimento is None:
        if invalidato_prima:
            motivo = "INVALIDATED_BEFORE_ENTRY"
        elif armato is None and ingressi:
            motivo = "ENTRY_CONDITIONS_NOT_MET"
        elif not percorso:
            motivo = "NO_MARKET_DATA"
        else:
            motivo = "EXPIRED"
        in_attesa = adesso < valido_fino and not invalidato_prima
        return ShadowOutcome(
            **base,  # type: ignore[arg-type]
            status="PENDING" if in_attesa else "NOT_TRIGGERED",
            not_triggered_reason=None if in_attesa else motivo,
            notes=tuple(note),
        )

    indice, entrata = riempimento
    rischio = abs(entrata - sl)
    if rischio <= 0 or s * (entrata - sl) <= 0:
        # Gap oltre lo stop all'ingresso: perdita immediata, misurata come tale.
        rischio = abs((piano.entry_price or entrata) - sl) or 1e-12
    mae = mfe = 0.0
    uscita: tuple[str, float, datetime] | None = None
    ambigua = False
    for b in percorso[indice:]:
        favorevole = s * ((b.high if s > 0 else b.low) - entrata)
        avverso = s * (entrata - (b.low if s > 0 else b.high))
        # Regola unica di risoluzione della barra (gap, poi stop prudente se ambigua).
        esito = risolvi_barra(s, sl, tp, open_=b.open, high=b.high, low=b.low)
        colpito_tp = esito.evento == "TP"
        if esito.evento == "SL":
            ambigua = esito.ambiguo
            prezzo_sl = esito.prezzo if esito.prezzo is not None else sl
            mae = max(mae, s * (entrata - prezzo_sl))
            if not ambigua:
                mfe = max(mfe, favorevole)
            uscita = ("SL", prezzo_sl, b.fine)
            break
        mae = max(mae, avverso)
        if colpito_tp:
            # Oltre il target la posizione non c'è più: l'MFE si ferma al target.
            mfe = max(mfe, s * (tp - entrata))
            uscita = ("TP", tp, b.fine)
            break
        mfe = max(mfe, favorevole)
        if prima_invalidazione and percorso[indice].inizio < prima_invalidazione[1] <= b.fine:
            uscita = ("INVALIDATION", b.close, b.fine)
            break

    if uscita is None:
        if not trascorso:
            return ShadowOutcome(
                **base,  # type: ignore[arg-type]
                status="PENDING",
                triggered=True,
                triggered_at=percorso[indice].inizio,
                entry_fill=entrata,
                mae_r=round(mae / rischio, 3),
                mfe_r=round(mfe / rischio, 3),
                notes=tuple(note),
            )
        ultima = percorso[-1]
        uscita = ("TIME", ultima.close, ultima.fine)

    motivo_uscita, prezzo_uscita, istante = uscita
    lordo = s * (prezzo_uscita - entrata) / rischio
    costo = piano.spread_price / rischio
    return ShadowOutcome(
        **base,  # type: ignore[arg-type]
        status="CLOSED",
        triggered=True,
        triggered_at=percorso[indice].inizio,
        entry_fill=entrata,
        exit_reason=motivo_uscita,  # type: ignore[arg-type]
        exit_at=istante,
        exit_price=prezzo_uscita,
        tp_hit=motivo_uscita == "TP",
        sl_hit=motivo_uscita == "SL",
        invalidation_hit=motivo_uscita == "INVALIDATION",
        invalidation_label=(
            prima_invalidazione[0].etichetta()
            if motivo_uscita == "INVALIDATION" and prima_invalidazione
            else None
        ),
        ambiguous_bar=ambigua,
        mae_r=round(mae / rischio, 3),
        mfe_r=round(mfe / rischio, 3),
        r_gross=round(lordo, 3),
        cost_r=round(costo, 3),
        simulated_r=round(lordo - costo, 3),
        time_to_outcome_hours=round((istante - piano.decided_at).total_seconds() / 3600, 2),
        notes=tuple(note),
    )


def impronta_barre(barre: list[Barra]) -> str:
    return impronta([(b.inizio.isoformat(), b.open, b.high, b.low, b.close) for b in barre])
