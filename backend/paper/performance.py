"""Performance dei conti paper: equity, win rate, profit factor, expectancy, drawdown.

Tutto in R e in valuta del conto. Le statistiche sono anche divise per versione
della strategia (`strategy_version`): è l'esperimento interno, che **non blocca**
nulla — una versione nuova si affianca alle precedenti nelle tabelle.

Due misure "in ombra" (ADR 0027), che non cambiano nessun trade:

* **stop mobile** — per ogni trade chiuso, l'uscita che ogni regola avrebbe avuto,
  confrontata in R di prezzo con quella vera, sugli stessi trade;
* **posizioni correlate** — un trade è "correlato" se all'apertura lo stesso conto
  aveva già una posizione sulla stessa scommessa (stesso gruppo, stesso verso di
  rischio). Si ricostruisce dallo storico: vale anche per i trade passati.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from backend.paper.models import PaperBook, PaperTrade
from backend.paper.trailing import REGOLE

#: Sotto questo numero di trade chiusi il confronto fra conti non è significativo.
MINIMO_CONFRONTO = 30


def _riassunto(trades: list[PaperTrade]) -> dict[str, Any]:
    r = [t.r_multiple for t in trades]
    netti = [t.net_pnl for t in trades]
    vinti = [x for x in netti if x > 0]
    persi = [x for x in netti if x < 0]
    return {
        "trades": len(trades),
        "wins": len(vinti),
        "losses": len(persi),
        "win_rate": round(len(vinti) / len(trades), 3) if trades else None,
        "profit_factor": round(sum(vinti) / -sum(persi), 3) if persi else None,
        "expectancy_r": round(statistics.fmean(r), 3) if r else None,
        "expectancy_ccy": round(statistics.fmean(netti), 2) if netti else None,
        "total_r": round(sum(r), 3),
        "net_pnl": round(sum(netti), 2),
        "avg_win_r": round(statistics.fmean([x for x in r if x > 0]), 3)
        if any(x > 0 for x in r)
        else None,
        "avg_loss_r": round(statistics.fmean([x for x in r if x < 0]), 3)
        if any(x < 0 for x in r)
        else None,
    }


#: Strumento -> (gruppo, verso): due trade dello stesso gruppo con prodotto dei versi
#: positivo sono la stessa scommessa. Per i cambi il verso indica la valuta comune:
#: LONG USDJPY / EURJPY / GBPJPY = JPY debole; LONG EURUSD / GBPUSD = USD debole.
GRUPPI: dict[str, tuple[str, int]] = {
    "US30": ("Indici USA", 1),
    "US100": ("Indici USA", 1),
    "US500": ("Indici USA", 1),
    "USDJPY": ("Yen", 1),
    "EURJPY": ("Yen", 1),
    "GBPJPY": ("Yen", 1),
    "EURUSD": ("Dollaro", 1),
    "GBPUSD": ("Dollaro", 1),
    "XAUUSD": ("Metalli", 1),
    "XAGUSD": ("Metalli", 1),
}


def _scommessa(canonico: str, direzione: str) -> tuple[str, int] | None:
    g = GRUPPI.get(canonico)
    if g is None:
        return None
    return g[0], g[1] * (1 if direzione == "LONG" else -1)


def correlati(book: PaperBook) -> set[str]:
    """Id dei trade aperti mentre il conto aveva già la stessa scommessa in corso."""
    intervalli: list[tuple[str, datetime, datetime | None, tuple[str, int] | None]] = [
        (t.position_id, t.opened_at, t.closed_at, _scommessa(t.plan.canonical, t.plan.direction))
        for t in book.trades
    ] + [
        (p.position_id, p.opened_at, None, _scommessa(p.plan.canonical, p.plan.direction))
        for p in book.positions
        if p.opened_at is not None
    ]
    esito: set[str] = set()
    for pid, aperto, _, scommessa in intervalli:
        if scommessa is None:
            continue
        for altro, inizio, fine, sua in intervalli:
            if (
                altro != pid
                and sua == scommessa
                and inizio < aperto
                and (fine is None or fine > aperto)
            ):
                esito.add(pid)
                break
    return esito


def _correlazione(book: PaperBook, trades: list[PaperTrade]) -> dict[str, Any]:
    ids = correlati(book)
    si = [t for t in trades if t.position_id in ids]
    no = [t for t in trades if t.position_id not in ids]
    return {
        "correlated": _riassunto(si),
        "independent": _riassunto(no),
        #: Se la regola "una posizione per scommessa" fosse stata attiva: i trade
        #: correlati non ci sarebbero stati (stima: non considera cosa sarebbe successo
        #: al loro posto).
        "without_correlated_total_r": round(sum(t.r_multiple for t in no), 3),
        "groups": sorted({g for g, _ in GRUPPI.values()}),
    }


def stop_mobile_ombra(trades: list[PaperTrade]) -> dict[str, Any]:
    """Regole di stop mobile contro l'uscita vera, sugli stessi trade, in R di prezzo.

    Solo trade chiusi senza stop mobile reale e con la misura registrata: con una regola
    accesa l'uscita vera non sarebbe più il termine di paragone senza stop mobile.
    """
    base = [t for t in trades if t.shadow_r and t.price_r is not None and t.trailing_rule is None]
    vero = round(sum(t.price_r or 0.0 for t in base), 3)
    regole: dict[str, Any] = {}
    for nome, r in REGOLE.items():
        coppie = [(t.shadow_r[nome], t.price_r or 0.0) for t in base if nome in t.shadow_r]
        totale = round(sum(o for o, _ in coppie), 3)
        regole[nome] = {
            "name": r.nome,
            "description": r.descrizione,
            "trades": len(coppie),
            "total_r": totale,
            "difference_r": round(totale - sum(v for _, v in coppie), 3),
            "better": sum(1 for o, v in coppie if o > v + 0.01),
            "worse": sum(1 for o, v in coppie if o < v - 0.01),
        }
    return {"trades": len(base), "actual_r": vero, "rules": regole}


def performance(book: PaperBook) -> dict[str, Any]:
    trades = sorted(book.trades, key=lambda t: t.closed_at)
    saldo = picco = book.initial_balance
    dd = dd_pct = 0.0
    curva = [{"t": book.created_at.isoformat(), "equity": round(saldo, 2)}]
    for t in trades:
        saldo += t.net_pnl
        picco = max(picco, saldo)
        dd = max(dd, picco - saldo)
        dd_pct = max(dd_pct, (picco - saldo) / picco * 100 if picco > 0 else 0.0)
        curva.append({"t": t.closed_at.isoformat(), "equity": round(saldo, 2)})
    aperta = round(sum(p.unrealized_pnl or 0.0 for p in book.positions), 2)
    motivi: dict[str, int] = {}
    for t in trades:
        motivi[t.exit_reason] = motivi.get(t.exit_reason, 0) + 1
    versioni: dict[str, list[PaperTrade]] = {}
    for t in trades:
        versioni.setdefault(t.plan.strategy_version, []).append(t)
    return {
        "book": book.name,
        "currency": book.currency,
        "initial_balance": book.initial_balance,
        "balance": book.balance,
        "unrealized_pnl": aperta,
        "equity": round(book.balance + aperta, 2),
        "return_pct": round((book.balance + aperta) / book.initial_balance * 100 - 100, 3),
        "max_drawdown_ccy": round(dd, 2),
        "max_drawdown_pct": round(dd_pct, 3),
        **_riassunto(trades),
        "exit_reasons": motivi,
        "ambiguous_bars": sum(t.ambiguous_bar for t in trades),
        "costs": {
            "spread": round(sum(t.spread_cost for t in trades), 2),
            "slippage": round(sum(t.slippage_cost for t in trades), 2),
            "commission": round(sum(t.commission for t in trades), 2),
            "swap": round(sum(t.swap for t in trades), 2),
        },
        "mean_mae_r": round(statistics.fmean([t.mae_r for t in trades]), 3) if trades else None,
        "mean_mfe_r": round(statistics.fmean([t.mfe_r for t in trades]), 3) if trades else None,
        "mean_hours": round(statistics.fmean([t.hours for t in trades]), 2) if trades else None,
        "equity_curve": curva,
        "by_strategy_version": {v: _riassunto(ts) for v, ts in versioni.items()},
        "trailing_shadow": stop_mobile_ombra(trades),
        "correlation": _correlazione(book, trades),
        "by_trailing_rule": {
            (r or "nessuno"): _riassunto([t for t in trades if t.trailing_rule == r])
            for r in sorted({t.trailing_rule for t in trades}, key=lambda x: x or "")
        },
    }


def confronto(quant: PaperBook, ai: PaperBook) -> dict[str, Any]:
    q, a = performance(quant), performance(ai)
    n = min(q["trades"], a["trades"])
    diff = (
        round(a["expectancy_r"] - q["expectancy_r"], 3)
        if a["expectancy_r"] is not None and q["expectancy_r"] is not None
        else None
    )
    return {
        "quant": {
            k: q[k]
            for k in (
                "trades",
                "win_rate",
                "profit_factor",
                "expectancy_r",
                "total_r",
                "net_pnl",
                "max_drawdown_pct",
            )
        },
        "ai": {
            k: a[k]
            for k in (
                "trades",
                "win_rate",
                "profit_factor",
                "expectancy_r",
                "total_r",
                "net_pnl",
                "max_drawdown_pct",
            )
        },
        "expectancy_r_difference": diff,
        "verdict": "INSUFFICIENT_SAMPLE"
        if n < MINIMO_CONFRONTO
        else ("AI_BETTER" if (diff or 0) > 0 else "QUANT_BETTER_OR_EQUAL"),
        "note": f"Confronto indicativo solo con almeno {MINIMO_CONFRONTO} trade chiusi per conto.",
    }
