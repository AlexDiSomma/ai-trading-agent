"""Terminale MetaTrader 5 finto.

A cosa serve davvero:

* **sviluppare senza Windows e senza MT5 aperto** (CI, Docker, Mac);
* **testare il connettore vero**, non una sua copia semplificata: il mock
  implementa lo stesso protocollo `MT5Terminal`, quindi i test esercitano
  `MT5Connector` riga per riga;
* **provocare i guasti a comando**: terminale disconnesso, simbolo inesistente,
  risposta `None`, tick vecchio, timeout. Sono gli scenari della fase 12, e un
  sistema che non li ha mai visti non è pronto per il denaro reale.

I prezzi sono generati da un rumore **deterministico** indicizzato sul tempo: lo
stesso simbolo e lo stesso timeframe danno sempre la stessa serie, oggi e fra un
mese. Senza determinismo un test che falla una volta su venti diventa un test
che si impara a ignorare.

Nessun valore prodotto qui ha significato di mercato. Il connettore marca i dati
con `source="mt5-mock"` e `is_simulated=True` e la dashboard lo scrive in
chiaro: un numero inventato presentato come reale è peggio di un numero assente.
"""

from __future__ import annotations

import functools
import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.core.clock import Clock, SystemClock, ensure_utc

#: Origine della griglia temporale delle barre. Fissa, perché l'indice di una
#: barra (e quindi il suo prezzo) non deve dipendere da quando girano i test.
ANCHOR = datetime(2024, 1, 1, tzinfo=UTC)


@dataclass
class FakeSymbol:
    """Specifiche di contratto verosimili per uno strumento.

    I valori ricalcano l'ordine di grandezza di un conto XM standard, ma restano
    inventati: servono a verificare che il codice **legga** le specifiche dal
    broker, non a prevedere quelle vere.
    """

    name: str
    digits: int = 5
    point: float = 0.00001
    trade_contract_size: float = 100_000.0
    trade_tick_size: float = 0.00001
    trade_tick_value: float = 1.0
    volume_min: float = 0.01
    volume_max: float = 50.0
    volume_step: float = 0.01
    spread: int = 12
    trade_stops_level: int = 10
    trade_freeze_level: int = 0
    trade_mode: int = 4
    visible: bool = True
    description: str = ""
    #: Percorso di categoria, come lo dichiara XM.
    path: str = ""
    currency_base: str = "EUR"
    currency_profit: str = "USD"
    currency_margin: str = "EUR"
    base_price: float = 1.1250
    volatility: float = 0.0009

    #: Fattore che porta `trade_tick_value` nella valuta del conto.
    #: 1.0 per il forex, 0.8887 per metalli e indici: e' l'incoerenza
    #: **misurata** sul conto XM in EUR, riprodotta di proposito perche' un
    #: codice che assume tick_value in valuta conto debba rompersi nei test.
    account_conversion: float = 1.0

    #: Vero quando la conversione nella valuta del conto avviene al prezzo di
    #: questo stesso simbolo: succede alle coppie che hanno la valuta del conto
    #: come base (EURUSD, EURJPY su un conto in EUR). Riprodotto perche' rende il
    #: calcolo del profitto **non lineare** nel delta di prezzo, ed e' il difetto
    #: che ha dato 47.056 invece di 88.810 su EURUSD.
    conversion_uses_close: bool = False

    #: Modello di margine calibrato sui valori **misurati** sul conto XM (leva
    #: 1:1000): margine = volume x contratto x (prezzo se CFD) / leva, convertito
    #: nella valuta del conto. Per il forex il margine e' sul nozionale in valuta
    #: base, per metalli e indici sul nozionale in prezzo.
    margin_uses_price: bool = False
    #: Fattore dalla valuta di margine a quella del conto (EUR): 1.0 se il margine
    #: e' gia' in EUR (EURUSD), 0.8889 se in USD.
    margin_conversion: float = 1.0


