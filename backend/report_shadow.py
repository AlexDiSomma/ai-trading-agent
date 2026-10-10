"""Shadow mode su dati reali XM: `python -m backend.report_shadow [esegui|valuta]`.

* `esegui` — decisioni dal vivo **adesso** e in replay a istanti passati della
  settimana scorsa (contesto tagliato all'istante, nessuna barra successiva), poi
  valutazione degli esiti e report.
* `valuta` — solo valutazione delle decisioni nel diario con i dati di adesso, e
  report. Da rilanciare quando gli orizzonti delle decisioni dal vivo sono trascorsi.

Strumenti: GOLD, EURUSD, US100Cash richiesti; il quarto lo sceglie l'agente a ogni
istante. Nessun ordine, nessuna size: solo piani ombra in prezzi e R.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime

from backend.config import get_settings
from backend.config.paths import PROJECT_ROOT
from backend.core.logging import configure_logging
from backend.services.container import ApplicationContainer
from backend.services.shadow_runner import ShadowRunner
from backend.shadow.evaluator import ShadowOutcome
from backend.shadow.metrics import report
from backend.shadow.records import PIPELINE_VERSION, DecisionSnapshot, OutcomeSnapshot

RICHIESTI = ("GOLD", "EURUSD", "US100Cash")
#: Istanti di replay (UTC), in piena sessione europea o americana della settimana scorsa.
ISTANTI_REPLAY = (
    datetime(2026, 9, 29, 13, 0, tzinfo=UTC),
    datetime(2026, 9, 30, 8, 0, tzinfo=UTC),
    datetime(2026, 9, 30, 14, 30, tzinfo=UTC),
    datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
    datetime(2026, 10, 1, 13, 0, tzinfo=UTC),
    datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
)


def _riga(t: str = "") -> None:
    print(t, flush=True)


def _livelli(p) -> str:  # type: ignore[no-untyped-def]
    if p.status != "ACTIVE":
        return ""
    return (
        f" {p.entry_type} @ {p.entry_price:g}  SL {p.stop_loss:g}  TP {p.take_profit:g}"
        f"  RR {p.computed_rr}  orizzonte {p.horizon_hours} h"
    )


def _esito(o: ShadowOutcome | None) -> str:
    if o is None:
        return "non valutato"
    if o.status == "NOT_APPLICABLE":
        m = o.market_move
        return "nessuna operazione" + (
            f" — mercato: +{m.max_up_atr} / −{m.max_down_atr} ATR, "
            f"chiusura {m.close_change_atr:+} ATR"
            if m
            else ""
        )
    if o.status == "PENDING":
        return f"PENDING (innescato={o.triggered}, MAE {o.mae_r} R, MFE {o.mfe_r} R)"
    if o.status == "NOT_TRIGGERED":
        return f"non innescato ({o.not_triggered_reason})"
    return (
        f"{o.exit_reason} dopo {o.time_to_outcome_hours} h → {o.simulated_r:+} R netti "
        f"(lordo {o.r_gross:+}, costo {o.cost_r}) · MAE {o.mae_r} R · MFE {o.mfe_r} R"
        + (" · barra ambigua" if o.ambiguous_bar else "")
    )


def stampa(d: DecisionSnapshot, esito: OutcomeSnapshot | None) -> None:
    _riga("-" * 110)
    _riga(
        f"{d.canonical} ({d.broker_symbol}) · {d.decided_at:%a %d/%m %H:%M} UTC · {d.mode} · "
        f"regime {d.global_regime} · dati globali {d.global_data_quality} · "
        f"prezzo {d.reference['price']} · ATR H1 {d.reference['atr_h1']:.5g}"
    )
    _riga(f"  selezione: {d.selection_reason}")
    for n in d.notes:
        _riga(f"  nota: {n}")
    esiti = {o.group: o for o in esito.outcomes} if esito else {}
    for p in d.plans:
        _riga(f"  [{p.group}] {p.status} {p.decision}{_livelli(p)}")
        if p.status != "ACTIVE" and p.reasons:
            _riga(f"      motivo: {p.reasons[0][:200]}")
        _riga(f"      esito: {_esito(esiti.get(p.group))}")
    for nome, rec in (("TESI", d.thesis_record), ("FILTRO", d.filter_record)):
        if rec is None:
            continue
        _riga(
            f"  {nome} AI: {rec.status} · {rec.call.model_snapshot or rec.call.model_requested} · "
            f"token {rec.call.input_tokens}/{rec.call.output_tokens} · record {rec.record_id}"
        )
        for m in rec.failure_reasons[:6]:
            _riga(f"      ✗ {m[:220]}")
        for m in rec.unsupported_claims[:4]:
            _riga(f"      ~ unsupported: {m[:200]}")
        t = rec.thesis
        if t is not None:
            _riga(
                f"      decisione {t.decision} · setup {t.setup.type} · etichetta {t.confidence} "
                # L'RR non è più dichiarato dal modello (prompt 2.x): lo calcola il codice.
                f"(non probabilità)"
            )
            _riga(f"      scenario: {t.scenario.narrative[:400]}")
            if t.wait_reason:
                _riga(f"      perché WAIT: {t.wait_reason[:300]}")
            for c in t.supporting_evidence[:3]:
                _riga(f"      + {c.statement[:160]}  [{', '.join(c.evidence_ids[:3])}]")
            for c in t.contradictory_evidence[:3]:
                _riga(f"      − {c.statement[:160]}  [{', '.join(c.evidence_ids[:3])}]")
            for c in t.invalidation_conditions:
                _riga(f"      × invalida: {c.etichetta()} — {c.description[:120]}")
            for u in t.uncertainties[:3]:
                _riga(
                    f"      ? [{u.impact}] {u.description[:140]}"
                    + (f" ⟶ manca: {u.missing_information[:100]}" if u.missing_information else "")
                )
        v = rec.verdict
        if v is not None:
            _riga(f"      verdetto {v.verdict} · etichetta {v.confidence}")
            for c in v.reasons[:3]:
                _riga(f"      · {c.statement[:180]}  [{', '.join(c.evidence_ids[:3])}]")


async def main(modo: str) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if modo not in ("esegui", "valuta"):
        # Prima di avviare broker e AI: `esegui` costa chiamate al modello.
        print("Uso: python -m backend.report_shadow [valuta|esegui]  (esegui chiama l'AI)")
        return 2
    settings = get_settings()
    configure_logging(settings.log, mode=str(settings.app.effective_mode))
    container = ApplicationContainer(settings)
    await container.startup()
    try:
        if container.broker is None:
            _riga(f"Broker non disponibile: {container.broker_error}")
            return 1
        runner = ShadowRunner(container)
        candidati = tuple(s for s in settings.risk.allowed_symbols if s not in RICHIESTI)
        dati = await runner.leggi((*RICHIESTI, *candidati))
        if modo == "esegui":
            for t in ISTANTI_REPLAY:
                await runner.decidi(dati, t, "REPLAY", richiesti=RICHIESTI, candidati=candidati)
            await runner.decidi(
                dati, dati.letto_alle, "LIVE", richiesti=RICHIESTI, candidati=candidati
            )
        runner.valuta_tutto(dati)

        decisioni = runner.diario.decisioni()
        escluse = runner.diario.altre_versioni()
        esiti = runner.diario.ultimi_esiti()
        for d in decisioni:
            stampa(d, esiti.get(d.decision_id))
        r = report(decisioni, esiti)
        _riga("=" * 110)
        _riga(f"Versione della pipeline: {PIPELINE_VERSION}")
        _riga(f"Decisioni di versioni precedenti escluse dalle metriche: {escluse}")
        _riga("METRICHE (preparano la valutazione: il campione è troppo piccolo per concludere)")
        for g in r.groups:
            _riga(
                f"  {g.group}: decisioni {g.decisions} · attivi {g.active_plans} · WAIT {g.wait} · "
                f"filtrati {g.filtered} · rifiutati {g.rejected} · innescati {g.triggered} · "
                f"chiusi {g.closed} (TP {g.tp}, SL {g.sl}, inval {g.invalidation}, "
                f"tempo {g.time_exit}) "
                f"· pending {g.pending} · R medio {g.mean_r} · R totale {g.total_r} · "
                f"win {g.win_rate} · PF {g.profit_factor} · DD {g.max_drawdown_r} R · "
                f"MAE {g.mean_mae_r} · MFE {g.mean_mfe_r}"
            )
        _riga(
            f"  Filtro: approvate {r.filter_approved_r.mean} R (n={r.filter_approved_r.n}), "
            f"respinte {r.filter_rejected_r.mean} R (n={r.filter_rejected_r.n}) "
            f"→ {r.filter_verdict}"
        )
        _riga(
            f"  Tesi − quant (appaiato): {r.thesis_vs_quant.mean} R "
            f"[{r.thesis_vs_quant.low_95}, {r.thesis_vs_quant.high_95}] n={r.thesis_vs_quant.n} · "
            f"vinte {r.thesis_wins} perse {r.thesis_losses} pari {r.thesis_ties} "
            f"→ {r.thesis_verdict}"
        )
        _riga(f"  Record AI: {r.ai_records} · affermazioni unsupported {r.unsupported_claims}")
        _riga(f"  Calibrazione etichetta: {r.confidence_calibration}")
        destinazione = (
            PROJECT_ROOT
            / "docs"
            / "validazione"
            / f"shadow_report_{datetime.now():%Y-%m-%d_%H%M}.json"
        )
        destinazione.write_text(
            json.dumps(r.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _riga(f"  report: {destinazione}")
        return 0
    finally:
        await container.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "valuta")))
