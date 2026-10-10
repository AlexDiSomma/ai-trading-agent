"""Storico per il backtest: lettura a blocchi dal broker (sola lettura) e cache su file.

MT5 restituisce al massimo `maxbars` barre per timeframe (100.000 su questo terminale:
le M5 arrivano a circa 16 mesi, le M1 a 3) e il connettore al massimo 5.000 per
chiamata: si legge all'indietro a blocchi usando come fine l'apertura più vecchia del
blocco precedente. Gli orari restano quelli del broker, come li dà MT5: la conversione
in UTC si fa una volta, con lo scarto misurato (ADR 0015).

La cache (`var/backtest/cache`) evita di riscaricare a ogni esecuzione; si rinnova
con `--aggiorna`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.brokers.base import ReadOnlyBroker
from backend.core.enums import Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.schemas.market import Candle

logger = get_logger(__name__)

BLOCCO = 5_000
TENTATIVI = 3
#: Barre massime per timeframe (il terminale ne conserva 100.000).
MASSIMO = 99_000


class StoricoNonDisponibileError(TradingAgentError):
    """Il broker non ha dato barre: il simbolo va escluso, mai simulato su dati vuoti."""


@dataclass(frozen=True, slots=True)
class Barra:
    """Barra compatta in ora **broker** (come MT5): apertura, OHLC, spread in punti."""

    t: datetime
    o: float
    h: float
    l: float  # noqa: E741 — OHLC
    c: float
    spread: int


async def scarica(
    broker: ReadOnlyBroker, simbolo: str, tf: Timeframe, massimo: int = MASSIMO
) -> list[Barra]:
    """Barre chiuse, dalla più vecchia alla più recente, fino a `massimo`."""
    per_tempo: dict[datetime, Barra] = {}
    fine: datetime | None = None
    while len(per_tempo) < massimo:
        serie = None
        for tentativo in range(1, TENTATIVI + 1):
            try:
                serie = await broker.get_candles(simbolo, tf, BLOCCO, end=fine)
                break
            except TradingAgentError as errore:
                # Un errore passeggero del terminale non è "storico finito": il 9
                # ottobre ha svuotato le D1 di GBPUSD e azzerato i suoi segnali.
                logger.warning(
                    "backtest.lettura_fallita",
                    simbolo=simbolo,
                    tf=str(tf),
                    tentativo=tentativo,
                    motivo=str(errore)[:200],
                )
                await asyncio.sleep(1.0 * tentativo)
        if serie is None:
            if not per_tempo:
                raise StoricoNonDisponibileError(f"{simbolo} {tf}: nessuna barra dal broker.")
            break  # oltre l'inizio dello storico disponibile
        nuove = [c for c in serie.closed if c.time not in per_tempo]
        if not nuove:
            break
        for c in nuove:
            per_tempo[c.time] = _da_candela(c)
        fine = min(c.time for c in serie.closed) - timedelta(seconds=1)
    barre = sorted(per_tempo.values(), key=lambda b: b.t)
    return barre[-massimo:]


def _da_candela(c: Candle) -> Barra:
    return Barra(c.time, c.open, c.high, c.low, c.close, c.spread)


def salva(file: Path, barre: list[Barra]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    righe = [[int(b.t.timestamp()), b.o, b.h, b.l, b.c, b.spread] for b in barre]
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(righe), encoding="utf-8")
    tmp.replace(file)


def carica(file: Path) -> list[Barra]:
    return [
        Barra(datetime.fromtimestamp(r[0], UTC), r[1], r[2], r[3], r[4], int(r[5]))
        for r in json.loads(file.read_text(encoding="utf-8"))
    ]


async def storico(
    broker: ReadOnlyBroker,
    cartella: Path,
    simbolo: str,
    tf: Timeframe,
    *,
    aggiorna: bool = False,
) -> list[Barra]:
    file = cartella / f"{simbolo}_{tf}.json"
    if file.exists() and not aggiorna:
        barre = carica(file)
        if barre:
            return barre
    barre = await scarica(broker, simbolo, tf)
    salva(file, barre)
    logger.info("backtest.storico", simbolo=simbolo, tf=str(tf), barre=len(barre))
    return barre