#: Paniere allineato al conto XM demo reale (letto il 2026-10-04).
#:
#: I valori NON sono inventati: digits, contract size, tick value, volume step e
#: valute sono quelli che il broker dichiara davvero. Due dettagli deliberati:
#:
#: * `trade_tick_value` di EURUSD e' 0.8887 e non 1.0, perche' il conto e' in
#:   EUR. Un codice che assume 1.0 deve rompersi nei test, non in produzione.
#: * `trade_stops_level` e' 0 su tutti gli strumenti, come su XM: nessuna
#:   distanza minima di stop imposta dal broker.
#:
#: XAUUSD e US30 **non ci sono di proposito**: su XM non esistono con quel nome,
#: e servono a verificare che il sistema lo dica invece di sostituirli in
#: silenzio (vedi test_regressioni_xm.py).
_SIMBOLI_OPERATIVI: tuple[FakeSymbol, ...] = (
    FakeSymbol(
        "EURUSD",
        margin_conversion=1.0,
        conversion_uses_close=True,
        path=r"Forex\Standard\Majors\EURUSD",
        digits=5,
        point=0.00001,
        trade_contract_size=100_000.0,
        trade_tick_size=0.00001,
        trade_tick_value=0.8887,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        spread=20,
        trade_stops_level=0,
        description="Euro vs US Dollar",
        currency_base="EUR",
        currency_profit="USD",
        currency_margin="EUR",
        base_price=1.12501,
        volatility=0.0009,
    ),
    FakeSymbol(
        "GBPUSD",
        margin_conversion=1.0,
        path=r"Forex\Standard\Majors\GBPUSD",
        digits=5,
        point=0.00001,
        trade_contract_size=100_000.0,
        trade_tick_size=0.00001,
        trade_tick_value=0.8887,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        spread=89,
        trade_stops_level=0,
        description="Great Britain Pound vs US Dollar",
        currency_base="GBP",
        currency_profit="USD",
        currency_margin="GBP",
        base_price=1.32353,
        volatility=0.0011,
    ),
    FakeSymbol(
        "USDJPY",
        margin_conversion=0.8889,
        path=r"Forex\Standard\Majors\USDJPY",
        digits=3,
        point=0.001,
        trade_contract_size=100_000.0,
        trade_tick_size=0.001,
        trade_tick_value=0.5629,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        spread=48,
        trade_stops_level=0,
        description="US Dollar vs Japanese Yen",
        currency_base="USD",
        currency_profit="JPY",
        currency_margin="USD",
        base_price=157.842,
        volatility=0.14,
    ),
    FakeSymbol(
        "EURJPY",
        margin_conversion=1.0,
        conversion_uses_close=True,
        path=r"Forex\Standard\Minors\EURJPY",
        digits=3,
        point=0.001,
        trade_contract_size=100_000.0,
        trade_tick_size=0.001,
        trade_tick_value=0.5629,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        spread=72,
        trade_stops_level=0,
        description="Euro vs Japanese Yen",
        currency_base="EUR",
        currency_profit="JPY",
        currency_margin="EUR",
        base_price=177.582,
        volatility=0.16,
    ),
    FakeSymbol(
        # Attenzione: XM dichiara l'oro come USD/USD/USD, non XAU/USD.
        # Vedi DECISIONI.md e portfolio/exposure.py.
        "GOLD",
        margin_uses_price=True,
        margin_conversion=0.8887,
        account_conversion=0.8887,
        path=r"Derivatives\Spot Metals\GOLD",
        digits=2,
        point=0.01,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        spread=53,
        trade_stops_level=0,
        description="Gold vs US Dollar",
        currency_base="USD",
        currency_profit="USD",
        currency_margin="USD",
        base_price=4140.07,
        volatility=6.5,
    ),
    FakeSymbol(
        "SILVER",
        margin_uses_price=True,
        margin_conversion=0.8887,
        account_conversion=0.8887,
        path=r"Derivatives\Spot Metals\SILVER",
        digits=3,
        point=0.001,
        trade_contract_size=5_000.0,
        trade_tick_size=0.001,
        trade_tick_value=5.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        spread=93,
        trade_stops_level=0,
        description="Silver vs US Dollar",
        currency_base="USD",
        currency_profit="USD",
        currency_margin="USD",
        base_price=60.329,
        volatility=0.35,
    ),
    FakeSymbol(
        "US30Cash",
        margin_uses_price=True,
        margin_conversion=0.8887,
        account_conversion=0.8887,
        path=r"Derivatives\Cash\Cash Indices\US30Cash",
        digits=2,
        point=0.01,
        trade_contract_size=1.0,
        trade_tick_size=0.01,
        trade_tick_value=0.01,
        volume_min=0.1,
        volume_max=180.0,
        volume_step=0.1,
        spread=600,
        trade_stops_level=0,
        description="Dow Jones 30 Index",
        currency_base="USD",
        currency_profit="USD",
        currency_margin="USD",
        base_price=51_188.77,
        volatility=95.0,
    ),
    FakeSymbol(
        "US100Cash",
        margin_uses_price=True,
        margin_conversion=0.8887,
        account_conversion=0.8887,
        path=r"Derivatives\Cash\Cash Indices\US100Cash",
        digits=2,
        point=0.01,
        trade_contract_size=1.0,
        trade_tick_size=0.01,
        trade_tick_value=0.01,
        volume_min=0.1,
        volume_max=240.0,
        volume_step=0.1,
        spread=616,
        trade_stops_level=0,
        description="Nasdaq 100 Index",
        currency_base="USD",
        currency_profit="USD",
        currency_margin="USD",
        base_price=30_791.56,
        volatility=85.0,
    ),
    FakeSymbol(
        "US500Cash",
        margin_uses_price=True,
        margin_conversion=0.8887,
        account_conversion=0.8887,
        path=r"Derivatives\Cash\Cash Indices\US500Cash",
        digits=2,
        point=0.01,
        trade_contract_size=1.0,
        trade_tick_size=0.01,
        trade_tick_value=0.01,
        volume_min=0.1,
        volume_max=870.0,
        volume_step=0.1,
        spread=80,
        trade_stops_level=0,
        description="S&P 500 Index",
        currency_base="USD",
        currency_profit="USD",
        currency_margin="USD",
        base_price=7_722.67,
        volatility=22.0,
    ),
)


