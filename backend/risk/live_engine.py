"""Risk Engine per il conto reale: size, rischio aperto e budget giornaliero dal broker.

Nulla viene accumulato in memoria. A ogni approvazione si leggono dal broker:

* **equity** del conto;
* **rischio aperto**: perdita allo stop di ogni posizione, calcolata dal broker;
  una posizione **senza stop** rende il rischio non misurabile e blocca tutto;
* **budget giornaliero e settimanale**: realizzato dallo storico deal della
  giornata/settimana del broker più il flottante delle posizioni aperte;
* **picco di equity** per il drawdown: ricostruito dallo storico deal degli ultimi
  `GIORNI_PICCO` giorni (saldo attuale meno i risultati successivi a ogni istante).

L'approvazione (`RiskApproval`) vale pochi secondi ed è legata a questo motore
(`engine_id`): il gateway non accetta altro. Il package non importa nulla dell'AI.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from backend.brokers.base import AccountDataReader
from backend.config.risk_limits import RiskLimits
from backend.core.clock import Clock
from backend.core.enums import Direction, PositionSide
from backend.core.errors import TradingAgentError
from backend.execution.models import RiskApproval, chiave_idempotenza
from backend.market_data.instruments import InstrumentRegistry
from backend.portfolio.deals import DealHistory
from backend.risk.gate import RichiestaIngresso, valuta_ingresso
from backend.valuation.service import BrokerValuationService

VALIDITA_APPROVAZIONE = timedelta(seconds=30)
GIORNI_PICCO = 90


@dataclass(frozen=True)
class RichiestaLive:
    """Un piano tradotto in numeri, senza oggetti dell'AI (invariante 2)."""

    plan_ref: str
    canonical: str
    broker_symbol: str
    direction: str
    entry_reference: float
    stop: float
    target: float
    atr_h1: float
    spread_atr: float | None
    finestra_evento: bool
    dati_validi: bool
    motivi_dati: tuple[str, ...] = ()


