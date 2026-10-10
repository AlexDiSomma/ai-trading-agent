"""BrokerValuationService: l'unico punto in cui si calcolano soldi.

Profitto, perdita allo stop, valore di un movimento di prezzo, margine,
normalizzazione del volume, dimensione di una posizione. **Nessun altro modulo**
contiene una formula di P/L, pip value o margine; un test sull'AST lo verifica
(`test_valutazione_centralizzata.py`).

Perché centralizzare, con i numeri misurati sul conto XM demo in EUR:

* `trade_tick_value` è in EUR per il forex e in **USD** per metalli e indici:
  derivare da lì sbaglia la size del 12,5% (ADR 0012);
* `order_calc_profit` converte al prezzo di **chiusura**: su EURUSD un movimento
  di 1.0 dà 47.056 invece di 88.810 (ADR 0012);
* `order_calc_margin` **non valida il volume**: 0.05 lotti su US100Cash (passo
  0.1) restituiscono un margine come se fossero validi;
* BUY e SELL sullo stesso movimento differiscono di qualche centesimo (88.81 contro
  88.89 su EURUSD), per la stessa conversione al prezzo di chiusura.

Ognuna di queste è una trappola in cui cade chi riscrive la formula in un altro
modulo "perché è semplice". Qui la fonte autorevole è sempre MT5, e le uniche
operazioni nostre sono quelle che il broker non fa: normalizzare il volume e
scegliere la size.

Tutte le operazioni sono di **sola lettura**: i calcolatori `order_calc_*` non
creano ordini e non inviano nulla al server.
"""

from __future__ import annotations

import math
from decimal import ROUND_FLOOR, Decimal
from typing import Protocol

from backend.core.enums import Direction
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.schemas.instrument import Instrument
from backend.schemas.market import SymbolInfo
from backend.schemas.valuation import (
    MarginEstimate,
    SizeResult,
    StopLossEstimate,
    TickValueGap,
    VolumeNormalization,
)

logger = get_logger(__name__)

#: Tick usati per misurare il valore di un movimento di prezzo. Cento: abbastanza
#: da non perdersi negli arrotondamenti del broker (due decimali), abbastanza
#: pochi da non spostare il cambio usato per la conversione.
TICK_DI_PROVA = 100

#: Volume con cui si misura il valore per unità di prezzo. Il broker arrotonda il
#: profitto al centesimo: con 1 lotto e 100 tick su US100Cash restituisce 0.89 EUR
#: invece di 0.8887 (+0,14%). Misurato sul conto reale il 2026-10-04: il
#: calcolatore accetta volumi oltre `volume_max` ed è lineare nel volume
#: (1, 100, 1000, 10000 lotti danno lo stesso valore per unità a meno
#: dell'arrotondamento), quindi con 1000 lotti l'errore scende sotto lo 0,001%.
#: È un calcolo, non un ordine: il volume non deve essere eseguibile.
VOLUME_DI_PROVA = 1_000.0

#: Nessuna tolleranza sulla perdita attesa: la perdita allo stop calcolata dal
#: broker non supera **mai** il rischio consentito, nemmeno di un centesimo di
#: arrotondamento. Prima c'era una tolleranza di 0.01: è stata tolta il
#: 2026-10-04 perché il rischio è un limite dell'utente, non una stima.


class ValuationError(TradingAgentError):
    """Calcolo non eseguibile: dati incoerenti o broker che non risponde."""

    blocks_trading = True


class BrokerCalculator(Protocol):
    """Le due chiamate di calcolo del broker. Soddisfatto da `MT5Connector`."""

    async def calculate_profit(
        self,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
        *,
        sell: bool = False,
    ) -> float: ...

    async def calculate_margin(
        self, symbol: str, volume: float, price: float, *, sell: bool = False
    ) -> float: ...


