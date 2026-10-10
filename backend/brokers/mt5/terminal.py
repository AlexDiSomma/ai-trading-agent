"""Confine con il pacchetto `MetaTrader5`.

`MT5Terminal` elenca **solo** le funzioni che il sistema usa, tutte di lettura.
Il connettore dipende da questo protocollo e non dal modulo globale: da qui
nascono tre proprietà utili.

1. *Sola lettura per costruzione*: `order_send` non compare nel protocollo,
   quindi nessun codice che riceve un `MT5Terminal` può inviare un ordine —
   non per convenzione, ma perché il metodo non esiste.
2. *Testabilità*: il mock implementa lo stesso protocollo, quindi i test
   coprono il connettore vero e non una sua copia semplificata.
3. *Portabilità*: `import MetaTrader5` avviene dentro `RealMT5Terminal`, non a
   livello di modulo. Su Linux, in CI e in Docker l'import del backend riesce
   comunque; fallisce solo se si chiede davvero il terminale reale.

Il pacchetto MetaTrader5 è sincrono, bloccante e non thread-safe: la
serializzazione delle chiamate è responsabilità del connettore
(docs/adr/0003-mt5-gateway-windows.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from backend.core.errors import BrokerError


@runtime_checkable
class MT5Terminal(Protocol):
    """Sottoinsieme di sola lettura dell'API MetaTrader 5."""

    # --- ciclo di vita ---
    def initialize(self, **kwargs: Any) -> bool: ...
    def login(self, login: int, *, password: str, server: str, timeout: int) -> bool: ...
    def shutdown(self) -> None: ...
    def last_error(self) -> tuple[int, str]: ...

    # --- stato ---
    def terminal_info(self) -> Any: ...
    def account_info(self) -> Any: ...

    # --- strumenti ---
    def symbols_get(self, group: str | None = None) -> tuple[Any, ...] | None: ...
    def symbol_info(self, symbol: str) -> Any: ...
    def symbol_info_tick(self, symbol: str) -> Any: ...

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        """Aggiunge il simbolo al Market Watch locale.

        È l'unica chiamata che modifica qualcosa: lo stato della finestra del
        terminale sul PC dell'utente. Non tocca il conto, non invia ordini. Va
        fatta prima di leggere i dati di un simbolo non già visibile, altrimenti
        MT5 restituisce `None` e sembra che il simbolo non esista.
        """
        ...

    # --- storico prezzi ---
    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any: ...
    def copy_rates_from(self, symbol: str, timeframe: int, date_from: Any, count: int) -> Any: ...
    def copy_ticks_from(self, symbol: str, date_from: Any, count: int, flags: int) -> Any: ...

    # --- calcolatori ---
    def order_calc_profit(
        self, action: int, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        """Profitto in **valuta del conto** per un movimento di prezzo.

        Appartiene alla famiglia `order_calc_*`, che sono **calcolatori**: non
        inviano nulla al server, non creano ordini, non modificano niente. Si
        distinguono da `order_send`, `order_modify`, `position_close` (che
        cambiano lo stato del conto) e da `order_check` (che sottopone al server
        una richiesta d'ordine da validare, e resta fuori fino alla fase 11).

        Serve perche' `trade_tick_value` **non e' sempre nella valuta del conto**:
        su questo conto XM in EUR e' in EUR per il forex e in USD per metalli e
        indici. Il valore di un movimento derivato da quel campo e' sbagliato del
        12,5% su metalli e indici (verso dell'errore: FOUNDATION_VALIDATION §5).
        Vedi docs/adr/0012-valore-monetario-dal-broker.md.
        """
        ...

    def order_calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> float | None:
        """Margine richiesto in **valuta del conto**. Calcolatore: non agisce.

        Misurato sul conto XM: **non valida il volume**. Restituisce un margine
        anche per 0.05 lotti su US100Cash, dove il passo minimo e' 0.1. La
        normalizzazione del volume deve quindi avvenire prima, nel servizio di
        valutazione, e non puo' essere delegata al broker.
        """
        ...

    # --- conto ---
    def positions_get(self, *, symbol: str | None = None) -> tuple[Any, ...] | None: ...
    def orders_get(self, *, symbol: str | None = None) -> tuple[Any, ...] | None: ...
    def history_orders_get(self, date_from: Any, date_to: Any) -> tuple[Any, ...] | None: ...
    def history_deals_get(self, date_from: Any, date_to: Any) -> tuple[Any, ...] | None: ...


class RealMT5Terminal:
    """Adattatore sottile sul pacchetto `MetaTrader5`.

    Non contiene logica: traduce solo le firme con argomenti nominati verso
    quelle posizionali del pacchetto. Ogni comportamento sta nel connettore,
    dove è testabile.
    """

    def __init__(self) -> None:
        try:
            import MetaTrader5
        except ImportError as exc:  # pragma: no cover - dipende dalla macchina
            raise BrokerError(
                "Pacchetto MetaTrader5 non disponibile. Gira solo su Windows: "
                "installalo con `pip install -e .[mt5]` oppure imposta "
                "MT5_USE_MOCK=true per lavorare con il terminale finto.",
                platform_hint="windows-only",
            ) from exc
        self._mt5 = MetaTrader5

    # --- ciclo di vita ---
    def initialize(self, **kwargs: Any) -> bool:
        path = kwargs.pop("path", None)
        if path is not None:
            return bool(self._mt5.initialize(str(Path(path)), **kwargs))
        return bool(self._mt5.initialize(**kwargs))

    def login(self, login: int, *, password: str, server: str, timeout: int) -> bool:
        return bool(self._mt5.login(login, password=password, server=server, timeout=timeout))

    def shutdown(self) -> None:
        self._mt5.shutdown()

    def last_error(self) -> tuple[int, str]:
        code, description = self._mt5.last_error()
        return int(code), str(description)

    # --- stato ---
    def terminal_info(self) -> Any:
        return self._mt5.terminal_info()

    def account_info(self) -> Any:
        return self._mt5.account_info()

    # --- strumenti ---
    def symbols_get(self, group: str | None = None) -> tuple[Any, ...] | None:
        return self._mt5.symbols_get(group) if group else self._mt5.symbols_get()

    def symbol_info(self, symbol: str) -> Any:
        return self._mt5.symbol_info(symbol)

    def symbol_info_tick(self, symbol: str) -> Any:
        return self._mt5.symbol_info_tick(symbol)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return bool(self._mt5.symbol_select(symbol, enable))

    # --- storico prezzi ---
    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any:
        return self._mt5.copy_rates_from_pos(symbol, timeframe, start, count)

    def copy_rates_from(self, symbol: str, timeframe: int, date_from: Any, count: int) -> Any:
        return self._mt5.copy_rates_from(symbol, timeframe, date_from, count)

    def copy_ticks_from(self, symbol: str, date_from: Any, count: int, flags: int) -> Any:
        return self._mt5.copy_ticks_from(symbol, date_from, count, flags)

    # --- calcolatori ---
    def order_calc_profit(
        self, action: int, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        risultato = self._mt5.order_calc_profit(action, symbol, volume, price_open, price_close)
        return float(risultato) if risultato is not None else None

    def order_calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> float | None:
        risultato = self._mt5.order_calc_margin(action, symbol, volume, price)
        return float(risultato) if risultato is not None else None

    # --- conto ---
    def positions_get(self, *, symbol: str | None = None) -> tuple[Any, ...] | None:
        return self._mt5.positions_get(symbol=symbol) if symbol else self._mt5.positions_get()

    def orders_get(self, *, symbol: str | None = None) -> tuple[Any, ...] | None:
        return self._mt5.orders_get(symbol=symbol) if symbol else self._mt5.orders_get()

    def history_orders_get(self, date_from: Any, date_to: Any) -> tuple[Any, ...] | None:
        return self._mt5.history_orders_get(date_from, date_to)

    def history_deals_get(self, date_from: Any, date_to: Any) -> tuple[Any, ...] | None:
        return self._mt5.history_deals_get(date_from, date_to)
