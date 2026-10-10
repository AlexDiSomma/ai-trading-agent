"""Test per strumento: EURUSD, USDJPY, GOLD, SILVER, US100Cash, US30Cash.

Per ognuno dei sei strumenti si verificano le sette cose richieste dalla
consolidazione della foundation — calcolo del profitto, perdita allo stop, size,
margine, normalizzazione del volume, stato della quotazione, mappatura del
simbolo — e ogni risultato viene **confrontato con il conto demo reale**.

I valori reali stanno in `tests/dati/xm_demo_2026-10-04.json`, prodotto da
`python -m backend.validazione_foundation` sul conto XM (EUR, leva 1:1000) il
2026-10-04. Il file è congelato: se lo si rigenera e i test diventano rossi, è
il broker che è cambiato, e va capito prima di toccare le tolleranze.

Tolleranze, motivate:

* specifiche, mappatura, size, normalizzazione: **uguaglianza esatta**;
* profitto e margine: **0,1%**. Il terminale finto converte con un cambio fisso
  (0.8887), il broker con quello del momento; la differenza misurata è ≤ 0,04%.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.brokers.mt5.connector import MT5Connector
from backend.brokers.mt5.mock import FakeMT5Terminal
from backend.core.enums import Direction, MarketStatus, QuoteQualityStatus, Timeframe
from backend.market_data.broker_time import BrokerClock
from backend.market_data.instruments import InstrumentMappingError, InstrumentRegistry
from backend.market_data.quote_quality import QuoteQualityEvaluator
from backend.market_data.specifications import SpecificationRepository
from backend.schemas.instrument import Instrument
from backend.trade_planner.intent import TradeIntentBuilder
from backend.valuation.service import BrokerValuationService, ValuationError

DATI_REALI: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "dati" / "xm_demo_2026-10-04.json").read_text(encoding="utf-8")
)
REALE: dict[str, dict[str, Any]] = DATI_REALI["strumenti"]
STRUMENTI = ("EURUSD", "USDJPY", "GOLD", "SILVER", "US100Cash", "US30Cash")
TOLLERANZA = 0.001
BUDGET = DATI_REALI["strumenti"]["EURUSD"]["size"]["budget"]

LeggiStrumento = Callable[[str], Awaitable[Instrument]]


def test_il_file_reale_copre_tutti_gli_strumenti() -> None:
    assert DATI_REALI["conto"]["valuta"] == "EUR"
    assert DATI_REALI["conto"]["tipo"] == "DEMO"
    for nome in STRUMENTI:
        assert "errore" not in REALE[nome], f"{nome}: la validazione reale non è riuscita"


def _scenario(nome: str) -> tuple[float, float, float]:
    """Entry, stop long, stop short usati sul conto reale."""
    reale = REALE[nome]
    entry = reale["prezzo_riferimento"]
    cifre = reale["strumento"]["contract"]["digits"]
    distanza = reale["distanza_stop"]
    return entry, round(entry - distanza, cifre), round(entry + distanza, cifre)


# ======================================================================
# Mappatura del simbolo
# ======================================================================
@pytest.mark.parametrize("nome", STRUMENTI)
class TestMappatura:
    async def test_identita_uguale_a_quella_reale(
        self, nome: str, strumento: LeggiStrumento
    ) -> None:
        mio = await strumento(nome)
        vero = REALE[nome]["strumento"]
        for campo in (
            "canonical",
            "broker_symbol",
            "asset_class",
            "base_currency",
            "quote_currency",
            "profit_currency",
            "margin_currency",
            "mapping_source",
        ):
            assert str(getattr(mio, campo)) == vero[campo], campo
        assert mio.canonical_is_certain is True

    async def test_specifica_di_contratto_uguale_a_quella_reale(
        self, nome: str, strumento: LeggiStrumento
    ) -> None:
        mio = (await strumento(nome)).contract.model_dump(mode="json")
        vero = REALE[nome]["strumento"]["contract"]
        for campo in (
            "digits",
            "tick_size",
            "contract_size",
            "volume_min",
            "volume_max",
            "volume_step",
            "stops_level_points",
            "freeze_level_points",
            "trade_mode",
        ):
            assert mio[campo] == vero[campo], campo


class TestIdentitaIndipendenteDaXm:
    """Il resto dell'applicazione ragiona sui nomi canonici, non su quelli di XM."""

    @pytest.fixture
    async def registro(self, connettore: MT5Connector, clock) -> InstrumentRegistry:  # type: ignore[no-untyped-def]
        registro = InstrumentRegistry(SpecificationRepository(connettore, clock=clock), clock=clock)
        for nome in STRUMENTI:
            await registro.by_broker_symbol(nome)
        return registro

    @pytest.mark.parametrize(
        ("canonico", "xm"),
        [
            ("XAUUSD", "GOLD"),
            ("XAGUSD", "SILVER"),
            ("US100", "US100Cash"),
            ("US30", "US30Cash"),
            ("EURUSD", "EURUSD"),
            ("usdjpy", "USDJPY"),
        ],
    )
    async def test_dal_canonico_al_simbolo_del_broker(
        self, registro: InstrumentRegistry, canonico: str, xm: str
    ) -> None:
        assert registro.by_canonical(canonico).broker_symbol == xm

    async def test_canonico_sconosciuto_non_viene_indovinato(
        self, registro: InstrumentRegistry
    ) -> None:
        with pytest.raises(InstrumentMappingError):
            registro.by_canonical("NAS100")


