"""Contenitore dell'applicazione: dipendenze costruite una volta sola.

Non è un framework di dependency injection: è un oggetto che tiene insieme le
dipendenze a vita lunga (configurazione, orologio, kill switch, bus, database,
cache, broker) e le passa a chi serve. Le classi restano pulite — ognuna riceve
ciò che usa nel costruttore e nei test si costruisce a mano.

Regola di avvio, scelta deliberata: **nessun guasto di un componente esterno
impedisce l'avvio.** Database giù, Redis giù, MT5 chiuso: il processo parte
comunque e `/api/health` spiega quale pezzo manca e perché non si può operare.
Il fail closed riguarda l'*operatività*, non la *diagnosticabilità*: un backend
che si rifiuta di partire non ha modo di dire cosa è rotto, ed è esattamente la
situazione da cui nasce questo progetto.
"""

from __future__ import annotations

from backend.ai.agents.market_analyst import MarketAnalyst
from backend.ai.factory import costruisci_client
from backend.ai.health import ClientMonitorato, SaluteAI
from backend.ai.journal import AnalysisJournal
from backend.brokers.base import ReadOnlyBroker
from backend.brokers.factory import build_broker
from backend.config.paths import resolve
from backend.config.settings import Settings
from backend.core.clock import Clock, SystemClock
from backend.core.enums import AssetClass
from backend.core.errors import TradingAgentError
from backend.core.events import Event, EventBus, EventType
from backend.core.kill_switch import KillSwitch
from backend.core.logging import get_logger
from backend.database.session import Database
from backend.global_market.reader import GlobalDataReader
from backend.market_data.ingestion import CandleIngestion
from backend.market_data.instruments import InstrumentRegistry
from backend.market_data.reader import MarketDataService
from backend.market_data.registry import SymbolRegistry
from backend.market_data.sessions import BrokerClock, QuoteStateDetector
from backend.market_data.specifications import SpecificationRepository
from backend.portfolio.deals import DealHistory
from backend.portfolio.exposure import ExposureCalculator
from backend.schemas.market import SymbolInfo
from backend.services.asset_analysis import AssetAnalyst
from backend.services.cache import RedisCache
from backend.services.global_intelligence import FontiEsterne, GlobalIntelligenceService
from backend.services.market_snapshot import MarketSnapshotService
from backend.services.persistence import MarketPersistence
from backend.services.system_journal import SystemJournal
from backend.trade_planner.intent import TradeIntentBuilder
from backend.valuation.service import BrokerValuationService

logger = get_logger(__name__)


