"""Simulatore del backtest: dai segnali alle operazioni, con le regole del paper.

Deterministico (stessi dati → stessi numeri) e senza look-ahead: a ogni segnale si
conoscono solo le operazioni già **chiuse**; l'uscita di una posizione si calcola sul
suo percorso di prezzo, che non dipende dalle altre.

Uguale al paper (stesso codice):
* gate di rischio `risk.gate.valuta_ingresso` coi limiti dell'utente;
* risoluzione intrabar `paper.geometry.risolvi_barra` (gap, poi stop prudente);
* stop mobile `paper.trailing` (picco solo da barre chiuse, stop che non arretra);
* gruppi di correlazione `paper.performance.GRUPPI`.

Approssimazioni dichiarate (tutte riportate nel rapporto):
* gestione su barre **M5** (il paper usa M1, disponibili solo per 3 mesi): una barra
  che tocca stop e target è più spesso ambigua → vince lo stop (prudente);
* ingresso all'apertura della prima M5 dopo la decisione, all'ask per i LONG (spread
  della barra) più lo slittamento configurato;
* equity, perdita giornaliera e drawdown sul **realizzato** (il paper usa anche il
  flottante); swap e commissioni non simulati (commissione per lotto 0 nel conto XM);
* nessuna notizia né calendario storico: i freni su eventi e notizie non agiscono.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.backtesting.segnali import Segnale
from backend.backtesting.storico import Barra
from backend.config.risk_limits import RiskLimits
from backend.paper.geometry import risolvi_barra
from backend.paper.performance import GRUPPI
from backend.paper.trailing import REGOLE, nuovo_stop_r
from backend.risk.gate import RichiestaIngresso, valuta_ingresso


@dataclass(frozen=True, slots=True)
class Variante:
    nome: str
    descrizione: str
    #: Stop in ATR H1 (None = quello del piano, 1,5). Il target resta a 2R.
    stop_atr: float | None = None
    orizzonte_ore: int = 24
    regola_mobile: str | None = None
    una_per_scommessa: bool = False
    #: Segnale capovolto: verso opposto, stop e target specchiati (ADR 0029).
    invertito: bool = False
    #: Target in R (None = quello del piano, 2R). Il gate accetta un RR minimo pari.
    target_r: float | None = None
    #: Ore senza nuove aperture sullo strumento dopo uno stop in perdita (0 = nessuna).
    pausa_dopo_stop_ore: int = 0


@dataclass(frozen=True, slots=True)
class Operazione:
    simbolo: str
    canonico: str
    verso: int
    aperta: datetime
    chiusa: datetime
    motivo: str
    entrata: float
    uscita: float
    r: float
    mfe_r: float
    #: Costo dello spread d'ingresso in R.
    costo_r: float
    rischio: float
    pnl: float
    ambigua: bool


@dataclass
class Esito:
    variante: Variante
    operazioni: list[Operazione] = field(default_factory=list)
    rifiuti: dict[str, int] = field(default_factory=dict)
    segnali: int = 0
    equity_iniziale: float = 0.0


@dataclass(frozen=True, slots=True)
class Mercato:
    """Barre di un simbolo in UTC, pronte per la ricerca per tempo."""

    m5: list[Barra]
    m5_inizi: list[datetime]
    h1: list[Barra]
    h1_fini: list[datetime]

    @classmethod
    def da_broker(cls, m5: Sequence[Barra], h1: Sequence[Barra], scarto: timedelta) -> Mercato:
        m5u = [Barra(b.t - scarto, b.o, b.h, b.l, b.c, b.spread) for b in m5]
        h1u = [Barra(b.t - scarto, b.o, b.h, b.l, b.c, b.spread) for b in h1]
        return cls(m5u, [b.t for b in m5u], h1u, [b.t + timedelta(hours=1) for b in h1u])


def _invalidazione_alle(m: Mercato, verso: int, livello: float, dopo: datetime) -> datetime | None:
    """Fine della prima barra H1 chiusa dopo `dopo` con chiusura oltre il livello."""
    i = bisect.bisect_right(m.h1_fini, dopo)
    for b, fine in zip(m.h1[i:], m.h1_fini[i:], strict=True):
        if (b.c < livello) if verso > 0 else (b.c > livello):
            return fine
    return None


def _uscita(
    m: Mercato,
    i: int,
    verso: int,
    entrata: float,
    stop: float,
    target: float,
    scadenza: datetime,
    invalidata: datetime | None,
    regola: str | None,
    punto: float,
    slittamento: float,
) -> tuple[int, float, str, float, bool]:
    """(indice barra, prezzo, motivo, MFE in R, ambigua) dall'ingresso nella barra i."""
    distanza = abs(entrata - stop)
    stop_r: float | None = None
    picco = 0.0
    j = i
    for j in range(i, len(m.m5)):
        b = m.m5[j]
        spread = b.spread * punto
        fine = b.t + timedelta(minutes=5)
        livello = stop if stop_r is None else entrata + verso * stop_r * distanza
        e = risolvi_barra(verso, livello, target, open_=b.o, high=b.h, low=b.l, spread=spread)
        if e.evento == "SL":
            prezzo = (e.prezzo or livello) - verso * slittamento
            return j, prezzo, "TRAIL" if stop_r is not None else "SL", picco, e.ambiguo
        if e.evento == "TP":
            return j, target, "TP", max(picco, abs(target - entrata) / distanza), False
        favorevole = verso * ((b.h if verso > 0 else b.l + spread) - entrata) / distanza
        picco = max(picco, favorevole)
        if regola is not None:
            stop_r = nuovo_stop_r(REGOLE[regola], picco, stop_r)
        chiusura = b.c if verso > 0 else b.c + spread
        if invalidata is not None and fine >= invalidata:
            return j, chiusura, "INVALIDATION", picco, False
        if fine >= scadenza:
            return j, chiusura, "TIME", picco, False
    b = m.m5[j]
    return j, b.c if verso > 0 else b.c + b.spread * punto, "FINE_DATI", picco, False


