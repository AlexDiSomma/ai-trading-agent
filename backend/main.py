"""Applicazione FastAPI.

Cosa fa l'API e cosa **non** fa. L'API serve letture e comandi di sicurezza. Non
esegue analisi, non chiama il modello, non valuta rischio e non invia ordini
dentro il ciclo di una richiesta HTTP: quello è compito dei worker
(docs/adr/0002-pipeline-a-due-cicli.md). Un'analisi dentro una richiesta
significherebbe che un refresh della dashboard muove denaro, e che il timeout di
un browser interrompe una valutazione a metà.

Gli errori di dominio vengono tradotti in risposte con un messaggio in italiano:
chi usa la dashboard deve capire *perché* il sistema non sta operando senza
aprire i log.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import __version__
from backend.api.router import api_router
from backend.config.settings import Settings, get_settings
from backend.core.errors import (
    ConfigurationError,
    KillSwitchEngagedError,
    RiskLimitViolationError,
    StaleDataError,
    SymbolNotAllowedError,
    TradingAgentError,
)
from backend.core.logging import configure_logging, get_logger
from backend.services.container import ApplicationContainer

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Avvio e arresto ordinati.

    Il contenitore viene costruito qui e non a livello di modulo: importare
    `backend.main` non deve aprire connessioni, altrimenti anche un semplice
    `--help` o la raccolta dei test proverebbe a collegarsi a MT5.
    """
    settings: Settings = app.state.settings
    container = ApplicationContainer(settings)
    app.state.container = container
    await container.startup()
    # MVP paper: lo scheduler parte con il backend (PAPER_AUTOPILOT_ENABLED). Gira in
    # background, mai dentro una richiesta HTTP, e non invia ordini al broker.
    autopilot = None
    if settings.paper.autopilot_enabled:
        from backend.services.autopilot import Autopilot

        autopilot = Autopilot(container)
        await autopilot.avvia()
    app.state.autopilot = autopilot
    try:
        yield
    finally:
        if autopilot is not None:
            await autopilot.ferma()
        await container.shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Costruisce l'applicazione. I test la chiamano con una configurazione propria."""
    settings = settings or get_settings()
    configure_logging(settings.log, mode=str(settings.app.effective_mode))

    app = FastAPI(
        title="AI Trading Agent",
        version=__version__,
        summary="Piattaforma di trading algoritmico AI-assisted su MetaTrader 5",
        description=(
            "Prima si guarda il quadro generale, poi il singolo mercato, poi il "
            "portafoglio, poi il rischio. Solo infine l'esecuzione.\n\n"
            "In questa fase l'API e' di **sola lettura**: nessun endpoint apre, "
            "modifica o chiude posizioni."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(api_router)
    _register_exception_handlers(app)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "nome": "AI Trading Agent",
            "versione": __version__,
            "modalita": str(settings.app.effective_mode),
            "documentazione": "/docs",
            "stato": "/api/health",
        }

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Traduce gli errori di dominio in risposte comprensibili.

    La mappatura dei codici non è arbitraria: 403 per ciò che l'utente ha
    vietato, 409 per un limite di rischio raggiunto, 503 per un dato che non c'è
    o non è più valido. Un monitor esterno può reagire al codice senza leggere
    il corpo.
    """

    @app.exception_handler(SymbolNotAllowedError)
    async def _symbol_not_allowed(_: Request, exc: SymbolNotAllowedError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc), "tipo": "perimetro"})

    @app.exception_handler(KillSwitchEngagedError)
    async def _kill_switch(_: Request, exc: KillSwitchEngagedError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "tipo": "kill_switch"})

    @app.exception_handler(RiskLimitViolationError)
    async def _risk_limit(_: Request, exc: RiskLimitViolationError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "tipo": "rischio"})

    @app.exception_handler(StaleDataError)
    async def _stale(_: Request, exc: StaleDataError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "tipo": "dato_scaduto"})

    @app.exception_handler(ConfigurationError)
    async def _config(_: Request, exc: ConfigurationError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc), "tipo": "configurazione"})

    @app.exception_handler(TradingAgentError)
    async def _generic(_: Request, exc: TradingAgentError) -> JSONResponse:
        # Gli errori di dominio non noti restano 503 e non 500: quasi sempre
        # significano "una risorsa esterna non risponde", non "il codice è rotto".
        logger.warning("api.errore_dominio", errore=str(exc), tipo=exc.__class__.__name__)
        return JSONResponse(
            status_code=503, content={"detail": str(exc), "tipo": exc.__class__.__name__}
        )


app = create_app()


def run() -> None:
    """Punto d'ingresso `trading-api`, per avviare senza ricordare il comando uvicorn."""
    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=settings.app.debug,
        log_config=None,  # il logging lo configura `create_app`
    )


if __name__ == "__main__":
    run()