class LiveRiskEngine:
    def __init__(
        self,
        *,
        account: AccountDataReader,
        valuation: BrokerValuationService,
        instruments: InstrumentRegistry,
        deals: DealHistory,
        deal_reader: Any,
        limits: RiskLimits | Any,
        kill_switch: Any,
        ora_broker: Any,
        clock: Clock,
    ) -> None:
        self.engine_id = uuid.uuid4().hex
        self._conto = account
        self._v = valuation
        self._strumenti = instruments
        self._deals = deals
        self._lettore_deal = deal_reader
        self._fonte_limiti = limits
        self._kill = kill_switch
        self._ora_broker = ora_broker
        self._clock = clock

    @property
    def _limiti(self) -> RiskLimits:
        f = self._fonte_limiti
        return f() if callable(f) else f

    async def stato_conto(self) -> dict[str, Any]:
        conto = await self._conto.get_account()
        posizioni = await self._conto.get_positions()
        rischio_aperto = 0.0
        senza_stop: list[int] = []
        for p in posizioni:
            if not p.stop_loss:
                senza_stop.append(p.ticket)
                continue
            strumento = await self._strumenti.by_broker_symbol(p.symbol)
            verso = Direction.LONG if p.side is PositionSide.BUY else Direction.SHORT
            stima = await self._v.loss_at_stop(
                strumento, verso, p.volume, p.price_open, p.stop_loss
            )
            rischio_aperto += stima.loss
        ora = self._ora_broker()
        if ora is None:
            raise TradingAgentError("Ora del broker ignota: budget giornaliero non ricostruibile.")
        flottante = sum(p.profit + p.swap + p.commission for p in posizioni)
        giorno = await self._deals.realized_on_broker_day(ora)
        settimana = await self._deals.realized_on_broker_week(ora)
        picco = await self._picco(conto.balance, ora)
        return {
            "equity": conto.equity,
            "balance": conto.balance,
            "currency": conto.currency,
            "positions": len(posizioni),
            "symbols": frozenset(p.symbol for p in posizioni),
            "open_risk": round(rischio_aperto, 2),
            "positions_without_stop": senza_stop,
            "pnl_today": round(giorno.realized_pnl + flottante, 2),
            "pnl_week": round(settimana.realized_pnl + flottante, 2),
            "peak_equity": max(picco, conto.equity),
            "broker_time": ora,
            "trade_allowed": conto.trade_allowed and conto.trade_expert_allowed,
        }

    async def _picco(self, saldo_attuale: float, ora: datetime) -> float:
        deal = await self._lettore_deal(ora - timedelta(days=GIORNI_PICCO), ora)
        netti = sorted(
            ((d.executed_at, d.profit + d.commission + d.swap + d.fee) for d in deal),
            key=lambda x: x[0],
        )
        saldo = saldo_attuale - sum(n for _, n in netti)
        picco = saldo
        for _, n in netti:
            saldo += n
            picco = max(picco, saldo)
        return picco

    async def approva(self, r: RichiestaLive) -> RiskApproval | list[str]:
        s = await self.stato_conto()
        limiti = self._limiti
        motivi: list[str] = []
        if s["positions_without_stop"]:
            motivi.append(
                f"Posizioni senza stop {s['positions_without_stop']}: rischio non misurabile."
            )
        if not s["trade_allowed"]:
            motivi.append(
                "Trading non consentito sul conto o algo trading disattivato nel terminale."
            )
        if s["pnl_week"] <= -s["equity"] * limiti.weekly_max_loss_pct / 100:
            motivi.append(f"Perdita settimanale oltre il {limiti.weekly_max_loss_pct}%.")
        strumento = await self._strumenti.by_broker_symbol(r.broker_symbol)
        verso = Direction(r.direction)
        budget = s["equity"] * limiti.max_risk_per_trade_pct / 100
        size = await self._v.size_for_risk(
            strumento,
            verso,
            r.entry_reference,
            r.stop,
            budget,
            max_volume=limiti.max_position_size_lots,
        )
        rischio = size.expected_loss if size.tradeable else 0.0
        rr = abs(r.target - r.entry_reference) / abs(r.entry_reference - r.stop)
        motivi += valuta_ingresso(
            RichiestaIngresso(
                simbolo=r.broker_symbol,
                rr=rr,
                stop_atr=abs(r.entry_reference - r.stop) / r.atr_h1,
                spread_atr=r.spread_atr,
                rischio=rischio,
                equity=s["equity"],
                posizioni_aperte=s["positions"],
                simboli_aperti=s["symbols"],
                rischio_aperto=s["open_risk"],
                pnl_oggi=s["pnl_today"],
                picco_equity=s["peak_equity"],
                ora_broker=s["broker_time"],
                kill_switch=self._kill(),
                finestra_evento=r.finestra_evento,
                dati_validi=r.dati_validi,
                motivi_dati=r.motivi_dati,
            ),
            limiti,
        )
        if motivi:
            return list(dict.fromkeys(motivi))
        adesso = self._clock.now()
        return RiskApproval(
            approval_id=uuid.uuid4().hex[:16],
            engine_id=self.engine_id,
            idempotency_key=chiave_idempotenza("OPEN", r.plan_ref),
            canonical=r.canonical,
            broker_symbol=r.broker_symbol,
            direction=r.direction,  # type: ignore[arg-type]
            volume=size.volume,
            entry_reference=r.entry_reference,
            stop_loss=r.stop,
            take_profit=r.target,
            risk_amount=rischio,
            max_slippage_points=limiti.max_slippage_points,
            issued_at=adesso,
            expires_at=adesso + VALIDITA_APPROVAZIONE,
            reasons=(f"rischio {rischio:.2f} {s['currency']} su equity {s['equity']:.2f}",),
        )

    async def sano(self) -> tuple[bool, list[str]]:
        """Il motore può lavorare: conto leggibile, budget ricostruibile, stop ovunque."""
        try:
            s = await self.stato_conto()
        except TradingAgentError as e:
            return False, [f"Stato del conto non ricostruibile: {e}"]
        problemi = []
        if s["positions_without_stop"]:
            problemi.append(f"Posizioni senza stop: {s['positions_without_stop']}.")
        return not problemi, problemi or [
            f"equity {s['equity']:.2f} {s['currency']}, P/L oggi {s['pnl_today']:.2f}, "
            f"rischio aperto {s['open_risk']:.2f}"
        ]