class ApplicationContainer:
    """Dipendenze condivise per la durata del processo."""

    def __init__(self, settings: Settings, *, clock: Clock | None = None) -> None:
        self.settings = settings
        # Limiti modificati dall'utente dalla dashboard, sopra al .env (ADR 0026).
        from backend.config.risk_overrides import carica_all_avvio

        self.risk_override_notes = carica_all_avvio(settings)
        self.clock: Clock = clock or SystemClock()
        self.started_at = self.clock.now()

        self.kill_switch = KillSwitch(
            settings.app.kill_switch_file,
            forced=settings.app.kill_switch_enabled,
            clock=self.clock,
        )
        self.events = EventBus(on_handler_error=self._log_handler_error)
        self.database = Database(settings.db)
        self.cache = RedisCache(settings.redis)
        self.journal = SystemJournal(
            self.database, mode=settings.app.effective_mode, clock=self.clock
        )

        self.persistence = MarketPersistence(self.database, clock=self.clock)

        #: L'ora del broker vive nel contenitore e non nei servizi: una sola
        #: stima condivisa. Si stabilisce solo con più quotazioni concordi
        #: (`ensure_broker_time`), mai dal singolo tick che si sta valutando.
        self.broker_clock = BrokerClock(clock=self.clock)

        #: Il broker può non essere disponibile: in quel caso `broker_error`
        #: contiene il motivo in italiano, pronto per System Health.
        self.broker: ReadOnlyBroker | None = None
        self.broker_error: str | None = None
        self.market_data: MarketDataService | None = None

        #: Componenti di sola lettura, costruiti quando il broker risponde.
        self.registry: SymbolRegistry | None = None
        self.specifications: SpecificationRepository | None = None
        self.instruments: InstrumentRegistry | None = None
        self.valuation: BrokerValuationService | None = None
        self.intents: TradeIntentBuilder | None = None
        self.global_intelligence: GlobalIntelligenceService | None = None
        #: Fase 5A: analista AI READ ONLY. Non dipende dal broker: riceve solo il
        #: contesto globale già calcolato.
        #: Salute e consumo del provider AI, condivisa da analisi, tesi e notizie.
        self.ai_health = SaluteAI(self.clock, resolve(settings.paper.data_dir) / "ai_usage.json")
        self.market_ai = MarketAnalyst(
            self.client_ai(),
            AnalysisJournal(resolve(settings.ai.journal_dir)),
            temperature=settings.ai.temperature,
            max_output_tokens=settings.ai.max_output_tokens,
            clock=self.clock,
            persist=self.persistence.save_ai_analysis,
        )
        self.ingestion: CandleIngestion | None = None
        self.analyst: AssetAnalyst | None = None
        self.exposure: ExposureCalculator | None = None
        self.deals: DealHistory | None = None
        self.snapshots: MarketSnapshotService | None = None

    # ------------------------------------------------------------------
    # Ciclo di vita
    # ------------------------------------------------------------------
    async def startup(self) -> None:
        """Avvia i componenti. Non solleva per guasti di risorse esterne."""
        for warning in self.settings.startup_warnings():
            logger.warning("avvio.avviso", messaggio=warning)

        # Il diario ascolta tutti gli eventi: va iscritto prima di qualunque
        # altro componente, altrimenti i primi eventi non verrebbero registrati.
        self.events.subscribe_all(self.journal.handle, name="system_journal")

        await self._connect_broker()

        kill_switch_state = self.kill_switch.state()
        if kill_switch_state.engaged:
            logger.critical(
                "avvio.kill_switch_attivo",
                motivo=kill_switch_state.reason,
                origine=kill_switch_state.source,
            )

        await self.events.publish(
            Event(
                type=EventType.SYSTEM_STARTED,
                occurred_at=self.clock.now(),
                payload={
                    "message": "Backend avviato",
                    "mode": str(self.settings.app.effective_mode),
                    "broker": self.broker.name if self.broker else "non disponibile",
                    "simulato": self.broker.is_simulated if self.broker else None,
                },
                source="container",
            )
        )
        logger.info(
            "avvio.completato",
            mode=str(self.settings.app.effective_mode),
            broker_connesso=self.broker is not None,
        )

    async def _connect_broker(self) -> None:
        try:
            broker = build_broker(self.settings, clock=self.clock)
            health = await broker.connect()
            self.broker = broker
            self.market_data = MarketDataService(
                broker, self.settings.risk, clock=self.clock, broker_clock=self.broker_clock
            )
            conto = await broker.get_account()
            self._costruisci_analisi(broker, valuta_conto=conto.currency)
            self.broker_error = None
            await self.ensure_broker_time()
            logger.info("avvio.broker", stato=str(health.status), dettaglio=health.detail)
        except TradingAgentError as exc:
            # Nessun fallback al mock: vedi brokers/factory.py. Il sistema resta
            # senza dati di mercato e lo dichiara.
            self.broker = None
            self.market_data = None
            self.registry = None
            self.specifications = None
            self.instruments = None
            self.valuation = None
            self.intents = None
            self.global_intelligence = None
            self.ingestion = None
            self.analyst = None
            self.exposure = None
            self.deals = None
            self.snapshots = None
            self.broker_error = str(exc)
            logger.error("avvio.broker_non_disponibile", motivo=str(exc))
            await self.events.publish(
                Event(
                    type=EventType.BROKER_CONNECTION_LOST,
                    occurred_at=self.clock.now(),
                    payload={"message": str(exc)},
                    source="container",
                )
            )

    def client_ai(self) -> ClientMonitorato:
        """Client LLM dalla configurazione, con esiti e consumo registrati in `ai_health`."""
        return ClientMonitorato(costruisci_client(self.settings.ai), self.ai_health)

    def _costruisci_analisi(self, broker: ReadOnlyBroker, *, valuta_conto: str) -> None:
        """Monta la catena di sola lettura: registro, specifiche, analisi, snapshot.

        Tutto una volta sola, all'avvio. Ogni componente riceve soltanto quello
        che usa. Ogni numero monetario — profitto, perdita allo stop, margine,
        size — passa da un solo oggetto, `BrokerValuationService` (ADR 0014).
        """
        assert self.market_data is not None

        self.registry = SymbolRegistry(broker, clock=self.clock)
        self.specifications = SpecificationRepository(
            broker, clock=self.clock, persist=self._salva_specifica
        )
        self.instruments = InstrumentRegistry(self.specifications, clock=self.clock)
        self.valuation = BrokerValuationService(
            broker,  # type: ignore[arg-type]
            account_currency=valuta_conto,
        )
        self.intents = TradeIntentBuilder(self.valuation, clock=self.clock)
        self.ingestion = CandleIngestion(self.market_data, clock=self.clock)
        detector = QuoteStateDetector(
            max_tick_age_seconds=self.settings.mt5.max_tick_age_seconds,
            max_spread_points=self.settings.risk.max_spread_points,
            max_spread_atr_ratio=self.settings.risk.max_spread_atr_ratio,
            broker_clock=self.broker_clock,
        )
        self.analyst = AssetAnalyst(
            ingestion=self.ingestion,
            specifications=self.specifications,
            detector=detector,
            broker_clock=self.broker_clock,
            instruments=self.instruments,
            valuation=self.valuation,
            get_tick=self._tick,
            limits=self.settings.risk,
            clock=self.clock,
            ensure_broker_time=self.ensure_broker_time,
        )
        self.exposure = ExposureCalculator(
            valuation=self.valuation,
            instrument_of=self.instruments.by_broker_symbol,
            clock=self.clock,
        )
        self.deals = DealHistory(broker.get_deals, clock=self.clock)  # type: ignore[attr-defined]
        self.global_intelligence = GlobalIntelligenceService(
            GlobalDataReader(
                broker,
                self.instruments,
                self.broker_clock,
                max_tick_age_seconds=self.settings.mt5.max_tick_age_seconds,
                clock=self.clock,
            ),
            ensure_broker_time=self.ensure_broker_time,
            persist=self.persistence.save_global_context,
            fonti=self._fonti_esterne(),
            prima_minuti=self.settings.risk.block_high_impact_before_minutes,
            dopo_minuti=self.settings.risk.block_high_impact_after_minutes,
            clock=self.clock,
        )
        self.snapshots = MarketSnapshotService(
            broker=broker,
            registry=self.registry,
            analyst=self.analyst,
            exposure=self.exposure,
            detector=detector,
            broker_clock=self.broker_clock,
            limits=self.settings.risk,
            clock=self.clock,
            global_intelligence=self.global_intelligence,
        )

    # ------------------------------------------------------------------
    # Adattatori iniettati nei componenti
    # ------------------------------------------------------------------
    async def _salva_specifica(self, info: SymbolInfo, classe: AssetClass) -> None:
        in_whitelist = self.settings.risk.is_symbol_allowed(info.name)
        await self.persistence.save_symbol(info, classe, in_whitelist=in_whitelist)

    def _fonti_esterne(self) -> FontiEsterne:
        """Fonti esterne scelte in configurazione (EXT_*). `none` = non configurata."""
        from backend.providers.cboe import CboeVix
        from backend.providers.forexfactory import ForexFactoryCalendar
        from backend.providers.us_treasury import UsTreasuryRates

        cfg = self.settings.external
        limite = cfg.http_timeout_seconds
        return FontiEsterne(
            reference=CboeVix(timeout=limite, clock=self.clock)
            if cfg.vix_provider == "cboe"
            else None,
            calendar=ForexFactoryCalendar(timeout=limite, clock=self.clock)
            if cfg.calendar_provider == "forexfactory"
            else None,
            rates=UsTreasuryRates(timeout=limite, clock=self.clock)
            if cfg.rates_provider == "us_treasury"
            else None,
        )

    async def ensure_broker_time(self) -> bool:
        """Misura lo scarto dell'ora broker se non è già affidabile (ADR 0015).

        Legge le quotazioni di tutta la whitelist e lascia decidere a
        `BrokerClock.observe_many`, che stabilisce lo scarto solo se almeno due
        strumenti concordano. A mercato chiuso non ci riesce, ed è giusto così:
        nessuna quotazione potrà risultare valida finché non ci riesce.
        """
        stima = self.broker_clock.estimate()
        if stima.source == "MEASURED":
            return True
        if self.broker is None:
            return False
        tick = []
        for simbolo in self.settings.risk.allowed_symbols:
            try:
                tick.append(await self.broker.get_tick(simbolo))
            except TradingAgentError:
                continue
        misurato = self.broker_clock.observe_many(tick, now=self.clock.now())
        logger.info(
            "ora_broker.misura",
            esito="misurato" if misurato else "non_misurabile",
            quotazioni=len(tick),
            scarto_secondi=self.broker_clock.estimate().offset_seconds,
        )
        return misurato

    async def _tick(self, symbol: str) -> object:
        assert self.market_data is not None
        return (await self.market_data.get_tick(symbol)).value

    async def shutdown(self) -> None:
        """Chiude tutto in ordine inverso, senza mai sollevare."""
        await self.events.publish(
            Event(
                type=EventType.SYSTEM_STOPPED,
                occurred_at=self.clock.now(),
                payload={"message": "Backend in arresto"},
                source="container",
            )
        )
        if self.broker is not None:
            try:
                await self.broker.disconnect()
            except TradingAgentError:
                logger.warning("arresto.broker_errore", exc_info=True)
        try:
            await self.cache.close()
        except Exception:  # pragma: no cover - chiusura best effort
            logger.warning("arresto.redis_errore", exc_info=True)
        await self.database.dispose()
        logger.info("arresto.completato")

    # ------------------------------------------------------------------
    # Utilità
    # ------------------------------------------------------------------
    @property
    def uptime_seconds(self) -> float:
        return (self.clock.now() - self.started_at).total_seconds()

    def require_broker(self) -> ReadOnlyBroker:
        """Broker o errore parlante.

        Il messaggio riporta il motivo raccolto all'avvio: chi legge la risposta
        HTTP capisce cosa fare senza aprire i log.
        """
        if self.broker is None:
            raise TradingAgentError(
                self.broker_error or "Broker non disponibile: connessione a MT5 non stabilita."
            )
        return self.broker

    def require_analyst(self) -> AssetAnalyst:
        if self.analyst is None:
            raise TradingAgentError(
                self.broker_error or "Analisi non disponibile: connessione a MT5 non stabilita."
            )
        return self.analyst

    def require_snapshots(self) -> MarketSnapshotService:
        if self.snapshots is None:
            raise TradingAgentError(
                self.broker_error
                or "Istantanea di mercato non disponibile: connessione a MT5 non stabilita."
            )
        return self.snapshots

    def require_valuation(self) -> BrokerValuationService:
        if self.valuation is None or self.instruments is None:
            raise TradingAgentError(
                self.broker_error or "Valutazione non disponibile: connessione a MT5 non stabilita."
            )
        return self.valuation

    def require_instruments(self) -> InstrumentRegistry:
        if self.instruments is None:
            raise TradingAgentError(
                self.broker_error
                or "Registro degli strumenti non disponibile: connessione a MT5 non stabilita."
            )
        return self.instruments

    def require_global_intelligence(self) -> GlobalIntelligenceService:
        if self.global_intelligence is None:
            raise TradingAgentError(
                self.broker_error
                or "Intelligence globale non disponibile: connessione a MT5 non stabilita."
            )
        return self.global_intelligence

    def require_registry(self) -> SymbolRegistry:
        if self.registry is None:
            raise TradingAgentError(
                self.broker_error
                or "Registro dei simboli non disponibile: connessione a MT5 non stabilita."
            )
        return self.registry

    def require_market_data(self) -> MarketDataService:
        if self.market_data is None:
            raise TradingAgentError(
                self.broker_error
                or "Dati di mercato non disponibili: connessione a MT5 non stabilita."
            )
        return self.market_data

    @staticmethod
    def _log_handler_error(event: Event, handler: str, error: Exception) -> None:
        """Un handler rotto non interrompe gli altri, ma non passa inosservato."""
        logger.error(
            "bus.handler_fallito",
            event_type=str(event.type),
            handler=handler,
            errore=str(error),
        )