class BrokerValuationService:
    """Valutazioni monetarie con MT5 come fonte autorevole."""

    def __init__(self, broker: BrokerCalculator, *, account_currency: str) -> None:
        self._broker = broker
        self._valuta = account_currency.upper()

    @property
    def account_currency(self) -> str:
        return self._valuta

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_volume(instrument: Instrument, volume: float) -> VolumeNormalization:
        """Porta un volume sulle regole del broker, **sempre per difetto**.

        Il broker non lo fa per noi: `order_calc_margin` accetta volumi fuori
        passo. Arrotondare per eccesso farebbe rischiare più del calcolato, e il
        rischio è dell'utente; sotto il minimo il risultato è zero, non il minimo.

        Aritmetica decimale: con i float `0.3 / 0.1` vale 2.9999999999999996, e un
        `floor` darebbe un passo in meno.
        """
        c = instrument.contract
        base = {
            "requested": max(volume, 0.0),
            "volume_min": c.volume_min,
            "volume_max": c.volume_max,
            "volume_step": c.volume_step,
        }
        if not math.isfinite(volume) or volume <= 0:
            return VolumeNormalization(volume=0.0, adjustment="below_min", **base)

        passo = Decimal(str(c.volume_step))
        passi = (Decimal(str(volume)) / passo).quantize(Decimal(1), rounding=ROUND_FLOOR)
        normalizzato = float(passi * passo)
        aggiustamento = "step" if normalizzato < volume - 1e-12 else "none"

        if normalizzato > c.volume_max:
            massimi = (Decimal(str(c.volume_max)) / passo).quantize(
                Decimal(1), rounding=ROUND_FLOOR
            )
            normalizzato = float(massimi * passo)
            aggiustamento = "max"
        if normalizzato < c.volume_min - 1e-12:
            return VolumeNormalization(volume=0.0, adjustment="below_min", **base)

        return VolumeNormalization(volume=normalizzato, adjustment=aggiustamento, **base)

    # ------------------------------------------------------------------
    # Profitto e perdita
    # ------------------------------------------------------------------
    async def profit(
        self,
        instrument: Instrument,
        direction: Direction,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float:
        """Profitto (negativo se perdita) in valuta del conto, dal broker."""
        if volume <= 0 or price_open <= 0 or price_close <= 0:
            raise ValuationError(
                "Profitto non calcolabile: volume e prezzi devono essere positivi.",
                symbol=instrument.broker_symbol,
            )
        return await self._broker.calculate_profit(
            instrument.broker_symbol,
            volume,
            price_open,
            price_close,
            sell=direction is Direction.SHORT,
        )

    async def loss_at_stop(
        self,
        instrument: Instrument,
        direction: Direction,
        volume: float,
        entry: float,
        stop: float,
    ) -> StopLossEstimate:
        """Perdita se lo stop viene toccato, calcolata dal broker **su quel movimento**.

        Non si usa il valore per unità moltiplicato per la distanza: su EURUSD la
        conversione avviene al prezzo di chiusura, quindi il calcolo diretto
        fra entry e stop è più fedele di qualunque linearizzazione.
        """
        self._verifica_stop(instrument, direction, entry, stop)
        profitto = await self.profit(instrument, direction, volume, entry, stop)
        distanza = abs(entry - stop)
        return StopLossEstimate(
            broker_symbol=instrument.broker_symbol,
            direction=direction,
            volume=volume,
            entry=entry,
            stop=stop,
            # Uno stop sul lato giusto produce una perdita, cioè un profitto
            # negativo dal broker: la perdita è il suo opposto, mai negativa.
            loss=round(max(-profitto, 0.0), 2),
            account_currency=self._valuta,
            stop_distance=distanza,
            stop_distance_ticks=round(distanza / instrument.contract.tick_size, 4),
        )

    async def open_position_risk(
        self,
        instrument: Instrument,
        direction: Direction,
        volume: float,
        price_open: float,
        stop: float,
    ) -> float:
        """Perdita residua se lo stop di una posizione **già aperta** viene toccato.

        Diversa da `loss_at_stop`: una posizione aperta può avere lo stop già oltre
        l'entry (profitto protetto), e allora la perdita allo stop è zero, non un
        errore. Non si applica `stops_level`, che vale per i nuovi ordini.
        """
        in_profitto = (direction is Direction.LONG and stop >= price_open) or (
            direction is Direction.SHORT and stop <= price_open
        )
        if in_profitto:
            return 0.0
        profitto = await self.profit(instrument, direction, volume, price_open, stop)
        return round(max(-profitto, 0.0), 2)

    async def value_per_price_unit(self, instrument: Instrument, reference_price: float) -> float:
        """Valore in valuta del conto di un movimento di 1.0 di prezzo, per 1 lotto.

        Misurato con cento tick su mille lotti e normalizzato: il calcolatore non è
        lineare nel delta (ADR 0012) e arrotonda al centesimo. Serve per il nozionale
        e i confronti, non per lo stop: per lo stop si usa `loss_at_stop`, sul movimento esatto.
        """
        if reference_price <= 0:
            raise ValuationError("Il prezzo di riferimento deve essere positivo.")
        delta = instrument.contract.tick_size * TICK_DI_PROVA
        profitto = await self.profit(
            instrument, Direction.LONG, VOLUME_DI_PROVA, reference_price, reference_price + delta
        )
        return profitto / (delta * VOLUME_DI_PROVA)

    async def notional(self, instrument: Instrument, volume: float, price: float) -> float:
        """Valore nozionale in valuta del conto: volume x prezzo x valore per unità."""
        return round(volume * price * await self.value_per_price_unit(instrument, price), 2)

    # ------------------------------------------------------------------
    # Margine
    # ------------------------------------------------------------------
    async def margin(
        self,
        instrument: Instrument,
        direction: Direction,
        volume: float,
        price: float,
    ) -> MarginEstimate:
        """Margine richiesto, dal broker, su un volume **già valido**.

        Rifiuta un volume fuori dalle regole del broker invece di chiederne il
        margine: il broker lo calcolerebbe comunque, restituendo un numero per una
        posizione che non si potrebbe aprire.
        """
        normalizzato = self.normalize_volume(instrument, volume)
        if normalizzato.volume <= 0 or abs(normalizzato.volume - volume) > 1e-9:
            raise ValuationError(
                f"Volume {volume} non valido per {instrument.broker_symbol} "
                f"(minimo {instrument.contract.volume_min}, passo "
                f"{instrument.contract.volume_step}, massimo {instrument.contract.volume_max}). "
                "Il broker non lo verifica: va normalizzato prima.",
                symbol=instrument.broker_symbol,
                volume=volume,
            )
        valore = await self._broker.calculate_margin(
            instrument.broker_symbol, volume, price, sell=direction is Direction.SHORT
        )
        return MarginEstimate(
            broker_symbol=instrument.broker_symbol,
            direction=direction,
            volume=volume,
            price=price,
            margin=round(valore, 2),
            account_currency=self._valuta,
        )

    # ------------------------------------------------------------------
    # Dimensionamento
    # ------------------------------------------------------------------
    async def size_for_risk(
        self,
        instrument: Instrument,
        direction: Direction,
        entry: float,
        stop: float,
        risk_amount: float,
        *,
        max_volume: float | None = None,
    ) -> SizeResult:
        """Volume massimo tale che la perdita allo stop non superi `risk_amount`.

        1. perdita per lotto, dal broker, sul movimento entry → stop;
        2. volume grezzo = rischio / perdita per lotto;
        3. tetto: massimo del broker e, se indicato, dell'utente;
        4. normalizzazione per difetto al passo; sotto il minimo → zero;
        5. **verifica**: la perdita col volume scelto viene richiesta di nuovo al
           broker, e se supera il rischio si scende di un passo. La perdita non
           è perfettamente lineare nel volume solo per gli arrotondamenti del
           broker, ma la garanzia deve valere comunque.
        """
        if risk_amount < 0 or not math.isfinite(risk_amount):
            raise ValuationError("Il rischio deve essere un importo non negativo.")

        per_lotto = (await self.loss_at_stop(instrument, direction, 1.0, entry, stop)).loss
        if per_lotto <= 0:
            raise ValuationError(
                f"Perdita per lotto nulla su {instrument.broker_symbol}: stop coincidente "
                "con l'entry o broker che non quota.",
                symbol=instrument.broker_symbol,
            )

        motivi: list[str] = []
        grezzo = risk_amount / per_lotto
        limite = "risk"
        tetto = instrument.contract.volume_max
        if max_volume is not None and max_volume < tetto:
            tetto = max_volume
        if grezzo > tetto:
            grezzo = tetto
            limite = "user_max" if tetto != instrument.contract.volume_max else "volume_max"
            motivi.append(f"Volume limitato a {tetto} lotti ({limite}).")

        norm = self.normalize_volume(instrument, grezzo)
        volume = norm.volume
        if norm.adjustment == "below_min":
            motivi.append(
                f"Il rischio consentito ({risk_amount:.2f} {self._valuta}) non basta "
                f"per il volume minimo di {instrument.contract.volume_min} lotti, che "
                f"perderebbe {per_lotto * instrument.contract.volume_min:.2f}. "
                "Size zero: non si arrotonda per eccesso."
            )
            limite = "below_min"
        elif norm.adjustment == "step" and limite == "risk":
            limite = "step"

        perdita = 0.0
        passo = instrument.contract.volume_step
        while volume > 0:
            perdita = (await self.loss_at_stop(instrument, direction, volume, entry, stop)).loss
            if perdita <= risk_amount:
                break
            volume = self.normalize_volume(instrument, volume - passo).volume
            motivi.append("Volume ridotto di un passo: la perdita ricalcolata superava il rischio.")
        if volume <= 0:
            perdita = 0.0

        return SizeResult(
            broker_symbol=instrument.broker_symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            risk_amount=risk_amount,
            volume=volume,
            expected_loss=round(perdita, 2),
            loss_per_lot=per_lotto,
            limited_by=limite,
            account_currency=self._valuta,
            reasons=tuple(motivi),
        )

    # ------------------------------------------------------------------
    # Diagnostica
    # ------------------------------------------------------------------
    async def tick_value_gap(
        self, instrument: Instrument, info: SymbolInfo, reference_price: float
    ) -> TickValueGap:
        """Scarto fra il valore derivato da `tick_value` e quello del broker.

        **Unico punto** del sistema, fuori dalla mappatura MT5 e dalla
        persistenza, che legge `tick_value`: serve a mostrare perché non lo si usa
        (12,5% su metalli e indici) e non entra in nessun calcolo di rischio.
        """
        dal_broker = await self.value_per_price_unit(instrument, reference_price)
        derivato = info.tick_value / info.tick_size
        return TickValueGap(
            broker_symbol=instrument.broker_symbol,
            from_broker=round(dal_broker, 6),
            from_tick_value=round(derivato, 6),
            gap_pct=round((derivato / dal_broker - 1) * 100, 3) if dal_broker else 0.0,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _verifica_stop(
        instrument: Instrument, direction: Direction, entry: float, stop: float
    ) -> None:
        """Lo stop deve stare dal lato della perdita e oltre il minimo del broker."""
        if entry <= 0 or stop <= 0:
            raise ValuationError("Entry e stop devono essere positivi.")
        if direction is Direction.LONG and stop >= entry:
            raise ValuationError(
                f"Stop {stop} non sotto l'entry {entry} per un LONG su {instrument.broker_symbol}.",
                symbol=instrument.broker_symbol,
            )
        if direction is Direction.SHORT and stop <= entry:
            raise ValuationError(
                f"Stop {stop} non sopra l'entry {entry} per uno SHORT su "
                f"{instrument.broker_symbol}.",
                symbol=instrument.broker_symbol,
            )
        minimo = instrument.contract.stops_level_price
        if minimo > 0 and abs(entry - stop) < minimo:
            raise ValuationError(
                f"Stop a {abs(entry - stop)} dal prezzo, sotto il minimo del broker ({minimo}).",
                symbol=instrument.broker_symbol,
            )
