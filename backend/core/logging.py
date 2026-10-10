"""Logging strutturato.

Scelte e motivi:

* **structlog con eventi a chiavi.** Un log di trading serve a rispondere a
  domande come "perché il 12 marzo alle 15:31 non è partito l'ordine su
  XAUUSD": una frase libera non è interrogabile, una coppia chiave/valore sì.
* **JSON in produzione, testo leggibile in sviluppo.** Stessi campi, due rese.
* **Censura dei segreti nel processore, non nei punti di chiamata.** Affidarsi
  alla disciplina di chi scrive `logger.info(...)` significa che prima o poi una
  password finisce su disco. Qui il filtro è obbligato e centralizzato.
* **`correlation_id` legato al contesto asincrono.** Tutti i log nati dalla
  stessa osservazione di mercato condividono un identificativo, altrimenti con
  più worker concorrenti le righe diventano illeggibili.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, unbind_contextvars

from backend.config.settings import LogSettings

#: Chiavi il cui valore non deve mai comparire nei log, a nessun livello di
#: annidamento. Confronto per sottostringa: copre `mt5_password`,
#: `anthropic_api_key`, `authorization`, ...
_SECRET_HINTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
    "dsn",
)

_REDACTED = "**********"


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def _redact(value: Any, depth: int = 0) -> Any:
    """Censura ricorsiva. La profondità è limitata per non girare a vuoto."""
    if depth > 6:
        return value
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_secret_key(str(key)) else _redact(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(item, depth + 1) for item in value)
    return value


def redact_secrets(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Processore structlog: azzera i valori sensibili prima della resa."""
    return _redact(event_dict)  # type: ignore[return-value]


def add_mode(mode: str) -> structlog.types.Processor:
    """Marca ogni riga con la modalità operativa.

    Leggere un log senza sapere se era PAPER o LIVE è il modo più rapido per
    trarre conclusioni sbagliate su cosa è realmente accaduto sul conto.
    """

    def processor(
        _logger: Any, _method: str, event_dict: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        event_dict.setdefault("mode", mode)
        return event_dict

    return processor


def configure_logging(settings: LogSettings, *, mode: str = "UNKNOWN") -> None:
    """Configura structlog e la libreria standard insieme.

    Anche le librerie terze (uvicorn, sqlalchemy) passano da structlog: due
    formati diversi nello stesso file renderebbero i log non analizzabili.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        add_mode(mode),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        redact_secrets,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor
    if settings.format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if settings.file is not None:
        settings.file.parent.mkdir(parents=True, exist_ok=True)
        # Rotazione: una sessione di trading continuo riempie un file in fretta
        # e un log illeggibile per dimensione è un log perduto.
        file_handler = logging.handlers.RotatingFileHandler(
            settings.file, maxBytes=20_000_000, backupCount=10, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(settings.level)
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Logger con nome. Convenzione: `get_logger(__name__)`."""
    return structlog.stdlib.get_logger(name)


@contextmanager
def correlation(correlation_id: UUID | None = None, **extra: Any) -> Iterator[UUID]:
    """Lega un `correlation_id` a tutti i log emessi nel blocco.

    Usato all'inizio di ogni ciclo di analisi: da lì in poi ogni riga, in
    qualunque modulo, porta lo stesso identificativo e il journal può
    ricostruire la catena completa che ha portato (o non ha portato) a un trade.
    """
    cid = correlation_id or uuid4()
    bind_contextvars(correlation_id=str(cid), **extra)
    try:
        yield cid
    finally:
        unbind_contextvars("correlation_id", *extra.keys())


def clear_log_context() -> None:
    clear_contextvars()
