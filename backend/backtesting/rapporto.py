"""Metriche del backtest: in R e in equity, per metà del periodo e con Monte Carlo.

* **Due metà** (fuori campione a rovescio): una variante che migliora solo in una metà
  è probabilmente adattata al caso. Le varianti non sono state ottimizzate sui dati:
  sono quelle discusse prima di vedere i risultati (ADR 0028).
* **Monte Carlo**: ricampionamento con reinserimento delle operazioni (seme fisso,
  quindi ripetibile) → intervallo al 90% dell'expectancy e quota di campioni in
  cui l'expectancy è positiva. Ignora la dipendenza fra operazioni vicine: è una
  misura dell'incertezza, non una garanzia.
"""

from __future__ import annotations

import random
import statistics
from collections import Counter
from datetime import datetime
from typing import Any

from backend.backtesting.simulatore import Esito, Operazione

CAMPIONI = 2_000
SEME = 20261009


def _base(ops: list[Operazione]) -> dict[str, Any]:
    r = [o.r for o in ops]
    vinti = [x for x in r if x > 0]
    persi = [x for x in r if x <= 0]
    return {
        "trades": len(ops),
        "win_rate": round(len(vinti) / len(r), 3) if r else None,
        "expectancy_r": round(statistics.fmean(r), 3) if r else None,
        "total_r": round(sum(r), 2),
        "profit_factor": round(sum(vinti) / -sum(persi), 2) if persi and sum(persi) < 0 else None,
    }


def _equity(ops: list[Operazione], iniziale: float) -> dict[str, float]:
    saldo = picco = iniziale
    dd = 0.0
    for o in sorted(ops, key=lambda x: x.chiusa):
        saldo += o.pnl
        picco = max(picco, saldo)
        dd = max(dd, (picco - saldo) / picco * 100)
    return {
        "final": round(saldo, 2),
        "return_pct": round((saldo / iniziale - 1) * 100, 2),
        "max_drawdown_pct": round(dd, 2),
    }


def montecarlo(r: list[float]) -> dict[str, float | None]:
    if len(r) < 10:
        return {"low_90": None, "high_90": None, "positive_share": None}
    caso = random.Random(SEME)  # noqa: S311 — statistica ripetibile, non crittografia
    medie = sorted(statistics.fmean(caso.choices(r, k=len(r))) for _ in range(CAMPIONI))
    return {
        "low_90": round(medie[int(CAMPIONI * 0.05)], 3),
        "high_90": round(medie[int(CAMPIONI * 0.95)], 3),
        "positive_share": round(sum(m > 0 for m in medie) / CAMPIONI, 3),
    }


def riassunto(e: Esito, meta: datetime) -> dict[str, Any]:
    ops = sorted(e.operazioni, key=lambda o: o.aperta)
    prima = [o for o in ops if o.aperta < meta]
    seconda = [o for o in ops if o.aperta >= meta]
    per_simbolo: dict[str, list[Operazione]] = {}
    for o in ops:
        per_simbolo.setdefault(o.canonico, []).append(o)
    return {
        "variant": e.variante.nome,
        "description": e.variante.descrizione,
        **_base(ops),
        **_equity(ops, e.equity_iniziale),
        "first_half": _base(prima),
        "second_half": _base(seconda),
        "montecarlo": montecarlo([o.r for o in ops]),
        "exit_reasons": dict(Counter(o.motivo for o in ops)),
        "ambiguous_bars": sum(o.ambigua for o in ops),
        "mean_cost_r": round(statistics.fmean([o.costo_r for o in ops]), 3) if ops else None,
        "mean_mfe_r": round(statistics.fmean([o.mfe_r for o in ops]), 3) if ops else None,
        "by_symbol": {k: _base(v) for k, v in sorted(per_simbolo.items())},
        "signals": e.segnali,
        "rejections": dict(sorted(e.rifiuti.items(), key=lambda x: -x[1])),
    }
