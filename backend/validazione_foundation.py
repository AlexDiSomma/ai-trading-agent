"""Validazione della foundation sul conto reale: `python -m backend.validazione_foundation`.

Per ognuno dei sei strumenti di riferimento confronta ciò che calcola il sistema
(`BrokerValuationService`, `InstrumentRegistry`, `QuoteQualityEvaluator`) con le
chiamate **grezze** al terminale MT5, e salva tutti i numeri in
`docs/validazione/xm_demo_<data>.json`. Quel file è la fonte dei valori reali con
cui i test automatici confrontano il terminale finto.

Sola lettura. I calcolatori `order_calc_profit` e `order_calc_margin` non creano
ordini e non toccano il conto (ADR 0012). Nessun ordine viene inviato: il codice
per farlo non esiste (ADR 0004).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.brokers.mt5.mapping import ORDER_TYPE_BUY, ORDER_TYPE_SELL
from backend.config import get_settings
from backend.config.paths import PROJECT_ROOT
from backend.core.enums import Direction, Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import configure_logging
from backend.market_data.quote_quality import QuoteQualityEvaluator
from backend.services.container import ApplicationContainer
from backend.valuation.service import BrokerValuationService

#: Strumenti richiesti dalla validazione, con una distanza di stop realistica per
#: ognuno (in prezzo): 50 pip sul forex, 10 dollari sull'oro, 100 punti indice.
STRUMENTI: dict[str, float] = {
    "EURUSD": 0.0050,
    "USDJPY": 0.50,
    "GOLD": 10.0,
    "SILVER": 0.30,
    "US100Cash": 100.0,
    "US30Cash": 200.0,
}

#: Budget di rischio della prova, in valuta del conto.
BUDGET = 100.0


def _arrotonda(valore: float | None, cifre: int = 6) -> float | None:
    return None if valore is None else round(float(valore), cifre)


async def _valida_strumento(
    container: ApplicationContainer, nome: str, distanza: float
) -> dict[str, Any]:
    strumenti = container.require_instruments()
    valutazione: BrokerValuationService = container.require_valuation()
    broker = container.require_broker()
    terminale = broker._terminal  # type: ignore[attr-defined]

    info = await broker.get_symbol(nome)
    strumento = await strumenti.by_broker_symbol(nome)

    # --- prezzo di riferimento: tick se c'è, altrimenti ultima chiusura H1 ---
    tick = None
    try:
        tick = await broker.get_tick(nome)
    except TradingAgentError:
        tick = None
    candele = await broker.get_candles(nome, Timeframe.H1, 30)
    chiuse = candele.closed
    ultima_chiusura = chiuse[-1].close if chiuse else None
    entry = round(tick.mid if tick else float(ultima_chiusura or 0.0), info.digits)
    passo = strumento.contract.volume_step

    # --- grezzo MT5 ---
    def grezzo_profitto(azione: int, volume: float, apertura: float, chiusura: float) -> float:
        return float(terminale.order_calc_profit(azione, nome, volume, apertura, chiusura))

    def grezzo_margine(azione: int, volume: float, prezzo: float) -> float:
        return float(terminale.order_calc_margin(azione, nome, volume, prezzo))

    stop_long = round(entry - distanza, info.digits)
    stop_short = round(entry + distanza, info.digits)

    # --- servizio ---
    perdita_long = await valutazione.loss_at_stop(strumento, Direction.LONG, 1.0, entry, stop_long)
    perdita_short = await valutazione.loss_at_stop(
        strumento, Direction.SHORT, 1.0, entry, stop_short
    )
    margine_1 = await valutazione.margin(strumento, Direction.LONG, 1.0, entry)
    margine_min = await valutazione.margin(
        strumento, Direction.LONG, strumento.contract.volume_min, entry
    )
    size = await valutazione.size_for_risk(strumento, Direction.LONG, entry, stop_long, BUDGET)
    perdita_size_grezza = (
        -grezzo_profitto(ORDER_TYPE_BUY, size.volume, entry, stop_long) if size.volume else 0.0
    )
    perdita_passo_in_piu = (
        -grezzo_profitto(ORDER_TYPE_BUY, round(size.volume + passo, 8), entry, stop_long)
        if size.volume
        else None
    )
    scarto = await valutazione.tick_value_gap(strumento, info, entry)

    # --- invarianti misurati sul broker vero ---
    volumi = [strumento.contract.volume_min * k for k in (1, 2, 10, 100)]
    perdite_per_volume = [
        (await valutazione.loss_at_stop(strumento, Direction.LONG, v, entry, stop_long)).loss
        for v in volumi
    ]
    perdite_per_distanza = [
        (
            await valutazione.loss_at_stop(
                strumento, Direction.LONG, 1.0, entry, round(entry - distanza * k, info.digits)
            )
        ).loss
        for k in (0.5, 1, 2)
    ]
    size_per_budget = [
        (await valutazione.size_for_risk(strumento, Direction.LONG, entry, stop_long, b)).volume
        for b in (400.0, 200.0, 100.0, 50.0, 10.0)
    ]

    # --- qualità della quotazione adesso ---
    qualita = QuoteQualityEvaluator(
        max_tick_age_seconds=container.settings.mt5.max_tick_age_seconds
    ).evaluate(
        info,
        tick,
        now=container.clock.now(),
        broker_clock=container.broker_clock,
        last_candle_time=chiuse[-1].time if chiuse else None,
        last_candle_timeframe=Timeframe.H1,
        last_close=ultima_chiusura,
        quote_missing=tick is None,
    )

    normalizzazioni = {
        str(v): valutazione.normalize_volume(strumento, v).model_dump(mode="json")
        for v in (
            0.123,
            strumento.contract.volume_min / 2,
            strumento.contract.volume_max * 2,
            1.0 + passo / 3,
        )
    }

    return {
        "strumento": strumento.model_dump(mode="json"),
        "specifica": {
            "tick_value": info.tick_value,
            "tick_size": info.tick_size,
            "contract_size": info.contract_size,
            "currency_base": info.currency_base,
            "currency_profit": info.currency_profit,
            "currency_margin": info.currency_margin,
            "path": info.path,
        },
        "prezzo_riferimento": entry,
        "fonte_prezzo": "tick" if tick else "ultima_chiusura_H1",
        "distanza_stop": distanza,
        "profitto": {
            "grezzo_long_1_lotto_allo_stop": grezzo_profitto(ORDER_TYPE_BUY, 1.0, entry, stop_long),
            "grezzo_short_1_lotto_allo_stop": grezzo_profitto(
                ORDER_TYPE_SELL, 1.0, entry, stop_short
            ),
            "servizio_perdita_long": perdita_long.loss,
            "servizio_perdita_short": perdita_short.loss,
            "valore_unita_prezzo": _arrotonda(
                await valutazione.value_per_price_unit(strumento, entry)
            ),
            "scarto_tick_value_pct": scarto.gap_pct,
        },
        "margine": {
            "grezzo_1_lotto": grezzo_margine(ORDER_TYPE_BUY, 1.0, entry),
            "grezzo_1_lotto_sell": grezzo_margine(ORDER_TYPE_SELL, 1.0, entry),
            "servizio_1_lotto": margine_1.margin,
            "servizio_volume_min": margine_min.margin,
            "grezzo_volume_non_normalizzato": grezzo_margine(
                ORDER_TYPE_BUY, strumento.contract.volume_min / 2, entry
            ),
        },
        "size": {
            "budget": BUDGET,
            "volume": size.volume,
            "perdita_attesa": size.expected_loss,
            "perdita_grezza_al_volume": round(perdita_size_grezza, 2),
            "perdita_grezza_un_passo_in_piu": _arrotonda(perdita_passo_in_piu, 2),
            "limitato_da": size.limited_by,
        },
        "invarianti": {
            "volumi": volumi,
            "perdite_per_volume": perdite_per_volume,
            "perdite_per_distanza_0.5_1_2": perdite_per_distanza,
            "size_per_budget_400_200_100_50_10": size_per_budget,
        },
        "normalizzazioni": normalizzazioni,
        "qualita_quotazione": qualita.model_dump(mode="json"),
    }


async def valida() -> int:
    settings = get_settings()
    configure_logging(settings.log, mode=str(settings.app.effective_mode))
    if settings.mt5.use_mock:
        print("MT5_USE_MOCK=true: la validazione ha senso solo sul terminale reale.")
        return 2

    container = ApplicationContainer(settings)
    await container.startup()
    try:
        if container.broker is None:
            print(f"Broker non disponibile: {container.broker_error}")
            return 1
        conto = await container.broker.get_account()
        stima = container.broker_clock.estimate()
        risultato: dict[str, Any] = {
            "eseguito_utc": container.clock.now().isoformat(),
            "conto": {
                "server": conto.server,
                "valuta": conto.currency,
                "leva": conto.leverage,
                "tipo": conto.account_type,
            },
            "ora_broker": {
                "fonte": stima.source,
                "scarto_secondi": stima.offset_seconds,
                "quotazioni_concordi": stima.corroborating_ticks,
            },
            "strumenti": {},
        }
        for nome, distanza in STRUMENTI.items():
            try:
                risultato["strumenti"][nome] = await _valida_strumento(container, nome, distanza)
                print(f"[ok]  {nome}")
            except TradingAgentError as errore:
                risultato["strumenti"][nome] = {"errore": str(errore)}
                print(f"[NO]  {nome}: {errore}")

        await asyncio.to_thread(_salva, risultato)
        return 0
    finally:
        await container.shutdown()


def _salva(risultato: dict[str, Any]) -> None:
    cartella = PROJECT_ROOT / "docs" / "validazione"
    cartella.mkdir(parents=True, exist_ok=True)
    destinazione: Path = cartella / f"xm_demo_{datetime.now():%Y-%m-%d}.json"
    destinazione.write_text(
        json.dumps(risultato, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print()
    print(f"Risultati salvati in {destinazione}")


def main() -> None:
    sys.exit(asyncio.run(valida()))


if __name__ == "__main__":
    main()
