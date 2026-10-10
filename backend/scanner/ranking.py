"""Market scanner: graduatoria degli strumenti autorizzati, con i fattori leggibili.

I fattori restano separati accanto al punteggio (ANALISI §2.4): chi legge la
dashboard deve capire *perché* uno strumento è in cima. Pesi provvisori e
dichiarati; uno strumento con dati non utilizzabili non entra in graduatoria ma
resta elencato con il motivo. Una lista vuota è un esito valido.
"""

from __future__ import annotations

from typing import Any

from backend.config.risk_limits import RiskLimits
from backend.core.enums import Timeframe
from backend.schemas.context import AssetMarketContext

PESI = {"allineamento": 0.4, "tendenza_h1": 0.3, "costo": 0.2, "coerenza": 0.1}


def classifica(contesti: dict[str, AssetMarketContext], limiti: RiskLimits) -> list[dict[str, Any]]:
    righe: list[dict[str, Any]] = []
    for simbolo, ctx in contesti.items():
        h1 = ctx.timeframes.get(Timeframe.H1)
        m = ctx.multi_timeframe
        spread = ctx.quote.spread_atr_ratio
        fattori = {
            "allineamento": round(abs(m.alignment), 3),
            "tendenza_h1": round(abs(h1.trend.score), 3) if h1 else 0.0,
            "costo": round(1 - min((spread or 0) / limiti.max_spread_atr_ratio, 1.0), 3),
            "coerenza": 0.0 if m.structure_execution_conflict else 1.0,
        }
        punteggio = round(sum(PESI[k] * v for k, v in fattori.items()), 4)
        bloccanti = list(ctx.blocking_reasons)
        if ctx.global_backdrop and ctx.global_backdrop.event_windows:
            bloccanti.append("Finestra evento ad alto impatto.")
        righe.append(
            {
                "symbol": simbolo,
                "canonical": ctx.instrument.canonical if ctx.instrument else simbolo,
                "score": punteggio,
                "direction_hint": {1: "LONG", -1: "SHORT"}.get(m.structural_bias, "NONE"),
                "factors": fattori,
                "regime": str(m.dominant_regime),
                "alignment": m.alignment,
                "quality": str(ctx.quality),
                "quote_state": str(ctx.quote.state),
                "spread_atr": spread,
                "tradeable": not bloccanti,
                "blocking_reasons": bloccanti,
            }
        )
    return sorted(righe, key=lambda r: (not r["tradeable"], -r["score"]))
