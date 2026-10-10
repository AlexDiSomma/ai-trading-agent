"""Gestione delle posizioni reali aperte dal sistema.

A ogni ciclo, partendo dal broker (fonte di verità) e non dalla memoria:

* ordini in volo → riconciliazione (`gateway.riconcilia_tutto`);
* posizione registrata ma assente al broker → chiusa lato server (SL/TP) o
  dall'esterno: si legge il risultato dallo storico deal e si registra `CLOSED`;
* posizione presente → SL/TP lato server verificati, poi invalidazioni (condizioni
  tipizzate sulle barre chiuse dopo l'ingresso) e scadenza → chiusura;
* posizione con il numero magico del sistema ma sconosciuta all'archivio → stato
  inatteso: il breaker scatta (non si adotta in silenzio ciò che non si conosce).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from backend.core.clock import Clock
from backend.core.logging import get_logger
from backend.execution.gateway import LiveOrderGateway
from backend.execution.models import MAGIC, LiveOrder
from backend.execution.mt5_terminal import TradingTerminal
from backend.execution.store import ExecutionStore
from backend.shadow.conditions import Barra, PriceCondition, prima_volta

logger = get_logger(__name__)

Barre = Callable[[str, str], Awaitable[list[Barra]]]


def motivo_chiusura_server(commenti: list[str]) -> str:
    """MT5 marca le chiusure lato server nel commento del deal: `[sl ...]`, `[tp ...]`."""
    testo = " ".join(commenti).lower()
    if "[sl" in testo:
        return "SL"
    if "[tp" in testo:
        return "TP"
    if "so:" in testo or "stop out" in testo:
        return "STOP_OUT"
    return "EXTERNAL"


class LivePositionManager:
    def __init__(
        self,
        *,
        gateway: LiveOrderGateway,
        terminal: TradingTerminal,
        store: ExecutionStore,
        barre: Barre,
        clock: Clock,
    ) -> None:
        self._g = gateway
        self._t = terminal
        self._store = store
        self._barre = barre
        self._clock = clock

    async def ciclo(self) -> list[str]:
        eventi: list[str] = []
        await self._g.riconcilia_tutto()
        aperti = [
            o
            for o in await self._store.lista(frozenset({"FILLED", "PARTIALLY_FILLED"}))
            if o.purpose == "OPEN"
        ]
        posizioni = {p.ticket: p for p in await self._t.positions()}
        noti = {o.position_ticket for o in aperti}
        for p in posizioni.values():
            if p.magic == MAGIC and p.ticket not in noti:
                self._g.breaker.scatta(
                    f"Posizione {p.ticket} del sistema sconosciuta all'archivio."
                )
                eventi.append(f"orfana {p.ticket}")
        for o in aperti:
            p = posizioni.get(o.position_ticket or -1)
            if p is None:
                await self._registra_chiusura_server(o)
                eventi.append(f"chiusa al broker {o.canonical} ({o.close_reason})")
                continue
            if not (p.stop_loss and p.take_profit):
                await self._g.proteggi(o, p)
            motivo = await self._da_chiudere(o)
            if motivo:
                await self._g.chiudi(o, motivo)
                eventi.append(f"chiusura {o.canonical} per {motivo}")
        return eventi

    async def _registra_chiusura_server(self, o: LiveOrder) -> None:
        adesso = self._clock.now()
        deal = [
            d
            for d in await self._t.deals(o.created_at - timedelta(hours=1), adesso)
            if d.position_id == o.position_ticket
        ]
        uscite = [d for d in deal if d.entry in ("OUT", "OUT_BY", "INOUT")]
        o.realized_pnl = round(sum(d.profit + d.commission + d.swap + d.fee for d in deal), 2)
        o.close_reason = o.close_reason or motivo_chiusura_server([d.comment for d in uscite])
        o.state = "CLOSED"
        o.evento(adesso, "CLOSED_AT_BROKER", reason=o.close_reason, pnl=o.realized_pnl)
        await self._store.update(o)

    async def _da_chiudere(self, o: LiveOrder) -> str | None:
        piano = o.plan or {}
        scadenza = piano.get("expires_at")
        if scadenza and self._clock.now() >= datetime.fromisoformat(str(scadenza)):
            return "TIME"
        for grezza in piano.get("invalidations", []) or []:  # type: ignore[union-attr]
            c = PriceCondition.model_validate(grezza)
            barre = await self._barre(c.subject, c.timeframe)
            if prima_volta(
                c, barre, dopo=o.created_at, entro=self._clock.now() + timedelta(days=1)
            ):
                return "INVALIDATION"
        return None
