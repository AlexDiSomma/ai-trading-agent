"""Diagnostica da riga di comando: `python -m backend.verifica`.

Risponde alla domanda che si fa per prima dopo l'installazione: *funziona?*
E, se no, *cosa manca*, in italiano e senza aprire i log.

Non invia nessun ordine: il codice per inviarli non esiste in questa fase
(ADR 0004). Legge soltanto.
"""

from __future__ import annotations

import asyncio
import sys

from backend.config import get_settings
from backend.core.enums import HealthStatus, Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import configure_logging
from backend.services.container import ApplicationContainer
from backend.services.health import HealthService

#: Caratteri invece di colori: funzionano anche in un terminale senza ANSI.
SEGNO = {
    HealthStatus.HEALTHY: "[ok]  ",
    HealthStatus.DEGRADED: "[~]   ",
    HealthStatus.UNHEALTHY: "[NO]  ",
    HealthStatus.UNKNOWN: "[?]   ",
}


def titolo(testo: str) -> None:
    print(f"\n{testo}\n{'-' * len(testo)}")


async def verifica() -> int:
    settings = get_settings()
    # Log al minimo: qui conta l'esito, non la traccia di cosa è stato tentato.
    configure_logging(settings.log, mode=str(settings.app.effective_mode))

    titolo("Configurazione")
    print(f"      modalità richiesta   {settings.app.mode}")
    print(f"      modalità effettiva   {settings.app.effective_mode}")
    print(f"      invio ordini reali   {'SÌ' if settings.app.can_send_orders else 'no'}")
    print(f"      terminale            {'FINTO (mock)' if settings.mt5.use_mock else 'reale'}")
    print(f"      conto configurato    {settings.mt5.login} su {settings.mt5.server}")
    print(f"      provider AI          {settings.ai.provider} ({settings.ai.model})")
    print(f"      strumenti autorizzati {', '.join(settings.risk.allowed_symbols)}")

    titolo("Limiti di rischio (definiti dall'utente, non modificabili a runtime)")
    limiti = settings.risk
    print(f"      perdita giornaliera  {limiti.daily_max_loss_pct}%")
    print(f"      perdita settimanale  {limiti.weekly_max_loss_pct}%")
    print(f"      drawdown massimo     {limiti.max_drawdown_pct}%")
    print(f"      rischio per trade    {limiti.max_risk_per_trade_pct}%")
    print(f"      rischio aperto max   {limiti.max_open_risk_pct}%")
    print(f"      posizioni massime    {limiti.max_open_positions}")
    print(f"      R/R minimo           {limiti.minimum_rr}")
    print(f"      confidence minima    {limiti.min_confidence}")

    avvisi = settings.startup_warnings()
    if avvisi:
        titolo("Da sapere")
        for avviso in avvisi:
            print(f"      - {avviso}")

    container = ApplicationContainer(settings)
    await container.startup()
    try:
        titolo("Componenti")
        stato = await HealthService(container).check()
        for componente in stato.components:
            critico = "" if componente.critical else "  (non critico)"
            print(f"{SEGNO[componente.status]}{componente.name}{critico}")
            print(f"        {componente.detail}")

        titolo("Esito")
        if stato.can_trade:
            print("[ok]  Il sistema può operare.")
        else:
            print("[NO]  Il sistema NON può operare:")
            for motivo in stato.blocking_reasons:
                print(f"        - {motivo}")

        if container.broker is None:
            print("\n      Nessuna lettura di mercato possibile: broker non disponibile.")
            return 1

        titolo("Lettura del conto")
        conto = await container.broker.get_account()
        print(f"      {conto.login} · {conto.company}")
        print(f"      saldo   {conto.balance:,.2f} {conto.currency}")
        print(f"      equity  {conto.equity:,.2f} {conto.currency}")
        print(f"      leva    1:{conto.leverage}")
        etichetta = {
            "DEMO": "demo (nessun denaro reale)",
            "CONTEST": "contest",
            "REAL": "REALE — DENARO VERO",
            "UNKNOWN": "non dichiarato dal broker: trattato come REALE",
        }[conto.account_type]
        print(f"      conto   {etichetta}")

        posizioni = await container.broker.get_positions()
        print(f"\n      posizioni aperte: {len(posizioni)}")
        for posizione in posizioni:
            protezione = (
                f"SL {posizione.stop_loss}" if posizione.has_protection else "SENZA STOP LOSS"
            )
            print(
                f"        {posizione.symbol} {posizione.side} {posizione.volume} lotti · "
                f"{protezione} · P/L {posizione.net_profit:+.2f}"
            )

        titolo("Lettura di mercato (strumenti autorizzati)")
        market = container.require_market_data()
        for simbolo in market.watchlist:
            try:
                info = (await market.get_symbol(simbolo)).value
                tick = await market.get_tick(simbolo)
                candele = (await market.get_candles(simbolo, Timeframe.H1, 50)).value
                eta = tick.age(market.now())
                nota = "  (quotazione vecchia: mercato chiuso?)" if eta > 60 else ""
                print(
                    f"[ok]  {simbolo:12s} bid {tick.value.bid:<12} "
                    f"spread {info.spread_points:>4}pt  "
                    f"vol.min {info.volume_min}  "
                    f"{len(candele.closed)} barre H1 chiuse{nota}"
                )
            except TradingAgentError as errore:
                print(f"[NO]  {simbolo:12s} {errore}")

        return 0 if stato.can_trade else 1
    finally:
        await container.shutdown()


def main() -> int:
    try:
        return asyncio.run(verifica())
    except KeyboardInterrupt:  # pragma: no cover - interruzione manuale
        return 130


if __name__ == "__main__":
    sys.exit(main())
