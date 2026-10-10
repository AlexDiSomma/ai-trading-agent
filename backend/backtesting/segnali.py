"""Segnali QUANT nel passato, con lo **stesso** codice del paper.

A ogni istante di decisione `T` (chiusura di una barra H1) si tagliano le candele di
ogni timeframe d'analisi alle sole barre **chiuse entro T**, le ultime
`BARRE_RICHIESTE[tf]` come nel live, e si chiama `AssetAnalyst.analyse_replay` →
`costruisci_catalogo` → `piano_quant`: nessuna logica copiata, nessuna barra futura.

Differenza dichiarata dal live: il paper valuta ogni 5 minuti, qui ogni ora. QUANT
dipende soprattutto da D1/H4/H1 (80% del peso); M15 e M5 spostano l'allineamento al
margine, quindi alcuni ingressi del live arriverebbero prima o non arriverebbero.

I segnali dipendono solo da dati e versione della regola: si salvano su file e le
varianti di gestione li riusano senza ricalcolarli.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.backtesting.storico import Barra
from backend.core.enums import DataQuality, Timeframe
from backend.core.freshness import Fresh
from backend.market_data.ingestion import (
    BARRE_MINIME_ASSOLUTE,
    BARRE_PER_EMA200,
    BARRE_RICHIESTE,
    TIMEFRAMES_ANALISI,
    TimeframeCandles,
)
from backend.schemas.market import Candle, CandleSeries
from backend.services.asset_analysis import AssetAnalyst
from backend.shadow.evidence import costruisci_catalogo
from backend.shadow.quant_baseline import QUANT_VERSION, piano_quant


@dataclass(frozen=True, slots=True)
class Segnale:
    simbolo: str
    canonico: str
    #: Istante della decisione, UTC.
    t: datetime
    verso: int
    #: Prezzo di riferimento (ultima chiusura M5), da cui stop e target del piano.
    riferimento: float
    stop: float
    target: float
    atr_h1: float
    #: Livello d'invalidazione (chiusura H1 oltre), se il piano ne ha uno.
    invalidazione: float | None
    #: Ultimo swing H1 opposto, per le varianti con stop diverso.
    swing: float | None
    spread_atr: float | None
    punto: float
    cifre: int


def _qualita(n: int) -> DataQuality:
    if n < BARRE_MINIME_ASSOLUTE:
        return DataQuality.INSUFFICIENT
    if n < BARRE_PER_EMA200:
        return DataQuality.SHORT_HISTORY
    return DataQuality.GOOD


class Tagliatore:
    """Candele di un simbolo pronte da tagliare a qualunque istante in O(log n)."""

    def __init__(self, simbolo: str, per_tf: dict[Timeframe, list[Barra]]) -> None:
        self.simbolo = simbolo
        self._candele: dict[Timeframe, list[Candle]] = {}
        self._chiusure: dict[Timeframe, list[datetime]] = {}
        for tf, barre in per_tf.items():
            durata = timedelta(minutes=tf.minutes)
            self._candele[tf] = [
                Candle(time=b.t, open=b.o, high=b.h, low=b.l, close=b.c, spread=b.spread)
                for b in barre
            ]
            self._chiusure[tf] = [b.t + durata for b in barre]

    def taglia(self, limite_broker: datetime) -> dict[Timeframe, TimeframeCandles]:
        esito: dict[Timeframe, TimeframeCandles] = {}
        for tf in TIMEFRAMES_ANALISI:
            fine = bisect.bisect_right(self._chiusure[tf], limite_broker)
            tenute = tuple(self._candele[tf][max(0, fine - BARRE_RICHIESTE[tf]) : fine])
            serie = CandleSeries(
                symbol=self.simbolo,
                timeframe=tf,
                candles=tenute,
                as_of=limite_broker,
                source="backtest",
                last_candle_closed=True,
            )
            esito[tf] = TimeframeCandles(
                timeframe=tf,
                fresh=Fresh(value=serie, as_of=limite_broker, ttl_seconds=0, source="backtest"),
                quality=_qualita(len(tenute)),
            )
        return esito

    def ultima_chiusura_m5(self, limite_broker: datetime) -> datetime | None:
        i = bisect.bisect_right(self._chiusure[Timeframe.M5], limite_broker)
        return self._chiusure[Timeframe.M5][i - 1] if i else None


async def segnali_simbolo(
    analista: AssetAnalyst,
    tagliatore: Tagliatore,
    istanti_utc: list[datetime],
    scarto: timedelta,
    *,
    punto: float,
    cifre: int,
) -> list[Segnale]:
    esiti: list[Segnale] = []
    for t in istanti_utc:
        limite = t + scarto
        ultima = tagliatore.ultima_chiusura_m5(limite)
        # Mercato chiuso: nessuna barra M5 negli ultimi 10 minuti, nessuna decisione.
        if ultima is None or limite - ultima > timedelta(minutes=10):
            continue
        candele = tagliatore.taglia(limite)
        ctx = await analista.analyse_replay(tagliatore.simbolo, candele, as_of=t)
        if ctx.instrument is None:
            continue
        m5 = candele[Timeframe.M5].series.closed
        catalogo = costruisci_catalogo(
            ctx, ctx.instrument.canonical, prezzo=m5[-1].close if m5 else None
        )
        piano = piano_quant(ctx, catalogo, decided_at=t, mode="REPLAY")
        if piano.status != "ACTIVE" or piano.stop_loss is None or piano.take_profit is None:
            continue
        verso = 1 if piano.decision == "LONG" else -1
        struttura = ctx.timeframes[Timeframe.H1].structure
        swing = struttura.last_swing_low if verso > 0 else struttura.last_swing_high
        inval = piano.invalidation_conditions[0].level if piano.invalidation_conditions else None
        esiti.append(
            Segnale(
                simbolo=tagliatore.simbolo,
                canonico=ctx.instrument.canonical,
                t=t,
                verso=verso,
                riferimento=catalogo.prezzo,
                stop=piano.stop_loss,
                target=piano.take_profit,
                atr_h1=catalogo.atr_h1 or 0.0,
                invalidazione=inval,
                swing=swing,
                spread_atr=ctx.quote.spread_atr_ratio,
                punto=punto,
                cifre=cifre,
            )
        )
    return esiti


def salva_segnali(file: Path, segnali: list[Segnale], intestazione: dict[str, str]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    righe = [{**asdict(s), "t": s.t.isoformat()} for s in segnali]
    file.write_text(
        json.dumps({"versione": QUANT_VERSION, **intestazione, "segnali": righe}),
        encoding="utf-8",
    )


def carica_segnali(file: Path, intestazione: dict[str, str]) -> list[Segnale] | None:
    """Segnali salvati, se calcolati con la stessa regola sugli stessi dati."""
    if not file.exists():
        return None
    dati = json.loads(file.read_text(encoding="utf-8"))
    if dati.get("versione") != QUANT_VERSION or any(
        dati.get(k) != v for k, v in intestazione.items()
    ):
        return None
    return [
        Segnale(**{**r, "t": datetime.fromisoformat(r["t"]).astimezone(UTC)})
        for r in dati["segnali"]
    ]
