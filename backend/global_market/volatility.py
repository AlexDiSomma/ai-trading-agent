"""Regime di volatilità: livello, tendenza, shock.

Tre misure per strumento e timeframe, tutte relative alla storia dello strumento
stesso (un ATR di 0.0008 non dice niente; essere al 90° percentile sì):

* percentile dell'ATR(14) sulle ultime 250 barre;
* percentile dell'ampiezza di Bollinger sulle ultime 120;
* rapporto fra volatilità realizzata breve (10 barre) e lunga (50): sopra 1.20
  espansione, sotto 0.85 compressione.

Aggregato: mediana fra strumenti — la mediana e non la media, perché un singolo
strumento impazzito non deve dichiarare in tempesta tutto il mercato. Lo
**shock** è il caso opposto e si misura a parte: molti strumenti *insieme* oltre
il 97° percentile dell'ATR veloce.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.config.global_universe import UNIVERSO
from backend.core.enums import ReferenceGroup, Timeframe, VolatilityLevel, VolatilityTrend
from backend.global_market import parametri as par
from backend.global_market.currency_strength import Misurazioni
from backend.global_market.features import mediana
from backend.global_market.qualita import Stati, qualita_dimensione
from backend.schemas.global_market import AssetVolatility, VolatilityView


def livello(percentile: float | None) -> VolatilityLevel:
    if percentile is None:
        return VolatilityLevel.UNKNOWN
    if percentile >= par.LIVELLO_ESTREMO:
        return VolatilityLevel.EXTREME
    if percentile >= par.LIVELLO_ELEVATO:
        return VolatilityLevel.ELEVATED
    if percentile <= par.LIVELLO_BASSO:
        return VolatilityLevel.LOW
    return VolatilityLevel.NORMAL


def tendenza(rapporto: float | None) -> VolatilityTrend:
    if rapporto is None:
        return VolatilityTrend.UNKNOWN
    if rapporto >= par.SOGLIA_ESPANSIONE:
        return VolatilityTrend.EXPANDING
    if rapporto <= par.SOGLIA_COMPRESSIONE:
        return VolatilityTrend.COMPRESSING
    return VolatilityTrend.STABLE


def _percentile_corrente(per_tf: dict[Timeframe, float | None]) -> float | None:
    """Percentile "di adesso": media di H4 e H1 dove disponibili."""
    valori = [v for tf in (Timeframe.H4, Timeframe.H1) if (v := per_tf.get(tf)) is not None]
    return round(sum(valori) / len(valori), 6) if valori else None


def _rapporto_corrente(per_tf: dict[Timeframe, float | None]) -> float | None:
    return (
        per_tf.get(Timeframe.H1)
        if per_tf.get(Timeframe.H1) is not None
        else per_tf.get(Timeframe.H4)
    )


def _aggrega(assets: Sequence[AssetVolatility]) -> tuple[dict, dict, dict]:  # type: ignore[type-arg]
    atr = {tf: mediana([a.atr_percentile.get(tf) for a in assets]) for tf in par.TIMEFRAMES}
    bb = {
        tf: mediana([a.bollinger_width_percentile.get(tf) for a in assets]) for tf in par.TIMEFRAMES
    }
    rv = {tf: mediana([a.realized_vol_ratio.get(tf) for a in assets]) for tf in par.TIMEFRAMES}
    return atr, bb, rv


def regime_volatilita(misure: Misurazioni, stati: Stati) -> VolatilityView:
    assets: list[AssetVolatility] = []
    for s in UNIVERSO:
        per = misure.get(s.canonical)
        if not per:
            continue
        rv = {tf: (per[tf].rapporto_volatilita if tf in per else None) for tf in par.TIMEFRAMES}
        assets.append(
            AssetVolatility(
                canonical=s.canonical,
                group=s.group,
                atr_percentile={
                    tf: (per[tf].atr_percentile if tf in per else None) for tf in par.TIMEFRAMES
                },
                bollinger_width_percentile={
                    tf: (per[tf].bollinger_percentile if tf in per else None)
                    for tf in par.TIMEFRAMES
                },
                realized_vol_ratio=rv,
                realized_vol={
                    tf: (per[tf].volatilita_barra if tf in per else None) for tf in par.TIMEFRAMES
                },
                trend=tendenza(_rapporto_corrente(rv)),
            )
        )

    atr, bb, rv = _aggrega(assets)
    motivi: list[str] = []

    per_gruppo: dict[str, VolatilityLevel] = {}
    tendenza_gruppo: dict[str, VolatilityTrend] = {}
    for gruppo in ReferenceGroup:
        membri = [a for a in assets if a.group is gruppo]
        if not membri:
            continue
        g_atr, _, g_rv = _aggrega(membri)
        per_gruppo[str(gruppo)] = livello(_percentile_corrente(g_atr))
        tendenza_gruppo[str(gruppo)] = tendenza(_rapporto_corrente(g_rv))

    # Shock: quota di strumenti con ATR veloce (M15 o H1) oltre il 97° percentile.
    con_dati = [
        a
        for a in assets
        if a.atr_percentile.get(Timeframe.M15) is not None
        or a.atr_percentile.get(Timeframe.H1) is not None
    ]
    in_shock = [
        a.canonical
        for a in con_dati
        if max(
            a.atr_percentile.get(Timeframe.M15) or 0.0, a.atr_percentile.get(Timeframe.H1) or 0.0
        )
        >= par.PERCENTILE_SHOCK
    ]
    shock = len(con_dati) >= 5 and len(in_shock) / len(con_dati) >= par.QUOTA_SHOCK
    if shock:
        motivi.append(
            f"Shock di volatilità: {len(in_shock)} strumenti su {len(con_dati)} oltre il "
            f"{par.PERCENTILE_SHOCK:.0%} percentile dell'ATR veloce."
        )

    corrente = _percentile_corrente(atr)
    lvl = livello(corrente)
    trd = tendenza(_rapporto_corrente(rv))
    if corrente is not None:
        motivi.append(f"Percentile ATR mediano (H4/H1): {corrente:.2f}.")
    if lvl is VolatilityLevel.UNKNOWN:
        motivi.append("Storia insufficiente per i percentili di volatilità (servono 260 barre).")

    return VolatilityView(
        level=lvl,
        trend=trd,
        shock=shock,
        median_atr_percentile=atr,
        median_bollinger_width_percentile=bb,
        median_realized_vol_ratio=rv,
        by_group=per_gruppo,
        trend_by_group=tendenza_gruppo,
        assets=tuple(assets),
        quality=qualita_dimensione("volatility", [s.canonical for s in UNIVERSO], stati),
        reasons=tuple(motivi),
    )