#: Valori indicativi delle valute contro USD, solo per costruire prezzi verosimili
#: dei cross dell'universo globale. Non sono quotazioni.
_VALORE_IN_USD = {
    "USD": 1.0,
    "EUR": 1.125,
    "GBP": 1.32,
    "JPY": 1 / 157.8,
    "CHF": 1.25,
    "AUD": 0.66,
    "CAD": 0.72,
    "NZD": 0.59,
    "SEK": 0.095,
}


def _cross(nome: str) -> FakeSymbol:
    base, quotazione = nome[:3], nome[3:]
    prezzo = _VALORE_IN_USD[base] / _VALORE_IN_USD[quotazione]
    jpy = quotazione == "JPY"
    return FakeSymbol(
        nome,
        path=rf"Forex\Standard\Minors\{nome}",
        digits=3 if jpy else 5,
        point=0.001 if jpy else 0.00001,
        trade_tick_size=0.001 if jpy else 0.00001,
        trade_stops_level=0,
        spread=30,
        currency_base=base,
        currency_profit=quotazione,
        currency_margin=base,
        base_price=round(prezzo, 3 if jpy else 5),
        volatility=prezzo * 0.0008,
        description=f"{base} vs {quotazione}",
    )


def _indice(nome: str, prezzo: float, valuta: str, cartella: str = "Cash Indices") -> FakeSymbol:
    return FakeSymbol(
        nome,
        margin_uses_price=True,
        margin_conversion=0.8887,
        account_conversion=0.8887,
        path=rf"Derivatives\Cash\{cartella}\{nome}",
        digits=2,
        point=0.01,
        trade_contract_size=1.0,
        trade_tick_size=0.01,
        trade_tick_value=0.01,
        volume_min=0.1,
        volume_max=100.0,
        volume_step=0.1,
        spread=200,
        trade_stops_level=0,
        currency_base=valuta,
        currency_profit=valuta,
        currency_margin=valuta,
        base_price=prezzo,
        volatility=prezzo * 0.0012,
    )


