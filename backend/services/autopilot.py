"""Scheduler dell'MVP operativo in PAPER: tre cicli indipendenti.

1. **Scansione** (`PAPER_SCAN_INTERVAL_SECONDS`): contesto globale → contesto di
   ogni strumento autorizzato → graduatoria → per i migliori: piano quant (conto
   QUANT) e tesi AI (conto AI) → `TradePlan` deterministico → gate di rischio →
   ingresso paper.
2. **Gestione** (`PAPER_MANAGE_INTERVAL_SECONDS`): ordini in attesa, SL/TP,
   invalidazioni, scadenze, swap, P/L non realizzato.
3. **Analisi AI del contesto** (`PAPER_MARKET_ANALYSIS_INTERVAL_SECONDS`, fase 5A).

I cicli girano in background nel processo del backend, mai dentro una richiesta
HTTP (ADR 0002). Un errore in un ciclo viene registrato e il ciclo riprende al
giro successivo: la piattaforma resta usabile. Nessun ciclo invia ordini al broker.

Costo dell'AI: una tesi per strumento al massimo una volta per barra H1, e non più
di `PAPER_MAX_AI_THESES_PER_CYCLE` tesi per scansione.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from backend.ai.contracts import AnalysisRecord
from backend.ai.input_builder import costruisci_ingresso
from backend.ai.news_classifier import NewsClassifier
from backend.ai.thesis.agent import ThesisAgent
from backend.ai.thesis.contracts import AiDecisionRecord
from backend.ai.thesis.input_builder import costruisci_ingresso_tesi
from backend.ai.thesis.prompts import THESIS_PROMPT_VERSION
from backend.config.paths import resolve
from backend.core.enums import Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.execution.stack import LiveStack, costruisci_stack
from backend.global_market.backdrop import sfondo
from backend.news.service import NewsService, asset_strumento
from backend.paper.executor import PaperExecutor
from backend.paper.ledger import PaperLedger
from backend.paper.planner import con_target, da_piano_ombra, inverti
from backend.paper.validation import trade_validati
from backend.risk.live_engine import RichiestaLive
from backend.scanner.ranking import classifica
from backend.schemas.context import AssetMarketContext
from backend.schemas.global_market import GlobalMarketContext
from backend.services.container import ApplicationContainer
from backend.services.controls import Controlli
from backend.shadow.evidence import costruisci_catalogo
from backend.shadow.groups import piano_tesi
from backend.shadow.quant_baseline import QUANT_VERSION, piano_quant

logger = get_logger(__name__)

AI_VERSION = f"ai-thesis|{THESIS_PROMPT_VERSION}"


class Autopilot:
    def __init__(self, container: ApplicationContainer) -> None:
        self.c = container
        self.cfg = container.settings.paper
        ai = container.settings.ai
        self.agente = ThesisAgent(
            container.client_ai(),
            temperature=ai.temperature,
            max_output_tokens=ai.max_output_tokens,
            clock=container.clock,
        )
        self.ledger = PaperLedger(resolve(self.cfg.data_dir))
        self.ledger.carica()
        self.executor: PaperExecutor | None = None
        self._task: list[asyncio.Task[None]] = []
        self.scansione_manuale: asyncio.Task[None] | None = None
        #: Fase 11: stack di esecuzione reale. Esiste, ma ogni invio rivaluta tutti i
        #: gate LIVE: con APP_MODE=PAPER nessun ordine reale può partire.
        self.live: LiveStack | None = None
        self.prontezza: dict[str, Any] | None = None
        self._broker_di: dict[str, str] = {}
        self.asset_per_simbolo: dict[str, set[str]] = {}
        #: Notizie da fonti gratuite: freno di rischio, evidenze per l'AI, lettura.
        cn = container.settings.news
        self.news: NewsService | None = None
        if cn.enabled:
            classificatore = (
                NewsClassifier(container.client_ai()).classifica if cn.ai_classification else None
            )
            self.news = NewsService(
                resolve(cn.data_dir),
                clock=container.clock,
                classificatore=classificatore,
                max_ai=cn.max_ai_items,
                freno_minuti=cn.brake_minutes,
            )
        #: Comandi dell'utente: pausa, mercati e strategie attive (dashboard).
        self.controlli = Controlli(
            resolve(self.cfg.data_dir) / "controls.json",
            tuple(container.settings.risk.allowed_symbols),
        )
        self._cache_ai: dict[str, datetime] = {}
        self.globale: GlobalMarketContext | None = None
        self.analisi: AnalysisRecord | None = None
        self.opportunita: list[dict[str, Any]] = []
        self.tesi: dict[str, dict[str, Any]] = {}
        self.decisioni: deque[dict[str, Any]] = deque(maxlen=60)
        self.stato: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "cycles": {"scan": 0, "manage": 0, "analysis": 0, "live": 0, "news": 0},
            "last": {"scan": None, "manage": None, "analysis": None, "live": None, "news": None},
            "last_error": {},
            "busy": None,
        }

    # ------------------------------------------------------------------
    # Avvio e arresto
    # ------------------------------------------------------------------
    async def avvia(self) -> None:
        c = self.c
        if c.broker is None or c.analyst is None or c.valuation is None:
            self.stato["last_error"]["start"] = f"Broker non disponibile: {c.broker_error}"
            return
        conto = await c.require_broker().get_account()
        self.ledger.inizializza(
            self.cfg.initial_balance or conto.balance, conto.currency, c.clock.now()
        )
        assert c.market_data and c.instruments and c.specifications and c.intents
        self.executor = PaperExecutor(
            ledger=self.ledger,
            market=c.market_data,
            instruments=c.instruments,
            specifications=c.specifications,
            valuation=c.valuation,
            intents=c.intents,
            broker_clock=c.broker_clock,
            limits=lambda: c.settings.risk,
            settings=self.cfg,
            clock=c.clock,
            kill_switch=lambda: c.kill_switch.state().engaged,
            contesto=self._contesto,
            freni_notizie=self._freni_notizie,
            stop_mobile=self.controlli.regola_mobile,
            # Il target QUANT scelto dall'utente vale anche come RR minimo di quel conto.
            rr_minimo=lambda libro: self.controlli.target_quant if libro == "QUANT" else None,
        )
        if self.news is not None:
            # Il contesto globale vede le notizie come fonte NEWS configurata.
            c.require_global_intelligence().fonti.news = self.news
        self.live = costruisci_stack(
            c,
            paper_trades=self.trade_paper,
            paper_critical_errors=self.errori_critici,
            barre=self._barre_live,
        )
        # Guasto o ripresa del provider AI: una riga nel diario, una sola per passaggio.
        c.ai_health.al_cambio = self._cambio_ai
        self.stato.update(running=True, started_at=c.clock.now().isoformat())
        self._task = [
            asyncio.create_task(self._ciclo("live", 60, self.ciclo_live)),
            *(
                [
                    asyncio.create_task(
                        self._ciclo("news", c.settings.news.interval_seconds, self.notizie)
                    )
                ]
                if self.news is not None
                else []
            ),
            asyncio.create_task(
                self._ciclo("manage", self.cfg.manage_interval_seconds, self.gestione)
            ),
            asyncio.create_task(
                self._ciclo("scan", self.cfg.scan_interval_seconds, self.scansione)
            ),
            asyncio.create_task(
                self._ciclo("analysis", self.cfg.market_analysis_interval_seconds, self.analizza)
            ),
        ]
        logger.info("autopilot.avvio", conti=list(self.ledger.books))

    async def ferma(self) -> None:
        for t in self._task:
            t.cancel()
        await asyncio.gather(*self._task, return_exceptions=True)
        self._task = []
        self.stato["running"] = False

    async def _ciclo(
        self, nome: str, intervallo: int, funzione: Callable[[], Awaitable[None]]
    ) -> None:
        if nome == "analysis":
            # L'analisi del contesto usa il contesto della prima scansione.
            await asyncio.sleep(30)
        while True:
            try:
                self.stato["busy"] = nome
                await funzione()
                self.stato["cycles"][nome] += 1
                self.stato["last"][nome] = self.c.clock.now().isoformat()
                self.stato["last_error"].pop(nome, None)
            except asyncio.CancelledError:
                raise
            except Exception as errore:
                self.stato["last_error"][nome] = f"{type(errore).__name__}: {errore}"[:500]
                logger.exception("autopilot.errore", ciclo=nome)
                # Un guasto transitorio del broker non è un difetto del ciclo; tutto il
                # resto (eccezioni inattese) è un errore critico e blocca il gate LIVE.
                if not (isinstance(errore, TradingAgentError) and errore.transient):
                    self.ledger.evento(
                        "CRITICAL_ERROR",
                        self.c.clock.now(),
                        cycle=nome,
                        error=f"{type(errore).__name__}: {errore}"[:500],
                    )
            finally:
                self.stato["busy"] = None
            await asyncio.sleep(intervallo)

    # ------------------------------------------------------------------
    # Cicli
    # ------------------------------------------------------------------
    async def _contesto(self, simbolo: str) -> AssetMarketContext:
        ctx = await self.c.require_analyst().analyse(simbolo)
        if ctx.instrument is not None and self.globale is not None:
            ctx = ctx.model_copy(update={"global_backdrop": sfondo(ctx.instrument, self.globale)})
        return ctx

    def _cambio_ai(self, ok: bool, tipo: str | None, spiegazione: str | None) -> None:
        self.ledger.evento(
            "AI_RESTORED" if ok else "AI_UNAVAILABLE",
            self.c.clock.now(),
            kind=tipo,
            reason=spiegazione or "Provider AI di nuovo raggiungibile.",
        )

    async def notizie(self) -> None:
        if self.news is not None:
            await self.news.raccogli()

    @staticmethod
    def _asset(ctx: AssetMarketContext) -> set[str]:
        i = ctx.instrument
        if i is None:
            return set()
        return asset_strumento(i.base_currency, i.quote_currency, str(i.asset_class))

    def _freni_notizie(self, ctx: AssetMarketContext) -> list[str]:
        return self.news.freni(self._asset(ctx)) if self.news is not None else []

    async def gestione(self) -> None:
        if self.executor is not None:
            await self.executor.gestisci()

    async def analizza(self) -> None:
        if self.globale is None:
            return
        # A mercato chiuso (fine settimana) il contesto non cambia: niente chiamata AI.
        if str(self.globale.market_phase) == "CLOSED_INFERRED":
            return
        self.analisi = await self.c.market_ai.analizza(self.globale)

    # ------------------------------------------------------------------
    # Fase 11: prontezza LIVE ed esecuzione reale (spenta finché i gate non sono veri)
    # ------------------------------------------------------------------
    def trade_paper(self) -> tuple[int, int]:
        trades = [t for b in self.ledger.books.values() for t in b.trades]
        validati, _ = trade_validati(trades)
        return validati, len(trades)

    def errori_critici(self) -> int:
        return sum(
            1 for e in self.ledger.ultimi_eventi(100_000) if e.get("type") == "CRITICAL_ERROR"
        )

    async def _barre_live(self, soggetto: str, tf: str) -> list[Any]:
        if self.executor is None:
            return []
        simbolo = self._broker_di.get(soggetto, soggetto)
        return [b for b, _ in await self.executor._barre(simbolo, Timeframe(tf), 300)]

    async def ciclo_live(self) -> None:
        if self.live is None:
            return
        self.prontezza = await self.live.readiness.valuta()
        if self.prontezza["ready"] and self.live.manager is not None:
            await self.live.manager.ciclo()

    async def _esegui_live(self, piano: Any, ctx: AssetMarketContext) -> None:
        """Instrada un piano verso il conto reale. Solo con tutti i gate veri."""
        if (
            self.live is None
            or self.live.gateway is None
            or self.live.engine is None
            or not (self.prontezza and self.prontezza["ready"])
            or piano.book != self.cfg.live_source
        ):
            return
        richiesta = RichiestaLive(
            plan_ref=piano.plan_id,
            canonical=piano.canonical,
            broker_symbol=piano.broker_symbol,
            direction=piano.direction,
            entry_reference=piano.entry_price,
            stop=piano.stop,
            target=piano.target,
            atr_h1=piano.atr_h1,
            spread_atr=ctx.quote.spread_atr_ratio,
            finestra_evento=bool(ctx.global_backdrop and ctx.global_backdrop.event_windows),
            dati_validi=ctx.usable_for_decisions,
            motivi_dati=ctx.blocking_reasons,
        )
        esito = await self.live.engine.approva(richiesta)
        if isinstance(esito, list):
            self._decisione(book="LIVE", symbol=piano.canonical, outcome="REJECTED", reasons=esito)
            return
        ordine = await self.live.gateway.apri(esito, plan=piano.model_dump(mode="json"))
        self._decisione(
            book="LIVE",
            symbol=piano.canonical,
            outcome=ordine.state,
            direction=piano.direction,
            rr=piano.rr,
        )

    def _decisione(self, **dati: Any) -> None:
        self.decisioni.appendleft({"at": self.c.clock.now().isoformat(), **dati})

    async def scansione(self) -> None:
        c = self.c
        assert self.executor is not None
        _, self.globale = await c.require_global_intelligence().build(force=True)
        contesti: dict[str, AssetMarketContext] = {}
        for simbolo in c.settings.risk.allowed_symbols:
            try:
                contesti[simbolo] = await self._contesto(simbolo)
            except TradingAgentError as errore:
                self._decisione(symbol=simbolo, outcome="SKIPPED", reasons=[str(errore)])
        self.opportunita = classifica(contesti, c.settings.risk)
        self.asset_per_simbolo = {s: self._asset(x) for s, x in contesti.items()}
        for r in self.opportunita:
            r["enabled"] = self.controlli.mercato_attivo(r["symbol"])
        if self.controlli.in_pausa:
            self._decisione(
                symbol="—",
                outcome="PAUSED",
                reasons=["Scheduler in pausa: analisi aggiornata, nessuna nuova apertura."],
            )
            return
        ingresso_globale = costruisci_ingresso(self.globale)
        adesso = c.clock.now()
        budget_ai = self.cfg.max_ai_theses_per_cycle
        limiti = c.settings.risk
        vincoli = {
            "minimum_rr": limiti.minimum_rr,
            "stop_distance_atr_h1": [limiti.min_stop_distance_atr, limiti.max_stop_distance_atr],
        }
        candidati = [r for r in self.opportunita if r["tradeable"] and r["enabled"]]
        for riga in candidati[: self.cfg.top_opportunities]:
            simbolo, canonico = riga["symbol"], riga["canonical"]
            self._broker_di[canonico] = simbolo
            ctx = contesti[simbolo]
            catalogo = costruisci_catalogo(ctx, canonico)
            occupati = {
                nome: {p.plan.canonical for p in b.positions}
                for nome, b in self.ledger.books.items()
            }

            # --- conto QUANT: baseline deterministica ---
            if canonico not in occupati["QUANT"] and self.controlli.strategia_attiva("QUANT"):
                ombra = piano_quant(ctx, catalogo, decided_at=adesso, mode="LIVE")
                piano = da_piano_ombra(
                    ombra, book="QUANT", broker_symbol=simbolo, strategy_version=QUANT_VERSION
                )
                if piano is not None and (tr := self.controlli.target_quant) is not None:
                    piano = con_target(piano, tr)
                if piano is not None and self.controlli.invertito("QUANT"):
                    piano = inverti(piano)
                if piano is None:
                    self._decisione(
                        book="QUANT", symbol=canonico, outcome="WAIT", reasons=list(ombra.reasons)
                    )
                else:
                    motivi = await self.executor.proponi(piano, ctx)
                    await self._esegui_live(piano, ctx)
                    self._decisione(
                        book="QUANT",
                        symbol=canonico,
                        outcome="REJECTED" if motivi else piano.entry_type,
                        direction=piano.direction,
                        rr=piano.rr,
                        reasons=motivi,
                    )

            # --- conto AI: tesi, al massimo una per barra H1 ---
            h1 = ctx.timeframes.get(Timeframe.H1)
            if (
                canonico in occupati["AI"]
                or not self.controlli.strategia_attiva("AI")
                or budget_ai <= 0
                or h1 is None
                or self._cache_ai.get(canonico) == h1.last_closed_at
            ):
                continue
            # Una tesi che il gate rifiuterebbe comunque è credito sprecato: se il conto
            # non può aprire nulla (posizioni al massimo, fuori sessione, limiti del
            # giorno...) l'AI non viene interpellata e la barra H1 resta disponibile.
            blocchi = self.executor.blocchi_conto("AI") + self._freni_notizie(ctx)
            if ctx.global_backdrop and ctx.global_backdrop.event_windows:
                blocchi.append(
                    "Finestra di un evento ad alto impatto sulle valute dello strumento."
                )
            if blocchi:
                self._decisione(
                    book="AI",
                    symbol=canonico,
                    outcome="SKIPPED",
                    reasons=["AI non interpellata: il conto non può aprire.", *blocchi[:2]],
                )
                continue
            budget_ai -= 1
            self._cache_ai[canonico] = h1.last_closed_at
            chiusure = {(canonico, str(tf)): a.indicators.close for tf, a in ctx.timeframes.items()}
            ingresso = costruisci_ingresso_tesi(
                ingresso_globale,
                catalogo,
                as_of=adesso,
                mode="LIVE",
                ultime_chiusure=chiusure,
                vincoli=vincoli,
                notizie=self.news.per_tesi(self._asset(ctx)) if self.news is not None else None,
            )
            record = await self.agente.tesi(
                ingresso,
                catalogo,
                ultime_chiusure=chiusure,
                qualita_globale=str(self.globale.data_quality),
            )
            self._salva_record(record)
            ombra = piano_tesi(
                record,
                reference_price=catalogo.prezzo,
                atr_h1=catalogo.atr_h1,
                spread_price=catalogo.spread_prezzo,
            )
            self.tesi[canonico] = self._sintesi(record, ombra.status)
            piano = da_piano_ombra(
                ombra, book="AI", broker_symbol=simbolo, strategy_version=AI_VERSION
            )
            if piano is not None and self.controlli.invertito("AI"):
                piano = inverti(piano)
            if piano is None:
                self._decisione(
                    book="AI",
                    symbol=canonico,
                    outcome=ombra.status,
                    reasons=list(ombra.reasons)[:3],
                )
                continue
            motivi = await self.executor.proponi(piano, ctx)
            await self._esegui_live(piano, ctx)
            self._decisione(
                book="AI",
                symbol=canonico,
                outcome="REJECTED" if motivi else piano.entry_type,
                direction=piano.direction,
                rr=piano.rr,
                reasons=motivi,
            )

    # ------------------------------------------------------------------
    # Registrazione
    # ------------------------------------------------------------------
    def _salva_record(self, record: AiDecisionRecord) -> None:
        cartella = self.ledger.cartella / "ai_records"
        cartella.mkdir(parents=True, exist_ok=True)
        i = record.input
        nome = f"{i.context_as_of:%Y%m%d_%H%M%S}_{i.canonical}_{record.record_id}.json"
        with (cartella / nome).open("x", encoding="utf-8") as f:
            f.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False))

    @staticmethod
    def _sintesi(record: AiDecisionRecord, stato_piano: str) -> dict[str, Any]:
        t = record.thesis
        return {
            "at": record.input.context_as_of.isoformat(),
            "status": record.status,
            "plan_status": stato_piano,
            "record_id": record.record_id,
            "model": record.call.model_snapshot or record.call.model_requested,
            "failure_reasons": list(record.failure_reasons[:4]),
            "decision": t.decision if t else None,
            "setup": t.setup.type if t else None,
            "confidence_label": t.confidence if t else None,
            "scenario": t.scenario.narrative if t else None,
            "wait_reason": t.wait_reason if t else None,
            "supporting": [c.statement for c in t.supporting_evidence[:3]] if t else [],
            "contradictory": [c.statement for c in t.contradictory_evidence[:3]] if t else [],
            "invalidations": [c.etichetta() for c in t.invalidation_conditions] if t else [],
        }
