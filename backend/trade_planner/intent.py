"""Costruzione di un TradeIntent: da un'idea a numeri del broker.

Fa tre cose e nessuna di più:

1. dimensiona con `BrokerValuationService.size_for_risk` — nessuna formula qui;
2. chiede al broker il margine sul volume ottenuto;
3. dichiara se le **precondizioni sui dati** sono soddisfatte.

Regola invariante, verificata da test di proprietà: una quotazione che non sia
`VALID` con mercato `OPEN_CONFIRMED` produce sempre un intent **non eseguibile**.
Le quotazioni `STALE`, `MISSING`, `INCONSISTENT`, `MARKET_CLOSED` e lo stato di
mercato `UNKNOWN` o `CLOSED_INFERRED` non passano, qualunque sia il resto.

Il volume viene comunque calcolato anche quando l'intent non è eseguibile: serve a
spiegare cosa sarebbe successo, ed è utile nel journal. Ma un volume calcolato non
è un permesso.
"""

from __future__ import annotations

from backend.core.clock import Clock, SystemClock
from backend.core.enums import Direction, MarketStatus, QuoteQualityStatus
from backend.core.errors import TradingAgentError
from backend.schemas.instrument import Instrument
from backend.schemas.intent import TradeIntent
from backend.schemas.quote import QuoteQuality
from backend.valuation.service import BrokerValuationService


class TradeIntentBuilder:
    """Traduce un'idea operativa in un `TradeIntent` dimensionato."""

    def __init__(self, valuation: BrokerValuationService, *, clock: Clock | None = None) -> None:
        self._valutazione = valuation
        self._clock = clock or SystemClock()

    async def build(
        self,
        instrument: Instrument,
        direction: Direction,
        *,
        entry: float,
        stop: float,
        risk_budget: float,
        quote: QuoteQuality,
        max_volume: float | None = None,
    ) -> TradeIntent:
        blocchi: list[str] = []

        # Le precondizioni sui dati si valutano per prime e indipendentemente
        # dal dimensionamento: un dato non valido blocca anche se la size torna.
        if quote.status is not QuoteQualityStatus.VALID:
            blocchi.append(f"Quotazione {quote.status.value}: " + " ".join(quote.reasons))
        if quote.market_status is not MarketStatus.OPEN_CONFIRMED:
            blocchi.append(
                f"Mercato {quote.market_status.value}: serve una quotazione fresca "
                "che confermi l'apertura."
            )
        if quote.broker_symbol != instrument.broker_symbol:
            blocchi.append(
                f"La quotazione è di {quote.broker_symbol}, l'intent di {instrument.broker_symbol}."
            )
        if not instrument.tradeable:
            blocchi.append("Il broker non consente aperture su questo strumento.")

        volume = 0.0
        perdita = 0.0
        margine: float | None = None
        try:
            size = await self._valutazione.size_for_risk(
                instrument, direction, entry, stop, risk_budget, max_volume=max_volume
            )
            volume, perdita = size.volume, size.expected_loss
            if not size.tradeable:
                blocchi.extend(size.reasons or ("Size nulla per il rischio consentito.",))
            else:
                prezzo = quote.ask if direction is Direction.LONG else quote.bid
                margine = (
                    await self._valutazione.margin(instrument, direction, volume, prezzo or entry)
                ).margin
        except TradingAgentError as errore:
            blocchi.append(f"Dimensionamento non possibile: {errore}")

        if perdita > risk_budget:
            blocchi.append(f"Perdita attesa {perdita} oltre il budget {risk_budget}.")

        return TradeIntent(
            canonical=instrument.canonical,
            broker_symbol=instrument.broker_symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            risk_budget=risk_budget,
            volume=volume,
            expected_loss=perdita,
            margin_required=margine,
            account_currency=self._valutazione.account_currency,
            quote_status=quote.status,
            market_status=quote.market_status,
            executable=not blocchi and volume > 0,
            blocking_reasons=tuple(blocchi),
            created_at=self._clock.now(),
        )
