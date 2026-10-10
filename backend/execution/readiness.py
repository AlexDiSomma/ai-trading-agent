"""Gate di attivazione del LIVE: tutti veri **insieme**, o nessun ordine reale parte.

1. `paper_trades`         — almeno `MINIMO_TRADE_PAPER` trade paper chiusi e validati;
2. `paper_cycle`          — nessun errore critico nel ciclo entry → management → exit;
3. `persistence`          — archivio ordini PostgreSQL durevole e sano (migrazione 0005);
4. `mt5`                  — terminale reale (non simulato) connesso e sano, trading e
                            algo trading consentiti sul conto;
5. `risk_engine`          — stato del conto e budget giornaliero ricostruibili dal broker;
6. `kill_switch_tested`   — esercitazione del kill switch superata negli ultimi giorni;
7. `circuit_breaker`      — chiuso;
8. `no_unknown_orders`    — nessun ordine con esito ignoto;
9. `configuration`        — `APP_MODE=LIVE` e `APP_ENABLE_LIVE_TRADING=true`;
10. `user_confirmation`   — file di conferma firmato dall'utente per **questo** conto,
                            con la frase esatta, recente.

La conferma non si crea dal codice né dall'API: è un file che l'utente scrive a mano.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.clock import Clock
from backend.core.enums import HealthStatus, TradingMode

MINIMO_TRADE_PAPER = 30
VALIDITA_ESERCITAZIONE = timedelta(days=7)
VALIDITA_CONFERMA = timedelta(hours=24)
FRASE_CONFERMA = "CONFERMO L'ATTIVAZIONE DEL TRADING LIVE SU QUESTO CONTO"


@dataclass(frozen=True)
class Gate:
    name: str
    ok: bool
    detail: str


class LiveReadiness:
    def __init__(
        self,
        *,
        settings: Any,
        clock: Clock,
        paper_trades: Callable[[], tuple[int, int]],
        paper_critical_errors: Callable[[], int],
        store: Any | None,
        broker: Any | None,
        risk_engine: Any | None,
        breaker: Any,
        drills_file: Path,
        confirmation_file: Path,
    ) -> None:
        self._s = settings
        self._clock = clock
        self._trade = paper_trades
        self._errori = paper_critical_errors
        self._store = store
        self._broker = broker
        self._rischio = risk_engine
        self._breaker = breaker
        self._esercitazioni = drills_file
        self._conferma = confirmation_file
        self.ultimo: dict[str, Any] | None = None

    async def valuta(self) -> dict[str, Any]:
        g: list[Gate] = []
        validati, totali = self._trade()
        g.append(
            Gate(
                "paper_trades",
                validati >= MINIMO_TRADE_PAPER,
                f"{validati} validati su {totali} chiusi (servono {MINIMO_TRADE_PAPER})",
            )
        )
        errori = self._errori()
        g.append(
            Gate("paper_cycle", errori == 0, f"{errori} errori critici registrati nel ciclo paper")
        )

        if self._store is None or not getattr(self._store, "durable", False):
            g.append(
                Gate("persistence", False, "Archivio ordini durevole (PostgreSQL) non configurato.")
            )
        else:
            sano, dettaglio = await self._store.healthy()
            g.append(Gate("persistence", sano, dettaglio))

        login: int | None = None
        if self._broker is None:
            g.append(Gate("mt5", False, "Broker non connesso."))
        else:
            salute = await self._broker.health()
            conto = await self._broker.get_account() if salute.connected else None
            login = conto.login if conto else None
            problemi = []
            if self._broker.is_simulated:
                problemi.append("terminale simulato")
            if salute.status is not HealthStatus.HEALTHY:
                problemi.append(f"salute {salute.status}: {salute.detail}")
            if conto and not conto.trade_allowed:
                problemi.append("trading non consentito sul conto")
            if conto and not conto.trade_expert_allowed:
                problemi.append("algo trading disattivato nel terminale")
            g.append(
                Gate(
                    "mt5",
                    not problemi,
                    "; ".join(problemi)
                    or f"connesso, conto {conto.account_type if conto else '?'}",
                )
            )

        if self._rischio is None:
            g.append(Gate("risk_engine", False, "Risk Engine live non costruito (broker assente)."))
        else:
            sano, note = await self._rischio.sano()
            g.append(Gate("risk_engine", sano, "; ".join(note)))

        g.append(self._gate_esercitazione())
        g.append(
            Gate(
                "circuit_breaker",
                not self._breaker.aperto,
                "chiuso"
                if not self._breaker.aperto
                else f"aperto: {self._breaker.stato['reason']}",
            )
        )
        in_volo = [] if self._store is None else await self._ordini_in_volo()
        g.append(
            Gate(
                "no_unknown_orders",
                not in_volo,
                "nessuno" if not in_volo else f"{len(in_volo)} ordini da riconciliare",
            )
        )
        live = self._s.app.effective_mode is TradingMode.LIVE
        g.append(
            Gate(
                "configuration",
                live,
                f"APP_MODE={self._s.app.mode}, "
                f"APP_ENABLE_LIVE_TRADING={self._s.app.enable_live_trading}",
            )
        )
        g.append(self._gate_conferma(login))

        risultato = {
            "ready": all(x.ok for x in g),
            "evaluated_at": self._clock.now().isoformat(),
            "gates": [asdict(x) for x in g],
            "missing": [x.name for x in g if not x.ok],
        }
        self.ultimo = risultato
        return risultato

    async def _ordini_in_volo(self) -> list[Any]:
        try:
            return await self._store.lista(frozenset({"SENT", "UNKNOWN"}))  # type: ignore[union-attr]
        except Exception:
            return []

    def _gate_esercitazione(self) -> Gate:
        if not self._esercitazioni.exists():
            return Gate(
                "kill_switch_tested", False, "Nessuna esercitazione del kill switch registrata."
            )
        dati = json.loads(self._esercitazioni.read_text(encoding="utf-8"))
        ultima = dati.get("kill_switch")
        if not ultima or not ultima.get("passed"):
            return Gate(
                "kill_switch_tested", False, "Ultima esercitazione del kill switch fallita."
            )
        quando = datetime.fromisoformat(ultima["at"])
        if self._clock.now() - quando > VALIDITA_ESERCITAZIONE:
            return Gate(
                "kill_switch_tested", False, f"Esercitazione del {quando:%d/%m} troppo vecchia."
            )
        return Gate("kill_switch_tested", True, f"superata il {quando:%d/%m %H:%M} UTC")

    def _gate_conferma(self, login: int | None) -> Gate:
        if not self._conferma.exists():
            return Gate(
                "user_confirmation",
                False,
                f"Manca il file {self._conferma.name} scritto dall'utente.",
            )
        try:
            dati = json.loads(self._conferma.read_text(encoding="utf-8"))
            quando = datetime.fromisoformat(dati["confirmed_at"])
        except (ValueError, KeyError) as e:
            return Gate("user_confirmation", False, f"File di conferma non valido: {e}")
        if dati.get("text") != FRASE_CONFERMA:
            return Gate("user_confirmation", False, "Frase di conferma assente o diversa.")
        if login is None or dati.get("account_login") != login:
            return Gate("user_confirmation", False, "La conferma non riguarda il conto connesso.")
        if self._clock.now() - quando > VALIDITA_CONFERMA:
            return Gate("user_confirmation", False, "Conferma più vecchia di 24 ore.")
        return Gate("user_confirmation", True, f"confermato il {quando:%d/%m %H:%M}")

    async def consenti(self) -> tuple[bool, list[str]]:
        """Usata dal gateway prima di ogni invio: rivaluta tutti i gate."""
        r = await self.valuta()
        return r["ready"], [f"{x['name']}: {x['detail']}" for x in r["gates"] if not x["ok"]]


def registra_esercitazione(file: Path, nome: str, esito: dict[str, Any]) -> None:
    dati = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    dati[nome] = esito
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(dati, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


ProvaKill = Callable[[], Awaitable[dict[str, Any]]]