# ======================================================================
# Profitto, perdita allo stop, valore per unità
# ======================================================================
@pytest.mark.parametrize("nome", STRUMENTI)
class TestProfittoEPerdita:
    async def test_profitto_come_sul_conto_reale(
        self, nome: str, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        entry, stop_long, stop_short = _scenario(nome)
        s = await strumento(nome)
        reale = REALE[nome]["profitto"]
        long = await valutazione.profit(s, Direction.LONG, 1.0, entry, stop_long)
        short = await valutazione.profit(s, Direction.SHORT, 1.0, entry, stop_short)
        assert long == pytest.approx(reale["grezzo_long_1_lotto_allo_stop"], rel=TOLLERANZA)
        assert short == pytest.approx(reale["grezzo_short_1_lotto_allo_stop"], rel=TOLLERANZA)

    async def test_perdita_allo_stop_come_sul_conto_reale(
        self, nome: str, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        entry, stop_long, stop_short = _scenario(nome)
        s = await strumento(nome)
        reale = REALE[nome]["profitto"]
        long = await valutazione.loss_at_stop(s, Direction.LONG, 1.0, entry, stop_long)
        short = await valutazione.loss_at_stop(s, Direction.SHORT, 1.0, entry, stop_short)
        assert long.loss == pytest.approx(reale["servizio_perdita_long"], rel=TOLLERANZA)
        assert short.loss == pytest.approx(reale["servizio_perdita_short"], rel=TOLLERANZA)
        assert long.loss > 0 and short.loss > 0
        assert long.account_currency == "EUR"

    async def test_stop_dal_lato_sbagliato_rifiutato(
        self, nome: str, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        entry, stop_long, _ = _scenario(nome)
        with pytest.raises(ValuationError):
            await valutazione.loss_at_stop(
                await strumento(nome), Direction.SHORT, 1.0, entry, stop_long
            )

    async def test_valore_per_unita_e_scarto_da_tick_value(
        self,
        nome: str,
        valutazione: BrokerValuationService,
        strumento: LeggiStrumento,
        connettore: MT5Connector,
    ) -> None:
        entry, _, _ = _scenario(nome)
        s = await strumento(nome)
        reale = REALE[nome]["profitto"]
        valore = await valutazione.value_per_price_unit(s, entry)
        assert valore == pytest.approx(reale["valore_unita_prezzo"], rel=TOLLERANZA)
        scarto = await valutazione.tick_value_gap(s, await connettore.get_symbol(nome), entry)
        # 12,5% su metalli e indici, ~0 sul forex: lo scarto per cui tick_value
        # non entra in nessun calcolo.
        assert scarto.gap_pct == pytest.approx(reale["scarto_tick_value_pct"], abs=0.2)


class TestAsimmetriaRealeDelForex:
    async def test_eurusd_long_e_short_perdono_importi_diversi(
        self, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        """Misurato: 446.38 long contro 442.43 short sullo stesso movimento.

        Il broker converte al prezzo di chiusura, che per uno short in perdita è
        più alto. Una formula `distanza x valore_per_unità` darebbe lo stesso
        numero ai due lati: per questo la perdita si chiede sul movimento esatto.
        """
        entry, stop_long, stop_short = _scenario("EURUSD")
        s = await strumento("EURUSD")
        long = await valutazione.loss_at_stop(s, Direction.LONG, 1.0, entry, stop_long)
        short = await valutazione.loss_at_stop(s, Direction.SHORT, 1.0, entry, stop_short)
        assert long.loss > short.loss
        reale = REALE["EURUSD"]["profitto"]
        assert reale["servizio_perdita_long"] > reale["servizio_perdita_short"]


# ======================================================================
# Size
# ======================================================================
@pytest.mark.parametrize("nome", STRUMENTI)
class TestSize:
    async def test_size_uguale_a_quella_reale(
        self, nome: str, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        entry, stop_long, _ = _scenario(nome)
        size = await valutazione.size_for_risk(
            await strumento(nome), Direction.LONG, entry, stop_long, BUDGET
        )
        reale = REALE[nome]["size"]
        assert size.volume == reale["volume"]
        assert size.expected_loss == pytest.approx(reale["perdita_attesa"], rel=TOLLERANZA)

    async def test_la_size_reale_rispetta_il_budget_e_un_passo_in_piu_no(self, nome: str) -> None:
        """Verificato sul broker vero: la size è la massima entro il budget."""
        reale = REALE[nome]["size"]
        assert reale["perdita_grezza_al_volume"] <= reale["budget"]
        assert reale["perdita_grezza_un_passo_in_piu"] > reale["budget"]

    async def test_budget_insufficiente_per_il_minimo_da_size_zero(
        self, nome: str, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        entry, stop_long, _ = _scenario(nome)
        size = await valutazione.size_for_risk(
            await strumento(nome), Direction.LONG, entry, stop_long, 0.5
        )
        assert size.volume == 0.0
        assert size.tradeable is False
        assert size.limited_by == "below_min"


# ======================================================================
# Margine
# ======================================================================
@pytest.mark.parametrize("nome", STRUMENTI)
class TestMargine:
    async def test_margine_come_sul_conto_reale(
        self, nome: str, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        entry, _, _ = _scenario(nome)
        s = await strumento(nome)
        reale = REALE[nome]["margine"]
        uno = await valutazione.margin(s, Direction.LONG, 1.0, entry)
        minimo = await valutazione.margin(s, Direction.LONG, s.contract.volume_min, entry)
        assert uno.margin == pytest.approx(reale["servizio_1_lotto"], rel=TOLLERANZA)
        assert minimo.margin == pytest.approx(reale["servizio_volume_min"], abs=0.02)

    async def test_volume_non_normalizzato_rifiutato_anche_se_il_broker_lo_calcola(
        self, nome: str, valutazione: BrokerValuationService, strumento: LeggiStrumento
    ) -> None:
        """Il broker non valida il volume (misurato): lo fa il servizio."""
        entry, _, _ = _scenario(nome)
        s = await strumento(nome)
        assert REALE[nome]["margine"]["grezzo_volume_non_normalizzato"] > 0
        with pytest.raises(ValuationError):
            await valutazione.margin(s, Direction.LONG, s.contract.volume_min / 2, entry)


# ======================================================================
# Normalizzazione del volume
# ======================================================================
@pytest.mark.parametrize("nome", STRUMENTI)
async def test_normalizzazione_come_sul_conto_reale(nome: str, strumento: LeggiStrumento) -> None:
    s = await strumento(nome)
    for richiesto, attesa in REALE[nome]["normalizzazioni"].items():
        esito = BrokerValuationService.normalize_volume(s, float(richiesto))
        assert esito.volume == attesa["volume"], richiesto
        assert esito.adjustment == attesa["adjustment"], richiesto


# ======================================================================
# Stato della quotazione
# ======================================================================
@pytest.mark.parametrize("nome", STRUMENTI)
class TestStatoQuotazione:
    async def test_mercato_chiuso_dedotto_come_sul_conto_reale(
        self, nome: str, connettore: MT5Connector, terminale: FakeMT5Terminal, clock
    ) -> None:
        """Domenica sul conto reale: tick fermi da 30 ore, scarto non misurabile."""
        vero = REALE[nome]["qualita_quotazione"]
        assert vero["status"] == "MARKET_CLOSED"
        assert vero["market_status"] == "CLOSED_INFERRED"
        assert vero["is_inference"] is True

        terminale.simula_mercato_chiuso(30 * 3_600)
        info = await connettore.get_symbol(nome)
        tick = await connettore.get_tick(nome)
        candele = await connettore.get_candles(nome, Timeframe.H1, 30)
        qualita = QuoteQualityEvaluator(max_tick_age_seconds=15.0).evaluate(
            info,
            tick,
            now=clock.now(),
            broker_clock=BrokerClock(),
            last_candle_time=candele.closed[-1].time,
            last_candle_timeframe=Timeframe.H1,
        )
        assert str(qualita.status) == vero["status"]
        assert str(qualita.market_status) == vero["market_status"]
        assert qualita.is_inference is True

    async def test_quotazione_fresca_valida_solo_con_ora_broker_nota(
        self, nome: str, connettore: MT5Connector, orologio_broker: BrokerClock, clock
    ) -> None:
        info = await connettore.get_symbol(nome)
        tick = await connettore.get_tick(nome)
        valutatore = QuoteQualityEvaluator(max_tick_age_seconds=15.0)

        nota = valutatore.evaluate(info, tick, now=clock.now(), broker_clock=orologio_broker)
        assert nota.status is QuoteQualityStatus.VALID
        assert nota.market_status is MarketStatus.OPEN_CONFIRMED
        assert nota.bid and nota.ask and nota.spread and nota.spread > 0

        ignota = valutatore.evaluate(info, tick, now=clock.now(), broker_clock=BrokerClock())
        assert ignota.status is QuoteQualityStatus.STALE
        assert ignota.market_status is MarketStatus.UNKNOWN

    async def test_quotazione_mancante(
        self, nome: str, connettore: MT5Connector, orologio_broker: BrokerClock, clock
    ) -> None:
        info = await connettore.get_symbol(nome)
        qualita = QuoteQualityEvaluator(max_tick_age_seconds=15.0).evaluate(
            info, None, now=clock.now(), broker_clock=orologio_broker, quote_missing=True
        )
        assert qualita.status is QuoteQualityStatus.MISSING
        assert qualita.market_status is MarketStatus.UNKNOWN

    async def test_prezzo_incoerente(
        self, nome: str, connettore: MT5Connector, orologio_broker: BrokerClock, clock
    ) -> None:
        """Prezzo a 50 ATR dall'ultima chiusura: dato corrotto o simbolo sbagliato."""
        info = await connettore.get_symbol(nome)
        tick = await connettore.get_tick(nome)
        atr = tick.mid * 0.001
        qualita = QuoteQualityEvaluator(max_tick_age_seconds=15.0).evaluate(
            info,
            tick,
            now=clock.now(),
            broker_clock=orologio_broker,
            atr=atr,
            last_close=tick.mid - 50 * atr,
        )
        assert qualita.status is QuoteQualityStatus.INCONSISTENT
        assert qualita.market_status is MarketStatus.UNKNOWN


# ======================================================================
# Dall'idea al TradeIntent
# ======================================================================
@pytest.mark.parametrize("nome", STRUMENTI)
async def test_trade_intent_eseguibile_solo_con_quotazione_valida(
    nome: str,
    connettore: MT5Connector,
    terminale: FakeMT5Terminal,
    valutazione: BrokerValuationService,
    strumento: LeggiStrumento,
    orologio_broker: BrokerClock,
    clock,  # type: ignore[no-untyped-def]
) -> None:
    s = await strumento(nome)
    info = await connettore.get_symbol(nome)
    valutatore = QuoteQualityEvaluator(max_tick_age_seconds=15.0)
    costruttore = TradeIntentBuilder(valutazione, clock=clock)

    tick = await connettore.get_tick(nome)
    distanza = REALE[nome]["distanza_stop"]
    entry = tick.ask
    stop = round(entry - distanza, s.contract.digits)

    valida = valutatore.evaluate(info, tick, now=clock.now(), broker_clock=orologio_broker)
    intent = await costruttore.build(
        s, Direction.LONG, entry=entry, stop=stop, risk_budget=BUDGET, quote=valida
    )
    assert intent.executable is True, intent.blocking_reasons
    assert intent.canonical == REALE[nome]["strumento"]["canonical"]
    assert 0 < intent.expected_loss <= BUDGET
    assert intent.margin_required and intent.margin_required > 0

    terminale.tick_lag_seconds = 120
    vecchio = await connettore.get_tick(nome)
    stale = valutatore.evaluate(
        info, vecchio, now=clock.now() + timedelta(seconds=0), broker_clock=orologio_broker
    )
    bloccato = await costruttore.build(
        s, Direction.LONG, entry=entry, stop=stop, risk_budget=BUDGET, quote=stale
    )
    assert bloccato.executable is False
    # Il volume viene calcolato comunque, per spiegare: ma non è un permesso.
    assert bloccato.volume == intent.volume
