"""Endpoint di conto e posizioni — sola lettura.

Qui non esiste nessun endpoint che apra, modifichi o chiuda una posizione, e non
esisterà prima della fase 11. Una rotta di scrittura aggiunta "per provare"
sarebbe il modo più banale di aggirare il Risk Engine.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from backend.api.deps import ContainerDep, Protected
from backend.core.errors import TradingAgentError
from backend.schemas.account import AccountSnapshot, PortfolioSnapshot, PositionSnapshot

router = APIRouter(prefix="/account", tags=["Conto"], dependencies=[Protected])


def _broker_or_503(container: ContainerDep):  # type: ignore[no-untyped-def]
    try:
        return container.require_broker()
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get(
    "",
    response_model=AccountSnapshot,
    summary="Stato del conto",
    description=(
        "Saldo, equity, margine e leva come li dichiara il broker. "
        "Questi valori sono l'unica fonte di verità sul capitale: il sistema non "
        "tiene un proprio saldo."
    ),
)
async def get_account(container: ContainerDep) -> AccountSnapshot:
    return await _broker_or_503(container).get_account()


@router.get(
    "/positions",
    response_model=list[PositionSnapshot],
    summary="Posizioni aperte",
)
async def get_positions(
    container: ContainerDep, symbol: str | None = None
) -> list[PositionSnapshot]:
    positions = await _broker_or_503(container).get_positions(symbol)
    return list(positions)


@router.get(
    "/portfolio",
    response_model=PortfolioSnapshot,
    summary="Conto e posizioni nello stesso istante",
    description=(
        "Letti insieme perché il rischio calcolato su un'equity di adesso e "
        "posizioni di dieci secondi fa descrive una situazione mai esistita."
    ),
)
async def get_portfolio(container: ContainerDep) -> PortfolioSnapshot:
    return await _broker_or_503(container).get_portfolio()