def simula(
    segnali: Sequence[Segnale],
    mercati: dict[str, Mercato],
    variante: Variante,
    limiti: RiskLimits,
    *,
    scarto: timedelta,
    equity_iniziale: float = 10_000.0,
    slittamento_punti: float = 0.0,
) -> Esito:
    esito = Esito(variante=variante, equity_iniziale=equity_iniziale)
    equity = picco_equity = equity_iniziale
    aperte: list[tuple[Operazione, tuple[str, int] | None]] = []
    chiuse_per_giorno: dict[object, float] = {}
    chiuse_per_settimana: dict[object, float] = {}
    #: Ultimo stop in perdita per simbolo, noto solo dopo la chiusura (niente look-ahead).
    ultimo_stop: dict[str, datetime] = {}

    def realizza(fino: datetime) -> None:
        nonlocal equity, picco_equity
        for op, sc in sorted([x for x in aperte if x[0].chiusa <= fino], key=lambda x: x[0].chiusa):
            aperte.remove((op, sc))
            equity += op.pnl
            picco_equity = max(picco_equity, equity)
            giorno = (op.chiusa + scarto).date()
            chiuse_per_giorno[giorno] = chiuse_per_giorno.get(giorno, 0.0) + op.pnl
            settimana = giorno.isocalendar()[:2]
            chiuse_per_settimana[settimana] = chiuse_per_settimana.get(settimana, 0.0) + op.pnl
            esito.operazioni.append(op)
            if op.motivo in ("SL", "TRAIL") and op.r < 0:
                ultimo_stop[op.simbolo] = op.chiusa

    def rifiuta(motivo: str) -> None:
        # Numeri tolti: "Spread 0.35 ATR" e "Spread 0.41 ATR" sono lo stesso motivo.
        chiave = re.sub(r"\d+(\.\d+)?", "#", motivo.split(" (")[0])
        esito.rifiuti[chiave] = esito.rifiuti.get(chiave, 0) + 1

    for s in sorted(segnali, key=lambda x: (x.t, x.simbolo)):
        esito.segnali += 1
        realizza(s.t)
        m = mercati.get(s.simbolo)
        if m is None:
            continue
        i = bisect.bisect_left(m.m5_inizi, s.t)
        if i >= len(m.m5):
            continue
        barra = m.m5[i]
        if barra.t - s.t > timedelta(minutes=30):
            rifiuta("Mercato chiuso all'ingresso")
            continue
        pausa = timedelta(hours=variante.pausa_dopo_stop_ore)
        if pausa and (u := ultimo_stop.get(s.simbolo)) and s.t - u < pausa:
            rifiuta("Pausa dopo uno stop sullo strumento")
            continue
        verso = -s.verso if variante.invertito else s.verso
        scommessa = None
        if variante.una_per_scommessa and (g := GRUPPI.get(s.canonico)):
            scommessa = (g[0], g[1] * verso)
            if any(sc == scommessa for _, sc in aperte):
                rifiuta("Stessa scommessa già aperta")
                continue
        slit = slittamento_punti * s.punto
        k = variante.stop_atr
        stop = s.stop if k is None else round(s.riferimento - s.verso * k * s.atr_h1, s.cifre)
        target = (
            s.target if k is None else round(s.riferimento + s.verso * 2 * k * s.atr_h1, s.cifre)
        )
        if variante.target_r is not None:
            target = round(
                s.riferimento + s.verso * variante.target_r * abs(s.riferimento - stop), s.cifre
            )
        if variante.invertito:
            # Specchio rispetto al riferimento, come `paper.planner.inverti`.
            stop, target = 2 * s.riferimento - stop, 2 * s.riferimento - target
        spread = barra.spread * s.punto
        entrata = barra.o + spread + slit if verso > 0 else barra.o - slit
        if verso * (entrata - stop) <= 0 or verso * (target - entrata) <= 0:
            rifiuta("Prezzo già oltre stop o target")
            continue
        rischio = equity * limiti.max_risk_per_trade_pct / 100
        giorno = (s.t + scarto).date()
        motivi = valuta_ingresso(
            RichiestaIngresso(
                simbolo=s.canonico,
                rr=abs(target - s.riferimento) / abs(s.riferimento - stop),
                stop_atr=abs(entrata - stop) / s.atr_h1,
                spread_atr=s.spread_atr,
                rischio=rischio,
                equity=equity,
                posizioni_aperte=len(aperte),
                simboli_aperti=frozenset(op.canonico for op, _ in aperte),
                rischio_aperto=sum(op.rischio for op, _ in aperte),
                pnl_oggi=chiuse_per_giorno.get(giorno, 0.0),
                pnl_settimana=chiuse_per_settimana.get(giorno.isocalendar()[:2], 0.0),
                picco_equity=picco_equity,
                ora_broker=s.t + scarto,
                kill_switch=False,
                finestra_evento=False,
                dati_validi=True,
            ),
            limiti
            if variante.target_r is None
            else limiti.model_copy(
                update={"minimum_rr": min(limiti.minimum_rr, variante.target_r)}
            ),
        )
        if motivi:
            rifiuta(motivi[0])
            continue
        livello_inv = None if variante.invertito else s.invalidazione
        if k is not None and not variante.invertito:
            livello_inv = (
                s.swing
                if s.swing is not None
                and min(s.riferimento, stop) < s.swing < max(s.riferimento, stop)
                else None
            )
        invalidata = (
            _invalidazione_alle(m, verso, livello_inv, barra.t) if livello_inv is not None else None
        )
        j, prezzo, motivo, mfe, ambigua = _uscita(
            m,
            i,
            verso,
            entrata,
            stop,
            target,
            barra.t + timedelta(hours=variante.orizzonte_ore),
            invalidata,
            variante.regola_mobile,
            s.punto,
            slit,
        )
        distanza = abs(entrata - stop)
        r = round(verso * (prezzo - entrata) / distanza, 4)
        aperte.append(
            (
                Operazione(
                    simbolo=s.simbolo,
                    canonico=s.canonico,
                    verso=verso,
                    aperta=barra.t,
                    chiusa=m.m5[j].t + timedelta(minutes=5),
                    motivo=motivo,
                    entrata=entrata,
                    uscita=prezzo,
                    r=r,
                    mfe_r=round(mfe, 4),
                    costo_r=round(spread / distanza, 4),
                    rischio=rischio,
                    pnl=round(r * rischio, 2),
                    ambigua=ambigua,
                ),
                scommessa,
            )
        )
    realizza(datetime.max.replace(tzinfo=segnali[0].t.tzinfo) if segnali else datetime.max)
    return esito


def limiti_senza_blocchi_di_conto(limiti: RiskLimits) -> RiskLimits:
    """Stessi limiti per singolo trade, senza perdita giornaliera/settimanale, obiettivo
    giornaliero e drawdown: per misurare la strategia e non quando il conto si ferma
    (a −10% il blocco del drawdown fermerebbe tutto il resto del periodo)."""
    return limiti.model_copy(
        update={
            "daily_max_loss_pct": 100.0,
            "weekly_max_loss_pct": 100.0,
            "max_drawdown_pct": 100.0,
            "daily_profit_target_pct": None,
        }
    )
