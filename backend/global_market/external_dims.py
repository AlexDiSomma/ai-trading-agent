"""Dimensioni da fonti esterne o derivate: VIX, tassi USA, DXY ricostruito (fase 4.5).

Funzioni pure. Ricevono solo osservazioni già **disponibili** all'istante di
analisi (`ExternalSeries.available(as_of)`), quindi nessun look-ahead.

Qualità comune alle serie giornaliere esterne:

* `INSUFFICIENT` — fonte assente o storia troppo corta (VIX: un anno, 252 sedute,
  per il percentile; tassi: 21 sedute, per la variazione a 20);
* `DEGRADED` — ultima osservazione disponibile da più di 4 giorni (oltre un fine
  settimana normale);
* `GOOD` — altrimenti.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from backend.core.enums import GlobalDataQuality, StrengthLabel, Timeframe, VolatilityTrend
from backend.global_market import parametri as par
from backend.global_market.currency_strength import etichetta
from backend.global_market.features import SerieDiRiferimento, composito, misura
from backend.global_market.volatility import livello
from backend.intermarket.correlation import allinea, pearson, rendimenti_per_orario
from backend.schemas.external import DailyObservation, YieldCurvePoint
from backend.schemas.global_market import (
    DimensionQuality,
    DollarIndexView,
    ImpliedVolatility,
    RatesView,
)

SEDUTE_ANNO = 252
GIORNI_MASSIMI = 4
SOGLIA_TASSI_BP = 15.0

#: Formula ICE del Dollar Index: costante e esponenti (pesi del paniere del 1973).
DXY_COSTANTE = 50.14348112
DXY_PESI: dict[str, float] = {
    "EURUSD": -0.576,
    "USDJPY": 0.136,
    "GBPUSD": -0.119,
    "USDCAD": 0.091,
    "USDSEK": 0.042,
    "USDCHF": 0.036,
}


def _qualita(
    nome: str,
    n: int,
    ultimo: datetime | None,
    as_of: datetime,
    assente: str | None,
    minimo: int = SEDUTE_ANNO,
) -> DimensionQuality:
    if assente is not None or ultimo is None:
        return DimensionQuality(
            dimension=nome,
            quality=GlobalDataQuality.INSUFFICIENT,
            coverage=0.0,
            reasons=(assente or "Nessuna osservazione disponibile.",),
        )
    if n < minimo:
        return DimensionQuality(
            dimension=nome,
            quality=GlobalDataQuality.INSUFFICIENT,
            coverage=round(n / minimo, 4),
            reasons=(f"Solo {n} osservazioni: ne servono {minimo}.",),
        )
    if as_of - ultimo > timedelta(days=GIORNI_MASSIMI):
        return DimensionQuality(
            dimension=nome,
            quality=GlobalDataQuality.DEGRADED,
            coverage=1.0,
            reasons=(f"Ultima osservazione disponibile dal {ultimo:%d/%m}: dato vecchio.",),
        )
    return DimensionQuality(dimension=nome, quality=GlobalDataQuality.GOOD, coverage=1.0)


# ======================================================================
# VIX
# ======================================================================
def volatilita_implicita(
    osservazioni: Sequence[DailyObservation],
    *,
    as_of: datetime,
    fonte: str,
    assente: str | None = None,
) -> ImpliedVolatility:
    ultimo = osservazioni[-1] if osservazioni else None
    qualita = _qualita(
        "implied_volatility",
        len(osservazioni),
        ultimo.available_at if ultimo else None,
        as_of,
        assente,
    )
    if ultimo is None or qualita.quality is GlobalDataQuality.INSUFFICIENT:
        return ImpliedVolatility(source=fonte, quality=qualita)
    anno = [o.close for o in osservazioni[-SEDUTE_ANNO:]]
    percentile = sum(1 for v in anno if v < ultimo.close) / (len(anno) - 1)
    venti = osservazioni[-21].close if len(osservazioni) > 20 else None
    mediana = sorted(o.close for o in osservazioni[-20:])[10]
    if ultimo.close >= mediana * 1.15:
        tendenza = VolatilityTrend.EXPANDING
    elif ultimo.close <= mediana * 0.85:
        tendenza = VolatilityTrend.COMPRESSING
    else:
        tendenza = VolatilityTrend.STABLE
    return ImpliedVolatility(
        source=fonte,
        value=ultimo.close,
        day=ultimo.day,
        percentile_1y=round(percentile, 4),
        change_20d=round(ultimo.close - venti, 4) if venti else None,
        level=livello(percentile),
        trend=tendenza,
        quality=qualita,
    )


# ======================================================================
# Tassi
# ======================================================================
def tassi(
    punti: Sequence[YieldCurvePoint], *, as_of: datetime, fonte: str, assente: str | None = None
) -> RatesView:
    ultimo = punti[-1] if punti else None
    # Per livello, pendenza e variazione a 20 sedute bastano 21 osservazioni.
    qualita = _qualita(
        "rates", len(punti), ultimo.available_at if ultimo else None, as_of, assente, minimo=21
    )
    if ultimo is None or qualita.quality is GlobalDataQuality.INSUFFICIENT:
        return RatesView(source=fonte, quality=qualita)
    prima = punti[-21] if len(punti) > 20 else None
    variazioni = (
        {
            k: round((v - prima.yields[k]) * 100, 1)
            for k, v in ultimo.yields.items()
            if k in prima.yields
        }
        if prima
        else {}
    )
    y = ultimo.yields
    pendenza_2s10s = round((y["10Y"] - y["2Y"]) * 100, 1)
    pendenza_3m10y = round((y["10Y"] - y["3M"]) * 100, 1)
    delta_10 = variazioni.get("10Y")
    if delta_10 is None:
        direzione = "UNKNOWN"
    elif delta_10 >= SOGLIA_TASSI_BP:
        direzione = "RISING"
    elif delta_10 <= -SOGLIA_TASSI_BP:
        direzione = "FALLING"
    else:
        direzione = "STABLE"
    return RatesView(
        source=fonte,
        day=ultimo.day,
        yields=dict(y),
        change_20d_bp=variazioni,
        slope_2s10s_bp=pendenza_2s10s,
        slope_3m10y_bp=pendenza_3m10y,
        curve_inverted=pendenza_2s10s < 0 or pendenza_3m10y < 0,
        ten_year_trend=direzione,
        quality=qualita,
    )


# ======================================================================
# DXY ricostruito
# ======================================================================
def serie_dxy(
    serie: Mapping[tuple[str, Timeframe], SerieDiRiferimento], tf: Timeframe
) -> SerieDiRiferimento | None:
    """DXY a ogni barra in cui tutti e sei i cambi hanno una chiusura."""
    componenti = {c: serie.get((c, tf)) for c in DXY_PESI}
    if any(s is None for s in componenti.values()):
        return None
    chiusure = {c: dict(zip(s.tempi, s.chiusure, strict=True)) for c, s in componenti.items()}  # type: ignore[union-attr]
    tempi = sorted(set.intersection(*(set(v) for v in chiusure.values())))
    valori = []
    for t in tempi:
        log = math.log(DXY_COSTANTE) + math.fsum(
            peso * math.log(chiusure[c][t]) for c, peso in DXY_PESI.items()
        )
        valori.append(math.exp(log))
    if not valori:
        return None
    # Il DXY non ha massimi e minimi propri: si usano le chiusure (basta al momentum).
    tuplo = tuple(valori)
    return SerieDiRiferimento(
        "DXY", componenti["EURUSD"].group, tf, tuple(tempi), tuplo, tuplo, tuplo
    )  # type: ignore[union-attr]


def indice_dollaro(
    serie: Mapping[tuple[str, Timeframe], SerieDiRiferimento],
    *,
    usd_h1: Sequence[tuple[datetime, float]],
    usd_label: StrengthLabel,
    stati_componenti: Mapping[str, bool],
) -> DollarIndexView:
    mancanti = tuple(c for c in DXY_PESI if not stati_componenti.get(c, False))
    per_tf: dict[Timeframe, float | None] = {}
    ultimo_valore: float | None = None
    serie_h1: SerieDiRiferimento | None = None
    for tf in par.TIMEFRAMES:
        dxy = serie_dxy(serie, tf)
        if dxy is None:
            per_tf[tf] = None
            continue
        per_tf[tf] = misura(dxy).momentum_z
        if tf is Timeframe.H1:
            serie_h1 = dxy
        if tf is Timeframe.M15 or ultimo_valore is None:
            ultimo_valore = round(dxy.chiusure[-1], 4)

    correlazione: float | None = None
    if serie_h1 is not None and usd_h1:
        xs, ys = allinea(rendimenti_per_orario(serie_h1.tempi, serie_h1.chiusure), dict(usd_h1))
        if len(xs) >= par.CAMPIONE_BASE_MINIMO:
            correlazione = pearson(xs, ys)
    punteggio = composito(per_tf)
    label = etichetta(punteggio, per_tf)
    opposti = {StrengthLabel.STRONG: StrengthLabel.WEAK, StrengthLabel.WEAK: StrengthLabel.STRONG}
    d_accordo: bool | None = None
    if label in opposti and usd_label in (
        StrengthLabel.STRONG,
        StrengthLabel.WEAK,
        StrengthLabel.NEUTRAL,
    ):
        d_accordo = usd_label is not opposti[label]

    if mancanti:
        qualita = DimensionQuality(
            dimension="dollar_index",
            quality=GlobalDataQuality.INSUFFICIENT,
            coverage=round(1 - len(mancanti) / len(DXY_PESI), 4),
            reasons=("Componenti mancanti: " + ", ".join(mancanti) + ".",),
        )
    else:
        qualita = DimensionQuality(
            dimension="dollar_index", quality=GlobalDataQuality.GOOD, coverage=1.0
        )
    return DollarIndexView(
        value=ultimo_valore,
        by_timeframe=per_tf,
        score=punteggio,
        label=label,
        correlation_with_usd_strength=round(correlazione, 4) if correlazione is not None else None,
        agrees_with_usd_strength=d_accordo,
        missing_components=mancanti,
        quality=qualita,
    )