#: Universo di riferimento dell'intelligence globale (fase 4): i cross mancanti,
#: gli indici europei e asiatici, il petrolio. Nomi come su XM.
_SIMBOLI_GLOBALI: tuple[FakeSymbol, ...] = (
    *(
        _cross(n)
        for n in (
            "USDCHF",
            "AUDUSD",
            "USDCAD",
            "NZDUSD",
            "EURGBP",
            "EURCHF",
            "EURAUD",
            "EURCAD",
            "EURNZD",
            "GBPJPY",
            "GBPCHF",
            "GBPAUD",
            "GBPCAD",
            "GBPNZD",
            "CHFJPY",
            "AUDJPY",
            "CADJPY",
            "NZDJPY",
            "AUDCHF",
            "CADCHF",
            "NZDCHF",
            "AUDCAD",
            "AUDNZD",
            "NZDCAD",
            "USDSEK",
        )
    ),
    _indice("GER40Cash", 24_000.0, "EUR"),
    _indice("UK100Cash", 9_500.0, "GBP"),
    _indice("JP225Cash", 45_000.0, "JPY"),
    _indice("OILCash", 62.0, "USD", "Cash Energies"),
)

DEFAULT_SYMBOLS: tuple[FakeSymbol, ...] = _SIMBOLI_OPERATIVI + tuple(
    s for s in _SIMBOLI_GLOBALI if s.name not in {o.name for o in _SIMBOLI_OPERATIVI}
)


@dataclass
class FakeAccount:
    login: int = 169_444_760
    server: str = "XMGlobalMU-MT5 2"
    name: str = "Conto demo di prova"
    company: str = "XM Global Limited"
    currency: str = "EUR"
    # 1:1000 come sul conto demo reale: i margini del mock sono calibrati su
    # questa leva.
    leverage: int = 1_000
    balance: float = 10_000.0
    equity: float = 10_000.0
    margin: float = 0.0
    margin_free: float = 10_000.0
    margin_level: float = 0.0
    profit: float = 0.0
    trade_allowed: bool = True
    trade_expert: bool = True
    # 0 = demo, come il conto XM di prova. Il mock non deve mai presentarsi
    # come conto reale.
    trade_mode: int = 0


@dataclass
class FakeTerminalInfo:
    connected: bool = True
    build: int = 4530
    trade_allowed: bool = True
    community_account: bool = False
    path: str = "C:/Program Files/XM Global MT5"


@dataclass
class FakePosition:
    ticket: int
    symbol: str
    type: int  # 0 = BUY, 1 = SELL
    volume: float
    price_open: float
    price_current: float
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    magic: int = 0
    comment: str = ""
    time: float = field(default_factory=lambda: ANCHOR.timestamp())


@dataclass
class FakeDeal:
    ticket: int
    order: int = 0
    position_id: int = 0
    symbol: str = ""
    type: int = 0
    entry: int = 1
    volume: float = 0.0
    price: float = 0.0
    profit: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    fee: float = 0.0
    magic: int = 0
    comment: str = ""
    time: float = field(default_factory=lambda: ANCHOR.timestamp())


#: Scarto dell'ora di XM rispetto a UTC misurato il 2026-10-04 (ora legale
#: americana): ultimo tick del venerdi' etichettato 23:57:59, cioe' 20:58 UTC.
ORA_BROKER_XM_SECONDI = 3 * 3_600.0


