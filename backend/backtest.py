"""Backtest della strategia QUANT: `python -m backend.backtest [--aggiorna] [--simboli X,Y]`.

Legge lo storico dal broker (sola lettura, cache in `var/backtest/cache`), calcola i
segnali QUANT a ogni chiusura H1 con lo stesso codice del paper, simula le varianti e
scrive `var/backtest/rapporto_<data>.json`. **Nessuna chiamata all'AI**: QUANT è
deterministica (il backtest dell'AI richiederebbe migliaia di chiamate e un modello
che conosce già il passato — ADR 0028).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta

from backend.backtesting.rapporto import riassunto
from backend.backtesting.segnali import (
    Segnale,
    Tagliatore,
    carica_segnali,
    salva_segnali,
    segnali_simbolo,
)
from backend.backtesting.simulatore import (
    Mercato,
    Variante,
    limiti_senza_blocchi_di_conto,
    simula,
)
from backend.backtesting.storico import StoricoNonDisponibileError, storico
from backend.config import get_settings
from backend.config.paths import resolve
from backend.core.enums import Timeframe
from backend.core.logging import configure_logging
from backend.market_data.ingestion import BARRE_RICHIESTE, TIMEFRAMES_ANALISI
from backend.services.container import ApplicationContainer

VARIANTI: tuple[Variante, ...] = (
    Variante("base", "QUANT come opera oggi: stop 1,5 ATR H1, target 2R, 24 ore."),
    Variante("pareggio_1r", "Base + stop in pareggio a +1R.", regola_mobile="pareggio_1r"),
    Variante("blocca_05r", "Base + stop a +0,5R quando arriva a +1R.", regola_mobile="blocca_05r"),
    Variante("trailing_1r", "Base + trailing a 1R da +1R.", regola_mobile="trailing_1r"),
    Variante(
        "una_per_scommessa",
        "Base, ma una sola posizione per scommessa (indici USA, yen, dollaro, metalli).",
        una_per_scommessa=True,
    ),
    Variante("orizzonte_72h", "Base con orizzonte 72 ore invece di 24.", orizzonte_ore=72),
    Variante("target_13r", "Base con target a 1,3R invece di 2R.", target_r=1.3),
    Variante(
        "target_13r_stop_25atr_48h",
        "Target 1,3R, stop 2,5 ATR H1, orizzonte 48 ore.",
        target_r=1.3,
        stop_atr=2.5,
        orizzonte_ore=48,
    ),
    Variante(
        "pausa_4h",
        "Base + 4 ore senza aperture sullo strumento dopo uno stop.",
        pausa_dopo_stop_ore=4,
    ),
    Variante(
        "pausa_24h",
        "Base + 24 ore senza aperture sullo strumento dopo uno stop.",
        pausa_dopo_stop_ore=24,
    ),
    Variante(
        "target_13r_pausa_4h",
        "Target 1,3R (impostazione attuale) + pausa 4 ore.",
        target_r=1.3,
        pausa_dopo_stop_ore=4,
    ),
    Variante(
        "target_13r_pausa_24h",
        "Target 1,3R (impostazione attuale) + pausa 24 ore.",
        target_r=1.3,
        pausa_dopo_stop_ore=24,
    ),
    Variante(
        "inverso",
        "Segnali capovolti: verso opposto, stop e target specchiati (esperimento).",
        invertito=True,
    ),
    Variante(
        "inverso_stop_25atr_48h",
        "Segnali capovolti con stop 2,5 ATR H1 e orizzonte 48 ore.",
        invertito=True,
        stop_atr=2.5,
        orizzonte_ore=48,
    ),
    Variante(
        "stop_25atr_48h",
        "Stop più largo (2,5 ATR H1), target 2R, orizzonte 48 ore.",
        stop_atr=2.5,
        orizzonte_ore=48,
    ),
)


def _riga(testo: str = "") -> None:
    print(testo, flush=True)


async def main(argomenti: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m backend.backtest")
    p.add_argument("--aggiorna", action="store_true", help="riscarica lo storico dal broker")
    p.add_argument("--simboli", help="solo questi simboli broker, separati da virgola")
    a = p.parse_args(argomenti)

    settings = get_settings()
    configure_logging(settings.log, mode=str(settings.app.effective_mode))
    c = ApplicationContainer(settings)
    await c.startup()
    try:
        if c.broker is None or not await c.ensure_broker_time():
            _riga(f"Broker o ora del broker non disponibili: {c.broker_error}")
            return 1
        stima = c.broker_clock.estimate(now=c.clock.now())
        assert stima.offset_seconds is not None
        scarto = timedelta(seconds=stima.offset_seconds)
        cartella = resolve("var/backtest")
        simboli = a.simboli.split(",") if a.simboli else list(settings.risk.allowed_symbols)
        analista = c.require_analyst()
        assert c.specifications is not None

        mercati: dict[str, Mercato] = {}
        esclusi: dict[str, str] = {}
        segnali: list[Segnale] = []
        inizio_glob = fine_glob = None
        for s in simboli:
            t0 = time.perf_counter()
            try:
                per_tf = {
                    tf: await storico(
                        c.require_broker(), cartella / "cache", s, tf, aggiorna=a.aggiorna
                    )
                    for tf in TIMEFRAMES_ANALISI
                }
            except StoricoNonDisponibileError as errore:
                esclusi[s] = str(errore)
                _riga(f"{s}: escluso — {errore}")
                continue
            # Ogni timeframe deve avere almeno la storia che il live usa.
            corti = [
                f"{tf} {len(per_tf[tf])}"
                for tf in TIMEFRAMES_ANALISI
                if len(per_tf[tf]) < BARRE_RICHIESTE[tf] + 10
            ]
            m5 = per_tf[Timeframe.M5]
            if corti or len(m5) < 1000:
                esclusi[s] = f"storico corto: {', '.join(corti) or f'M5 {len(m5)}'}"
                _riga(f"{s}: escluso — {esclusi[s]}")
                continue
            mercati[s] = Mercato.da_broker(m5, per_tf[Timeframe.H1], scarto)
            # Riscaldamento: 350 barre M5 (le più corte da avere complete) prima di decidere.
            inizio = (m5[0].t - scarto + timedelta(days=3)).replace(minute=0, second=0)
            fine = m5[-1].t - scarto
            inizio_glob = max(inizio_glob or inizio, inizio)
            fine_glob = min(fine_glob or fine, fine)
            info = (await c.specifications.get(s)).value
            intestazione = {"dal": m5[0].t.isoformat(), "al": m5[-1].t.isoformat()}
            file = cartella / "segnali" / f"{s}.json"
            propri = carica_segnali(file, intestazione)
            if propri is None:
                istanti = []
                t = inizio + timedelta(hours=1)
                while t <= fine:
                    istanti.append(t)
                    t += timedelta(hours=1)
                propri = await segnali_simbolo(
                    analista,
                    Tagliatore(s, per_tf),
                    istanti,
                    scarto,
                    punto=info.point,
                    cifre=info.digits,
                )
                salva_segnali(file, propri, intestazione)
            segnali.extend(propri)
            _riga(
                f"{s:10} M5 dal {m5[0].t:%Y-%m-%d} · {len(propri)} segnali · "
                f"{time.perf_counter() - t0:.0f}s"
            )
        if not segnali or inizio_glob is None or fine_glob is None:
            _riga("Nessun segnale: niente da simulare.")
            return 1
        # Stesso periodo per tutti i simboli: si scartano i segnali fuori dall'intersezione.
        segnali = [x for x in segnali if inizio_glob <= x.t <= fine_glob]
        meta = inizio_glob + (fine_glob - inizio_glob) / 2

        rapporti = []
        liberi = limiti_senza_blocchi_di_conto(settings.risk)
        for v in VARIANTI:
            puro = riassunto(
                simula(
                    segnali,
                    mercati,
                    v,
                    liberi,
                    scarto=scarto,
                    slittamento_punti=settings.paper.slippage_points,
                ),
                meta,
            )
            conto = riassunto(
                simula(
                    segnali,
                    mercati,
                    v,
                    settings.risk,
                    scarto=scarto,
                    slittamento_punti=settings.paper.slippage_points,
                ),
                meta,
            )
            rapporti.append({**puro, "with_account_limits": conto})

        _riga()
        _riga(f"Periodo {inizio_glob:%Y-%m-%d} → {fine_glob:%Y-%m-%d} (metà: {meta:%Y-%m-%d})")
        _riga(
            f"{'variante':20}{'trade':>7}{'win':>7}{'exp R':>8}{'tot R':>9}{'DD %':>7}"
            f"{'rend %':>8}{'1ª metà':>9}{'2ª metà':>9}{'MC 90%':>18}{'P>0':>6}"
        )
        for r in rapporti:
            mc = r["montecarlo"]
            _riga(
                f"{r['variant']:20}{r['trades']:>7}{(r['win_rate'] or 0) * 100:>6.0f}%"
                f"{r['expectancy_r'] or 0:>+8.3f}{r['total_r']:>+9.1f}{r['max_drawdown_pct']:>7.1f}"
                f"{r['return_pct']:>+8.1f}{r['first_half']['expectancy_r'] or 0:>+9.3f}"
                f"{r['second_half']['expectancy_r'] or 0:>+9.3f}"
                f"  [{mc['low_90'] or 0:+.3f}, {mc['high_90'] or 0:+.3f}]"
                f"{mc['positive_share'] or 0:>6.2f}"
            )
        destinazione = cartella / f"rapporto_{datetime.now(UTC):%Y%m%d_%H%M}.json"
        destinazione.write_text(
            json.dumps(
                {
                    "period": [inizio_glob.isoformat(), fine_glob.isoformat()],
                    "half": meta.isoformat(),
                    "symbols": list(mercati),
                    "excluded": esclusi,
                    "signals_per_symbol": {
                        s: sum(1 for x in segnali if x.simbolo == s) for s in mercati
                    },
                    "risk_per_trade_pct": settings.risk.max_risk_per_trade_pct,
                    "variants": rapporti,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        _riga()
        _riga("Con anche i blocchi del conto (perdita giornaliera/settimanale, drawdown 10%):")
        for r in rapporti:
            k = r["with_account_limits"]
            _riga(
                f"  {r['variant']:20}{k['trades']:>5} trade · {k['total_r']:+.1f}R · "
                f"rendimento {k['return_pct']:+.1f}% · drawdown {k['max_drawdown_pct']:.1f}%"
            )
        _riga(f"Rapporto completo: {destinazione}")
        return 0
    finally:
        await c.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
