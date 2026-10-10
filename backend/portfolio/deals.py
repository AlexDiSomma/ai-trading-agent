"""Storico delle operazioni chiuse e PnL realizzato.

Serve a una cosa che sembra contabile e invece è una sicurezza: il **budget di
rischio giornaliero** va ricostruito dallo storico del broker a ogni avvio, non
accumulato in memoria. Un contatore in RAM ripartirebbe da zero dopo un riavvio, e
il limite di perdita giornaliero dell'utente sarebbe raddoppiabile riavviando il
processo.

Due dettagli che cambiano i numeri.

**Solo le uscite realizzano.** MT5 registra un deal in ingresso e uno in uscita
per ogni posizione; sommare il profitto di tutti i deal conterebbe due volte.
Filtrare su `is_realizing` è obbligatorio.

**I movimenti non di mercato non sono trade.** Versamenti, prelievi, commissioni
mensili e correzioni (`deal_type` da `BALANCE` in su) muovono l'equity ma non sono
operazioni: entrano nella variazione del saldo, non nelle statistiche di
performance né nel consumo del budget di rischio.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.core.clock import Clock, SystemClock, ensure_utc
from backend.core.logging import get_logger
from backend.schemas.account import DealSnapshot

logger = get_logger(__name__)

#: Tipi di deal che sono operazioni di mercato. Tutto il resto è movimento di
#: cassa o rettifica contabile.
TIPI_DI_MERCATO = frozenset({"BUY", "SELL"})

#: Firma del lettore dello storico, iniettato per non dipendere dal connettore.
LettoreDeal = Callable[[datetime, datetime], Awaitable[tuple[DealSnapshot, ...]]]


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """Operazione ricostruita dai deal di una stessa posizione.

    MT5 non ha un concetto di "trade": ha deal. Un'operazione completa è
    l'insieme dei deal con lo stesso `position_id`, e ricostruirla qui evita che
    ogni modulo a valle lo rifaccia a modo proprio.
    """

    position_id: int
    symbol: str
    direction: str
    volume: float
    opened_at: datetime
    closed_at: datetime
    entry_price: float
    exit_price: float
    net_result: float
    commission: float
    swap: float
    magic: int
    comment: str

    @property
    def duration(self) -> timedelta:
        return self.closed_at - self.opened_at

    @property
    def is_win(self) -> bool:
        return self.net_result > 0


@dataclass(frozen=True, slots=True)
class RealizedSummary:
    """PnL realizzato in un intervallo, con il conteggio delle operazioni."""

    since: datetime
    until: datetime
    realized_pnl: float
    trades: int
    wins: int
    losses: int
    #: Movimenti non di mercato (versamenti, commissioni periodiche, rettifiche).
    non_trading_flow: float
    deals_considered: int

    @property
    def win_rate(self) -> float | None:
        chiuse = self.wins + self.losses
        return round(self.wins / chiuse, 4) if chiuse else None


class DealHistory:
    """Letture sullo storico dei deal del broker."""

    def __init__(self, lettore: LettoreDeal, *, clock: Clock | None = None) -> None:
        self._lettore = lettore
        self._clock = clock or SystemClock()

    async def realized_between(self, da: datetime, a: datetime) -> RealizedSummary:
        """Riepilogo del realizzato fra due istanti.

        Gli istanti sono in **ora del broker**: lo storico di MT5 è datato con
        quella, e un intervallo costruito sull'ora locale taglierebbe la giornata
        nel punto sbagliato (ADR 0010).
        """
        deal = await self._lettore(da, a)
        realizzo = [d for d in deal if d.is_realizing and d.deal_type in TIPI_DI_MERCATO]
        non_mercato = [d for d in deal if d.deal_type not in TIPI_DI_MERCATO]

        pnl = round(sum(d.net_result for d in realizzo), 2)
        return RealizedSummary(
            since=ensure_utc(da),
            until=ensure_utc(a),
            realized_pnl=pnl,
            trades=len(realizzo),
            wins=sum(1 for d in realizzo if d.net_result > 0),
            losses=sum(1 for d in realizzo if d.net_result < 0),
            non_trading_flow=round(sum(d.net_result for d in non_mercato), 2),
            deals_considered=len(deal),
        )

    async def realized_on_broker_day(self, broker_now: datetime) -> RealizedSummary:
        """Realizzato della giornata di trading **del broker**.

        La mezzanotte è quella del server, non quella del PC: usare l'ora locale
        creerebbe ogni giorno una finestra in cui il limite di perdita vale il
        doppio.
        """
        inizio = ensure_utc(broker_now).replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.realized_between(inizio, ensure_utc(broker_now))

    async def realized_on_broker_week(self, broker_now: datetime) -> RealizedSummary:
        """Realizzato della settimana di trading, da lunedì sull'ora del broker."""
        adesso = ensure_utc(broker_now)
        inizio_giorno = adesso.replace(hour=0, minute=0, second=0, microsecond=0)
        inizio = inizio_giorno - timedelta(days=adesso.weekday())
        return await self.realized_between(inizio, adesso)

    async def closed_trades(self, da: datetime, a: datetime) -> tuple[ClosedTrade, ...]:
        """Operazioni complete ricostruite dai deal, in ordine di chiusura.

        Una posizione chiusa in più tranche produce più deal di uscita: vengono
        aggregati, e il prezzo di uscita è la media **ponderata sul volume**. Una
        media semplice darebbe un prezzo che non corrisponde al risultato.
        """
        deal = await self._lettore(da, a)
        per_posizione: dict[int, list[DealSnapshot]] = {}
        for singolo in deal:
            if singolo.deal_type not in TIPI_DI_MERCATO or not singolo.position_id:
                continue
            per_posizione.setdefault(singolo.position_id, []).append(singolo)

        operazioni: list[ClosedTrade] = []
        for position_id, gruppo in per_posizione.items():
            ingressi = [d for d in gruppo if d.entry == "IN"]
            uscite = [d for d in gruppo if d.is_realizing]
            if not ingressi or not uscite:
                # Posizione ancora aperta, o storico troncato dall'intervallo
                # richiesto: non è un'operazione chiusa e non va inventata.
                continue

            volume_ingresso = sum(d.volume for d in ingressi) or 1.0
            volume_uscita = sum(d.volume for d in uscite) or 1.0
            operazioni.append(
                ClosedTrade(
                    position_id=position_id,
                    symbol=gruppo[0].symbol,
                    # Il deal di ingresso BUY apre un long: la direzione della
                    # posizione è quella dell'ingresso, non dell'uscita.
                    direction="LONG" if ingressi[0].deal_type == "BUY" else "SHORT",
                    volume=round(volume_ingresso, 4),
                    opened_at=min(d.executed_at for d in ingressi),
                    closed_at=max(d.executed_at for d in uscite),
                    entry_price=round(
                        sum(d.price * d.volume for d in ingressi) / volume_ingresso, 8
                    ),
                    exit_price=round(sum(d.price * d.volume for d in uscite) / volume_uscita, 8),
                    net_result=round(sum(d.net_result for d in uscite), 2),
                    commission=round(sum(d.commission for d in gruppo), 2),
                    swap=round(sum(d.swap for d in gruppo), 2),
                    magic=gruppo[0].magic,
                    comment=next((d.comment for d in gruppo if d.comment), ""),
                )
            )

        return tuple(sorted(operazioni, key=lambda t: t.closed_at))
