"""Endpoint di analisi di mercato — sola lettura.

Restituiscono descrizioni, non proposte: `AssetMarketContext` dice com'è il
mercato, non cosa fare. Entry, stop e direzioni arrivano con la fase 7, e tenere
separate le due cose è ciò che permette al Risk Engine di restare l'unico varco
verso l'esecuzione.

Ogni risposta porta `usable_for_decisions` e, se falso, i motivi. Un consumatore
non deve dedurre l'affidabilità dai valori: gliela diciamo.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.deps import ContainerDep, Protected
from backend.core.enums import Timeframe
from backend.core.errors import SymbolNotAllowedError, TradingAgentError
from backend.market_data.ingestion import TIMEFRAMES_ANALISI
from backend.schemas.context import AssetMarketContext, MarketSnapshot

router = APIRouter(prefix="/analysis", tags=["Analisi di mercato"], dependencies=[Protected])


@router.get(
    "/symbols",
    summary="Registro dei simboli e verifica della whitelist",
    description=(
        "Stato del catalogo del broker e controllo della whitelist dell'utente. "
        "Una voce sbagliata in RISK_ALLOWED_SYMBOLS non produce errori: produce "
        "uno strumento che il sistema non guarda mai. Qui quell'assenza diventa "
        "un messaggio con i candidati probabili — che **non** vengono sostituiti "
        "automaticamente."
    ),
)
async def get_registry(container: ContainerDep) -> dict[str, Any]:
    registry = _oppure_503(container, container.require_registry)
    await registry.ensure_loaded()
    limiti = container.settings.risk
    problemi = registry.check_whitelist(limiti.allowed_symbols)

    return {
        "simboli_presso_il_broker": len(registry.symbols),
        "catalogo_caricato_il": registry.loaded_at,
        "whitelist": [
            {
                "richiesto": voce,
                "risolto": (esito := registry.resolve(voce)).canonical,
                "corrispondenza": str(esito.match),
                "utilizzabile": esito.usable,
                "candidati": list(esito.candidates),
                "nota": esito.note,
            }
            for voce in limiti.allowed_symbols
        ],
        "problemi": [
            {
                "richiesto": p.requested,
                "gravita": p.severity,
                "messaggio": p.message,
                "candidati": list(p.suggestions),
            }
            for p in problemi
        ],
        "nota": (
            "Gli alias sono suggerimenti: il sistema non opera su uno strumento "
            "diverso da quello che hai autorizzato."
        ),
    }


@router.get(
    "/specifications/{symbol}",
    summary="Specifiche di contratto e valore monetario",
    description=(
        "Include `value_per_price_unit`: il valore in valuta del conto di un "
        "movimento di 1.0 di prezzo per 1 lotto, **calcolato dal broker**. "
        "Non viene derivato da `tick_value`, che su questo conto XM in EUR è in "
        "EUR per il forex ma in USD per metalli e indici (ADR 0012)."
    ),
)
async def get_specifications(symbol: str, container: ContainerDep) -> dict[str, Any]:
    repository = _oppure_503(container, lambda: container.specifications)
    market = _oppure_503(container, container.require_market_data)
    canonico = market.canonical_symbol(symbol)
    if canonico is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{symbol}' non è fra gli strumenti autorizzati.",
        )
    try:
        fresh = await repository.get(canonico)
        strumenti = container.require_instruments()
        valutazione = container.require_valuation()
        strumento = await strumenti.by_broker_symbol(canonico)
        tick = (await market.get_tick(canonico)).value
        scarto = await valutazione.tick_value_gap(strumento, fresh.value, tick.mid)
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    info = fresh.value
    return {
        "strumento": strumento.model_dump(mode="json"),
        "specifiche": info.model_dump(mode="json"),
        "classe_attivita": str(strumento.asset_class),
        "percorso_broker": info.path,
        "valore_unita_prezzo": scarto.from_broker,
        "valore_da_tick_value": scarto.from_tick_value,
        "scarto_percentuale": scarto.gap_pct,
        "nota": (
            "Lo scarto fra i due valori è la ragione per cui ogni calcolo monetario "
            "passa da BrokerValuationService (ADR 0014): su metalli e indici "
            "tick_value non è nella valuta del conto."
        ),
        "as_of": fresh.as_of,
        "cambiamenti_rilevati": [str(c) for c in repository.changes if c.symbol == canonico],
    }


@router.get(
    "/asset/{symbol}",
    response_model=AssetMarketContext,
    summary="Contesto di mercato di uno strumento",
    description=(
        "Analisi multi-timeframe D1 → H4 → H1 → M15 → M5 con indicatori, struttura, "
        "livelli, rotture e classificazione della tendenza. Descrive il mercato: "
        "non contiene entry, stop né direzioni suggerite."
    ),
)
async def get_asset_context(
    symbol: str,
    container: ContainerDep,
    timeframe: list[Timeframe] | None = Query(default=None),
) -> AssetMarketContext:
    analyst = _oppure_503(container, container.require_analyst)
    market = _oppure_503(container, container.require_market_data)
    canonico = market.canonical_symbol(symbol)
    if canonico is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"'{symbol}' non è fra gli strumenti autorizzati. "
                "Aggiungilo a RISK_ALLOWED_SYMBOLS se lo vuoi includere."
            ),
        )
    try:
        return await analyst.analyse(
            canonico, tuple(timeframe) if timeframe else TIMEFRAMES_ANALISI
        )
    except SymbolNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get(
    "/snapshot",
    response_model=MarketSnapshot,
    summary="Istantanea normalizzata del mercato",
    description=(
        "Conto, esposizione e contesti di tutti gli strumenti autorizzati, letti "
        "nello stesso giro. Un'analisi composta da letture fatte in momenti diversi "
        "descrive una situazione mai esistita."
    ),
)
async def get_snapshot(
    container: ContainerDep,
    timeframe: list[Timeframe] | None = Query(default=None),
) -> MarketSnapshot:
    servizio = _oppure_503(container, container.require_snapshots)
    try:
        return await servizio.build(
            timeframes=tuple(timeframe) if timeframe else TIMEFRAMES_ANALISI
        )
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


def _oppure_503(container: ContainerDep, getter):  # type: ignore[no-untyped-def]
    """Esegue un accessorio del contenitore traducendo l'assenza in 503 parlante."""
    try:
        valore = getter()
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if valore is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=container.broker_error or "Componente non disponibile.",
        )
    return valore
