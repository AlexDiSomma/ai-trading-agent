"""Fixture comuni.

Due principi, entrambi imparati a spese di altri progetti:

**I test non devono poter leggere il `.env` reale.** Le impostazioni di prova
vengono costruite con `_env_file=None` e la fixture `ambiente_pulito` rimuove
dall'ambiente ogni variabile con i prefissi del progetto. Senza questa
precauzione i test passerebbero o fallirebbero a seconda di cosa l'utente ha
scritto nel suo `.env`, e in un caso sfortunato si collegherebbero al conto vero.

**Il tempo è fermo.** `FrozenClock` rende verificabili i controlli di freschezza:
con l'orologio di sistema un'asserzione sull'età di un dato sarebbe una corsa.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.brokers.mt5.connector import MT5Connector
from backend.brokers.mt5.mock import FakeAccount, FakeMT5Terminal
from backend.config.risk_limits import RiskLimits
from backend.config.settings import (
    AISettings,
    ApiSettings,
    AppSettings,
    DatabaseSettings,
    ExternalSettings,
    LogSettings,
    MT5Settings,
    PaperSettings,
    RedisSettings,
    Settings,
)
from backend.core.clock import FrozenClock
from backend.core.enums import TradingMode
from backend.market_data.broker_time import BrokerClock
from backend.market_data.instruments import costruisci_strumento
from backend.schemas.instrument import Instrument
from backend.valuation.service import BrokerValuationService

#: Prefissi delle variabili d'ambiente del progetto.
_PREFISSI = ("APP_", "LOG_", "DB_", "REDIS_", "MT5_", "AI_", "RISK_", "API_", "EXT_")
_NOMI_ESATTI = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL")

#: Istante fisso dei test: un martedì alle 10:00 UTC, dentro la sessione
#: autorizzata di default, così i test sulle sessioni non dipendono dal giorno
#: in cui vengono eseguiti.
ADESSO = datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def ambiente_pulito(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isola i test dall'ambiente e dal .env dell'utente."""
    for nome in list(os.environ):
        if nome.startswith(_PREFISSI) or nome in _NOMI_ESATTI:
            monkeypatch.delenv(nome, raising=False)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(ADESSO)


@pytest.fixture
def limiti() -> RiskLimits:
    """Limiti di rischio di prova, coerenti fra loro."""
    return RiskLimits(
        _env_file=None,
        RISK_ALLOWED_SYMBOLS="EURUSD,GOLD,US100Cash",
        RISK_ALLOWED_SESSIONS="08:00-17:30",
        RISK_ALLOWED_WEEKDAYS="0,1,2,3,4",
    )


def crea_settings(
    *,
    mode: TradingMode = TradingMode.PAPER,
    enable_live: bool = False,
    use_mock: bool = True,
    kill_switch_file: Path | None = None,
    allowed_symbols: str = "EURUSD,GOLD,US100Cash",
    ai_provider: str = "null",
    ai_required: bool = False,
    api_token: str | None = None,
) -> Settings:
    """Configurazione di prova, costruita senza leggere nulla da disco."""
    return Settings(
        app=AppSettings(
            _env_file=None,
            mode=mode,
            enable_live_trading=enable_live,
            debug=True,
            kill_switch_file=kill_switch_file or Path("var/KILL_SWITCH_TEST"),
        ),
        log=LogSettings(_env_file=None, level="WARNING", format="console", file=None),
        # 127.0.0.1 e non "localhost": quest'ultimo su Windows risolve sia IPv6
        # sia IPv4, e asyncpg tenta entrambi (con e senza SSL) prima di
        # arrendersi. Con il timeout breve il controllo "database spento" dura
        # decimi di secondo invece di quattro.
        db=DatabaseSettings(
            _env_file=None,
            host="127.0.0.1",
            name="trading_agent_test",
            connect_timeout_seconds=0.3,
        ),
        # Timeout molto breve: i test girano con Redis e PostgreSQL spenti di
        # proposito, e un'attesa di secondi per ogni controllo renderebbe la
        # suite cosi' lenta da non essere piu' eseguita.
        redis=RedisSettings(_env_file=None, connect_timeout_seconds=0.2),
        mt5=MT5Settings(
            _env_file=None,
            enabled=True,
            use_mock=use_mock,
            login=169_444_760,
            password="prova",
            server="XMGlobalMU-Demo 2",
            max_tick_age_seconds=15.0,
        ),
        paper=PaperSettings(
            _env_file=None,
            autopilot_enabled=False,
            data_dir=(kill_switch_file.parent if kill_switch_file else Path("var")) / "paper_test",
        ),
        ai=AISettings(
            _env_file=None,
            provider=ai_provider,
            required=ai_required,
            journal_dir=(kill_switch_file.parent if kill_switch_file else Path("var"))
            / "ai_journal_test",
        ),
        api=ApiSettings(_env_file=None, auth_token=api_token),
        risk=RiskLimits(_env_file=None, RISK_ALLOWED_SYMBOLS=allowed_symbols),
        # I test non escono mai in rete: nessuna fonte esterna configurata.
        external=ExternalSettings(
            _env_file=None, calendar_provider="none", vix_provider="none", rates_provider="none"
        ),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Kill switch nella directory temporanea: un test non deve poter lasciare
    # il sistema bloccato per quello successivo.
    return crea_settings(kill_switch_file=tmp_path / "KILL_SWITCH")


@pytest.fixture
def terminale(clock: FrozenClock) -> FakeMT5Terminal:
    return FakeMT5Terminal(clock=clock, account=FakeAccount())


@pytest.fixture
async def connettore(
    settings: Settings, terminale: FakeMT5Terminal, clock: FrozenClock
) -> AsyncIterator[MT5Connector]:
    connector = MT5Connector(
        settings.mt5, terminale, clock=clock, simulated=True, reference_symbol="EURUSD"
    )
    await connector.connect()
    yield connector
    await connector.disconnect()


@pytest.fixture
def cartella_log(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "logs"


#: Scarto dell'ora di XM, come nel terminale finto (ADR 0015).
SCARTO_XM_SECONDI = 3 * 3_600


@pytest.fixture
def orologio_broker() -> BrokerClock:
    """Scarto dell'ora broker già stabilito, come dopo l'avvio del contenitore."""
    orologio = BrokerClock()
    orologio.seed(SCARTO_XM_SECONDI, ADESSO)
    return orologio


@pytest.fixture
def valutazione(connettore: MT5Connector) -> BrokerValuationService:
    """Il servizio di valutazione sul terminale finto, conto in EUR come quello reale."""
    return BrokerValuationService(connettore, account_currency="EUR")


@pytest.fixture
def strumento(connettore: MT5Connector) -> Callable[[str], Awaitable[Instrument]]:
    """Identità di uno strumento letta dal broker (finto)."""

    async def leggi(nome: str) -> Instrument:
        return costruisci_strumento(await connettore.get_symbol(nome))

    return leggi


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """I test `live_broker` girano solo con `RUN_LIVE_MT5=1`.

    Toccano il terminale e il conto reali: non devono partire per sbaglio con un
    `pytest` qualunque, né in un ambiente senza MT5.
    """
    if os.environ.get("RUN_LIVE_MT5") == "1":
        return
    salta = pytest.mark.skip(reason="test sul conto reale: imposta RUN_LIVE_MT5=1")
    for item in items:
        if "live_broker" in item.keywords:
            item.add_marker(salta)
