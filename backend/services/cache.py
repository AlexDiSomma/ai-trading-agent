"""Redis: cache dei contesti e, in seguito, code dei worker.

Nel disegno complessivo Redis tiene i contesti condivisi (globale, macro, news)
prodotti dal ciclo lento e letti dal ciclo veloce: vedi
docs/adr/0002-pipeline-a-due-cicli.md. Qui c'è la parte minima di fase 1, cioè
la connessione, il controllo di stato e la scrittura/lettura di contesti con
scadenza.

Ogni valore salvato porta la propria scadenza (TTL). Un contesto senza scadenza
in cache è la via più breve per decidere su un'immagine del mondo di tre ore
prima senza accorgersene.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.config.settings import RedisSettings
from backend.core.enums import HealthStatus
from backend.core.logging import get_logger

logger = get_logger(__name__)


class RedisCache:
    """Accesso a Redis, con il minimo necessario in fase 1."""

    def __init__(self, settings: RedisSettings) -> None:
        self._settings = settings
        self._client: Redis = Redis.from_url(
            settings.dsn,
            decode_responses=True,
            socket_connect_timeout=settings.connect_timeout_seconds,
            socket_timeout=settings.connect_timeout_seconds,
            health_check_interval=30,
        )

    @property
    def safe_dsn(self) -> str:
        return self._settings.safe_dsn

    async def check(self) -> tuple[HealthStatus, str, float | None]:
        """Verifica la raggiungibilità senza sollevare eccezioni."""
        started = perf_counter()
        try:
            await self._client.ping()
            return (
                HealthStatus.HEALTHY,
                f"Connesso a {self.safe_dsn}.",
                round((perf_counter() - started) * 1000, 2),
            )
        except (RedisError, OSError) as exc:
            return (
                HealthStatus.UNHEALTHY,
                f"Redis non raggiungibile su {self.safe_dsn} ({exc.__class__.__name__}).",
                None,
            )

    async def put_context(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        """Salva un contesto con scadenza obbligatoria."""
        if ttl_seconds <= 0:
            raise ValueError(
                "ttl_seconds deve essere positivo: un contesto senza scadenza "
                "verrebbe riusato quando non è più valido"
            )
        await self._client.set(key, json.dumps(value, default=str), ex=ttl_seconds)

    async def get_context(self, key: str) -> Any | None:
        """Legge un contesto. `None` significa assente **o scaduto**.

        I due casi si trattano allo stesso modo di proposito: in entrambi non
        c'è un'informazione valida su cui decidere.
        """
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("redis.chiuso")
