"""Confronto diretto con MT5 reale sul conto demo XM.

Esclusi di default: girano solo con `RUN_LIVE_MT5=1` e un terminale MT5 aperto e
collegato al conto configurato nel `.env` (con `MT5_USE_MOCK=false`).

    set RUN_LIVE_MT5=1
    .venv\\Scripts\\python -m pytest backend/tests/live -m live_broker

A differenza dei test d'unità, qui il termine di confronto non è un file di
valori registrati ma **la chiamata grezza al terminale nello stesso istante**:
ogni numero prodotto da `BrokerValuationService` deve coincidere al centesimo
con `order_calc_profit` / `order_calc_margin` chiamati direttamente.

Sola lettura: i calcolatori non creano ordini (ADR 0012), e il codice per
inviarne non esiste (ADR 0004).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from backend.brokers.mt5.mapping import ORDER_TYPE_BUY, ORDER_TYPE_SELL
from backend.config.settings import Settings
from backend.core.enums import Direction, MarketStatus, QuoteQualityStatus, Timeframe
from backend.market_data.quote_quality import QuoteQualityEvaluator
from backend.services.container import ApplicationContainer

pytestmark = pytest.mark.live_broker

STRUMENTI = {
    "EURUSD": ("EURUSD", 0.0050),
    "USDJPY": ("USDJPY", 0.50),
    "GOLD": ("XAUUSD", 10.0),
    "SILVER": ("XAGUSD", 0.30),
    "US100Cash": ("US100", 100.0),
    "US30Cash": ("US30", 200.0),
}
BUDGET = 100.0


@pytest.fixture
async def reale() -> AsyncIterator[ApplicationContainer]:
    settings = Settings()
    if settings.mt5.use_mock:
        pytest.skip("MT5_USE_MOCK=true: servono il terminale e il conto reali.")
    container = ApplicationContainer(settings)
    await container.startup()
    if container.broker is None:
        await container.shutdown()
        pytest.skip(f"Broker non disponibile: {container.broker_error}")
    try:
        yield container
    finally:
        await container.shutdown()


@pytest.mark.parametrize("nome", list(STRUMENTI))
async def test_servizio_coincide_con_mt5_grezzo(nome: str, reale: ApplicationContainer) -> None:
    canonico_atteso, distanza = STRUMENTI[nome]
    broker = reale.require_broker()
    terminale = broker._terminal  # type: ignore[attr-defined]
    valutazione = reale.require_valuation()
    strumento = await reale.require_instruments().by_broker_symbol(nome)
    info = await broker.get_symbol(nome)

    # --- mappatura ---
    assert strumento.canonical == canonico_atteso
    assert strumento.broker_symbol == nome
    assert reale.require_instruments().by_canonical(canonico_atteso).broker_symbol == nome

    candele = await broker.get_candles(nome, Timeframe.H1, 30)
    entry = round(candele.closed[-1].close, info.digits)
    stop_long = round(entry - distanza, info.digits)
    stop_short = round(entry + distanza, info.digits)

    # --- profitto e perdita allo stop: identici al centesimo ---
    grezzo_long = terminale.order_calc_profit(ORDER_TYPE_BUY, nome, 1.0, entry, stop_long)
    grezzo_short = terminale.order_calc_profit(ORDER_TYPE_SELL, nome, 1.0, entry, stop_short)
    long = await valutazione.loss_at_stop(strumento, Direction.LONG, 1.0, entry, stop_long)
    short = await valutazione.loss_at_stop(strumento, Direction.SHORT, 1.0, entry, stop_short)
    assert long.loss == pytest.approx(-grezzo_long, abs=0.005)
    assert short.loss == pytest.approx(-grezzo_short, abs=0.005)

    # --- margine ---
    grezzo_margine = terminale.order_calc_margin(ORDER_TYPE_BUY, nome, 1.0, entry)
    margine = await valutazione.margin(strumento, Direction.LONG, 1.0, entry)
    assert margine.margin == pytest.approx(grezzo_margine, abs=0.005)

    # --- size: la massima entro il budget, verificata col broker ---
    size = await valutazione.size_for_risk(strumento, Direction.LONG, entry, stop_long, BUDGET)
    assert size.volume > 0
    passi = Decimal(str(size.volume)) / Decimal(str(strumento.contract.volume_step))
    assert passi == passi.to_integral_value()
    perdita = -terminale.order_calc_profit(ORDER_TYPE_BUY, nome, size.volume, entry, stop_long)
    assert perdita <= BUDGET
    un_passo_in_piu = float(
        Decimal(str(size.volume)) + Decimal(str(strumento.contract.volume_step))
    )
    if un_passo_in_piu <= strumento.contract.volume_max:
        oltre = -terminale.order_calc_profit(
            ORDER_TYPE_BUY, nome, un_passo_in_piu, entry, stop_long
        )
        assert oltre > BUDGET

    # --- invarianti sul broker vero ---
    minimo = strumento.contract.volume_min
    perdite = [
        (await valutazione.loss_at_stop(strumento, Direction.LONG, v, entry, stop_long)).loss
        for v in (minimo, minimo * 10, minimo * 100)
    ]
    assert perdite == sorted(perdite) and perdite[0] < perdite[-1]
    lontano = round(entry - 2 * distanza, info.digits)
    assert (
        await valutazione.loss_at_stop(strumento, Direction.LONG, 1.0, entry, lontano)
    ).loss > long.loss


@pytest.mark.parametrize("nome", list(STRUMENTI))
async def test_stato_quotazione_coerente_con_il_broker(
    nome: str, reale: ApplicationContainer
) -> None:
    """Valido solo se l'ora broker è misurata; chiuso solo come deduzione."""
    broker = reale.require_broker()
    info = await broker.get_symbol(nome)
    tick = await broker.get_tick(nome)
    candele = await broker.get_candles(nome, Timeframe.H1, 30)
    qualita = QuoteQualityEvaluator(
        max_tick_age_seconds=reale.settings.mt5.max_tick_age_seconds
    ).evaluate(
        info,
        tick,
        now=reale.clock.now(),
        broker_clock=reale.broker_clock,
        last_candle_time=candele.closed[-1].time,
        last_candle_timeframe=Timeframe.H1,
    )
    if qualita.status is QuoteQualityStatus.VALID:
        assert reale.broker_clock.estimate().reliable
        assert qualita.market_status is MarketStatus.OPEN_CONFIRMED
    if qualita.market_status is MarketStatus.CLOSED_INFERRED:
        assert qualita.is_inference is True
    if not reale.broker_clock.estimate().reliable:
        assert qualita.status is not QuoteQualityStatus.VALID


async def test_simbolo_inesistente_non_diventa_un_altro(reale: ApplicationContainer) -> None:
    """XAUUSD non esiste su XM: non deve essere sostituito in silenzio con GOLD."""
    from backend.core.errors import TradingAgentError

    with pytest.raises(TradingAgentError):
        await reale.require_broker().get_symbol("XAUUSD")
