"""Gate di rischio pre-ingresso: regole deterministiche sui limiti dell'utente.

Riceve numeri, non oggetti dell'AI né del piano: così il package `risk` resta
indipendente da `ai` anche indirettamente (invariante 2). Ogni violazione è un motivo
leggibile; nessun motivo = ingresso consentito. I limiti vengono da `RiskLimits`
(file `.env`, `RISK_*`), immutabili a runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.config.risk_limits import RiskLimits


@dataclass(frozen=True)
class RichiestaIngresso:
    simbolo: str
    rr: float
    stop_atr: float
    spread_atr: float | None
    #: Rischio dell'operazione in valuta del conto (perdita allo stop, dal broker).
    rischio: float
    equity: float
    posizioni_aperte: int
    simboli_aperti: frozenset[str]
    rischio_aperto: float
    #: Risultato realizzato oggi (giornata del broker), negativo se perdita.
    pnl_oggi: float
    #: Picco storico dell'equity del conto, per il drawdown massimo.
    picco_equity: float
    ora_broker: datetime | None
    kill_switch: bool
    finestra_evento: bool
    dati_validi: bool
    motivi_dati: tuple[str, ...] = ()
    #: Risultato della settimana del broker (realizzato + flottante).
    pnl_settimana: float = 0.0
    #: Notizie ad alto impatto confermate e recenti sugli asset dello strumento.
    motivi_notizie: tuple[str, ...] = ()


def valuta_ingresso(r: RichiestaIngresso, limiti: RiskLimits) -> list[str]:
    motivi: list[str] = []
    if r.kill_switch:
        motivi.append("Kill switch attivo.")
    if not r.dati_validi:
        motivi.extend(r.motivi_dati or ("Dati o quotazione non validi.",))
    if r.ora_broker is None:
        motivi.append("Ora del broker ignota: sessione non verificabile.")
    elif not limiti.is_session_open(r.ora_broker):
        motivi.append(f"Fuori dalle sessioni consentite ({r.ora_broker:%a %H:%M} ora broker).")
    motivi.extend(r.motivi_notizie)
    if r.finestra_evento:
        motivi.append("Finestra di un evento ad alto impatto sulle valute dello strumento.")
    if r.rr < limiti.minimum_rr:
        motivi.append(f"RR {r.rr:.2f} sotto il minimo {limiti.minimum_rr}.")
    if not limiti.min_stop_distance_atr <= r.stop_atr <= limiti.max_stop_distance_atr:
        motivi.append(
            f"Stop a {r.stop_atr:.2f} ATR H1, fuori da "
            f"[{limiti.min_stop_distance_atr}, {limiti.max_stop_distance_atr}]."
        )
    if r.spread_atr is not None and r.spread_atr > limiti.max_spread_atr_ratio:
        motivi.append(f"Spread {r.spread_atr:.2f} ATR, oltre {limiti.max_spread_atr_ratio}.")
    if r.simbolo in r.simboli_aperti:
        motivi.append("Già una posizione o un ordine su questo strumento.")
    if r.posizioni_aperte >= limiti.max_open_positions:
        motivi.append(f"Già {r.posizioni_aperte} posizioni (massimo {limiti.max_open_positions}).")
    if r.equity > 0:
        if r.rischio > r.equity * limiti.max_risk_per_trade_pct / 100 * 1.001:
            motivi.append("Rischio dell'operazione oltre il massimo per trade.")
        if r.rischio_aperto + r.rischio > r.equity * limiti.max_open_risk_pct / 100:
            motivi.append(
                f"Rischio aperto totale oltre il {limiti.max_open_risk_pct}% dell'equity."
            )
        if r.pnl_settimana <= -r.equity * limiti.weekly_max_loss_pct / 100:
            motivi.append(f"Perdita settimanale oltre il {limiti.weekly_max_loss_pct}%.")
        obiettivo = limiti.daily_profit_target_pct
        if obiettivo is not None and r.pnl_oggi >= r.equity * obiettivo / 100:
            motivi.append(f"Obiettivo giornaliero del {obiettivo}% raggiunto: stop fino a domani.")
        if r.pnl_oggi <= -r.equity * limiti.daily_max_loss_pct / 100:
            motivi.append(f"Perdita giornaliera oltre il {limiti.daily_max_loss_pct}%.")
        if r.picco_equity > 0 and r.equity <= r.picco_equity * (1 - limiti.max_drawdown_pct / 100):
            motivi.append(f"Drawdown oltre il {limiti.max_drawdown_pct}% dal picco.")
    if r.rischio <= 0:
        motivi.append("Size nulla per il rischio consentito.")
    return motivi
