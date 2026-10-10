"""Gateway degli ordini reali. Ogni apertura passa da qui, e solo con una `RiskApproval`.

Ordine dei controlli prima di qualunque invio (fail closed, nell'ordine):

1. **autorizzazione LIVE** (`LiveReadiness`): senza tutti i gate, nessun invio;
2. **kill switch globale** (solo aperture: chiusure e protezioni restano possibili);
3. **circuit breaker** (solo aperture);
4. **persistenza durevole sana**: non si invia ciò che non si può registrare;
5. **nessun ordine in stato ignoto**: prima si riconcilia;
6. approvazione valida (emittente, scadenza, SL/TP lato giusto e oltre lo stops level).

Idempotenza: la chiave dell'approvazione è registrata (`UNIQUE`) **prima** dell'invio;
una chiave già presente restituisce il record esistente e non invia nulla.

Esiti (`retcodes.classe`):
* `DONE` / `PARTIAL` — si registra volume e prezzo reali, poi si verifica che la
  posizione abbia SL e TP **lato server**; se mancano si impostano, e se non ci si
  riesce la posizione si chiude per protezione e il breaker scatta;
* `RETRY` (requote, prezzo cambiato) — nuovo prezzo, solo entro lo slittamento
  approvato e la validità dell'approvazione, al massimo `MAX_TENTATIVI`;
* `REJECTED` — si registra, conta per il breaker;
* `UNKNOWN` (timeout, connessione, nessuna risposta) — **mai** reinviare: si
  riconcilia con posizioni e storico. Se il broker non risponde nemmeno lì, l'ordine
  resta `UNKNOWN`, il breaker scatta e nessun'altra apertura parte.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from backend.core.clock import Clock
from backend.core.errors import (
    BrokerError,
    KillSwitchEngagedError,
    PersistenceUnavailableError,
    TradingAgentError,
)
from backend.core.logging import get_logger
from backend.execution import retcodes
from backend.execution.breaker import CircuitBreaker
from backend.execution.models import (
    MAGIC,
    LiveOrder,
    RiskApproval,
    chiave_idempotenza,
    commento,
)
from backend.execution.mt5_terminal import (
    ORDER_TIME_GTC,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_SLTP,
    RispostaOrdine,
    TradingTerminal,
)
from backend.execution.store import ExecutionStore, IdempotencyConflictError
from backend.schemas.account import PositionSnapshot

logger = get_logger(__name__)

MAX_TENTATIVI = 3
STATI_IN_VOLO = frozenset({"SENT", "UNKNOWN"})


class LiveTradingDisabledError(TradingAgentError):
    """Il LIVE non è autorizzato: mancano uno o più gate."""


class CircuitBreakerOpenError(TradingAgentError):
    """Il circuit breaker è aperto: nessuna nuova apertura."""


class ExecutionStateUnknownError(TradingAgentError):
    """Esistono ordini con esito ignoto: prima vanno riconciliati."""


class ApprovalInvalidError(TradingAgentError):
    """Approvazione assente, scaduta, non emessa dal Risk Engine o incoerente."""


Autorizzazione = Callable[[], Awaitable[tuple[bool, list[str]]]]


class LiveOrderGateway:
    def __init__(
        self,
        *,
        terminal: TradingTerminal,
        store: ExecutionStore,
        breaker: CircuitBreaker,
        kill_switch: Callable[[], bool],
        autorizzazione: Autorizzazione,
        engine_id: str,
        clock: Clock,
    ) -> None:
        self._t = terminal
        self._store = store
        self.breaker = breaker
        self._kill = kill_switch
        self._autorizza = autorizzazione
        self._engine_id = engine_id
        self._clock = clock

    # ------------------------------------------------------------------
    # Controlli preliminari
    # ------------------------------------------------------------------
    async def _preliminari(self, *, apertura: bool) -> None:
        ok, motivi = await self._autorizza()
        if not ok:
            raise LiveTradingDisabledError("LIVE non autorizzato: " + "; ".join(motivi))
        if apertura and self._kill():
            raise KillSwitchEngagedError("Kill switch globale attivo: nessuna apertura.")
        if apertura and self.breaker.aperto:
            raise CircuitBreakerOpenError(f"Circuit breaker aperto: {self.breaker.stato['reason']}")
        sano, dettaglio = await self._store.healthy()
        if not sano or not self._store.durable:
            raise PersistenceUnavailableError(f"Archivio ordini non affidabile: {dettaglio}")
        if apertura and await self._store.lista(STATI_IN_VOLO):
            raise ExecutionStateUnknownError(
                "Ordini con esito ignoto: riconciliare prima di aprire."
            )

    def _verifica_approvazione(self, a: RiskApproval) -> None:
        motivi: list[str] = []
        if a.engine_id != self._engine_id:
            motivi.append("approvazione non emessa da questo Risk Engine")
        if self._clock.now() >= a.expires_at:
            motivi.append("approvazione scaduta")
        s = 1 if a.direction == "LONG" else -1
        if s * (a.entry_reference - a.stop_loss) <= 0:
            motivi.append("stop dalla parte sbagliata")
        if s * (a.take_profit - a.entry_reference) <= 0:
            motivi.append("target dalla parte sbagliata")
        if motivi:
            raise ApprovalInvalidError("Approvazione non valida: " + "; ".join(motivi))

    async def _salva(self, o: LiveOrder, evento: str, **dati: object) -> None:
        o.evento(self._clock.now(), evento, **dati)
        await self._store.update(o)

    # ------------------------------------------------------------------
    # Apertura
    # ------------------------------------------------------------------
    async def apri(self, a: RiskApproval, *, plan: dict[str, object] | None = None) -> LiveOrder:
        self._verifica_approvazione(a)
        await self._preliminari(apertura=True)
        esistente = await self._store.get(a.idempotency_key)
        if esistente is not None:
            logger.warning("live.idempotenza", chiave=a.idempotency_key, stato=esistente.state)
            return esistente
        adesso = self._clock.now()
        o = LiveOrder(
            idempotency_key=a.idempotency_key,
            purpose="OPEN",
            canonical=a.canonical,
            broker_symbol=a.broker_symbol,
            direction=a.direction,
            volume_requested=a.volume,
            stop_loss=a.stop_loss,
            take_profit=a.take_profit,
            approval_id=a.approval_id,
            plan=plan,
            created_at=adesso,
            updated_at=adesso,
        )
        o.evento(adesso, "CREATED", risk=a.risk_amount)
        try:
            await self._store.insert(o)
        except IdempotencyConflictError:
            return await self._store.get(a.idempotency_key)  # type: ignore[return-value]

        info = await self._t.symbol(a.broker_symbol)
        minimo = info.stops_level_points * info.point
        for livello in (a.stop_loss, a.take_profit):
            if abs(a.entry_reference - livello) < minimo:
                o.state = "REJECTED"
                await self._salva(o, "REJECTED", reason="SL/TP entro lo stops level del broker")
                return o
        riempimento = await self._t.filling(a.broker_symbol)
        tipo = ORDER_TYPE_BUY if a.direction == "LONG" else ORDER_TYPE_SELL

        for tentativo in range(1, MAX_TENTATIVI + 1):
            if self._clock.now() >= a.expires_at:
                o.state = "REJECTED"
                await self._salva(o, "REJECTED", reason="approvazione scaduta durante i tentativi")
                return o
            tick = await self._t.tick(a.broker_symbol)
            prezzo = tick.ask if a.direction == "LONG" else tick.bid
            slittamento = abs(prezzo - a.entry_reference) / info.point
            if slittamento > a.max_slippage_points:
                o.state = "REJECTED"
                await self._salva(
                    o,
                    "REJECTED",
                    reason=f"prezzo {prezzo} a {slittamento:.0f} punti dal riferimento",
                )
                self.breaker.fallimento("slittamento oltre il consentito")
                return o
            richiesta: dict[str, Any] = {
                "action": TRADE_ACTION_DEAL,
                "symbol": a.broker_symbol,
                "volume": a.volume,
                "type": tipo,
                "price": prezzo,
                "sl": a.stop_loss,
                "tp": a.take_profit,
                "deviation": int(a.max_slippage_points),
                "magic": MAGIC,
                "comment": commento(a.idempotency_key),
                "type_time": ORDER_TIME_GTC,
                "type_filling": riempimento,
            }
            controllo = await self._t.order_check(richiesta)
            if controllo.retcode not in (retcodes.CHECK_OK, retcodes.DONE):
                o.state, o.last_retcode = "REJECTED", controllo.retcode
                await self._salva(
                    o,
                    "CHECK_FAILED",
                    retcode=retcodes.descrivi(controllo.retcode),
                    comment=controllo.comment,
                )
                self.breaker.fallimento(f"order_check {retcodes.descrivi(controllo.retcode)}")
                return o

            # Registrato come SENT *prima* di inviare: se il processo muore qui, al
            # riavvio la riconciliazione sa che un ordine potrebbe essere partito.
            o.state, o.attempts = "SENT", tentativo
            await self._salva(o, "SENT", price=prezzo, attempt=tentativo)
            try:
                risposta = await self._t.order_send(richiesta)
            except BrokerError as e:
                o.state = "UNKNOWN"
                await self._salva(o, "UNKNOWN", error=str(e)[:300])
                return await self.riconcilia(o)

            classe = retcodes.classe(risposta.retcode)
            o.last_retcode = risposta.retcode
            if classe in ("DONE", "PARTIAL"):
                return await self._eseguito(o, risposta, classe)
            if classe == "UNKNOWN":
                o.state = "UNKNOWN"
                await self._salva(o, "UNKNOWN", retcode=retcodes.descrivi(risposta.retcode))
                return await self.riconcilia(o)
            if classe == "RETRY" and tentativo < MAX_TENTATIVI:
                o.state = "CREATED"
                await self._salva(o, "RETRY", retcode=retcodes.descrivi(risposta.retcode))
                continue
            o.state = "REJECTED"
            await self._salva(
                o, "REJECTED", retcode=retcodes.descrivi(risposta.retcode), comment=risposta.comment
            )
            self.breaker.fallimento(f"order_send {retcodes.descrivi(risposta.retcode)}")
            return o
        return o  # pragma: no cover - il ciclo esce sempre con un return

    async def _eseguito(self, o: LiveOrder, r: RispostaOrdine, classe: str) -> LiveOrder:
        o.volume_filled = r.volume or o.volume_requested
        o.fill_price, o.deal_ticket, o.order_ticket = r.price, r.deal, r.order
        o.state = (
            "PARTIALLY_FILLED"
            if classe == "PARTIAL" or o.volume_filled < o.volume_requested
            else "FILLED"
        )
        posizione = await self._posizione_di(o)
        if posizione is not None:
            o.position_ticket = posizione.ticket
            o.volume_filled = posizione.volume
        await self._salva(
            o, o.state, retcode=retcodes.descrivi(r.retcode), price=r.price, volume=o.volume_filled
        )
        self.breaker.successo()
        if o.purpose == "OPEN":
            await self.proteggi(o, posizione)
        return o

    async def _posizione_di(self, o: LiveOrder) -> PositionSnapshot | None:
        for p in await self._t.positions(o.broker_symbol):
            if p.magic == MAGIC and p.comment == commento(o.idempotency_key):
                return p
            if o.order_ticket and p.ticket == o.order_ticket:
                return p
        return None

    # ------------------------------------------------------------------
    # SL/TP lato server obbligatori
    # ------------------------------------------------------------------
    async def proteggi(self, o: LiveOrder, posizione: PositionSnapshot | None) -> None:
        if posizione is None:
            posizione = await self._posizione_di(o)
        if posizione is None:
            return
        if posizione.stop_loss and posizione.take_profit:
            return
        richiesta = {
            "action": TRADE_ACTION_SLTP,
            "symbol": o.broker_symbol,
            "position": posizione.ticket,
            "sl": o.stop_loss,
            "tp": o.take_profit,
            "magic": MAGIC,
        }
        try:
            r = await self._t.order_send(richiesta)
            if retcodes.classe(r.retcode) == "DONE":
                await self._salva(o, "SLTP_SET")
                return
            motivo = retcodes.descrivi(r.retcode)
        except BrokerError as e:
            motivo = str(e)[:200]
        await self._salva(o, "SLTP_FAILED", reason=motivo)
        self.breaker.scatta(
            f"Posizione {posizione.ticket} senza SL/TP lato server: chiusa per protezione."
        )
        await self.chiudi(o, "PROTECT")

    # ------------------------------------------------------------------
    # Chiusura (consentita anche con kill switch o breaker aperti)
    # ------------------------------------------------------------------
    async def chiudi(self, aperto: LiveOrder, motivo: str) -> LiveOrder:
        await self._preliminari(apertura=False)
        posizione = None
        if aperto.position_ticket is not None:
            posizione = next(
                (
                    p
                    for p in await self._t.positions(aperto.broker_symbol)
                    if p.ticket == aperto.position_ticket
                ),
                None,
            )
        if posizione is None:
            aperto.state, aperto.close_reason = "CLOSED", aperto.close_reason or "ALREADY_CLOSED"
            await self._salva(aperto, "CLOSED", reason="posizione già assente al broker")
            return aperto
        chiave = chiave_idempotenza("CLOSE", f"{posizione.ticket}:{posizione.volume}")
        esistente = await self._store.get(chiave)
        if esistente is not None and esistente.state not in ("REJECTED", "NOT_EXECUTED"):
            return esistente
        adesso = self._clock.now()
        c = LiveOrder(
            idempotency_key=chiave if esistente is None else f"{chiave}r{adesso:%H%M%S}"[:32],
            purpose="CLOSE",
            canonical=aperto.canonical,
            broker_symbol=aperto.broker_symbol,
            direction="SHORT" if aperto.direction == "LONG" else "LONG",
            volume_requested=posizione.volume,
            position_ticket=posizione.ticket,
            plan_ref=aperto.idempotency_key,
            close_reason=motivo,
            created_at=adesso,
            updated_at=adesso,
        )
        await self._store.insert(c)
        tick = await self._t.tick(aperto.broker_symbol)
        richiesta = {
            "action": TRADE_ACTION_DEAL,
            "symbol": aperto.broker_symbol,
            "volume": posizione.volume,
            "position": posizione.ticket,
            "type": ORDER_TYPE_SELL if aperto.direction == "LONG" else ORDER_TYPE_BUY,
            "price": tick.bid if aperto.direction == "LONG" else tick.ask,
            "deviation": 50,
            "magic": MAGIC,
            "comment": commento(c.idempotency_key),
            "type_time": ORDER_TIME_GTC,
            "type_filling": await self._t.filling(aperto.broker_symbol),
        }
        c.state, c.attempts = "SENT", 1
        await self._salva(c, "SENT", reason=motivo)
        try:
            r = await self._t.order_send(richiesta)
        except BrokerError as e:
            c.state = "UNKNOWN"
            await self._salva(c, "UNKNOWN", error=str(e)[:300])
            return await self.riconcilia(c)
        classe = retcodes.classe(r.retcode)
        c.last_retcode = r.retcode
        if classe in ("DONE", "PARTIAL"):
            c.volume_filled, c.fill_price, c.deal_ticket = r.volume, r.price, r.deal
            c.state = "FILLED" if classe == "DONE" else "PARTIALLY_FILLED"
            await self._salva(c, c.state, price=r.price, volume=r.volume)
            if classe == "DONE":
                aperto.state, aperto.close_reason = "CLOSED", motivo
                await self._salva(aperto, "CLOSED", reason=motivo, price=r.price)
            return c
        if classe == "UNKNOWN":
            c.state = "UNKNOWN"
            await self._salva(c, "UNKNOWN", retcode=retcodes.descrivi(r.retcode))
            return await self.riconcilia(c)
        c.state = "REJECTED"
        await self._salva(c, "REJECTED", retcode=retcodes.descrivi(r.retcode))
        self.breaker.fallimento(f"chiusura {retcodes.descrivi(r.retcode)}")
        return c

    # ------------------------------------------------------------------
    # Riconciliazione dopo timeout, disconnessione o riavvio
    # ------------------------------------------------------------------
    async def riconcilia(self, o: LiveOrder) -> LiveOrder:
        """Stabilisce dal broker cosa è successo a un ordine con esito ignoto. Non reinvia mai."""
        try:
            posizioni = await self._t.positions(o.broker_symbol)
            deal = await self._t.deals(
                o.created_at - timedelta(hours=1), self._clock.now() + timedelta(hours=1)
            )
        except BrokerError as e:
            await self._salva(o, "RECONCILE_FAILED", error=str(e)[:300])
            self.breaker.scatta(f"Esito ignoto per {o.idempotency_key} e broker irraggiungibile.")
            return o
        mio = commento(o.idempotency_key)
        if o.purpose == "OPEN":
            pos = next((p for p in posizioni if p.magic == MAGIC and p.comment == mio), None)
            if pos is not None:
                o.state, o.position_ticket = "FILLED", pos.ticket
                o.volume_filled, o.fill_price = pos.volume, pos.price_open
                o.state = "FILLED" if pos.volume >= o.volume_requested else "PARTIALLY_FILLED"
                await self._salva(o, "RECONCILED_FILLED", ticket=pos.ticket)
                await self.proteggi(o, pos)
                return o
            ingresso = next((d for d in deal if d.comment == mio and d.entry == "IN"), None)
            if ingresso is not None:
                # Eseguito e già chiuso (es. SL/TP immediato): la gestione lo chiude.
                o.state, o.position_ticket = "FILLED", ingresso.position_id
                o.volume_filled, o.fill_price = ingresso.volume, ingresso.price
                await self._salva(o, "RECONCILED_FILLED_CLOSED", ticket=ingresso.position_id)
                return o
            o.state = "NOT_EXECUTED"
            await self._salva(o, "RECONCILED_NOT_EXECUTED")
            return o
        # Chiusura: se la posizione non c'è più, è chiusa (da noi o dal server).
        ancora = any(p.ticket == o.position_ticket for p in posizioni)
        o.state = "NOT_EXECUTED" if ancora else "FILLED"
        await self._salva(o, "RECONCILED_CLOSE", position_still_open=ancora)
        return o

    async def riconcilia_tutto(self) -> list[LiveOrder]:
        """All'avvio e a ogni ciclo: nessun ordine resta in volo senza verifica."""
        return [await self.riconcilia(o) for o in await self._store.lista(STATI_IN_VOLO)]
