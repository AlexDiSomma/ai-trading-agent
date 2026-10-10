"""Report read-only del contesto globale: `python -m backend.report_globale`.

Costruisce il `GlobalMarketContext` sul conto configurato e stampa ciò che serve
per leggerlo: fase di mercato (dichiarata esplicitamente se chiuso), regime,
volatilità, classifica delle valute, sei strumenti di riferimento, correlazioni
intermarket, conflitti e qualità dei dati. Salva il contesto completo in
`docs/validazione/globale_<data-ora>.json`.

Sola lettura: candele, tick, specifiche. Nessun calcolatore, nessun ordine.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from typing import Any

from backend.config import get_settings
from backend.config.paths import PROJECT_ROOT
from backend.core.enums import MarketPhase
from backend.core.logging import configure_logging
from backend.global_market.backdrop import sfondo
from backend.schemas.global_market import GlobalMarketContext, GlobalMarketSnapshot
from backend.services.container import ApplicationContainer

STRUMENTI = {
    "GOLD": "XAUUSD",
    "EURUSD": "EURUSD",
    "USDJPY": "USDJPY",
    "US100Cash": "US100",
    "US30Cash": "US30",
    "SILVER": "XAGUSD",
}


def _f(v: float | None, cifre: int = 2) -> str:
    return "  n/d" if v is None else f"{v:+.{cifre}f}"


def _p(v: float | None) -> str:
    return " n/d" if v is None else f"{v:4.2f}"


def stampa(snap: GlobalMarketSnapshot, c: GlobalMarketContext, sfondi: dict[str, Any]) -> None:
    linea = "=" * 92
    print(linea)
    print(f"CONTESTO GLOBALE — {c.as_of:%Y-%m-%d %H:%M:%S} UTC  ·  motore {c.engine_version}")
    print(f"impronta ingresso {c.input_fingerprint[:16]}…  ·  dati simulati: {c.simulated}")
    print(linea)
    if c.market_phase is not MarketPhase.OPEN_CONFIRMED:
        print(f"!! MERCATO NON APERTO CONFERMATO: {c.market_phase}")
        print(f"!! {c.market_phase_reason}")
    else:
        print(f"Mercato: {c.market_phase} — {c.market_phase_reason}")
    print(
        f"Ora broker: scarto {snap.broker_time_offset_seconds} s (fonte {snap.broker_time_source})"
    )
    print()
    print("SINTESI")
    for riga in c.summary:
        print(f"  {riga.label:<26} {riga.value}")
    print(f"  {'Utilizzabile per decidere':<26} {c.usable_for_decisions}")

    print("\nREGIME GLOBALE:", c.regime)
    for m in c.regime_reasons:
        print("   ·", m)
    r = c.risk_sentiment
    print(f"  sentiment {r.label}  composito {_f(r.score)}  accordo {_p(r.agreement)}")
    print(
        f"  dimensioni forti risk-on {list(r.strong_risk_on)}  risk-off {list(r.strong_risk_off)}"
    )
    print("  per timeframe:", {str(k): v for k, v in r.by_timeframe.items()})
    for e in r.evidence:
        print(f"    {e.name:<22} {_f(e.score)}  peso {e.weight:.2f}  — {e.description}")

    v = c.volatility
    print(f"\nVOLATILITÀ: livello {v.level}, tendenza {v.trend}, shock {v.shock}")
    print("  percentile ATR mediano:", {str(k): x for k, x in v.median_atr_percentile.items()})
    print(
        "  percentile Bollinger mediano:",
        {str(k): x for k, x in v.median_bollinger_width_percentile.items()},
    )
    print(
        "  vol. breve/lunga mediana:", {str(k): x for k, x in v.median_realized_vol_ratio.items()}
    )
    print("  per gruppo:", {k: f"{lv}/{v.trend_by_group.get(k)}" for k, lv in v.by_group.items()})

    print("\nFONTI (XM non è l'unica: ADR 0018)")
    for f in snap.sources:
        if f.source == "xm":
            continue
        print(
            f"  {f.id:<9} {f.category:<21} fonte {f.source:<30} {f.state:<15} "
            f"osservazioni {f.observations}  ultima {f.last_observation or '-'}"
        )
    xm = [f for f in snap.sources if f.source == "xm"]
    print(
        f"  + {len(xm)} strumenti di riferimento da XM "
        f"({sum(1 for f in xm if str(f.state) == 'AVAILABLE')} con dati utilizzabili)"
    )
    for st in c.providers:
        errore = f" — errore: {st.last_error[:90]}" if st.last_error else ""
        print(f"  provider {st.kind:<18} {st.name:<14} {st.state}{errore}")

    iv = c.implied_volatility
    if iv:
        print(
            f"\nVIX ({iv.source}): {iv.value} del {iv.day}, percentile annuo "
            f"{_p(iv.percentile_1y)}, "
            f"variazione 20 sedute {_f(iv.change_20d)}, {iv.level}/{iv.trend}, qualità "
            f"{iv.quality.quality}"
        )
    rt = c.rates
    if rt:
        print(
            f"TASSI USA ({rt.source}) del {rt.day}: {rt.yields}  variazione 20 sedute (pb) "
            f"{rt.change_20d_bp}  2s10s {rt.slope_2s10s_bp} pb, 3m10y {rt.slope_3m10y_bp} pb, "
            f"invertita {rt.curve_inverted}, decennale {rt.ten_year_trend}"
        )
    dx = c.dollar_index
    if dx:
        tf = "  ".join(f"{k}:{_f(x)}" for k, x in dx.by_timeframe.items())
        print(
            f"DXY ricostruito: {dx.value}  {dx.label} ({_f(dx.score)}) [{tf}]  correlazione "
            f"con USD del grafo {_p(dx.correlation_with_usd_strength)}  concorda "
            f"{dx.agrees_with_usd_strength}"
        )

    print("\nFORZA DELLE VALUTE (modello a grafo; D1 H4 H1 M15; cross usati)")
    print(
        "  residuo mediano delle equazioni:",
        {str(k): v for k, v in c.currency_strength.graph_residual.items()},
        " barre stimate:",
        {str(k): v for k, v in c.currency_strength.bars_estimated.items()},
    )
    for cs in c.currency_strength.currencies:
        tf = "  ".join(_f(x) for x in cs.by_timeframe.values())
        print(
            f"  {cs.rank or '-':>2}. {cs.currency}  {_f(cs.score)}  {cs.label:<8} [{tf}]  "
            f"{cs.crosses_used}/7"
        )

    print("\nFORZA RELATIVA (tutti i gruppi)")
    for e in c.relative_strength.entries:
        print(
            f"  {e.rank_overall or '-':>2}. {e.name:<8} {e.group:<13} {_f(e.score)}  "
            f"(contro {e.measured_against})"
        )
    for g in (c.equities, c.metals):
        print(
            f"\n{g.group.upper()}: {g.label}  composito {_f(g.score)}  ampiezza {_p(g.breadth)}  "
            f"timeframe concordi {g.timeframes_agree}"
        )
        print("  membri:", g.members)
        if g.note:
            print("  nota:", g.note)

    print("\nSTRUMENTI")
    for broker, canonico in STRUMENTI.items():
        stato = next((i for i in snap.instruments if i.canonical == canonico), None)
        rs = next((e for e in c.instruments if e.canonical == canonico), None)
        vol = next((a for a in v.assets if a.canonical == canonico), None)
        print(f"  {canonico} ({broker})")
        if stato:
            serie = ", ".join(f"{s.timeframe}:{s.status}({s.bars})" for s in stato.series)
            print(f"     mercato {stato.market_status}  quotazione {stato.quote_status}")
            print(f"     serie   {serie}")
        if rs:
            tf = "  ".join(f"{k}:{_f(x)}" for k, x in rs.by_timeframe.items())
            print(
                f"     momentum normalizzato {_f(rs.score)} {rs.label}  [{tf}]  "
                f"timeframe concordi {rs.timeframes_agree}"
            )
        if vol:
            print(
                "     percentile ATR "
                + "  ".join(f"{k}:{_p(x)}" for k, x in vol.atr_percentile.items())
                + f"   tendenza vol. {vol.trend}"
            )
        sf = sfondi.get(canonico)
        if sf:
            for n in sf.notes:
                print("     ·", n)

    im = c.intermarket
    print(
        f"\nINTERMARKET: {im.state}  (coerenti {im.consistent}, indebolite {im.weakening}, "
        f"divergenti {im.diverging}, instabili {im.unstable})"
    )
    print("  rendimenti logaritmici; corrente = ultimi N, base = tutti i precedenti")
    for rel in im.relationships:
        print(
            f"  {rel.a:>7} ~ {rel.b:<7} {rel.timeframe:<3} corrente "
            f"{_f(rel.current_correlation)} (n={rel.current_sample:<3}) base "
            f"{_f(rel.baseline_correlation)} (n={rel.baseline_sample:<3}) "
            f"z {_f(rel.difference_z, 1)} stab. {_p(rel.stability)}  {rel.state}"
        )

    print(f"\nCONFLITTI: {len(c.conflicts)}")
    for k in c.conflicts:
        print(f"  [{k.severity}] {k.code}: {k.description}")

    m = c.macro_event_risk
    print(
        f"\nCALENDARIO ({m.calendar_provider}, {m.calendar_state}): livello {m.calendar_level} — "
        f"{m.calendar_reason}"
    )
    print(
        f"  copertura fino a {m.calendar_coverage_end}  valori pubblicati: {m.calendar_has_actuals}"
    )
    for w in m.active_windows:
        print(
            f"  FINESTRA ATTIVA {w.currency} {w.title} "
            f"{w.starts_at:%d/%m %H:%M}-{w.ends_at:%H:%M} UTC"
        )
    for v, e in m.next_high_impact.items():
        print(f"  prossimo HIGH {v}: {e.title} {e.scheduled_at:%a %d/%m %H:%M} UTC")
    for e in m.upcoming_events:
        print(
            f"  prossime 24h [{e.importance}] {e.currency} {e.title} "
            f"{e.scheduled_at:%d/%m %H:%M} UTC"
        )
    print(f"RISCHIO MACRO IMPLICITO NEI PREZZI: {m.market_implied_uncertainty} {list(m.drivers)}")

    print(f"\nQUALITÀ DEI DATI: {c.data_quality}")
    for d in c.dimension_quality:
        print(f"  {d.dimension:<18} {d.quality:<12} copertura {d.coverage:.0%}")
        for motivo in d.reasons:
            print(f"      - {motivo[:160]}")
    if c.warnings:
        print("\nAVVISI")
        for a in c.warnings:
            print("  !", a)


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log, mode=str(settings.app.effective_mode))
    container = ApplicationContainer(settings)
    await container.startup()
    try:
        if container.broker is None:
            print(f"Broker non disponibile: {container.broker_error}")
            return 1
        servizio = container.require_global_intelligence()
        snap, contesto = await servizio.build(force=True)
        strumenti = container.require_instruments()
        sfondi = {}
        for broker in STRUMENTI:
            ist = await strumenti.by_broker_symbol(broker)
            sfondi[ist.canonical] = sfondo(ist, contesto)
        stampa(snap, contesto, sfondi)
        cartella = PROJECT_ROOT / "docs" / "validazione"
        await asyncio.to_thread(cartella.mkdir, parents=True, exist_ok=True)
        destinazione = cartella / f"globale_{datetime.now():%Y-%m-%d_%H%M}.json"
        dati = {
            "snapshot": snap.model_dump(mode="json"),
            "contesto": contesto.model_dump(mode="json"),
        }
        testo = json.dumps(dati, indent=2, ensure_ascii=False)
        await asyncio.to_thread(destinazione.write_text, testo, encoding="utf-8")
        print(f"\nContesto completo salvato in {destinazione}")
        return 0
    finally:
        await container.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
