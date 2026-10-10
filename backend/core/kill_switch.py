"""Kill switch globale.

Tre scelte deliberate:

1. **Vive su file, non in memoria.** Se l'API e' bloccata o il processo e'
   impazzito, deve restare un modo di fermare tutto: creare un file. Funziona
   anche da Esplora risorse, senza terminale.
2. **E' "sticky".** Una volta scattato non si disarma da solo al riavvio: il
   file resta finche' qualcuno non lo rimuove consapevolmente.
3. **Blocca le aperture, non chiude le posizioni.** Chiudere tutto d'ufficio in
   un momento di panico puo' realizzare perdite che il mercato stava gia'
   rientrando. Lo svuotamento del portafoglio e' un comando separato e
   volontario (`flatten`), descritto in docs/adr/0006-kill-switch-e-breaker.md.

Lo stato viene riletto dal disco con una cache brevissima: va interrogato a ogni
ordine senza fare syscall a ogni tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend.core.clock import Clock, SystemClock
from backend.core.errors import KillSwitchEngagedError


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    engaged: bool
    reason: str | None
    engaged_at: datetime | None
    source: str


class KillSwitch:
    """Interruttore generale delle nuove operazioni."""

    #: Per quanto tempo fidarsi dell'ultima lettura del file.
    CACHE_SECONDS = 1.0

    def __init__(
        self,
        path: Path,
        *,
        forced: bool = False,
        clock: Clock | None = None,
    ) -> None:
        self._path = path
        #: `forced` viene da APP_KILL_SWITCH_ENABLED: permette di avviare il
        #: sistema gia' bloccato senza dover creare il file a mano.
        self._forced = forced
        self._clock = clock or SystemClock()
        self._cached: KillSwitchState | None = None
        self._cached_at: datetime | None = None

    @property
    def path(self) -> Path:
        return self._path

    def state(self) -> KillSwitchState:
        now = self._clock.now()
        if (
            self._cached is not None
            and self._cached_at is not None
            and (now - self._cached_at).total_seconds() < self.CACHE_SECONDS
        ):
            return self._cached

        state = self._read()
        self._cached = state
        self._cached_at = now
        return state

    def _read(self) -> KillSwitchState:
        if self._forced:
            return KillSwitchState(
                engaged=True,
                reason="Attivato da configurazione (APP_KILL_SWITCH_ENABLED)",
                engaged_at=None,
                source="config",
            )
        try:
            if not self._path.exists():
                return KillSwitchState(False, None, None, "file")
            text = self._path.read_text(encoding="utf-8").strip()
            stat = self._path.stat()
        except OSError as exc:
            # Non riusciamo a sapere se e' attivo: fail closed.
            return KillSwitchState(
                engaged=True,
                reason=f"Stato del kill switch illeggibile ({exc.__class__.__name__}): "
                "per sicurezza il trading resta bloccato",
                engaged_at=None,
                source="file-error",
            )
        return KillSwitchState(
            engaged=True,
            reason=text or "File kill switch presente, nessun motivo indicato",
            engaged_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
            source="file",
        )

    def engage(self, reason: str) -> KillSwitchState:
        """Attiva il blocco scrivendo il file. Idempotente."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stamp = self._clock.now().isoformat()
        self._path.write_text(f"{stamp} {reason}\n", encoding="utf-8")
        self._invalidate()
        return self.state()

    def release(self) -> KillSwitchState:
        """Disarma il blocco. Operazione volutamente manuale."""
        self._path.unlink(missing_ok=True)
        self._invalidate()
        return self.state()

    def require_clear(self) -> None:
        """Da chiamare prima di ogni invio d'ordine."""
        state = self.state()
        if state.engaged:
            raise KillSwitchEngagedError(
                "Kill switch attivo: nessun nuovo ordine",
                reason=state.reason,
                source=state.source,
            )

    def _invalidate(self) -> None:
        self._cached = None
        self._cached_at = None
