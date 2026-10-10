"""Report dell'AI Market Analyst su contesti reali XM: `python -m backend.report_ai`.

Tre casi costruiti dagli stessi dati reali, letti una sola volta:

1. **dal vivo** — contesto corrente con tutte le fonti;
2. **dal vivo senza fonti esterne** — stessi prezzi XM, ma senza calendario, VIX e
   tassi: mostra come le informazioni mancanti limitano l'analisi;
3. **replay alla chiusura di venerdì** — le barre XM tagliate a venerdì sera, le
   osservazioni esterne filtrate per disponibilità a quell'istante (la chiusura del
   VIX di venerdì non era ancora pubblica). È un contesto storico **senza hindsight**:
   il motore non vede nulla di successivo.

Ogni analisi viene registrata nel diario immutabile (`var/ai_journal`) e copiata in
`docs/validazione/ai_<caso>.json`. Sola lettura: nessun ordine, nessuna size.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.ai.contracts import AnalysisRecord
from backend.config import get_settings
from backend.config.paths import PROJECT_ROOT
from backend.core.enums import MarketStatus, ProviderKind, ProviderState, SeriesStatus
from backend.core.logging import configure_logging
from backend.global_market.engine import DatiEsterni, costruisci
from backend.schemas.external import ProviderStatus
from backend.schemas.global_market import GlobalMarketContext
from backend.services.container import ApplicationContainer

#: Chiusura del forex di venerdì 2 ottobre 2026 (20:58 UTC, misurata) più mezz'ora.
CHIUSURA_VENERDI = datetime(2026, 10, 2, 21, 30, tzinfo=UTC)


def _riga(t: str = "") -> None:
    print(t, flush=True)


def stampa(nome: str, c: GlobalMarketContext, r: AnalysisRecord) -> None:
    _riga("=" * 100)
    _riga(f"CASO: {nome}")
    _riga("=" * 100)
    _riga(
        f"contesto al {c.as_of:%Y-%m-%d %H:%M} UTC  ·  fase {c.market_phase}  ·  regime {c.regime}"
    )
    _riga("  motivi del regime: " + " | ".join(c.regime_reasons))
    _riga(
        "  qualità complessiva "
        + str(c.data_quality)
        + "  ·  per fonte: "
        + ", ".join(
            f"{q.domain}={q.quality if q.configured else 'NON CONFIGURATA'}"
            for q in c.source_quality
        )
    )
    _riga(
        f"AI: {r.status}  ·  modello {r.call.model_snapshot or r.call.model_requested}  ·  "
        f"prompt {r.input.prompt_version}  ·  token "
        f"{r.call.input_tokens}/{r.call.output_tokens}  ·  "
        f"{r.call.latency_ms} ms"
    )
    _riga(
        f"  record {r.record_id}  integrità {r.integrity_sha256[:16]}…  "
        f"ingresso {r.input.input_sha256[:16]}…"
    )
    if r.status != "AVAILABLE" or r.analysis is None:
        _riga("  ANALISI NON DISPONIBILE:")
        for m in r.failure_reasons:
            _riga(f"   - {m}")
        return
    a = r.analysis
    _riga(f"\n  SINTESI ({a.confidence} confidence): {a.market_summary}")
    _riga(
        f"  REGIME secondo il modello: {a.engine_regime_acknowledged} — {a.analyst_view_on_regime}"
    )
    _riga("\n  TEMI DOMINANTI")
    for t in a.dominant_themes:
        _riga(f"   · {t.title}: {t.description}  [{', '.join(t.refs)}]")
    _riga("\n  SCENARI")
    for s in a.scenarios:
        _riga(f"   · {s.name} — p={s.likelihood:.2f}, orizzonte {s.horizon_hours} h: {s.narrative}")
        _riga(f"       conferme: {'; '.join(s.confirming_signals)}")
        for inv in s.invalidation:
            soglia = f" {inv.threshold if inv.threshold is not None else inv.target_state or ''}"
            _riga(
                f"       invalida se: {inv.subject or '-'} {inv.metric} {inv.direction}{soglia} "
                f"entro {inv.horizon_hours} h — {inv.description}"
            )
    _riga("\n  EVIDENZE A FAVORE")
    for e in a.supporting_evidence:
        _riga(f"   + {e.statement}  [{', '.join(e.refs)}]")
    _riga("  EVIDENZE CONTRARIE")
    for e in a.contradictory_evidence:
        _riga(f"   - {e.statement}  [{', '.join(e.refs)}]")
    _riga("\n  INCERTEZZE")
    for u in a.uncertainties:
        manca = f"  ⟶ manca: {u.missing_information}" if u.missing_information else ""
        _riga(f"   ? [{u.impact}] {u.description}{manca}")
    _riga("\n  DA OSSERVARE")
    for w in a.assets_to_watch:
        _riga(f"   ◦ {w.canonical}: {w.reason} — osservare: {w.watch_for}")
    _riga("\n  CONDIZIONI DI INVALIDAZIONE GENERALI")
    for inv in a.invalidation_conditions:
        soglia = f" {inv.threshold if inv.threshold is not None else inv.target_state or ''}"
        _riga(
            f"   × {inv.subject or '-'} {inv.metric} {inv.direction}{soglia} entro "
            f"{inv.horizon_hours} h — {inv.description}"
        )
    _riga("\n  LIMITAZIONI NOTE DEL CONTESTO (date al modello)")
    for lim in c.known_limitations:
        _riga(f"   ! {lim}")


def _non_configurate() -> tuple[ProviderStatus, ...]:
    from backend.providers.base import (
        NotConfiguredCalendar,
        NotConfiguredMacro,
        NotConfiguredNews,
        NotConfiguredRates,
        NotConfiguredReference,
    )

    return tuple(
        p.status()
        for p in (
            NotConfiguredReference(),
            NotConfiguredCalendar(),
            NotConfiguredRates(),
            NotConfiguredMacro(),
            NotConfiguredNews(),
        )
    )


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log, mode=str(settings.app.effective_mode))
    container = ApplicationContainer(settings)
    await container.startup()
    try:
        if container.broker is None:
            _riga(f"Broker non disponibile: {container.broker_error}")
            return 1
        servizio = container.require_global_intelligence()
        await container.ensure_broker_time()
        lettura = await servizio._lettore.leggi()
        esterni = await servizio._esterni()
        base = {
            "broker_offset_seconds": lettura.broker_offset_seconds,
            "broker_time_source": lettura.broker_time_source,
            "simulated": lettura.simulated,
            "source": lettura.source,
        }
        casi: list[tuple[str, GlobalMarketContext]] = []
        _, vivo = costruisci(
            lettura.ingressi,
            as_of=lettura.as_of,
            limite_broker=lettura.limite_broker,
            esterni=esterni,
            **base,
        )
        casi.append(("dal_vivo", vivo))
        _, senza = costruisci(
            lettura.ingressi,
            as_of=lettura.as_of,
            limite_broker=lettura.limite_broker,
            esterni=DatiEsterni(stati=_non_configurate()),
            **base,
        )
        casi.append(("dal_vivo_senza_fonti_esterne", senza))
        chiusi = [
            replace(
                i,
                market_status=MarketStatus.CLOSED_INFERRED,
                stati={tf: SeriesStatus.CLOSED for tf in i.stati if i.stati[tf].usable}
                | {tf: s for tf, s in i.stati.items() if not s.usable},
            )
            for i in lettura.ingressi
        ]
        stati_storici = tuple(
            s.model_copy(update={"state": ProviderState.AVAILABLE})
            if s.kind is not ProviderKind.ECONOMIC_CALENDAR
            and s.state is not ProviderState.NOT_CONFIGURED
            else s
            for s in esterni.stati
        )
        _, venerdi = costruisci(
            chiusi,
            as_of=CHIUSURA_VENERDI,
            limite_broker=CHIUSURA_VENERDI
            + timedelta(seconds=lettura.broker_offset_seconds or 10_800),
            esterni=replace(esterni, stati=stati_storici),
            **base,
        )
        casi.append(("replay_chiusura_venerdi", venerdi))

        cartella = PROJECT_ROOT / "docs" / "validazione"
        for nome, contesto in casi:
            record = await container.market_ai.analizza(contesto)
            stampa(nome, contesto, record)
            testo = json.dumps(
                {
                    "contesto": contesto.model_dump(mode="json"),
                    "record": record.model_dump(mode="json"),
                },
                indent=2,
                ensure_ascii=False,
            )
            await asyncio.to_thread(
                (cartella / f"ai_{nome}_{datetime.now():%Y-%m-%d_%H%M}.json").write_text,
                testo,
                encoding="utf-8",
            )
        return 0
    finally:
        await container.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