class FakeMT5Terminal:
    """Implementazione finta di `MT5Terminal`, di sola lettura come l'originale.

    Nota deliberata: qui non esiste `order_send`, esattamente come nel
    protocollo. Il mock non deve poter diventare la scorciatoia con cui la
    scrittura rientra nel sistema prima della fase 11.
    """

    def __init__(
        self,
        *,
        account: FakeAccount | None = None,
        symbols: tuple[FakeSymbol, ...] = DEFAULT_SYMBOLS,
        positions: list[FakePosition] | None = None,
        deals: list[FakeDeal] | None = None,
        clock: Clock | None = None,
        seed: int = 20260101,
        connected: bool = True,
        broker_offset_seconds: float = ORA_BROKER_XM_SECONDI,
    ) -> None:
        self.account = account or FakeAccount()
        # Copia delle specifiche, non riferimento: `DEFAULT_SYMBOLS` e' una tupla
        # a livello di modulo di dataclass **mutabili**. Condividerle fra terminali
        # significa che un test che modifica un volume minimo inquina tutti quelli
        # successivi, e il risultato dipende dall'ordine di esecuzione. Difetto
        # trovato dalla suite completa il 2026-10-04.
        self.symbols: dict[str, FakeSymbol] = {s.name: replace(s) for s in symbols}
        self.positions: list[FakePosition] = positions or []
        self.pending_orders: list[Any] = []
        self.deals: list[FakeDeal] = deals or []
        self.terminal = FakeTerminalInfo(connected=connected)
        self._clock = clock or SystemClock()
        self._seed = seed
        self._initialized = False
        self._selected: set[str] = set()
        #: Scarto dell'ora del broker. Come il terminale vero, il finto etichetta
        #: tick e barre in **ora del broker spacciata per UTC** (XM: UTC+3 in ora
        #: legale). Con scarto zero il mock nascondeva il difetto di freschezza
        #: corretto dall'ADR 0015: tutti i test passavano su un'ipotesi falsa.
        self.broker_offset_seconds = broker_offset_seconds

        # --- iniezione di guasti, usata dai test ---
        #: Chiamate che devono restituire `None` (il modo in cui MT5 segnala
        #: errore) la prossima volta che vengono invocate.
        self.fail_calls: set[str] = set()
        #: Chiamate che devono restituire `None` per sempre.
        self.always_fail_calls: set[str] = set()
        self.error: tuple[int, str] = (0, "Success")
        #: Quanti secondi di ritardo aggiungere all'ora dei tick, per simulare
        #: un flusso prezzi fermo (mercato chiuso, connessione caduta).
        self.tick_lag_seconds: float = 0.0
        #: Simboli per cui restituire bid/ask a zero, come fa MT5 quando per
        #: quello strumento non e' ancora arrivata nessuna quotazione.
        self.symbols_without_quotes: set[str] = set()
        #: Secondi di cui arretrare la generazione delle barre. A mercato chiuso
        #: sono vecchi sia i tick sia le barre: invecchiare solo i tick simulava
        #: un flusso prezzi interrotto, che e' un guasto diverso.
        self.bar_lag_seconds: float = 0.0

    # ------------------------------------------------------------------
    # Iniezione di guasti
    # ------------------------------------------------------------------
    def break_next(
        self, call: str, *, code: int = -10005, message: str = "Errore simulato"
    ) -> None:
        """La prossima invocazione di `call` restituirà `None`."""
        self.fail_calls.add(call)
        self.error = (code, message)

    def break_always(
        self, call: str, *, code: int = -10005, message: str = "Errore simulato"
    ) -> None:
        self.always_fail_calls.add(call)
        self.error = (code, message)

    def simula_mercato_chiuso(self, da_secondi: float) -> None:
        """Invecchia tick e barre insieme, come a mercato chiuso."""
        self.tick_lag_seconds = da_secondi
        self.bar_lag_seconds = da_secondi

    def repair(self) -> None:
        self.fail_calls.clear()
        self.always_fail_calls.clear()
        self.error = (0, "Success")
        self.tick_lag_seconds = 0.0
        self.bar_lag_seconds = 0.0

    def _should_fail(self, call: str) -> bool:
        if call in self.always_fail_calls:
            return True
        if call in self.fail_calls:
            self.fail_calls.discard(call)
            return True
        return False

    # ------------------------------------------------------------------
    # Ciclo di vita
    # ------------------------------------------------------------------
    def initialize(self, **kwargs: Any) -> bool:
        if self._should_fail("initialize"):
            return False
        self._initialized = True
        # Se vengono passate credenziali, il conto finto le riflette: serve a
        # testare il controllo "sono collegato al conto giusto?".
        if (login := kwargs.get("login")) is not None:
            self.account.login = int(login)
        if (server := kwargs.get("server")) is not None:
            self.account.server = str(server)
        return True

    def login(self, login: int, *, password: str, server: str, timeout: int) -> bool:
        if self._should_fail("login"):
            return False
        self.account.login = int(login)
        self.account.server = server
        return True

    def shutdown(self) -> None:
        self._initialized = False
        self._selected.clear()

    def last_error(self) -> tuple[int, str]:
        return self.error

    # ------------------------------------------------------------------
    # Stato
    # ------------------------------------------------------------------
    def terminal_info(self) -> Any:
        if self._should_fail("terminal_info"):
            return None
        return self.terminal

    def account_info(self) -> Any:
        if self._should_fail("account_info") or not self.terminal.connected:
            return None
        # L'equity segue il profitto aperto, come su un conto vero.
        open_profit = sum(p.profit + p.swap + p.commission for p in self.positions)
        self.account.profit = round(open_profit, 2)
        self.account.equity = round(self.account.balance + open_profit, 2)
        self.account.margin_free = round(self.account.equity - self.account.margin, 2)
        self.account.margin_level = (
            round(self.account.equity / self.account.margin * 100, 2)
            if self.account.margin
            else 0.0
        )
        return self.account

    # ------------------------------------------------------------------
    # Strumenti
    # ------------------------------------------------------------------
    def symbols_get(self, group: str | None = None) -> tuple[Any, ...] | None:
        if self._should_fail("symbols_get"):
            return None
        values = list(self.symbols.values())
        if group:
            needle = group.strip("*").upper()
            values = [s for s in values if needle in s.name.upper()]
        return tuple(values)

    def symbol_info(self, symbol: str) -> Any:
        if self._should_fail("symbol_info"):
            return None
        return self.symbols.get(symbol)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        if self._should_fail("symbol_select"):
            return False
        if symbol not in self.symbols:
            return False
        if enable:
            self._selected.add(symbol)
        else:
            self._selected.discard(symbol)
        return True

    def symbol_info_tick(self, symbol: str) -> Any:
        if self._should_fail("symbol_info_tick"):
            return None
        spec = self.symbols.get(symbol)
        if spec is None:
            return None
        tick_time = self._clock.now() + timedelta(
            seconds=self.broker_offset_seconds - self.tick_lag_seconds
        )
        if symbol in self.symbols_without_quotes:
            return _FakeTick(time=tick_time.timestamp(), bid=0.0, ask=0.0, last=0.0, volume=0.0)

        price = self._current_price(spec)
        half_spread = spec.spread * spec.point / 2
        return _FakeTick(
            time=tick_time.timestamp(),
            bid=round(price - half_spread, spec.digits),
            ask=round(price + half_spread, spec.digits),
            last=round(price, spec.digits),
            volume=1.0,
        )

    # ------------------------------------------------------------------
    # Storico prezzi
    # ------------------------------------------------------------------
    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start: int, count: int
    ) -> list[dict[str, float]] | None:
        if self._should_fail("copy_rates_from_pos"):
            return None
        spec = self.symbols.get(symbol)
        if spec is None:
            return None
        minutes = _timeframe_minutes(timeframe)
        riferimento = self._clock.now() - timedelta(seconds=self.bar_lag_seconds)
        last_index = self._bar_index(riferimento, minutes)
        first_index = last_index - start - count + 1
        return self._bars(spec, minutes, first_index, last_index - start)

    def copy_rates_from(
        self, symbol: str, timeframe: int, date_from: Any, count: int
    ) -> list[dict[str, float]] | None:
        if self._should_fail("copy_rates_from"):
            return None
        spec = self.symbols.get(symbol)
        if spec is None:
            return None
        minutes = _timeframe_minutes(timeframe)
        # `date_from` e' in ora broker, come le etichette delle barre.
        momento = ensure_utc(date_from) - timedelta(seconds=self.broker_offset_seconds)
        end_index = self._bar_index(momento, minutes)
        return self._bars(spec, minutes, end_index - count + 1, end_index)

    def copy_ticks_from(
        self, symbol: str, date_from: Any, count: int, flags: int
    ) -> list[dict[str, float]] | None:
        if self._should_fail("copy_ticks_from"):
            return None
        spec = self.symbols.get(symbol)
        if spec is None:
            return None
        start = ensure_utc(date_from)
        half_spread = spec.spread * spec.point / 2
        base_index = int((start - ANCHOR).total_seconds())
        ticks: list[dict[str, float]] = []
        for i in range(count):
            scostamento = self._walk(f"{symbol}:ticks", base_index + i)
            price = spec.base_price + scostamento * spec.volatility
            ticks.append(
                {
                    # `date_from` e' gia' in ora broker: l'etichetta lo resta.
                    "time": (start + timedelta(seconds=i)).timestamp(),
                    "bid": round(price - half_spread, spec.digits),
                    "ask": round(price + half_spread, spec.digits),
                    "last": round(price, spec.digits),
                    "volume": 1.0,
                }
            )
        return ticks

    # ------------------------------------------------------------------
    # Conto
    # ------------------------------------------------------------------
    def positions_get(self, *, symbol: str | None = None) -> tuple[Any, ...] | None:
        if self._should_fail("positions_get"):
            return None
        items = self.positions
        if symbol:
            items = [p for p in items if p.symbol == symbol]
        return tuple(items)

    def orders_get(self, *, symbol: str | None = None) -> tuple[Any, ...] | None:
        if self._should_fail("orders_get"):
            return None
        items = self.pending_orders
        if symbol:
            items = [o for o in items if getattr(o, "symbol", None) == symbol]
        return tuple(items)

    # ------------------------------------------------------------------
    # Calcolatori (non inviano nulla)
    # ------------------------------------------------------------------
    def order_calc_profit(
        self, action: int, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        """Profitto in valuta del conto, con la conversione dichiarata dal simbolo.

        Riproduce il comportamento misurato su XM: il risultato NON coincide con
        `movimento / tick_size * tick_value * volume` per metalli e indici.
        """
        if self._should_fail("order_calc_profit"):
            return None
        spec = self.symbols.get(symbol)
        if spec is None:
            return None
        movimento = price_close - price_open
        if action == 1:  # vendita
            movimento = -movimento
        ticks = movimento / spec.trade_tick_size

        if spec.conversion_uses_close and price_close > 0:
            # Il valore del tick dichiarato e' quello convertito al prezzo
            # corrente; qui si risale al valore in valuta di quotazione e si
            # riconverte al prezzo di chiusura, come fa il broker. E' la fonte
            # della non linearita'.
            valore_in_quotazione = spec.trade_tick_value * spec.base_price
            return round(ticks * valore_in_quotazione * volume / price_close, 2)

        return round(ticks * spec.trade_tick_value * volume * spec.account_conversion, 2)

    def order_calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> float | None:
        """Margine in valuta del conto, come lo restituisce XM.

        Come il broker vero, **non valida il volume**: un volume fuori passo
        produce comunque un numero. E' la ragione per cui la normalizzazione sta
        nel servizio di valutazione.
        """
        if self._should_fail("order_calc_margin"):
            return None
        spec = self.symbols.get(symbol)
        if spec is None or price <= 0:
            return None
        nozionale = volume * spec.trade_contract_size * (price if spec.margin_uses_price else 1.0)
        return round(nozionale / self.account.leverage * spec.margin_conversion, 2)

    def history_orders_get(self, date_from: Any, date_to: Any) -> tuple[Any, ...] | None:
        if self._should_fail("history_orders_get"):
            return None
        return ()

    def history_deals_get(self, date_from: Any, date_to: Any) -> tuple[Any, ...] | None:
        if self._should_fail("history_deals_get"):
            return None
        start = ensure_utc(date_from).timestamp()
        end = ensure_utc(date_to).timestamp()
        return tuple(d for d in self.deals if start <= d.time <= end)

    # ------------------------------------------------------------------
    # Generazione dei prezzi
    # ------------------------------------------------------------------
    # Il prezzo di una barra e' una funzione pura del suo indice sulla griglia
    # temporale: nessuno stato, nessuna cache, nessuna serie da ricostruire.
    #
    # Perche' non una passeggiata casuale accumulata: per leggere le ultime 200
    # barre M1 di oggi bisognerebbe generare tutte quelle dall'origine della
    # griglia - circa un milione - a ogni avvio. Con il rumore indicizzato il
    # costo e' costante e, soprattutto, due letture della stessa barra danno
    # sempre lo stesso prezzo anche a distanza di giorni.
    #
    # La somma su piu' "ottave" (passo 1, 2, 4, 8...) produce movimento su piu'
    # scale temporali: ci sono tendenze lunghe, ritracciamenti e rumore breve.
    # Serve a non testare regime detector e indicatori su una linea piatta.
    OCTAVES = 6

    @staticmethod
    def _bar_index(moment: datetime, minutes: int) -> int:
        """Indice della barra sulla griglia temporale, contato dall'origine."""
        delta = ensure_utc(moment) - ANCHOR
        return int(delta.total_seconds() // 60 // minutes)

    def _walk(self, seed: str, index: int) -> float:
        """Rumore frazionario deterministico, ampiezza normalizzata a circa 1."""
        return _rumore_frazionario(self._seed, seed, index, self.OCTAVES)

    def _price_at(self, spec: FakeSymbol, minutes: int, index: int) -> float:
        """Prezzo sulla griglia del timeframe indicato.

        L'indice viene riportato ai minuti assoluti dall'origine, cosi' M5 e H1
        descrivono lo *stesso* mercato a risoluzioni diverse: altrimenti il
        multi-timeframe mostrerebbe grafici che si contraddicono.
        """
        absolute_minutes = index * minutes
        drift = self._walk(spec.name, absolute_minutes) * 12
        return spec.base_price + drift * spec.volatility

    def _bars(
        self, spec: FakeSymbol, minutes: int, first_index: int, last_index: int
    ) -> list[dict[str, float]]:
        if last_index < first_index:
            return []
        bars: list[dict[str, float]] = []
        for index in range(max(first_index, 0), last_index + 1):
            open_price = self._price_at(spec, minutes, index)
            close_price = self._price_at(spec, minutes, index + 1)
            # Le ombre hanno un rumore proprio, scorrelato dal corpo.
            wick = abs(self._walk(f"{spec.name}:wick", index * minutes)) * spec.volatility * 3
            high = max(open_price, close_price) + wick
            low = min(open_price, close_price) - wick
            volume_noise = abs(self._walk(f"{spec.name}:vol", index * minutes))
            bars.append(
                {
                    "time": (
                        ANCHOR
                        + timedelta(minutes=minutes * index, seconds=self.broker_offset_seconds)
                    ).timestamp(),
                    "open": round(open_price, spec.digits),
                    "high": round(high, spec.digits),
                    "low": round(low, spec.digits),
                    "close": round(close_price, spec.digits),
                    "tick_volume": float(round(300 + volume_noise * 4_000)),
                    "spread": float(spec.spread),
                    "real_volume": 0.0,
                }
            )
        return bars

    def _current_price(self, spec: FakeSymbol) -> float:
        """Prezzo corrente: la barra M1 in formazione."""
        return self._price_at(spec, 1, self._bar_index(self._clock.now(), 1))


@functools.lru_cache(maxsize=2_000_000)
def _rumore_frazionario(seme: int, seed: str, index: int, ottave: int) -> float:
    """Somma di ottave di rumore interpolato. Pura, quindi in cache."""
    total = 0.0
    norm = 0.0
    for octave in range(ottave):
        step = 2**octave
        position = index / step
        left = math.floor(position)
        fraction = position - left
        a = _value_noise(f"{seme}:{seed}:{octave}", left)
        b = _value_noise(f"{seme}:{seed}:{octave}", left + 1)
        # Smoothstep: evita gli spigoli dell'interpolazione lineare, che
        # creerebbero falsi "break of structure" a intervalli regolari.
        weight = fraction * fraction * (3 - 2 * fraction)
        amplitude = step**0.5
        total += (a + (b - a) * weight) * amplitude
        norm += amplitude
    return total / norm


@functools.lru_cache(maxsize=2_000_000)
def _value_noise(seed: str, index: int) -> float:
    """Valore pseudo-casuale stabile in [-1, 1] per la coppia (seme, indice).

    Hash e non `random.Random`: deve essere indicizzabile in O(1) e identico fra
    processi e sistemi operativi, cosa che lo stato interno di un generatore
    sequenziale non garantisce.
    """
    # In cache: è una funzione pura, e le ottave alte ripetono gli stessi indici
    # migliaia di volte. Con l'universo globale (37 strumenti x 4 timeframe x 400
    # barre) senza cache un'istantanea finta costava 10-20 secondi.
    digest = hashlib.blake2b(f"{seed}:{index}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**63 - 1.0


@dataclass
class _FakeTick:
    time: float
    bid: float
    ask: float
    last: float
    volume: float


#: Inverso di `MT5_TIMEFRAMES`: il mock riceve la costante numerica, non l'enum.
_MINUTES_BY_CONSTANT: dict[int, int] = {
    1: 1,
    5: 5,
    15: 15,
    30: 30,
    16385: 60,
    16388: 240,
    16408: 1_440,
    32769: 10_080,
    49153: 43_200,
}


def _timeframe_minutes(constant: int) -> int:
    try:
        return _MINUTES_BY_CONSTANT[constant]
    except KeyError as exc:  # pragma: no cover - difeso a monte
        raise ValueError(f"Costante timeframe MT5 non riconosciuta: {constant}") from exc
