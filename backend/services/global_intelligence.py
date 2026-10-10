"""Servizio dell'intelligence globale: lettura, fonti esterne, motore, cache, persistenza.

Il motore (`global_market/engine.py`) è puro; qui c'è il resto: misurare l'ora del
broker, leggere l'universo di riferimento da XM, interrogare le fonti esterne
(fase 4.5: VIX, curva dei rendimenti, calendario economico), salvare il risultato.

**Una fonte che non risponde non ferma niente.** Ogni fonte esterna viene
interrogata a parte; se fallisce, il suo stato diventa `FAILED` con il motivo, la
dimensione che ne dipende risulta non disponibile, e il resto del contesto si
costruisce comunque.

**Cache breve (60 secondi).** Un contesto costa circa 230 letture dal terminale.
Le fonti esterne hanno cache proprie, più lunghe (un'ora il calendario, sei ore VIX
e tassi): sono dati giornalieri o settimanali.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from backend.core.clock import Clock, SystemClock
from backend.core.logging import get_logger
from backend.global_market.engine import DatiEsterni, costruisci
from backend.global_market.reader import GlobalDataReader
from backend.providers.base import (
    EconomicCalendarProvider,
    MacroDataProvider,
    MarketReferenceProvider,
    NewsProvider,
    NotConfiguredCalendar,
    NotConfiguredMacro,
    NotConfiguredNews,
    NotConfiguredRates,
    NotConfiguredReference,
    ProviderError,
    RatesProvider,
)
from backend.schemas.external import CalendarSnapshot, ExternalSeries, YieldCurveHistory
from backend.schemas.global_market import GlobalMarketContext, GlobalMarketSnapshot

logger = get_logger(__name__)

DURATA_CACHE_SECONDI = 60.0

Salvataggio = Callable[[GlobalMarketSnapshot, GlobalMarketContext], Awaitable[None]]


class FontiEsterne:
    """Le cinque famiglie di provider. Quelle senza fonte sono `NotConfigured*`."""

    def __init__(
        self,
        *,
        reference: MarketReferenceProvider | None = None,
        calendar: EconomicCalendarProvider | None = None,
        rates: RatesProvider | None = None,
        macro: MacroDataProvider | None = None,
        news: NewsProvider | None = None,
    ) -> None:
        self.reference: MarketReferenceProvider = reference or NotConfiguredReference()
        self.calendar: EconomicCalendarProvider = calendar or NotConfiguredCalendar()
        self.rates: RatesProvider = rates or NotConfiguredRates()
        self.macro: MacroDataProvider = macro or NotConfiguredMacro()
        self.news: NewsProvider = news or NotConfiguredNews()

    def tutte(self) -> tuple[object, ...]:
        return (self.reference, self.calendar, self.rates, self.macro, self.news)

    def stati(self):  # type: ignore[no-untyped-def]
        return tuple(p.status() for p in self.tutte())  # type: ignore[attr-defined]


class GlobalIntelligenceService:
    def __init__(
        self,
        reader: GlobalDataReader,
        *,
        ensure_broker_time: Callable[[], Awaitable[object]] | None = None,
        persist: Salvataggio | None = None,
        fonti: FontiEsterne | None = None,
        prima_minuti: int = 60,
        dopo_minuti: int = 30,
        clock: Clock | None = None,
        cache_seconds: float = DURATA_CACHE_SECONDI,
    ) -> None:
        self._lettore = reader
        self._assicura_ora = ensure_broker_time
        self._salva = persist
        self.fonti = fonti or FontiEsterne()
        self._prima = prima_minuti
        self._dopo = dopo_minuti
        self._clock = clock or SystemClock()
        self._durata_cache = cache_seconds
        self.last: tuple[GlobalMarketSnapshot, GlobalMarketContext] | None = None

    async def _esterni(self) -> DatiEsterni:
        vix: ExternalSeries | None = None
        curva: YieldCurveHistory | None = None
        calendario: CalendarSnapshot | None = None
        if not isinstance(self.fonti.reference, NotConfiguredReference):
            try:
                vix = await self.fonti.reference.daily_series("VIX")
            except ProviderError as errore:
                logger.warning("globale.fonte_esterna_fallita", fonte="vix", motivo=str(errore))
        if not isinstance(self.fonti.rates, NotConfiguredRates):
            try:
                curva = await self.fonti.rates.yield_curve()
            except ProviderError as errore:
                logger.warning("globale.fonte_esterna_fallita", fonte="tassi", motivo=str(errore))
        if not isinstance(self.fonti.calendar, NotConfiguredCalendar):
            try:
                calendario = await self.fonti.calendar.calendar()
            except ProviderError as errore:
                logger.warning(
                    "globale.fonte_esterna_fallita", fonte="calendario", motivo=str(errore)
                )
        return DatiEsterni(
            vix=vix,
            curva=curva,
            calendario=calendario,
            stati=self.fonti.stati(),
            prima_minuti=self._prima,
            dopo_minuti=self._dopo,
        )

    async def build(
        self, *, force: bool = False
    ) -> tuple[GlobalMarketSnapshot, GlobalMarketContext]:
        adesso = self._clock.now()
        if (
            not force
            and self.last is not None
            and (adesso - self.last[1].as_of).total_seconds() <= self._durata_cache
        ):
            return self.last

        if self._assicura_ora is not None:
            await self._assicura_ora()
        lettura = await self._lettore.leggi()
        esterni = await self._esterni()
        snapshot, contesto = costruisci(
            lettura.ingressi,
            as_of=lettura.as_of,
            limite_broker=lettura.limite_broker,
            broker_offset_seconds=lettura.broker_offset_seconds,
            broker_time_source=lettura.broker_time_source,
            simulated=lettura.simulated,
            source=lettura.source,
            esterni=esterni,
        )
        logger.info(
            "globale.contesto",
            regime=str(contesto.regime),
            fase=str(contesto.market_phase),
            qualita=str(contesto.data_quality),
            conflitti=len(contesto.conflicts),
            impronta=contesto.input_fingerprint[:12],
        )
        self.last = (snapshot, contesto)
        if self._salva is not None:
            await self._salva(snapshot, contesto)
        return snapshot, contesto
