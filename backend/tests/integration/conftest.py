"""Fixture dei test di integrazione.

L'applicazione viene avviata in-process con il **vero** ciclo di startup: lo
stesso codice che gira in produzione, con il terminale MT5 finto al posto di
quello reale. Nessun test parla con il broker vero, nemmeno in lettura.

PostgreSQL e Redis **non** vengono avviati. Non è una scorciatoia: è lo scenario
che questi test devono verificare. Il backend deve partire comunque e dire quali
componenti mancano — se un giorno smettesse di partire con il database giù, non
sarebbe più in grado di spiegare perché non opera.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.brokers.mt5.mock import FakeMT5Terminal
from backend.config.settings import Settings
from backend.core.clock import FrozenClock
from backend.main import create_app
from backend.services.container import ApplicationContainer
from backend.tests.conftest import ADESSO, crea_settings

TOKEN = "token-di-prova-abbastanza-lungo"


@pytest.fixture
def settings_api(tmp_path: Path) -> Settings:
    return crea_settings(
        kill_switch_file=tmp_path / "KILL_SWITCH",
        allowed_symbols="EURUSD,GOLD",
        api_token=TOKEN,
    )


@pytest.fixture
async def app(settings_api: Settings) -> AsyncIterator[FastAPI]:
    """App con contenitore avviato davvero (broker finto, database assente)."""
    applicazione = create_app(settings_api)
    container = ApplicationContainer(settings_api, clock=FrozenClock(ADESSO))
    applicazione.state.container = container
    await container.startup()
    try:
        yield applicazione
    finally:
        await container.shutdown()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP autenticato."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http:
        yield http


@pytest.fixture
async def client_anonimo(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client senza token: serve a verificare che l'autenticazione morda."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def terminale_api(app: FastAPI) -> FakeMT5Terminal:
    """Il terminale finto usato dall'app, per iniettare guasti dall'esterno."""
    container: ApplicationContainer = app.state.container
    broker = container.broker
    assert broker is not None, "Il broker finto deve essere disponibile nei test"
    return broker._terminal  # type: ignore[attr-defined]
