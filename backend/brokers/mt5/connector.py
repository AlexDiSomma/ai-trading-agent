"""Connettore MetaTrader 5 — **sola lettura**.

Questo oggetto soddisfa `MarketDataReader` e `AccountDataReader`. Non soddisfa
`OrderWriter` e non contiene alcuna chiamata di invio ordini: fino alla fase 11
l'esecuzione non esiste come codice, non come flag spento.

Tre problemi del pacchetto MetaTrader5 e come sono affrontati qui.

**1. È sincrono e bloccante.** Una `copy_rates_from_pos` su molte barre può
costare centinaia di millisecondi. Dentro l'event loop di FastAPI bloccherebbe
tutte le altre richieste, inclusa la lettura delle posizioni aperte. Ogni
chiamata passa quindi per un executor.

**2. Non è thread-safe e tiene una sola connessione per processo.** L'executor
ha un unico thread e un `asyncio.Lock` serializza gli accessi: due worker che
interrogano MT5 insieme otterrebbero risposte mescolate o un crash del
terminale. Il costo è che le letture MT5 sono sequenziali — accettabile, perché
i timeframe operativi sono minuti, non microsecondi.

**3. Segnala gli errori restituendo `None`.** Nessun'eccezione: un simbolo
inesistente e un terminale disconnesso sono indistinguibili se non si
interroga `last_error()`. Qui ogni `None` diventa un'eccezione tipizzata, così
non può essere confuso con "nessun dato".

In più il connettore verifica che il conto a cui il terminale è collegato sia
quello configurato: un terminale lasciato aperto su un altro conto è un modo
realistico di operare per sbaglio sul conto sbagliato.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any, TypeVar

from backend.brokers.base import BrokerHealth
from backend.brokers.mt5.mapping import (
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    to_account_snapshot,
    to_candles,
    to_deal_snapshot,
    to_mt5_timeframe,
    to_pending_order_snapshot,
    to_position_snapshot,
    to_symbol_info,
    to_tick,
)
from backend.brokers.mt5.terminal import MT5Terminal
from backend.config.settings import MT5Settings
from backend.core.clock import Clock, SystemClock
from backend.core.enums import HealthStatus, Timeframe
from backend.core.errors import (
    BrokerError,
    BrokerNotConnectedError,
    BrokerTimeoutError,
    StaleDataError,
    SymbolNotFoundError,
)
from backend.core.logging import get_logger
from backend.schemas.account import (
    AccountSnapshot,
    DealSnapshot,
    PendingOrderSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)
from backend.schemas.market import CandleSeries, SymbolInfo, Tick

T = TypeVar("T")

logger = get_logger(__name__)

#: Barre massime per richiesta. Oltre questa soglia la chiamata diventa lenta e
#: quasi sempre significa che qualcuno sta usando il connettore per fare un
#: backtest: lo storico va scaricato dal modulo market_data, non da qui.
MAX_CANDLES_PER_REQUEST = 5_000


class MT5Connector:
    """Accesso in sola lettura al terminale MetaTrader 5."""

    def __init__(
        self,
        settings: MT5Settings,
        terminal: MT5Terminal,
        *,
        clock: Clock | None = None,
        simulated: bool = False,
        reference_symbol: str = "EURUSD",
    ) -> None:
        self._settings = settings
        self._terminal = terminal
        self._clock = clock or SystemClock()
        self._simulated = simulated
        self._reference_symbol = reference_symbol
        # Un solo thread: vedi il punto 2 nel docstring del modulo.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
        self._lock = asyncio.Lock()
        self._connected = False
        self._selected: set[str] = set()
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    # Identità
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "MetaTrader5 (simulato)" if self._simulated else "MetaTrader5"

    @property
    def is_simulated(self) -> bool:
        return self._simulated

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Ciclo di vita
    # ------------------------------------------------------------------
    async def connect(self) -> BrokerHealth:
        """Inizializza il terminale e verifica che il conto sia quello giusto."""
        kwargs: dict[str, Any] = {"timeout": self._settings.timeout_ms}
        if self._settings.terminal_path is not None:
            kwargs["path"] = self._settings.terminal_path
        if self._settings.portable:
            kwargs["portable"] = True
        if self._settings.login is not None and self._settings.password is not None:
            kwargs["login"] = self._settings.login
            kwargs["password"] = self._settings.password.get_secret_value()
            kwargs["server"] = self._settings.server

        initialized = await self._call("initialize", self._terminal.initialize, **kwargs)
        if not initialized:
            raise BrokerNotConnectedError(
                "Inizializzazione di MetaTrader 5 fallita. Controlla che il terminale "
                "sia installato, aperto e collegato al conto.",
                **await self._error_context(),
            )

        account = await self.get_account()

        # Fail closed sul conto sbagliato: un terminale aperto su un altro login
        # è il modo più semplice di operare per sbaglio dove non si deve.
        expected = self._settings.login
        if expected is not None and account.login != expected:
            await self.disconnect()
            raise BrokerError(
                "Il terminale è collegato a un conto diverso da quello configurato: "
                "connessione rifiutata per sicurezza.",
                expected_login=expected,
                actual_login=account.login,
            )

        self._connected = True
        logger.info(
            "mt5.connesso",
            login=account.login,
            server=account.server,
            currency=account.currency,
            leverage=account.leverage,
            simulated=self._simulated,
            # Vale la pena averlo nei log: su un conto reale ogni riga successiva
            # va letta con un'attenzione diversa.
            tipo_conto=account.account_type,
        )
        return await self.health()

    async def disconnect(self) -> None:
        if self._connected or not self._simulated:
            try:
                await self._call("shutdown", self._terminal.shutdown)
            except BrokerError:  # pragma: no cover - chiusura best effort
                logger.warning("mt5.shutdown_fallito", exc_info=True)
        self._connected = False
        self._selected.clear()
        self._executor.shutdown(wait=False)
        logger.info("mt5.disconnesso")

    # ------------------------------------------------------------------
    # Stato
    # ------------------------------------------------------------------
    async def health(self) -> BrokerHealth:
        """Stato della connessione, senza mai sollevare eccezioni.

        L'health check deve poter *raccontare* un guasto: se alzasse
        un'eccezione la pagina System Health andrebbe in errore proprio quando
        serve a spiegare cosa è rotto.
        """
        started = perf_counter()
        try:
            info = await self._call("terminal_info", self._terminal.terminal_info)
            if info is None:
                return BrokerHealth(
                    status=HealthStatus.UNHEALTHY,
                    detail="Terminale MT5 non raggiungibile (terminal_info ha restituito None).",
                    connected=False,
                    metadata={"simulated": self._simulated},
                )
            account = await self.get_account()
            latency_ms = (perf_counter() - started) * 1000

            connected = bool(getattr(info, "connected", False))
            if not connected:
                return BrokerHealth(
                    status=HealthStatus.UNHEALTHY,
                    detail="Il terminale MT5 è aperto ma non collegato al server del broker.",
                    connected=False,
                    latency_ms=latency_ms,
                    terminal_build=int(getattr(info, "build", 0) or 0) or None,
                )

            detail = f"Connesso al conto {account.login} su {account.server}."
            status = HealthStatus.HEALTHY
            if not account.trade_allowed:
                status = HealthStatus.DEGRADED
                detail += " Il broker non autorizza operazioni in questo momento."
            if self._simulated:
                status = HealthStatus.DEGRADED
                detail = "Terminale simulato: i prezzi NON sono quelli del broker."

            server_time = await self._safe_server_time()
            return BrokerHealth(
                status=status,
                detail=detail,
                connected=True,
                latency_ms=round(latency_ms, 2),
                server_time=server_time,
                terminal_build=int(getattr(info, "build", 0) or 0) or None,
                metadata={
                    "login": account.login,
                    "server": account.server,
                    "currency": account.currency,
                    "tipo_conto": account.account_type,
                    "simulated": self._simulated,
                    "equity": account.equity,
                },
            )
        except BrokerError as exc:
            return BrokerHealth(
                status=HealthStatus.UNHEALTHY,
                detail=str(exc),
                connected=False,
                metadata={"simulated": self._simulated},
            )

    async def server_time(self) -> datetime:
        """Ora del server del broker, dedotta dall'ultimo tick.

        MT5 non espone l'ora del server come valore a sé: l'unica fonte è il
        timestamp di un tick. Si usa un simbolo di riferimento molto liquido,
        altrimenti su uno strumento fermo si leggerebbe un'ora vecchia.
        """
        tick = await self.get_tick(self._reference_symbol)
        return tick.time

    async def _safe_server_time(self) -> datetime | None:
        try:
            return await self.server_time()
        except BrokerError:
            return None

    # ------------------------------------------------------------------
    # Dati di mercato
    # ------------------------------------------------------------------
    async def list_symbols(self, pattern: str | None = None) -> tuple[str, ...]:
        raw = await self._call("symbols_get", self._terminal.symbols_get, pattern)
        if raw is None:
            raise BrokerError("Elenco simboli non disponibile.", **await self._error_context())
        return tuple(str(item.name) for item in raw)

    async def get_symbol(self, symbol: str) -> SymbolInfo:
        """Specifiche di contratto del simbolo, rilette dal broker."""
        await self._ensure_selected(symbol)
        raw = await self._call("symbol_info", self._terminal.symbol_info, symbol)
        if raw is None:
            raise SymbolNotFoundError(
                f"Simbolo '{symbol}' non disponibile su questo conto.",
                symbol=symbol,
                **await self._error_context(),
            )
        return to_symbol_info(raw, as_of=self._clock.now())

    async def get_tick(self, symbol: str) -> Tick:
        """Ultima quotazione, senza alcun giudizio di freschezza.

        Il timestamp e' in **ora del broker etichettata UTC**: confrontarlo con
        l'ora di sistema da' un'eta' sbagliata di qualche ora (ADR 0015). La
        freschezza si giudica solo in `market_data.quote_quality`, con lo scarto
        dell'ora broker. Qui prima c'era un `require_fresh` che faceva proprio
        quel confronto sbagliato: e' stato rimosso.
        """
        await self._ensure_selected(symbol)
        raw = await self._call("symbol_info_tick", self._terminal.symbol_info_tick, symbol)
        if raw is None:
            raise SymbolNotFoundError(
                f"Nessuna quotazione per '{symbol}'.",
                symbol=symbol,
                **await self._error_context(),
            )
        # MT5 restituisce bid/ask a zero quando per quel simbolo non e' ancora
        # arrivata nessuna quotazione: mercato chiuso, simbolo appena aggiunto
        # al Market Watch, o strumento non quotato su questo conto. Non e' un
        # dato corrotto, e' assenza di dato: va detto, non fatto passare per un
        # prezzo di zero.
        bid = float(getattr(raw, "bid", 0.0) or 0.0)
        ask = float(getattr(raw, "ask", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            raise StaleDataError(
                f"Nessuna quotazione disponibile per {symbol}: mercato chiuso, "
                "simbolo appena attivato oppure non quotato su questo conto.",
                symbol=symbol,
            )

        return to_tick(raw, symbol=symbol)

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        *,
        end: datetime | None = None,
    ) -> CandleSeries:
        """Serie storica di candele.

        Quando `end` non è indicato si chiedono `count + 1` barre a partire
        dalla posizione 0: la barra 0 è quella **in formazione**. Viene
        restituita (serve per il prezzo corrente) ma la serie dichiara
        `last_candle_closed=False`, e `CandleSeries.closed` la esclude dai
        calcoli. È la differenza fra un backtest onesto e uno che "indovina" la
        chiusura della candela su cui sta operando.
        """
        if count < 1:
            raise ValueError("count deve essere almeno 1")
        if count > MAX_CANDLES_PER_REQUEST:
            raise ValueError(
                f"Richieste {count} barre: il massimo per chiamata è "
                f"{MAX_CANDLES_PER_REQUEST}. Per lo storico lungo usa market_data."
            )

        await self._ensure_selected(symbol)
        mt5_timeframe = to_mt5_timeframe(timeframe)

        if end is None:
            raw = await self._call(
                "copy_rates_from_pos",
                self._terminal.copy_rates_from_pos,
                symbol,
                mt5_timeframe,
                0,
                count + 1,
            )
            last_closed = False
        else:
            raw = await self._call(
                "copy_rates_from",
                self._terminal.copy_rates_from,
                symbol,
                mt5_timeframe,
                end,
                count,
            )
            last_closed = True

        if raw is None or len(raw) == 0:
            raise BrokerError(
                f"Nessuna candela {timeframe} per '{symbol}'. "
                "Il simbolo potrebbe non avere storico su questo timeframe.",
                symbol=symbol,
                timeframe=str(timeframe),
                **await self._error_context(),
            )

        candles = to_candles(raw)
        if end is not None and candles:
            # Con `end` esplicito l'ultima barra è chiusa solo se il suo
            # intervallo è interamente passato rispetto a `end`.
            bar_end = candles[-1].time + timedelta(minutes=timeframe.minutes)
            last_closed = bar_end <= end

        return CandleSeries(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            as_of=self._clock.now(),
            source="mt5-mock" if self._simulated else "mt5",
            last_candle_closed=last_closed,
        )

    # ------------------------------------------------------------------
    # Conto
    # ------------------------------------------------------------------
    async def get_account(self) -> AccountSnapshot:
        raw = await self._call("account_info", self._terminal.account_info)
        if raw is None:
            raise BrokerNotConnectedError(
                "Informazioni sul conto non disponibili: il terminale non è collegato.",
                **await self._error_context(),
            )
        return to_account_snapshot(raw, as_of=self._clock.now())

    async def get_positions(self, symbol: str | None = None) -> tuple[PositionSnapshot, ...]:
        raw = await self._call("positions_get", self._terminal.positions_get, symbol=symbol)
        # MT5 restituisce `None` sia per "nessuna posizione" sia per errore:
        # l'unico modo di distinguerli è leggere il codice d'errore.
        if raw is None:
            code, description = await self._raw_last_error()
            if code != 0:
                raise BrokerError(
                    "Lettura delle posizioni aperte fallita.",
                    mt5_error_code=code,
                    mt5_error=description,
                )
            return ()
        as_of = self._clock.now()
        return tuple(to_position_snapshot(item, as_of=as_of) for item in raw)

    async def get_pending_orders(
        self, symbol: str | None = None
    ) -> tuple[PendingOrderSnapshot, ...]:
        raw = await self._call("orders_get", self._terminal.orders_get, symbol=symbol)
        if raw is None:
            code, description = await self._raw_last_error()
            if code != 0:
                raise BrokerError(
                    "Lettura degli ordini pendenti fallita.",
                    mt5_error_code=code,
                    mt5_error=description,
                )
            return ()
        as_of = self._clock.now()
        return tuple(to_pending_order_snapshot(item, as_of=as_of) for item in raw)

    async def get_portfolio(self) -> PortfolioSnapshot:
        """Conto, posizioni e ordini letti nello stesso giro.

        Le tre letture sono comunque serializzate dal lock del connettore:
        chiederle insieme garantisce che il rischio sia calcolato su una
        fotografia coerente e non su tre istanti diversi.
        """
        account = await self.get_account()
        positions = await self.get_positions()
        orders = await self.get_pending_orders()
        return PortfolioSnapshot(
            account=account,
            positions=positions,
            pending_orders=orders,
            as_of=self._clock.now(),
        )

    # ------------------------------------------------------------------
    # Calcolatori del broker (nessun ordine, nessuna modifica)
    # ------------------------------------------------------------------
    async def calculate_profit(
        self,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
        *,
        sell: bool = False,
    ) -> float:
        """Profitto in **valuta del conto**, calcolato dal broker.

        `order_calc_profit` e' un calcolatore: non crea ordini, non invia nulla al
        server, non cambia niente sul conto. Sta qui perche' e' l'unica fonte
        affidabile del valore monetario di un movimento di prezzo: su questo conto
        XM in EUR, `trade_tick_value` e' in EUR per il forex ma in USD per metalli
        e indici: i numeri derivati da quel campo sono sbagliati del 12,5%
        (docs/adr/0012-valore-monetario-dal-broker.md, verso dell'errore in
        docs/FOUNDATION_VALIDATION.md §5).
        """
        await self._ensure_selected(symbol)
        risultato = await self._call(
            "order_calc_profit",
            self._terminal.order_calc_profit,
            ORDER_TYPE_SELL if sell else ORDER_TYPE_BUY,
            symbol,
            volume,
            price_open,
            price_close,
        )
        if risultato is None:
            raise BrokerError(
                f"Il broker non ha calcolato il profitto per {symbol}: "
                "senza questo valore la dimensione della posizione non e' calcolabile.",
                symbol=symbol,
                **await self._error_context(),
            )
        return float(risultato)

    async def calculate_margin(
        self, symbol: str, volume: float, price: float, *, sell: bool = False
    ) -> float:
        """Margine richiesto in valuta del conto, calcolato dal broker.

        Chiamata grezza: nessuna formula qui. Il volume deve arrivare gia'
        normalizzato, perche' il broker **non lo valida** (misurato: 0.05 lotti su
        US100Cash, passo 0.1, restituisce comunque 1.37). Le formule e i controlli
        stanno in `valuation/service.py`.
        """
        await self._ensure_selected(symbol)
        risultato = await self._call(
            "order_calc_margin",
            self._terminal.order_calc_margin,
            ORDER_TYPE_SELL if sell else ORDER_TYPE_BUY,
            symbol,
            volume,
            price,
        )
        if risultato is None:
            raise BrokerError(
                f"Il broker non ha calcolato il margine per {symbol}.",
                symbol=symbol,
                **await self._error_context(),
            )
        return float(risultato)

    async def get_deals(self, date_from: datetime, date_to: datetime) -> tuple[DealSnapshot, ...]:
        """Operazioni chiuse nell'intervallo.

        Serve al budget di rischio giornaliero: il PnL realizzato va ricostruito
        dallo storico del broker a ogni avvio, non accumulato in memoria. Dopo un
        riavvio un contatore in RAM ripartirebbe da zero e il limite giornaliero
        dell'utente sarebbe silenziosamente raddoppiato.
        """
        raw = await self._call(
            "history_deals_get", self._terminal.history_deals_get, date_from, date_to
        )
        if raw is None:
            code, description = await self._raw_last_error()
            if code != 0:
                raise BrokerError(
                    "Lettura dello storico operazioni fallita.",
                    mt5_error_code=code,
                    mt5_error=description,
                )
            return ()
        return tuple(to_deal_snapshot(item) for item in raw)

    # ------------------------------------------------------------------
    # Infrastruttura interna
    # ------------------------------------------------------------------
    async def _ensure_selected(self, symbol: str) -> None:
        """Aggiunge il simbolo al Market Watch, una volta per sessione.

        Senza questo passaggio MT5 restituisce `None` per i simboli non visibili
        nel terminale e sembra che non esistano sul conto.
        """
        if symbol in self._selected:
            return
        ok = await self._call("symbol_select", self._terminal.symbol_select, symbol, True)
        if not ok:
            # Quasi sempre significa che quel nome non esiste su questo conto.
            # I nomi variano fra broker e fra tipi di conto: su XM l'oro si
            # chiama GOLD e gli indici US30Cash, non XAUUSD e US30.
            raise SymbolNotFoundError(
                f"Il simbolo '{symbol}' non è disponibile su questo conto: "
                "controlla la grafia esatta usata dal broker nell'elenco dei "
                "simboli disponibili.",
                symbol=symbol,
                **await self._error_context(),
            )
        self._selected.add(symbol)

    async def _call(self, label: str, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Esegue una chiamata MT5 nel thread dedicato, una alla volta.

        Il timeout è il doppio di quello configurato per il terminale: serve a
        non far restare appeso per sempre il chiamante se MT5 non risponde.
        Non interrompe il thread — il pacchetto non è interrompibile — ma
        restituisce il controllo, e il circuit breaker può intervenire.
        """
        loop = asyncio.get_running_loop()
        timeout = self._settings.timeout_ms / 1000 * 2
        bound = functools.partial(func, *args, **kwargs)

        async with self._lock:
            started = perf_counter()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, bound), timeout=timeout
                )
            except TimeoutError as exc:
                self._connected = False
                raise BrokerTimeoutError(
                    f"MT5 non ha risposto entro {timeout:.0f}s alla chiamata '{label}'.",
                    call=label,
                ) from exc
            except Exception as exc:  # il pacchetto può alzare qualunque cosa
                raise BrokerError(
                    f"Errore nella chiamata MT5 '{label}': {exc}", call=label
                ) from exc
            elapsed_ms = (perf_counter() - started) * 1000

        if elapsed_ms > 1_000:
            logger.warning("mt5.chiamata_lenta", call=label, elapsed_ms=round(elapsed_ms, 1))
        else:
            logger.debug("mt5.chiamata", call=label, elapsed_ms=round(elapsed_ms, 1))
        return result

    async def run_serialized(
        self, label: str, func: Callable[..., T], *args: Any, **kwargs: Any
    ) -> T:
        """Esegue `func` nel thread MT5 del connettore, in coda alle letture.

        Il pacchetto MT5 non è thread-safe: chi deve parlare col terminale fuori da
        questo modulo (l'esecuzione della fase 11, `execution/mt5_terminal.py`)
        passa da qui, con lo stesso lock e lo stesso timeout. Il connettore resta
        privo di qualunque funzione di scrittura (test di sola lettura).
        """
        return await self._call(label, func, *args, **kwargs)

    async def last_error(self) -> tuple[int, str]:
        return await self._raw_last_error()

    async def _raw_last_error(self) -> tuple[int, str]:
        try:
            code, description = await self._call("last_error", self._terminal.last_error)
            return int(code), str(description)
        except BrokerError:  # pragma: no cover - errore nel leggere l'errore
            return -1, "last_error non disponibile"

    async def _error_context(self) -> dict[str, Any]:
        code, description = await self._raw_last_error()
        self._last_error = f"[{code}] {description}"
        return {"mt5_error_code": code, "mt5_error": description}
