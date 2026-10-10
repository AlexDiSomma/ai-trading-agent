"""Validazione dei trade paper chiusi: contano per il gate LIVE solo quelli coerenti.

Un trade è "validato" se i suoi numeri tornano fra loro: tempi ordinati, prezzi e
volume positivi, rischio misurato, P/L netto = lordo − commissioni + swap, R = netto /
rischio, escursioni non negative, motivo d'uscita coerente col prezzo. Un trade che
non torna segnala un difetto nel ciclo entry → management → exit, e non conta.
"""

from __future__ import annotations

from backend.paper.models import PaperTrade

TOLLERANZA_VALUTA = 0.02
TOLLERANZA_R = 0.01


def problemi_trade(t: PaperTrade) -> list[str]:
    p: list[str] = []
    if t.closed_at < t.opened_at:
        p.append("chiusura prima dell'apertura")
    if t.entry_fill <= 0 or t.exit_price <= 0 or t.volume <= 0:
        p.append("prezzi o volume non positivi")
    if t.risk_amount <= 0:
        p.append("rischio (1R) non misurato")
    if abs(t.net_pnl - (t.gross_pnl - t.commission + t.swap)) > TOLLERANZA_VALUTA:
        p.append("P/L netto incoerente con lordo, commissioni e swap")
    if t.risk_amount > 0 and abs(t.r_multiple - t.net_pnl / t.risk_amount) > TOLLERANZA_R:
        p.append("R incoerente con P/L e rischio")
    if t.mae_r < 0 or t.mfe_r < 0:
        p.append("escursioni negative")
    s = t.plan.verso
    if t.exit_reason == "TP" and s * (t.exit_price - t.plan.target) < -1e-9:
        p.append("uscita TP prima del target")
    if t.exit_reason == "SL" and s * (t.exit_price - t.plan.stop) > 1e-9:
        p.append("uscita SL migliore dello stop")
    if t.exit_reason == "TRAIL" and not t.trailing_rule:
        p.append("uscita a stop mobile senza regola attiva")
    # Le escursioni devono contenere l'uscita: un'uscita oltre il massimo registrato
    # è il difetto di misura trovato il 7 ottobre (MFE non aggiornato sul tick). Solo
    # per i trade con `price_r`, registrato da quando la misura è corretta.
    if t.price_r is not None:
        if t.price_r > t.mfe_r + TOLLERANZA_R:
            p.append("uscita oltre il massimo favorevole registrato")
        if -t.price_r > t.mae_r + TOLLERANZA_R:
            p.append("uscita oltre la massima escursione contraria registrata")
    return p


def trade_validati(trades: list[PaperTrade]) -> tuple[int, list[dict[str, object]]]:
    scartati = [
        {"position_id": t.position_id, "problems": pr} for t in trades if (pr := problemi_trade(t))
    ]
    return len(trades) - len(scartati), scartati
