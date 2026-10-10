"""Esercitazione del kill switch: prova che il blocco funziona sul percorso vero.

Usa il **file reale** del kill switch e il **gateway reale**, con un terminale
sentinella che registra qualunque tentativo di `order_check`/`order_send`:

1. attiva il kill switch (se non lo era già);
2. verifica che lo stato letto dal file sia "attivo";
3. chiede al gateway un'apertura con un'approvazione valida → deve rifiutare con
   `KillSwitchEngagedError` **senza** toccare il terminale;
4. verifica che il gate di rischio (paper e live) rifiuti con il kill switch;
5. lo rilascia (solo se l'aveva attivato l'esercitazione) e verifica il rilascio.

Per qualche secondo anche lo scheduler paper vede il kill switch attivo: è voluto,
il blocco è globale.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from backend.config.risk_limits import RiskLimits
from backend.core.clock import Clock
from backend.core.errors import KillSwitchEngagedError
from backend.core.kill_switch import KillSwitch
from backend.execution.breaker import CircuitBreaker
from backend.execution.gateway import LiveOrderGateway
from backend.execution.models import RiskApproval
from backend.execution.readiness import registra_esercitazione
from backend.execution.store import MemoryExecutionStore
from backend.risk.gate import RichiestaIngresso, valuta_ingresso


class TerminaleSentinella:
    """Ogni chiamata di scrittura è una violazione: la si registra."""

    def __init__(self) -> None:
        self.violazioni: list[str] = []

    async def order_check(self, request: dict[str, Any]) -> Any:
        self.violazioni.append("order_check")
        raise AssertionError("order_check chiamato con il kill switch attivo")

    async def order_send(self, request: dict[str, Any]) -> Any:
        self.violazioni.append("order_send")
        raise AssertionError("order_send chiamato con il kill switch attivo")

    def __getattr__(self, nome: str) -> Any:
        async def lettura(*_: Any, **__: Any) -> Any:
            self.violazioni.append(nome)
            raise AssertionError(f"{nome} chiamato: il gateway doveva fermarsi prima")

        return lettura


async def esercitazione_kill_switch(
    kill_switch: KillSwitch, *, clock: Clock, limiti: RiskLimits, file: Path, breaker_file: Path
) -> dict[str, Any]:
    controlli: list[dict[str, Any]] = []

    def controllo(nome: str, ok: bool, dettaglio: str = "") -> None:
        controlli.append({"check": nome, "ok": ok, "detail": dettaglio})

    gia_attivo = kill_switch.state().engaged
    if not gia_attivo:
        kill_switch.engage("Esercitazione automatica del kill switch")
    try:
        controllo("stato_da_file", kill_switch.state().engaged)
        sentinella = TerminaleSentinella()

        async def autorizzato() -> tuple[bool, list[str]]:
            return True, []

        gateway = LiveOrderGateway(
            terminal=sentinella,  # type: ignore[arg-type]
            store=MemoryExecutionStore(),
            breaker=CircuitBreaker(breaker_file, clock=clock),
            kill_switch=lambda: kill_switch.state().engaged,
            autorizzazione=autorizzato,
            engine_id="esercitazione",
            clock=clock,
        )
        adesso = clock.now()
        approvazione = RiskApproval(
            approval_id="drill",
            engine_id="esercitazione",
            idempotency_key="drill",
            canonical="EURUSD",
            broker_symbol="EURUSD",
            direction="LONG",
            volume=0.01,
            entry_reference=1.1,
            stop_loss=1.09,
            take_profit=1.12,
            risk_amount=1.0,
            max_slippage_points=20,
            issued_at=adesso,
            expires_at=adesso + timedelta(seconds=30),
        )
        try:
            await gateway.apri(approvazione)
            controllo("gateway_bloccato", False, "il gateway ha accettato l'apertura")
        except KillSwitchEngagedError:
            controllo("gateway_bloccato", True)
        controllo(
            "terminale_non_toccato", not sentinella.violazioni, ", ".join(sentinella.violazioni)
        )
        motivi = valuta_ingresso(
            RichiestaIngresso(
                simbolo="EURUSD",
                rr=2.0,
                stop_atr=1.5,
                spread_atr=0.05,
                rischio=1.0,
                equity=10_000,
                posizioni_aperte=0,
                simboli_aperti=frozenset(),
                rischio_aperto=0.0,
                pnl_oggi=0.0,
                picco_equity=10_000,
                ora_broker=adesso,
                kill_switch=kill_switch.state().engaged,
                finestra_evento=False,
                dati_validi=True,
            ),
            limiti,
        )
        controllo("gate_di_rischio_bloccato", any("Kill switch" in m for m in motivi))
    finally:
        if not gia_attivo:
            kill_switch.release()
    controllo(
        "rilascio",
        gia_attivo or not kill_switch.state().engaged,
        "lasciato attivo: era già attivo prima" if gia_attivo else "",
    )
    esito = {
        "passed": all(c["ok"] for c in controlli),
        "at": clock.now().isoformat(),
        "checks": controlli,
    }
    registra_esercitazione(file, "kill_switch", esito)
    return esito
