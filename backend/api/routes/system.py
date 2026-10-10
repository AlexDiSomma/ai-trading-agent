"""Endpoint di sistema: limiti di rischio attivi e kill switch.

I limiti sono esposti in **sola lettura**. Non esiste un endpoint che li
modifichi, e non per pigrizia: i limiti sono il contratto che l'utente impone al
sistema, e un'API che li cambia a caldo è un'API che prima o poi li cambia su
richiesta di un componente automatico. Si modificano in `.env` e si riavvia —
un gesto consapevole, tracciato in `config_versions`.

Il kill switch invece si aziona da qui, perché in un momento di urgenza aprire
un terminale non è ragionevole. L'attivazione è sempre concessa; il rilascio è
un'operazione separata che richiede di dichiarare un motivo, così non diventa un
clic automatico.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body

from backend.api.deps import ContainerDep, Protected, SettingsDep
from backend.core.events import Event, EventType

router = APIRouter(prefix="/system", tags=["Sistema"], dependencies=[Protected])


@router.get(
    "/risk-limits",
    summary="Limiti di rischio attivi",
    description=(
        "I vincoli definiti dall'utente. L'AI opera dentro questi limiti e non "
        "può modificarli, ignorarli o aggirarli."
    ),
)
async def get_risk_limits(settings: SettingsDep) -> dict[str, Any]:
    limits = settings.risk
    return {
        "valuta_conto": limits.account_currency,
        "budget_di_rischio": limits.risk_budget,
        "perdite_massime": {
            "giornaliera_pct": limits.daily_max_loss_pct,
            "settimanale_pct": limits.weekly_max_loss_pct,
            "drawdown_pct": limits.max_drawdown_pct,
        },
        "rischio": {
            "per_operazione_pct": limits.max_risk_per_trade_pct,
            "totale_aperto_pct": limits.max_open_risk_pct,
            "esposizione_correlata_pct": limits.max_correlated_exposure_pct,
            "posizioni_massime": limits.max_open_positions,
            "size_massima_lotti": limits.max_position_size_lots,
            "leva_massima": limits.max_leverage,
        },
        "qualita_minima": {
            "rapporto_rischio_rendimento": limits.minimum_rr,
            "confidence_minima": limits.min_confidence,
            "spread_massimo_punti": limits.max_spread_points,
            "slippage_massimo_punti": limits.max_slippage_points,
            "stop_minimo_atr": limits.min_stop_distance_atr,
            "stop_massimo_atr": limits.max_stop_distance_atr,
        },
        "perimetro": {
            "strumenti": list(limits.allowed_symbols),
            "sessioni": [f"{s:%H:%M}-{e:%H:%M}" for s, e in limits.allowed_sessions],
            "giorni_settimana": list(limits.allowed_weekdays),
        },
        "filtro_eventi_macro": {
            "blocco_prima_minuti": limits.block_high_impact_before_minutes,
            "blocco_dopo_minuti": limits.block_high_impact_after_minutes,
        },
        # La scala confidence -> quota di rischio: puo' solo ridurre il rischio
        # massimo dell'utente, mai aumentarlo.
        "scala_confidence": [
            {"soglia": soglia, "quota_rischio": quota} for soglia, quota in limits.confidence_ladder
        ],
        "nota": (
            "Questi valori si modificano in .env e richiedono un riavvio. "
            "Nessun componente automatico puo' cambiarli a runtime."
        ),
    }


@router.get("/kill-switch", summary="Stato del kill switch")
async def get_kill_switch(container: ContainerDep) -> dict[str, Any]:
    state = container.kill_switch.state()
    return {
        "attivo": state.engaged,
        "motivo": state.reason,
        "attivato_il": state.engaged_at,
        "origine": state.source,
        "file": str(container.kill_switch.path),
        "nota": (
            "Il kill switch blocca l'apertura di nuove posizioni. Non chiude quelle "
            "aperte: la chiusura forzata e' un comando separato e volontario."
        ),
    }


@router.post(
    "/kill-switch/engage",
    summary="Attiva il kill switch",
    description="Blocca immediatamente qualunque nuovo ordine. Sempre consentito.",
)
async def engage_kill_switch(
    container: ContainerDep,
    motivo: Annotated[str, Body(embed=True)] = "Attivato manualmente dalla dashboard",
) -> dict[str, Any]:
    state = container.kill_switch.engage(motivo)
    await container.events.publish(
        Event(
            type=EventType.KILL_SWITCH_ENGAGED,
            occurred_at=container.clock.now(),
            payload={"message": motivo},
            source="api",
        )
    )
    return {"attivo": state.engaged, "motivo": state.reason}


@router.post(
    "/kill-switch/release",
    summary="Disattiva il kill switch",
    description=(
        "Richiede un motivo esplicito: serve a rendere il rilascio una decisione "
        "e non un clic. Il motivo finisce nel diario di sistema."
    ),
)
async def release_kill_switch(
    container: ContainerDep,
    motivo: Annotated[str, Body(embed=True)],
) -> dict[str, Any]:
    state = container.kill_switch.release()
    await container.events.publish(
        Event(
            type=EventType.KILL_SWITCH_RELEASED,
            occurred_at=container.clock.now(),
            payload={"message": motivo},
            source="api",
        )
    )
    return {"attivo": state.engaged, "motivo_rilascio": motivo}
