"""Dipendenze FastAPI.

Il contenitore vive in `app.state`: creato una volta all'avvio, non per
richiesta. Costruire un connettore MT5 a ogni chiamata HTTP significherebbe
inizializzare il terminale decine di volte al minuto.

Autenticazione: un token statico in `Authorization: Bearer ...`. È poco, ma
copre il caso reale di questo progetto — un backend in ascolto su localhost con
accesso in lettura al conto di trading — e impedisce che qualunque processo sulla
macchina interroghi il conto. Il frontend non ha mai le credenziali MT5: ha solo
questo token, e il broker lo vede solo il backend.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config.settings import Settings
from backend.core.errors import TradingAgentError
from backend.market_data.reader import MarketDataService
from backend.services.container import ApplicationContainer
from backend.services.health import HealthService

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> ApplicationContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - solo se lo startup è saltato
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Applicazione non inizializzata.",
        )
    return container


ContainerDep = Annotated[ApplicationContainer, Depends(get_container)]


def get_settings_dep(container: ContainerDep) -> Settings:
    return container.settings


def get_health_service(container: ContainerDep) -> HealthService:
    return HealthService(container)


def get_market_data(container: ContainerDep) -> MarketDataService:
    """Servizio dati di mercato, o 503 con il motivo in chiaro."""
    try:
        return container.require_market_data()
    except TradingAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


def require_token(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> None:
    """Verifica il token, se configurato.

    Senza `API_AUTH_TOKEN` l'API è aperta: è accettabile in sviluppo su
    localhost e viene segnalato fra gli avvisi di System Health, ma in
    produzione la configurazione lo richiede.
    """
    expected = container.settings.api.auth_token
    if expected is None:
        return
    if credentials is None or credentials.credentials != expected.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token di accesso assente o non valido.",
            headers={"WWW-Authenticate": "Bearer"},
        )


Protected = Depends(require_token)
MarketDataDep = Annotated[MarketDataService, Depends(get_market_data)]
HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
