"""Scaricamento HTTP per i provider esterni, con cache e errori parlanti.

Un solo punto in cui si esce in rete. I test non ci passano mai: le impostazioni di
prova disattivano tutte le fonti esterne, e i parser si testano su file reali
registrati in `tests/dati/`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from backend.core.clock import Clock, SystemClock
from backend.providers.base import ProviderError

#: Alcune fonti (Cloudflare davanti a CBOE e Forex Factory) rifiutano i client senza
#: User-Agent. Ci si identifica in modo onesto.
USER_AGENT = "ai-trading-agent/0.1 (osservazione read-only; contatto nel repository)"


async def scarica(url: str, *, limite_secondi: float) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=limite_secondi, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            risposta = await client.get(url)
    except httpx.HTTPError as errore:
        raise ProviderError(f"{url}: {errore.__class__.__name__}: {errore}") from errore
    if risposta.status_code != 200:
        raise ProviderError(f"{url}: HTTP {risposta.status_code}")
    return risposta.text


class Cache[T]:
    """Ultimo valore riuscito, valido per `ttl` secondi."""

    def __init__(self, ttl_seconds: float, clock: Clock | None = None) -> None:
        self._ttl = ttl_seconds
        self._clock = clock or SystemClock()
        self._valore: T | None = None
        self._letto_a: datetime | None = None

    async def ottieni(self, carica: Callable[[], Awaitable[T]]) -> T:
        adesso = self._clock.now()
        if (
            self._valore is not None
            and self._letto_a is not None
            and (adesso - self._letto_a).total_seconds() <= self._ttl
        ):
            return self._valore
        valore = await carica()
        self._valore, self._letto_a = valore, adesso
        return valore
