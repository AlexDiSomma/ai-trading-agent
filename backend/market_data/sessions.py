"""Stato delle quotazioni e ora del server del broker.

**Scoperta che cambia il disegno (ADR 0011).** L'API Python di MetaTrader 5 non
espone gli orari di sessione: `symbol_info_sessions_quote` e
`symbol_info_sessions_trade` esistono solo in MQL5, e sul pacchetto Python non ci
sono. Non esiste nemmeno una funzione per l'ora corrente del server. In più i
campi `session_open` e `session_close` di `symbol_info` sono **prezzi**, non
orari: un nome che invita all'errore.

Conseguenza: "mercato chiuso" non può essere letto dal broker, va **dedotto**. Il
modulo tiene quindi separate due categorie di informazione:

* **fatti del broker** — `trade_mode` (vietato / solo chiusura / pieno), bid e ask
  a zero, età dell'ultima quotazione;
* **deduzioni nostre** — "il mercato è chiuso", marcata con `is_inference=True` e
  accompagnata dalle prove su cui si basa.

Chi legge uno `QuoteAssessment` sa sempre quale delle due sta guardando. È la
differenza fra "il broker dice che non si può operare" e "crediamo che il mercato
sia chiuso", e in un sistema che muove denaro non è una sfumatura.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from backend.core.enums import (
    MarketPhase,
    MarketStatus,
    QuoteQualityStatus,
    QuoteState,
    Timeframe,
)
from backend.core.logging import get_logger
from backend.market_data.broker_time import BrokerClock, BrokerTimeEstimate
from backend.market_data.quote_quality import (
    SOGLIA_MERCATO_CHIUSO_SECONDI,
    QuoteQualityEvaluator,
)
from backend.schemas.market import SymbolInfo, Tick
from backend.schemas.quote import QuoteQuality

logger = get_logger(__name__)


__all__ = [
    "SOGLIA_MERCATO_CHIUSO_SECONDI",
    "BrokerClock",
    "BrokerTimeEstimate",
    "QuoteAssessment",
    "QuoteStateDetector",
    "fase_di_mercato",
]

#: Quota di strumenti con quotazione valida sopra la quale il mercato si
#: considera aperto.
QUOTA_APERTO = 0.6


# ======================================================================
# Stato delle quotazioni
# ======================================================================
@dataclass(frozen=True, slots=True)
class QuoteAssessment:
    """Stato operativo di una quotazione: qualità del dato più politica di rischio.

    `quality` è la qualità del dato (`QuoteQuality`), calcolata in un punto solo;
    `state` aggiunge ciò che dice il broker sull'operatività (`trade_mode`) e il
    giudizio sullo spread.
    """

    symbol: str
    state: QuoteState
    #: Motivo in italiano, pronto per dashboard e diario.
    reason: str
    #: True quando lo stato è una **deduzione** nostra e non un dato del broker.
    is_inference: bool
    tick_age_seconds: float | None = None
    spread_points: int | None = None
    last_bar_age_seconds: float | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    quality: QuoteQuality | None = None

    @property
    def allows_new_positions(self) -> bool:
        return self.state.allows_new_positions

    @property
    def market_status(self) -> MarketStatus:
        return self.quality.market_status if self.quality else MarketStatus.UNKNOWN


#: Da qualità del dato a stato operativo. VALID non compare: dipende dallo spread.
_DA_QUALITA: dict[QuoteQualityStatus, QuoteState] = {
    QuoteQualityStatus.MISSING: QuoteState.NO_QUOTE,
    QuoteQualityStatus.INCONSISTENT: QuoteState.INCONSISTENT,
    QuoteQualityStatus.MARKET_CLOSED: QuoteState.MARKET_CLOSED,
    QuoteQualityStatus.STALE: QuoteState.STALE,
}


class QuoteStateDetector:
    """Stato operativo di una quotazione.

    Non ricalcola età né freschezza: le chiede a `QuoteQualityEvaluator`, unico
    punto in cui si decide se un prezzo è un dato valido. Qui si aggiungono solo i
    fatti del broker sull'operatività (`trade_mode`) e la politica sullo spread
    (ADR 0013). L'ordine va dal più accertato al più dedotto.
    """

    def __init__(
        self,
        *,
        max_tick_age_seconds: float,
        max_spread_points: float,
        max_spread_atr_ratio: float = 0.30,
        soglia_mercato_chiuso: float = SOGLIA_MERCATO_CHIUSO_SECONDI,
        broker_clock: BrokerClock | None = None,
    ) -> None:
        self._valutatore = QuoteQualityEvaluator(
            max_tick_age_seconds=max_tick_age_seconds,
            soglia_mercato_chiuso=soglia_mercato_chiuso,
        )
        self._max_spread = max_spread_points
        self._max_spread_atr = max_spread_atr_ratio
        self._broker_clock = broker_clock

    def assess(
        self,
        info: SymbolInfo,
        tick: Tick | None,
        *,
        now: datetime,
        last_bar_time: datetime | None = None,
        last_bar_timeframe: Timeframe = Timeframe.M5,
        quote_missing: bool = False,
        atr: float | None = None,
        last_close: float | None = None,
    ) -> QuoteAssessment:
        """Valuta lo stato. `atr` è quello del timeframe operativo (ADR 0013)."""
        qualita = self._valutatore.evaluate(
            info,
            tick,
            now=now,
            broker_clock=self._broker_clock,
            last_candle_time=last_bar_time,
            last_candle_timeframe=last_bar_timeframe,
            atr=atr,
            last_close=last_close,
            quote_missing=quote_missing,
        )
        prove: dict[str, object] = {
            "trade_mode": info.trade_mode,
            "visibile": info.visible,
            "qualita": str(qualita.status),
            "mercato": str(qualita.market_status),
        }
        if qualita.spread_to_atr is not None:
            prove["spread_su_atr"] = qualita.spread_to_atr
        comune = {
            "symbol": info.name,
            "tick_age_seconds": qualita.tick_age_seconds,
            "spread_points": info.spread_points,
            "last_bar_age_seconds": qualita.candle_age_seconds,
            "evidence": prove,
            "quality": qualita,
        }

        # --- 1. fatti del broker sull'operatività ---
        if info.trade_mode == 0:
            return QuoteAssessment(
                state=QuoteState.NOT_TRADEABLE,
                reason="Il broker ha disabilitato le operazioni su questo strumento.",
                is_inference=False,
                **comune,  # type: ignore[arg-type]
            )
        if info.close_only:
            return QuoteAssessment(
                state=QuoteState.CLOSE_ONLY,
                reason="Il broker consente solo la chiusura delle posizioni esistenti.",
                is_inference=False,
                **comune,  # type: ignore[arg-type]
            )

        # --- 2. qualità del dato ---
        if qualita.status is not QuoteQualityStatus.VALID:
            return QuoteAssessment(
                state=_DA_QUALITA[qualita.status],
                reason=" ".join(qualita.reasons),
                is_inference=qualita.is_inference,
                **comune,  # type: ignore[arg-type]
            )

        # --- 3. politica sullo spread ---
        rapporto = qualita.spread_to_atr
        if rapporto is not None and rapporto > self._max_spread_atr:
            return QuoteAssessment(
                state=QuoteState.WIDE_SPREAD,
                reason=(
                    f"Spread pari al {rapporto * 100:.0f}% dell'ATR (limite "
                    f"{self._max_spread_atr * 100:.0f}%): il costo d'ingresso si mangia "
                    "il movimento atteso."
                ),
                is_inference=False,
                **comune,  # type: ignore[arg-type]
            )
        if rapporto is None and info.spread_points > self._max_spread:
            return QuoteAssessment(
                state=QuoteState.WIDE_SPREAD,
                reason=(
                    f"Spread di {info.spread_points} punti oltre il tetto assoluto di "
                    f"{self._max_spread:.0f}, e ATR non calcolabile per il confronto relativo."
                ),
                is_inference=False,
                **comune,  # type: ignore[arg-type]
            )

        dettaglio = (
            f"spread {info.spread_points} punti ({rapporto * 100:.0f}% dell'ATR)"
            if rapporto is not None
            else f"spread {info.spread_points} punti"
        )
        return QuoteAssessment(
            state=QuoteState.TRADEABLE,
            reason=f"Quotazione valida, {qualita.tick_age_seconds:.0f}s, {dettaglio}.",
            is_inference=False,
            **comune,  # type: ignore[arg-type]
        )


def fase_di_mercato(
    valutazioni: Sequence[QuoteAssessment],
) -> tuple[MarketPhase, str]:
    """Fase complessiva del mercato, dall'insieme degli strumenti.

    Usa lo **stato di mercato** delle singole quotazioni, non la loro operatività:
    uno spread caro non dice nulla sul fatto che il mercato sia aperto. E conserva
    l'asimmetria fra conferma e deduzione: la fase è `CLOSED_INFERRED`, mai
    "chiusa" e basta.

    Guardare un solo strumento non basta: su XM gli indici chiudono mentre il forex
    continua. Quando si fermano tutti insieme, il messaggio non sceglie fra mercato
    chiuso e connessione caduta: dal lato nostro sono indistinguibili.
    """
    if not valutazioni:
        return MarketPhase.UNKNOWN, "Nessuno strumento valutato."

    totale = len(valutazioni)
    aperti = sum(1 for v in valutazioni if v.market_status is MarketStatus.OPEN_CONFIRMED)
    chiusi = sum(1 for v in valutazioni if v.market_status is MarketStatus.CLOSED_INFERRED)

    if aperti / totale >= QUOTA_APERTO:
        return (
            MarketPhase.OPEN_CONFIRMED,
            f"{aperti} strumenti su {totale} con quotazione fresca confermata.",
        )
    if chiusi == totale:
        return (
            MarketPhase.CLOSED_INFERRED,
            f"Tutti i {totale} strumenti hanno quotazioni ferme: mercato dedotto chiuso "
            "oppure connessione al broker interrotta (indistinguibili da qui).",
        )
    if aperti > 0:
        return (
            MarketPhase.PARTIALLY_OPEN,
            f"Solo {aperti} strumenti su {totale} con quotazione fresca: alcuni mercati "
            "risultano chiusi o fermi.",
        )
    return (
        MarketPhase.UNKNOWN,
        f"Nessuna quotazione fresca confermata e {totale - chiusi} strumenti non "
        "deducibili come chiusi: stato del mercato non determinabile.",
    )
