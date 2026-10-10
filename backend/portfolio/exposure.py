"""Calcolo dell'esposizione di portafoglio.

Lettura pura: legge posizioni, specifiche e valori monetari dal broker e produce
un `ExposureSnapshot`. Non decide niente — il Risk Engine della fase 6 userà
questi numeri, ma la separazione resta: qui si misura, là si giudica.

Nessuna formula monetaria in questo modulo (ADR 0014). Nozionale e rischio allo
stop vengono da `BrokerValuationService`, che li chiede al calcolatore del broker:

* nozionale = `valuation.notional(strumento, volume, prezzo_corrente)`;
* rischio allo stop = `valuation.open_position_risk(...)`, cioè il profitto che il
  broker calcola **sul movimento esatto** fra apertura e stop. Prima era
  `volume x distanza x valore_per_unità`, una linearizzazione che su EURUSD
  sbagliava perché il broker converte al prezzo di chiusura.

La vista per valuta usa la coppia **economica** dell'`Instrument`: l'oro è XAU
contro USD, non USD contro USD come lo dichiara XM.

Se il valore monetario non è ottenibile per uno strumento, il nozionale e il
rischio di quella posizione restano `None` e lo snapshot lo dichiara, invece di
usare un'approssimazione che nessuno saprebbe più distinguere da un numero vero.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from backend.core.clock import Clock, SystemClock
from backend.core.enums import AssetClass, Direction, PositionSide
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.schemas.account import PortfolioSnapshot, PositionSnapshot
from backend.schemas.exposure import (
    AssetClassExposure,
    CurrencyExposure,
    ExposureSnapshot,
    SymbolExposure,
)
from backend.schemas.instrument import Instrument
from backend.valuation.service import BrokerValuationService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Misura:
    """Misure di una singola posizione, in valuta del conto."""

    notional: float | None
    risk_at_stop: float | None
    segno: int


class ExposureCalculator:
    """Costruisce lo snapshot dell'esposizione da posizioni e specifiche."""

    def __init__(
        self,
        *,
        valuation: BrokerValuationService,
        instrument_of: Callable[[str], Awaitable[Instrument]],
        clock: Clock | None = None,
    ) -> None:
        self._valutazione = valuation
        self._strumento = instrument_of
        self._clock = clock or SystemClock()

    async def build(self, portafoglio: PortfolioSnapshot) -> ExposureSnapshot:
        """Esposizione completa a partire da conto e posizioni letti insieme."""
        avvisi: list[str] = []
        per_simbolo: dict[str, list[tuple[PositionSnapshot, _Misura, Instrument]]] = {}
        non_calcolabili: list[str] = []

        for posizione in portafoglio.positions:
            try:
                strumento = await self._strumento(posizione.symbol)
                misura = await self._misura(posizione, strumento)
            except TradingAgentError as errore:
                # Non si inventa: la posizione entra nei conteggi ma non nei
                # valori monetari, e lo snapshot lo dice.
                logger.warning(
                    "esposizione.valore_non_calcolabile",
                    symbol=posizione.symbol,
                    motivo=str(errore),
                )
                non_calcolabili.append(posizione.symbol)
                continue

            per_simbolo.setdefault(posizione.symbol, []).append((posizione, misura, strumento))

        if non_calcolabili:
            avvisi.append(
                "Valore monetario non calcolabile per: "
                + ", ".join(sorted(set(non_calcolabili)))
                + ". Nozionale e rischio di queste posizioni non sono inclusi."
            )

        esposizioni = tuple(
            self._aggrega_simbolo(simbolo, voci) for simbolo, voci in sorted(per_simbolo.items())
        )

        senza_stop = tuple(
            sorted({p.symbol for p in portafoglio.positions if not p.has_protection})
        )
        if senza_stop:
            avvisi.append(
                "Posizioni senza stop loss su: "
                + ", ".join(senza_stop)
                + ". Il rischio complessivo non è misurabile."
            )
        for esposizione in esposizioni:
            if esposizione.hedged:
                avvisi.append(
                    f"{esposizione.symbol}: posizioni opposte aperte insieme "
                    "(si pagano due spread per non avere esposizione)."
                )

        rischio_totale: float | None = None
        if not senza_stop and not non_calcolabili:
            parziali = [e.risk_at_stop for e in esposizioni if e.risk_at_stop is not None]
            rischio_totale = round(sum(parziali), 2) if parziali else 0.0

        return ExposureSnapshot(
            as_of=portafoglio.as_of,
            account_currency=portafoglio.account.currency,
            equity=portafoglio.account.equity,
            open_positions=len(portafoglio.positions),
            by_symbol=esposizioni,
            by_currency=self._per_valuta(per_simbolo),
            by_asset_class=self._per_classe(esposizioni),
            gross_notional=round(sum(e.gross_notional for e in esposizioni), 2),
            net_notional=round(sum(e.net_notional for e in esposizioni), 2),
            unrealized_pnl=portafoglio.unrealized_pnl,
            total_risk_at_stop=rischio_totale,
            positions_without_stop=senza_stop,
            warnings=tuple(avvisi),
        )

    # ------------------------------------------------------------------
    # Misure di una posizione
    # ------------------------------------------------------------------
    async def _misura(self, posizione: PositionSnapshot, strumento: Instrument) -> _Misura:
        lato = Direction.LONG if posizione.side is PositionSide.BUY else Direction.SHORT
        nozionale = await self._valutazione.notional(
            strumento, posizione.volume, posizione.price_current
        )
        rischio: float | None = None
        if posizione.stop_loss is not None:
            rischio = await self._valutazione.open_position_risk(
                strumento, lato, posizione.volume, posizione.price_open, posizione.stop_loss
            )
        segno = 1 if lato is Direction.LONG else -1
        return _Misura(notional=nozionale, risk_at_stop=rischio, segno=segno)

    def _aggrega_simbolo(
        self, simbolo: str, voci: Sequence[tuple[PositionSnapshot, _Misura, Instrument]]
    ) -> SymbolExposure:
        volume_netto = sum(m.segno * p.volume for p, m, _ in voci)
        volume_lordo = sum(p.volume for p, _, _ in voci)
        nozionale_netto = sum(m.segno * (m.notional or 0.0) for _, m, _ in voci)
        nozionale_lordo = sum(abs(m.notional or 0.0) for _, m, _ in voci)

        senza_stop = sum(1 for p, _, _ in voci if not p.has_protection)
        rischi = [m.risk_at_stop for _, m, _ in voci if m.risk_at_stop is not None]
        rischio = round(sum(rischi), 2) if senza_stop == 0 and rischi else None

        return SymbolExposure(
            symbol=simbolo,
            asset_class=voci[0][2].asset_class,
            net_volume=round(volume_netto, 4),
            gross_volume=round(volume_lordo, 4),
            positions=len(voci),
            net_notional=round(nozionale_netto, 2),
            gross_notional=round(nozionale_lordo, 2),
            risk_at_stop=rischio,
            positions_without_stop=senza_stop,
            unrealized_pnl=round(sum(p.net_profit for p, _, _ in voci), 2),
        )

    # ------------------------------------------------------------------
    # Aggregazioni
    # ------------------------------------------------------------------
    def _per_valuta(
        self, per_simbolo: dict[str, list[tuple[PositionSnapshot, _Misura, Instrument]]]
    ) -> tuple[CurrencyExposure, ...]:
        """Esposizione per valuta, sulla coppia **economica** dello strumento.

        Per una coppia forex l'esposizione è **opposta** sulle due valute: un long
        EURUSD è lungo euro e corto dollaro. Trattarle come una sola valuta
        nasconderebbe esattamente il rischio che questa vista deve mostrare.

        Si usa `Instrument.base_currency/quote_currency` e non le valute
        dichiarate da XM: GOLD è dichiarato USD/USD, e con quelle un long oro
        risultava esposizione **nulla** sul dollaro (contributo una volta sola,
        segno sbagliato). Con la coppia economica è lungo XAU e corto USD, come un
        long EURUSD è lungo EUR e corto USD. Correzione del 2026-10-04 (ADR 0014).
        Per un indice la base è l'indice stesso: US100 contro USD.
        """
        netto: dict[str, float] = {}
        lordo: dict[str, float] = {}
        simboli: dict[str, set[str]] = {}

        for simbolo, voci in per_simbolo.items():
            for _, misura, strumento in voci:
                nozionale = (misura.notional or 0.0) * misura.segno
                base = strumento.base_currency or "?"
                quotazione = strumento.quote_currency or "?"

                netto[base] = netto.get(base, 0.0) + nozionale
                lordo[base] = lordo.get(base, 0.0) + abs(nozionale)
                simboli.setdefault(base, set()).add(simbolo)

                if quotazione != base:
                    netto[quotazione] = netto.get(quotazione, 0.0) - nozionale
                    lordo[quotazione] = lordo.get(quotazione, 0.0) + abs(nozionale)
                    simboli.setdefault(quotazione, set()).add(simbolo)

        return tuple(
            CurrencyExposure(
                currency=valuta,
                net_notional=round(valore, 2),
                gross_notional=round(lordo[valuta], 2),
                symbols=tuple(sorted(simboli[valuta])),
            )
            for valuta, valore in sorted(netto.items(), key=lambda kv: -abs(kv[1]))
        )

    @staticmethod
    def _per_classe(esposizioni: Sequence[SymbolExposure]) -> tuple[AssetClassExposure, ...]:
        gruppi: dict[AssetClass, list[SymbolExposure]] = {}
        for esposizione in esposizioni:
            gruppi.setdefault(esposizione.asset_class, []).append(esposizione)

        risultato: list[AssetClassExposure] = []
        for classe, voci in gruppi.items():
            rischi = [v.risk_at_stop for v in voci if v.risk_at_stop is not None]
            completo = all(v.risk_at_stop is not None for v in voci)
            risultato.append(
                AssetClassExposure(
                    asset_class=classe,
                    net_notional=round(sum(v.net_notional for v in voci), 2),
                    gross_notional=round(sum(v.gross_notional for v in voci), 2),
                    net_volume=round(sum(v.net_volume for v in voci), 4),
                    symbols=tuple(sorted(v.symbol for v in voci)),
                    risk_at_stop=round(sum(rischi), 2) if completo and rischi else None,
                )
            )
        return tuple(sorted(risultato, key=lambda e: -abs(e.gross_notional)))
