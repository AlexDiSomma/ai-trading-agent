"""Invarianti della foundation, verificati con test di proprietà (hypothesis).

Un test d'esempio dice "con questi numeri funziona"; un test di proprietà dice
"con **qualunque** numero plausibile vale questa regola", e cerca da solo il
controesempio. Le regole qui sotto sono quelle che, se violate, mettono a
rischio il conto dell'utente:

1. aumentando il volume, la perdita allo stop non diminuisce;
2. spostando lo stop più lontano, la perdita non diminuisce;
3. riducendo il budget di rischio, la size non aumenta;
4. la size non supera mai `volume_max` (né il massimo dell'utente);
5. la size è sempre un multiplo esatto di `volume_step`, e zero o ≥ `volume_min`;
6. la perdita alla size scelta non supera mai il budget (nemmeno di un centesimo);
7. una quotazione non `VALID` / mercato non `OPEN_CONFIRMED` non produce mai un
   `TradeIntent` eseguibile;
8. un'inferenza non diventa mai certezza, e senza ora broker nota nessuna
   quotazione è valida.

Il broker è il terminale finto, che riproduce il calcolatore di XM (non
linearità del forex compresa) entro lo 0,04% dei valori reali.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from backend.brokers.mt5.mapping import ORDER_TYPE_BUY, ORDER_TYPE_SELL
from backend.brokers.mt5.mock import DEFAULT_SYMBOLS, FakeMT5Terminal
from backend.core.enums import Direction, MarketStatus, QuoteQualityStatus
from backend.market_data.broker_time import BrokerClock
from backend.market_data.instruments import costruisci_strumento
from backend.market_data.quote_quality import QuoteQualityEvaluator
from backend.schemas.instrument import Instrument
from backend.schemas.market import SymbolInfo, Tick
from backend.schemas.quote import QuoteQuality
from backend.trade_planner.intent import TradeIntentBuilder
from backend.valuation.service import BrokerValuationService

ADESSO = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)
NOMI = ("EURUSD", "USDJPY", "GOLD", "SILVER", "US100Cash", "US30Cash")

#: Distanza di stop di riferimento per strumento (come nella validazione reale).
DISTANZA = {
    "EURUSD": 0.0050,
    "USDJPY": 0.50,
    "GOLD": 10.0,
    "SILVER": 0.30,
    "US100Cash": 100.0,
    "US30Cash": 200.0,
}

IMPOSTAZIONI = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


class CalcolatoreFinto:
    """Il calcolatore del terminale finto, esposto come `BrokerCalculator`."""

    def __init__(self) -> None:
        self.terminale = FakeMT5Terminal()

    async def calculate_profit(
        self,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
        *,
        sell: bool = False,
    ) -> float:
        azione = ORDER_TYPE_SELL if sell else ORDER_TYPE_BUY
        return self.terminale.order_calc_profit(azione, symbol, volume, price_open, price_close)

    async def calculate_margin(
        self, symbol: str, volume: float, price: float, *, sell: bool = False
    ) -> float:
        azione = ORDER_TYPE_SELL if sell else ORDER_TYPE_BUY
        return self.terminale.order_calc_margin(azione, symbol, volume, price)


def _info(nome: str) -> SymbolInfo:
    from backend.brokers.mt5.mapping import to_symbol_info

    spec = next(s for s in DEFAULT_SYMBOLS if s.name == nome)
    return to_symbol_info(spec, as_of=ADESSO)


STRUMENTI: dict[str, Instrument] = {n: costruisci_strumento(_info(n)) for n in NOMI}
PREZZI: dict[str, float] = {
    n: next(s for s in DEFAULT_SYMBOLS if s.name == n).base_price for n in NOMI
}
SERVIZIO = BrokerValuationService(CalcolatoreFinto(), account_currency="EUR")


def esegui(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


nomi = st.sampled_from(NOMI)
direzioni = st.sampled_from([Direction.LONG, Direction.SHORT])
#: Moltiplicatori della distanza di riferimento: da uno stop strettissimo a uno
#: venti volte più largo.
fattori_distanza = st.floats(min_value=0.05, max_value=20.0, allow_nan=False)
budget = st.floats(min_value=0.0, max_value=50_000.0, allow_nan=False)


def _stop(nome: str, direzione: Direction, entry: float, fattore: float) -> float:
    s = STRUMENTI[nome]
    distanza = max(DISTANZA[nome] * fattore, s.contract.tick_size)
    stop = entry - distanza if direzione is Direction.LONG else entry + distanza
    return round(stop, s.contract.digits)


def _volume_valido(nome: str, passi: int) -> float:
    c = STRUMENTI[nome].contract
    return float(Decimal(str(c.volume_min)) + Decimal(str(c.volume_step)) * passi)


# ======================================================================
# 1-2. Monotonia della perdita
# ======================================================================
@IMPOSTAZIONI
@given(
    nome=nomi,
    direzione=direzioni,
    fattore=fattori_distanza,
    a=st.integers(0, 1_000),
    in_piu=st.integers(1, 1_000),
)
def test_piu_volume_non_meno_perdita(
    nome: str, direzione: Direction, fattore: float, a: int, in_piu: int
) -> None:
    piccolo, grande = _volume_valido(nome, a), _volume_valido(nome, a + in_piu)
    assume(grande <= STRUMENTI[nome].contract.volume_max)
    entry = PREZZI[nome]
    stop = _stop(nome, direzione, entry, fattore)
    s = STRUMENTI[nome]
    p1 = esegui(SERVIZIO.loss_at_stop(s, direzione, piccolo, entry, stop)).loss
    p2 = esegui(SERVIZIO.loss_at_stop(s, direzione, grande, entry, stop)).loss
    assert p2 >= p1
    if grande >= 2 * piccolo:
        assert p2 > p1 or p1 == 0


@IMPOSTAZIONI
@given(nome=nomi, direzione=direzioni, f1=fattori_distanza, f2=fattori_distanza)
def test_stop_piu_lontano_non_meno_perdita(
    nome: str, direzione: Direction, f1: float, f2: float
) -> None:
    vicino, lontano = sorted((f1, f2))
    entry = PREZZI[nome]
    s = STRUMENTI[nome]
    stop_vicino = _stop(nome, direzione, entry, vicino)
    stop_lontano = _stop(nome, direzione, entry, lontano)
    p1 = esegui(SERVIZIO.loss_at_stop(s, direzione, 1.0, entry, stop_vicino)).loss
    p2 = esegui(SERVIZIO.loss_at_stop(s, direzione, 1.0, entry, stop_lontano)).loss
    assert p2 >= p1


# ======================================================================
# 3-6. Size
# ======================================================================
@IMPOSTAZIONI
@given(nome=nomi, direzione=direzioni, fattore=fattori_distanza, b1=budget, b2=budget)
def test_meno_budget_non_piu_size(
    nome: str, direzione: Direction, fattore: float, b1: float, b2: float
) -> None:
    piccolo, grande = sorted((b1, b2))
    entry = PREZZI[nome]
    stop = _stop(nome, direzione, entry, fattore)
    s = STRUMENTI[nome]
    v_piccolo = esegui(SERVIZIO.size_for_risk(s, direzione, entry, stop, piccolo)).volume
    v_grande = esegui(SERVIZIO.size_for_risk(s, direzione, entry, stop, grande)).volume
    assert v_piccolo <= v_grande


@IMPOSTAZIONI
@given(
    nome=nomi,
    direzione=direzioni,
    fattore=fattori_distanza,
    rischio=st.floats(min_value=0.0, max_value=10_000_000.0, allow_nan=False),
    massimo_utente=st.one_of(st.none(), st.floats(min_value=0.0, max_value=500.0)),
)
def test_size_dentro_i_limiti_e_sul_passo(
    nome: str,
    direzione: Direction,
    fattore: float,
    rischio: float,
    massimo_utente: float | None,
) -> None:
    entry = PREZZI[nome]
    stop = _stop(nome, direzione, entry, fattore)
    s = STRUMENTI[nome]
    c = s.contract
    size = esegui(
        SERVIZIO.size_for_risk(s, direzione, entry, stop, rischio, max_volume=massimo_utente)
    )

    # 4. mai oltre il massimo del broker né quello dell'utente
    assert size.volume <= c.volume_max
    if massimo_utente is not None:
        assert size.volume <= massimo_utente

    # 5. multiplo esatto del passo, e zero oppure almeno il minimo
    passi = Decimal(str(size.volume)) / Decimal(str(c.volume_step))
    assert passi == passi.to_integral_value()
    assert size.volume == 0 or size.volume >= c.volume_min

    # 6. la perdita richiesta di nuovo al broker non supera il budget
    if size.volume > 0:
        perdita = esegui(SERVIZIO.loss_at_stop(s, direzione, size.volume, entry, stop)).loss
        assert perdita <= rischio
        assert size.expected_loss == perdita


# ======================================================================
# 7. Nessun TradeIntent eseguibile senza quotazione valida
# ======================================================================
def _qualita(nome: str, stato: QuoteQualityStatus, mercato: MarketStatus) -> QuoteQuality:
    prezzo = PREZZI[nome]
    return QuoteQuality(
        broker_symbol=nome,
        status=stato,
        market_status=mercato,
        is_inference=mercato is MarketStatus.CLOSED_INFERRED,
        bid=prezzo,
        ask=prezzo * 1.0001,
        max_tick_age_seconds=15.0,
        evaluated_at=ADESSO,
    )


#: Tutte le combinazioni ammesse dall'invariante di `QuoteQuality`, tranne quella
#: valida: sono gli stati che non devono mai produrre un intent eseguibile.
COMBINAZIONI_NON_VALIDE = [
    (QuoteQualityStatus.STALE, MarketStatus.UNKNOWN),
    (QuoteQualityStatus.MISSING, MarketStatus.UNKNOWN),
    (QuoteQualityStatus.INCONSISTENT, MarketStatus.UNKNOWN),
    (QuoteQualityStatus.MARKET_CLOSED, MarketStatus.CLOSED_INFERRED),
    (QuoteQualityStatus.STALE, MarketStatus.CLOSED_INFERRED),
]


@IMPOSTAZIONI
@given(
    nome=nomi,
    direzione=direzioni,
    fattore=fattori_distanza,
    rischio=st.floats(min_value=1.0, max_value=100_000.0),
    combinazione=st.sampled_from(COMBINAZIONI_NON_VALIDE),
)
def test_quotazione_non_valida_mai_intent_eseguibile(
    nome: str,
    direzione: Direction,
    fattore: float,
    rischio: float,
    combinazione: tuple[QuoteQualityStatus, MarketStatus],
) -> None:
    stato, mercato = combinazione
    entry = PREZZI[nome]
    stop = _stop(nome, direzione, entry, fattore)
    intent = esegui(
        TradeIntentBuilder(SERVIZIO).build(
            STRUMENTI[nome],
            direzione,
            entry=entry,
            stop=stop,
            risk_budget=rischio,
            quote=_qualita(nome, stato, mercato),
        )
    )
    assert intent.executable is False
    assert intent.blocking_reasons


@IMPOSTAZIONI
@given(nome=nomi, direzione=direzioni, fattore=fattori_distanza)
def test_quotazione_di_un_altro_strumento_blocca(
    nome: str, direzione: Direction, fattore: float
) -> None:
    altro = next(n for n in NOMI if n != nome)
    entry = PREZZI[nome]
    intent = esegui(
        TradeIntentBuilder(SERVIZIO).build(
            STRUMENTI[nome],
            direzione,
            entry=entry,
            stop=_stop(nome, direzione, entry, fattore),
            risk_budget=1_000.0,
            quote=_qualita(altro, QuoteQualityStatus.VALID, MarketStatus.OPEN_CONFIRMED),
        )
    )
    assert intent.executable is False


# ======================================================================
# 8. Inferenza e certezza
# ======================================================================
class TestInvariantiDiQuoteQuality:
    def test_chiuso_dedotto_richiede_inferenza(self) -> None:
        with pytest.raises(ValidationError):
            QuoteQuality(
                broker_symbol="GOLD",
                status=QuoteQualityStatus.MARKET_CLOSED,
                market_status=MarketStatus.CLOSED_INFERRED,
                is_inference=False,
                max_tick_age_seconds=15.0,
                evaluated_at=ADESSO,
            )

    @pytest.mark.parametrize(
        "stato", [s for s in QuoteQualityStatus if s is not QuoteQualityStatus.VALID]
    )
    def test_aperto_confermato_solo_con_dato_valido(self, stato: QuoteQualityStatus) -> None:
        with pytest.raises(ValidationError):
            QuoteQuality(
                broker_symbol="GOLD",
                status=stato,
                market_status=MarketStatus.OPEN_CONFIRMED,
                is_inference=False,
                max_tick_age_seconds=15.0,
                evaluated_at=ADESSO,
            )

    def test_aperto_confermato_non_puo_essere_un_inferenza(self) -> None:
        with pytest.raises(ValidationError):
            QuoteQuality(
                broker_symbol="GOLD",
                status=QuoteQualityStatus.VALID,
                market_status=MarketStatus.OPEN_CONFIRMED,
                is_inference=True,
                max_tick_age_seconds=15.0,
                evaluated_at=ADESSO,
            )

    def test_non_esiste_uno_stato_chiuso_certo(self) -> None:
        """MT5 non espone le sessioni: "chiuso" è sempre una deduzione."""
        assert {m.name for m in MarketStatus} == {"OPEN_CONFIRMED", "CLOSED_INFERRED", "UNKNOWN"}


@IMPOSTAZIONI
@given(
    nome=nomi,
    eta_etichetta=st.floats(min_value=-14 * 3_600, max_value=60 * 3_600, allow_nan=False),
)
def test_senza_ora_broker_nessuna_quotazione_e_valida(nome: str, eta_etichetta: float) -> None:
    """Regressione del fail-open (ADR 0015), su qualunque etichetta temporale."""
    prezzo = PREZZI[nome]
    tick = Tick(
        symbol=nome,
        time=ADESSO - timedelta(seconds=eta_etichetta),
        bid=prezzo,
        ask=prezzo * 1.0001,
    )
    qualita = QuoteQualityEvaluator(max_tick_age_seconds=15.0).evaluate(
        _info(nome), tick, now=ADESSO, broker_clock=BrokerClock()
    )
    assert qualita.status is not QuoteQualityStatus.VALID
    assert qualita.market_status is not MarketStatus.OPEN_CONFIRMED
    if qualita.market_status is MarketStatus.CLOSED_INFERRED:
        assert qualita.is_inference is True


@IMPOSTAZIONI
@given(
    nome=nomi,
    eta_vera=st.floats(min_value=0.0, max_value=48 * 3_600, allow_nan=False),
    scarto_ore=st.sampled_from([0, 2, 3, -5, 9]),
)
def test_con_ora_broker_nota_valido_solo_se_davvero_fresco(
    nome: str, eta_vera: float, scarto_ore: int
) -> None:
    """Con lo scarto noto, VALID se e solo se l'età vera è entro il limite."""
    prezzo = PREZZI[nome]
    orologio = BrokerClock()
    orologio.seed(scarto_ore * 3_600, ADESSO)
    tick = Tick(
        symbol=nome,
        time=ADESSO + timedelta(hours=scarto_ore) - timedelta(seconds=eta_vera),
        bid=prezzo,
        ask=prezzo * 1.0001,
    )
    qualita = QuoteQualityEvaluator(max_tick_age_seconds=15.0).evaluate(
        _info(nome), tick, now=ADESSO, broker_clock=orologio
    )
    assert (qualita.status is QuoteQualityStatus.VALID) == (eta_vera <= 15.0)


@IMPOSTAZIONI
@given(
    scarto_vero=st.sampled_from([2 * 3_600, 3 * 3_600, 0, -4 * 3_600]),
    ritardo=st.floats(min_value=0, max_value=6 * 3_600, allow_nan=False),
)
def test_un_tick_singolo_non_stabilisce_mai_lo_scarto(scarto_vero: int, ritardo: float) -> None:
    orologio = BrokerClock()
    tick = Tick(
        symbol="EURUSD",
        time=ADESSO + timedelta(seconds=scarto_vero - ritardo),
        bid=1.1,
        ask=1.1001,
    )
    assert orologio.observe(tick, now=ADESSO) is False
    assert orologio.observe_many([tick], now=ADESSO) is False
    assert orologio.estimate(now=ADESSO).offset_seconds is None
