"""Endpoint di portafoglio: esposizione e storico operazioni — sola lettura.

L'esposizione è calcolata con il valore monetario chiesto al broker, non derivato
da `tick_value` (ADR 0012): su metalli e indici la differenza è del 12,5%.

Lo storico serve al budget di rischio: il PnL realizzato va ricostruito dai deal
del broker, perché un contatore in memoria ripartirebbe da zero a ogni riavvio e
il limite giornaliero dell'utente sarebbe raddoppiabile riavviando il processo.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.deps import ContainerDep, Protected
from backend.core.errors import TradingAgentError
from backend.schemas.exposure import ExposureSnapshot

router = APIRouter(prefix="/portfolio", tags=["Portafoglio"], dependencies=[Protected])


@router.get(
    "/exposure",
    response_model=ExposureSnapshot,
    summary="Esposizione per strumento, valuta e classe di attività",
    description=(
        "Tre viste, non una: per valuta (quanto dipendo dal dollaro), per classe di "
        "attività (quanto dipendo dai metalli) e per strumento. La seconda serve "
        "perché XM dichiara GOLD come USD/USD, quindi la sola valuta metterebbe oro "
        "e indici americani nello stesso secchiello."
    ),
)
async def get_exposure(container: ContainerDep) -> ExposureSnapshot:
    broker = _broker(container)
    calcolatore = container.exposure
    if calcolatore is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=container.broker_error or "Calcolo esposizione non disponibile.",
        )
    try:
        return await calcolatore.build(await broker.get_portfolio())
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get(
    "/realized",
    summary="PnL realizzato e operazioni chiuse",
    description=(
        "Ricostruito dai deal del broker. Solo le uscite realizzano: sommare tutti i "
        "deal conterebbe due volte. I movimenti non di mercato (versamenti, "
        "commissioni periodiche, rettifiche) sono riportati a parte, perché muovono "
        "l'equity ma non sono operazioni."
    ),
)
async def get_realized(
    container: ContainerDep,
    giorni: int = Query(default=7, ge=1, le=365),
) -> dict[str, Any]:
    storico = container.deals
    if storico is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=container.broker_error or "Storico operazioni non disponibile.",
        )

    # L'intervallo si costruisce sull'ora del broker quando è nota: lo storico di
    # MT5 è datato con quella, e usare l'ora locale taglierebbe la giornata nel
    # punto sbagliato (ADR 0010).
    stima = container.broker_clock.estimate()
    adesso = stima.server_time or container.clock.now()
    da = adesso - timedelta(days=giorni)

    try:
        riepilogo = await storico.realized_between(da, adesso)
        operazioni = await storico.closed_trades(da, adesso)
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return {
        "da": riepilogo.since,
        "a": riepilogo.until,
        "ora_broker_attendibile": stima.reliable,
        "fonte_ora_broker": stima.source,
        "pnl_realizzato": riepilogo.realized_pnl,
        "operazioni": riepilogo.trades,
        "vinte": riepilogo.wins,
        "perse": riepilogo.losses,
        "percentuale_vinte": riepilogo.win_rate,
        "movimenti_non_di_mercato": riepilogo.non_trading_flow,
        "deal_esaminati": riepilogo.deals_considered,
        "chiuse": [
            {
                "position_id": t.position_id,
                "simbolo": t.symbol,
                "direzione": t.direction,
                "volume": t.volume,
                "apertura": t.opened_at,
                "chiusura": t.closed_at,
                "prezzo_ingresso": t.entry_price,
                "prezzo_uscita": t.exit_price,
                "risultato_netto": t.net_result,
                "durata_minuti": round(t.duration.total_seconds() / 60, 1),
                "magic": t.magic,
            }
            for t in operazioni
        ],
    }


def _broker(container: ContainerDep):  # type: ignore[no-untyped-def]
    try:
        return container.require_broker()
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
