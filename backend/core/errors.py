"""Gerarchia degli errori.

Ogni errore dichiara se e' *transitorio* (ritentabile) oppure *bloccante*: il
circuit breaker decide in base a questo e non in base al tipo concreto, cosi'
aggiungere un errore nuovo non richiede di toccare il breaker.
"""

from __future__ import annotations


class TradingAgentError(Exception):
    """Radice di tutti gli errori del sistema."""

    transient: bool = False
    #: True quando l'errore deve impedire qualunque nuova operazione.
    blocks_trading: bool = True

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def __str__(self) -> str:  # pragma: no cover - solo presentazione
        if not self.context:
            return self.message
        extra = " ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.message} ({extra})"


class ConfigurationError(TradingAgentError):
    """Configurazione assente o incoerente: si ferma all'avvio, non a runtime."""


class BrokerError(TradingAgentError):
    """Errore generico del broker."""


class BrokerNotConnectedError(BrokerError):
    transient = True


class BrokerTimeoutError(BrokerError):
    transient = True


class BrokerRejectedError(BrokerError):
    """Il broker ha rifiutato la richiesta: ritentare non serve."""


class SymbolNotFoundError(BrokerError):
    blocks_trading = False


class SymbolNotAllowedError(TradingAgentError):
    """Simbolo fuori dalla whitelist dell'utente. Non negoziabile."""


class StaleDataError(TradingAgentError):
    """Dato troppo vecchio per decidere. Fail closed."""

    transient = True


class ReadOnlyViolationError(TradingAgentError):
    """Tentativo di scrittura da un componente dichiarato di sola lettura.

    Esiste per rendere l'errore impossibile da ignorare: fino alla fase 11 il
    connettore MT5 non deve poter inviare ordini nemmeno per sbaglio.
    """


class RiskLimitViolationError(TradingAgentError):
    """Un limite dell'utente e' stato superato o si e' tentato di modificarlo."""


class KillSwitchEngagedError(TradingAgentError):
    """Kill switch attivo: nessun ordine puo' partire."""


class AIProviderError(TradingAgentError):
    """Il provider LLM non ha risposto o ha risposto in modo non valido.

    Non si inventa un output: con AI_REQUIRED=true si traduce in NO TRADE.
    """

    transient = True


class AIOutputInvalidError(AIProviderError):
    transient = False


class PersistenceUnavailableError(TradingAgentError):
    """La persistenza richiesta non è affidabile (ADR 0019).

    Blocca l'operatività: lo stato del rischio e gli intenti di esecuzione (fasi 6
    e 11) non possono esistere solo in memoria. L'intelligence read-only non la
    solleva mai: si degrada e lo dichiara.
    """

    blocks_trading = True
