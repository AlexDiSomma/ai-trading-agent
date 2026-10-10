"""Costruzione del broker a partire dalla configurazione.

Un solo punto in tutto il backend decide se si parla con MetaTrader 5 vero o con
il terminale finto. Due conseguenze volute:

* non esiste un *fallback silenzioso* al mock. Se MT5 è richiesto e non parte, il
  sistema resta senza dati e lo dichiara: un fallback automatico mostrerebbe
  prezzi inventati con l'aspetto di prezzi veri;
* il mock si ottiene solo con `MT5_USE_MOCK=true`, una scelta esplicita scritta
  in `.env` e riportata fra gli avvisi di System Health.
"""

from __future__ import annotations

from backend.brokers.base import ReadOnlyBroker
from backend.brokers.mt5.connector import MT5Connector
from backend.brokers.mt5.mock import FakeMT5Terminal
from backend.brokers.mt5.terminal import MT5Terminal, RealMT5Terminal
from backend.config.settings import Settings
from backend.core.clock import Clock, SystemClock
from backend.core.errors import ConfigurationError
from backend.core.logging import get_logger

logger = get_logger(__name__)


def build_broker(settings: Settings, *, clock: Clock | None = None) -> ReadOnlyBroker:
    """Crea il connettore di sola lettura previsto dalla configurazione."""
    if not settings.mt5.enabled:
        raise ConfigurationError(
            "MT5_ENABLED=false: nessun broker configurato. "
            "Il sistema non ha accesso ai dati di mercato."
        )

    if not settings.mt5.use_mock and settings.mt5.missing_credentials:
        raise ConfigurationError(
            "Credenziali MT5 mancanti: "
            + ", ".join(settings.mt5.missing_credentials)
            + ". Compila .env oppure imposta MT5_USE_MOCK=true per usare il terminale finto."
        )

    clock = clock or SystemClock()
    # Il simbolo di riferimento per leggere l'ora del server: il primo della
    # whitelist dell'utente, che è anche quello su cui opera davvero.
    reference_symbol = settings.risk.allowed_symbols[0]

    terminal: MT5Terminal
    if settings.mt5.use_mock:
        logger.warning(
            "broker.mock_attivo",
            motivo="MT5_USE_MOCK=true: i dati di mercato sono simulati",
        )
        terminal = FakeMT5Terminal(clock=clock)
    else:
        terminal = RealMT5Terminal()

    return MT5Connector(
        settings.mt5,
        terminal,
        clock=clock,
        simulated=settings.mt5.use_mock,
        reference_symbol=reference_symbol,
    )
