"""Esecuzione e gestione paper: ingresso, SL/TP/invalidazione/tempo, costi, P/L.

**Non invia ordini.** Legge prezzi dal broker e gli chiede calcoli (profitto, size)
tramite `BrokerValuationService`; posizioni e saldo esistono solo nel `PaperLedger`.

Realismo dei costi, tutti dichiarati:

* **spread** — LONG entra all'ask ed esce al bid, SHORT il contrario; sulle barre
  (bid) lo spread di ogni barra M1 si aggiunge al lato dell'uscita;
* **slittamento** — `PAPER_SLIPPAGE_POINTS` contro di noi su ingressi a mercato e
  STOP e sulle uscite allo stop (non sul target, che è un limite);
* **commissioni** — `PAPER_COMMISSION_PER_LOT` per lato;
* **swap** — dai valori del broker (`swap_long`/`swap_short`, modalità in punti) a
  ogni rollover della mezzanotte broker, triplo nel giorno dichiarato dal broker.

Gestione: a ogni ciclo si esaminano le barre M1 chiuse dopo l'ultimo controllo
(niente si perde fra due cicli) e poi il tick corrente. L'ambiguità dentro la barra
si risolve con `geometry.risolvi_barra` (gap, poi stop prudente).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from backend.config.risk_limits import RiskLimits
from backend.config.settings import PaperSettings
from backend.core.clock import Clock
from backend.core.enums import Direction, Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.market_data.instruments import InstrumentRegistry
from backend.market_data.reader import MarketDataService
from backend.market_data.sessions import BrokerClock
from backend.market_data.specifications import SpecificationRepository
from backend.paper.geometry import risolvi_barra
from backend.paper.ledger import PaperLedger
from backend.paper.models import PaperBook, PaperPosition, PaperTrade, TradePlan
from backend.paper.trailing import REGOLE, Ombra, esito_barra, nuovo_stop_r, prezzo_stop
from backend.risk.gate import RichiestaIngresso, valuta_ingresso
from backend.schemas.context import AssetMarketContext
from backend.schemas.instrument import Instrument
from backend.shadow.conditions import Barra, prima_volta
from backend.trade_planner.intent import TradeIntentBuilder
from backend.valuation.service import BrokerValuationService

logger = get_logger(__name__)

BARRE_M1 = 240
#: Tetto della lettura dopo un fermo: circa due settimane di M1.
BARRE_M1_MAX = 20_000


class PaperExecutor:
    def __init__(
        self,
        *,
        ledger: PaperLedger,
        market: MarketDataService,
        instruments: InstrumentRegistry,
        specifications: SpecificationRepository,
        valuation: BrokerValuationService,
        intents: TradeIntentBuilder,
        broker_clock: BrokerClock,
        limits: RiskLimits | Callable[[], RiskLimits],
        settings: PaperSettings,
        clock: Clock,
        kill_switch: Callable[[], bool],
        contesto: Callable[[str], Awaitable[AssetMarketContext]],
        freni_notizie: Callable[[AssetMarketContext], list[str]] | None = None,
        stop_mobile: Callable[[str], str | None] | None = None,
        rr_minimo: Callable[[str], float | None] | None = None,
    ) -> None:
        self.ledger = ledger
        self._m = market
        self._strumenti = instruments
        self._specifiche = specifications
        self._v = valuation
        self._intenti = intents
        self._ora_broker = broker_clock
        # I limiti si leggono a ogni decisione: l'utente può cambiarli dalla dashboard.
        self._fonte_limiti = limits
        self._cfg = settings
        self._clock = clock
        self._kill = kill_switch
        self._contesto = contesto
        self._freni_notizie = freni_notizie
        #: Regola di stop mobile accesa per un conto (None = spenta). Si fissa
        #: all'apertura: spegnerla non cambia le posizioni già aperte (ADR 0027).
        self._stop_mobile = stop_mobile
        #: RR minimo per conto scelto dall'utente (target QUANT): solo più basso del
        #: limite generale, e solo per quel conto (ADR 0029).
        self._rr_minimo = rr_minimo

    def _regola_mobile(self, libro: str) -> str | None:
        regola = self._stop_mobile(libro) if self._stop_mobile else None
        return regola if regola in REGOLE else None

    @property
    def _limiti(self) -> RiskLimits:
        f = self._fonte_limiti
        return f() if callable(f) else f

    # ------------------------------------------------------------------
    # Utilità
    # ------------------------------------------------------------------
    def _scarto(self) -> timedelta | None:
        stima = self._ora_broker.estimate(now=self._clock.now())
        if stima.reliable and stima.offset_seconds is not None:
            return timedelta(seconds=stima.offset_seconds)
        return None

    def ora_broker(self) -> datetime | None:
        scarto = self._scarto()
        return self._clock.now() + scarto if scarto is not None else None

    async def _barre(self, simbolo: str, tf: Timeframe, n: int) -> list[tuple[Barra, float]]:
        """Barre chiuse in UTC, con lo spread di ciascuna in prezzo."""
        scarto = self._scarto()
        if scarto is None:
            return []
        info = (await self._specifiche.get(simbolo)).value
        serie = (await self._m.get_candles(simbolo, tf, n)).value
        durata = timedelta(minutes=tf.minutes)
        return [
            (
                Barra(c.time - scarto, c.time - scarto + durata, c.open, c.high, c.low, c.close),
                (c.spread or info.spread_points) * info.point,
            )
            for c in serie.closed
        ]

    async def _valore(
        self, strumento: Instrument, volume: float, prezzo: float, delta: float
    ) -> float:
        """Valore in valuta del conto di un movimento `delta` per `volume`, dal broker."""
        if volume <= 0 or delta == 0:
            return 0.0
        return await self._v.profit(strumento, Direction.LONG, volume, prezzo, prezzo + delta)

    @staticmethod
    def equity(book: PaperBook) -> float:
        return book.balance + sum(p.unrealized_pnl or 0.0 for p in book.positions)

    @staticmethod
    def picco(book: PaperBook) -> float:
        saldo = picco = book.initial_balance
        for t in sorted(book.trades, key=lambda x: x.closed_at):
            saldo += t.net_pnl
            picco = max(picco, saldo)
        return picco

    def _pnl_settimana(self, book: PaperBook) -> float:
        ora = self.ora_broker()
        scarto = self._scarto() or timedelta(0)
        if ora is None:
            return 0.0
        inizio = (ora - timedelta(days=ora.weekday())).date()
        realizzato = sum(t.net_pnl for t in book.trades if (t.closed_at + scarto).date() >= inizio)
        return realizzato + sum(p.unrealized_pnl or 0.0 for p in book.positions)

    def _pnl_oggi(self, book: PaperBook) -> float:
        ora = self.ora_broker()
        scarto = self._scarto() or timedelta(0)
        if ora is None:
            return 0.0
        oggi = ora.date()
        return sum(t.net_pnl for t in book.trades if (t.closed_at + scarto).date() == oggi)

    def blocchi_conto(self, libro: str) -> list[str]:
        """Motivi per cui il conto non può aprire **nulla** adesso, qualunque piano arrivi.

        Stesso gate di `_dimensiona`, con una richiesta neutra (RR minimo, stop minimo,
        rischio simbolico): restano solo i blocchi del conto — kill switch, posizioni
        massime, sessione, perdita od obiettivo giornaliero, settimana, drawdown,
        rischio aperto già al tetto. Serve a non pagare una tesi AI che il gate
        rifiuterebbe comunque.
        """
        book = self.ledger.books[libro]
        lim = self._limiti
        aperti = list(book.positions)
        return valuta_ingresso(
            RichiestaIngresso(
                simbolo="",
                rr=lim.minimum_rr,
                stop_atr=lim.min_stop_distance_atr,
                spread_atr=None,
                rischio=0.01,
                equity=self.equity(book),
                posizioni_aperte=len(aperti),
                simboli_aperti=frozenset(),
                rischio_aperto=sum(p.risk_amount for p in aperti),
                pnl_oggi=self._pnl_oggi(book),
                pnl_settimana=self._pnl_settimana(book),
                picco_equity=self.picco(book),
                ora_broker=self.ora_broker(),
                kill_switch=self._kill(),
                finestra_evento=False,
                dati_validi=True,
            ),
            lim,
        )

    # ------------------------------------------------------------------
    # Ingresso
    # ------------------------------------------------------------------
    async def proponi(self, plan: TradePlan, ctx: AssetMarketContext) -> list[str]:
        """Prova ad aprire il piano. Restituisce i motivi di rifiuto (vuoto = aperto)."""
        async with self.ledger.lock:
            book = self.ledger.books[plan.book]
            if plan.entry_type == "MARKET":
                motivi = await self._apri_mercato(book, plan, ctx)
            else:
                motivi = await self._registra_attesa(book, plan, ctx)
            if motivi:
                self.ledger.rifiuto(plan.book, self._clock.now(), plan.canonical, motivi)
            self.ledger.salva()
            return motivi

    async def _dimensiona(
        self, book: PaperBook, plan: TradePlan, ctx: AssetMarketContext, entrata: float
    ) -> tuple[float, float, list[str]]:
        strumento = ctx.instrument or await self._strumenti.by_broker_symbol(plan.broker_symbol)
        equity = self.equity(book)
        budget = equity * self._limiti.max_risk_per_trade_pct / 100
        motivi: list[str] = []
        volume = rischio = 0.0
        if ctx.quote.quality is None:
            motivi.append("Qualità della quotazione non disponibile.")
        else:
            intento = await self._intenti.build(
                strumento,
                Direction(plan.direction),
                entry=entrata,
                stop=plan.stop,
                risk_budget=budget,
                quote=ctx.quote.quality,
                max_volume=self._limiti.max_position_size_lots,
            )
            volume, rischio = intento.volume, intento.expected_loss
            if not intento.executable:
                motivi.extend(intento.blocking_reasons)
        aperti = list(book.positions)
        backdrop = ctx.global_backdrop
        motivi += valuta_ingresso(
            RichiestaIngresso(
                simbolo=plan.canonical,
                rr=plan.rr,
                stop_atr=abs(entrata - plan.stop) / plan.atr_h1,
                spread_atr=ctx.quote.spread_atr_ratio,
                rischio=rischio,
                equity=equity,
                posizioni_aperte=len(aperti),
                simboli_aperti=frozenset(p.plan.canonical for p in aperti),
                rischio_aperto=sum(p.risk_amount for p in aperti),
                pnl_oggi=self._pnl_oggi(book),
                pnl_settimana=self._pnl_settimana(book),
                motivi_notizie=tuple(self._freni_notizie(ctx)) if self._freni_notizie else (),
                picco_equity=self.picco(book),
                ora_broker=self.ora_broker(),
                kill_switch=self._kill(),
                finestra_evento=bool(backdrop and backdrop.event_windows),
                dati_validi=ctx.usable_for_decisions,
                motivi_dati=ctx.blocking_reasons,
            ),
            self._limiti_conto(plan.book),
        )
        return volume, rischio, list(dict.fromkeys(motivi))

    def _limiti_conto(self, libro: str) -> RiskLimits:
        lim = self._limiti
        minimo = self._rr_minimo(libro) if self._rr_minimo else None
        if minimo is not None and minimo < lim.minimum_rr:
            return lim.model_copy(update={"minimum_rr": minimo})
        return lim

    async def _apri_mercato(
        self, book: PaperBook, plan: TradePlan, ctx: AssetMarketContext
    ) -> list[str]:
        try:
            tick = (await self._m.get_tick(plan.broker_symbol)).value
        except TradingAgentError as errore:
            return [f"Nessuna quotazione: {errore}"]
        info = (await self._specifiche.get(plan.broker_symbol)).value
        slit = self._cfg.slippage_points * info.point
        entrata = tick.ask + slit if plan.verso > 0 else tick.bid - slit
        if plan.verso * (entrata - plan.stop) <= 0 or plan.verso * (plan.target - entrata) <= 0:
            return ["Il prezzo è già oltre stop o target del piano."]
        volume, rischio, motivi = await self._dimensiona(book, plan, ctx, entrata)
        if motivi:
            return motivi
        adesso = self._clock.now()
        posizione = PaperPosition(
            position_id=uuid.uuid4().hex[:16],
            plan=plan,
            status="OPEN",
            opened_at=adesso,
            entry_fill=entrata,
            volume=volume,
            risk_amount=rischio,
            spread_at_entry=tick.ask - tick.bid,
            slippage_entry=slit,
            commission=self._cfg.commission_per_lot * volume,
            checked_until=adesso,
            last_price=tick.bid if plan.verso > 0 else tick.ask,
            trailing_rule=self._regola_mobile(plan.book),
            ombre=self._ombre_nuove(),
        )
        book.positions.append(posizione)
        self.ledger.evento(
            "OPEN",
            adesso,
            book=plan.book,
            symbol=plan.canonical,
            direction=plan.direction,
            price=entrata,
            volume=volume,
            stop=plan.stop,
            target=plan.target,
            rr=plan.rr,
            strategy=plan.strategy_version,
        )
        logger.info(
            "paper.apertura",
            libro=plan.book,
            simbolo=plan.canonical,
            verso=plan.direction,
            prezzo=entrata,
            volume=volume,
        )
        return []

    async def _registra_attesa(
        self, book: PaperBook, plan: TradePlan, ctx: AssetMarketContext
    ) -> list[str]:
        # Gate già ora, con la size stimata al livello: un ordine che verrebbe comunque
        # rifiutato non si registra. Al momento dell'innesco il gate si ripete.
        _, _, motivi = await self._dimensiona(book, plan, ctx, plan.entry_price)
        if motivi:
            return motivi
        adesso = self._clock.now()
        book.positions.append(
            PaperPosition(
                position_id=uuid.uuid4().hex[:16], plan=plan, status="PENDING", checked_until=adesso
            )
        )
        self.ledger.evento(
            "PENDING",
            adesso,
            book=plan.book,
            symbol=plan.canonical,
            direction=plan.direction,
            type=plan.entry_type,
            level=plan.entry_price,
            valid_until=plan.valid_until,
        )
        return []

    # ------------------------------------------------------------------
    # Gestione
    # ------------------------------------------------------------------
    async def gestisci(self) -> None:
        async with self.ledger.lock:
            for book in self.ledger.books.values():
                for p in list(book.positions):
                    try:
                        await self._gestisci(book, p)
                    except TradingAgentError as errore:
                        nota = f"{self._clock.now():%H:%M} gestione rinviata: {errore}"
                        p.notes = [*p.notes[-4:], nota]
                        logger.warning(
                            "paper.gestione", simbolo=p.plan.canonical, motivo=str(errore)
                        )
            self.ledger.salva()

    async def _invalidazione(self, p: PaperPosition, dopo: datetime) -> datetime | None:
        adesso = self._clock.now()
        prime: list[datetime] = []
        for c in p.plan.invalidations:
            tf = Timeframe(c.timeframe)
            barre = [
                b
                for b, _ in await self._barre(
                    p.plan.broker_symbol if c.subject == p.plan.canonical else c.subject, tf, 300
                )
            ]
            quando = prima_volta(c, barre, dopo=dopo, entro=adesso + timedelta(days=1))
            if quando is not None:
                prime.append(quando)
        return min(prime) if prime else None

    async def _gestisci(self, book: PaperBook, p: PaperPosition) -> None:
        adesso = self._clock.now()
        plan = p.plan
        # Abbastanza barre da coprire tutto il tempo dall'ultimo controllo: con un numero
        # fisso (4 ore) un backend spento più a lungo perdeva stop e target in silenzio.
        n = BARRE_M1
        if p.checked_until is not None:
            minuti = int((adesso - p.checked_until).total_seconds() // 60) + 5
            n = min(max(n, minuti), BARRE_M1_MAX)
        serie = await self._barre(plan.broker_symbol, Timeframe.M1, n)
        if p.checked_until is not None and serie and serie[0][0].inizio > p.checked_until:
            buco = f"{p.checked_until:%d/%m %H:%M}-{serie[0][0].inizio:%d/%m %H:%M} UTC"
            if not any(buco in x for x in p.notes):
                p.notes = [*p.notes[-4:], f"Barre M1 non disponibili {buco}: non verificate."]
        barre = [
            (b, sp) for b, sp in serie if p.checked_until is None or b.inizio >= p.checked_until
        ]
        info = (await self._specifiche.get(plan.broker_symbol)).value
        slit = self._cfg.slippage_points * info.point
        s = plan.verso
        if p.status == "PENDING":
            await self._innesca(book, p, barre)
            if p.status == "PENDING" or p not in book.positions:
                return
            assert p.opened_at is not None and p.entry_fill is not None
            # Barra d'innesco: dopo il riempimento il prezzo può aver toccato lo stop
            # nella stessa barra. Prima non si controllava (errore ottimistico).
            for b, spread in barre:
                if b.inizio != p.opened_at:
                    continue
                if (b.low <= plan.stop) if s > 0 else (b.high + spread >= plan.stop):
                    prezzo = plan.stop - s * slit
                    p.mae_price = max(p.mae_price, s * (p.entry_fill - prezzo))
                    p.notes = [
                        *p.notes[-4:],
                        "Stop toccato nella barra d'ingresso: ordine ignoto, si assume lo stop.",
                    ]
                    await self._chiudi(book, p, prezzo, "SL", b.fine, ambigua=True)
                    return
            barre = [(b, sp) for b, sp in barre if b.inizio > p.opened_at]
        await self._swap(p)
        assert p.opened_at is not None and p.entry_fill is not None
        invalidata = await self._invalidazione(p, p.opened_at) if plan.invalidations else None
        distanza = abs(p.entry_fill - plan.stop)
        for b, spread in barre:
            p.checked_until = b.fine
            favorevole = s * ((b.high if s > 0 else b.low + spread) - p.entry_fill)
            avverso = s * (p.entry_fill - (b.low if s > 0 else b.high + spread))
            # Prima le ombre, con gli stop decisi dalle barre precedenti: stessa regola
            # di risoluzione del trade vero.
            self._ombre_barra(p, b, spread, slit, distanza)
            stop = self._stop_effettivo(p, distanza)
            esito = risolvi_barra(
                s, stop, plan.target, open_=b.open, high=b.high, low=b.low, spread=spread
            )
            if esito.evento == "SL":
                prezzo = (esito.prezzo or stop) - s * slit
                p.mae_price = max(p.mae_price, s * (p.entry_fill - prezzo))
                await self._chiudi(
                    book, p, prezzo, self._motivo_stop(p), b.fine, ambigua=esito.ambiguo
                )
                return
            p.mae_price = max(p.mae_price, avverso)
            if esito.evento == "TP":
                p.mfe_price = max(p.mfe_price, s * (plan.target - p.entry_fill))
                await self._chiudi(book, p, plan.target, "TP", b.fine)
                return
            p.mfe_price = max(p.mfe_price, favorevole)
            if distanza > 0:
                self._nuovo_picco(p, favorevole / distanza)
            if invalidata is not None and invalidata <= b.fine:
                prezzo = b.close if s > 0 else b.close + spread
                await self._chiudi(book, p, prezzo, "INVALIDATION", b.fine)
                return
            if b.fine >= plan.expires_at:
                prezzo = b.close if s > 0 else b.close + spread
                await self._chiudi(book, p, prezzo, "TIME", b.fine)
                return

        # Tick corrente: la barra in formazione non è ancora fra quelle chiuse.
        tick = (await self._m.get_tick(plan.broker_symbol)).value
        uscita = tick.bid if s > 0 else tick.ask
        p.last_price = uscita
        # MFE/MAE anche sul tick: prima si aggiornavano solo sulle barre chiuse, e un
        # trade chiuso dal tick registrava un massimo inferiore all'uscita (trovato su
        # XAUUSD: +2,07R chiuso, MFE 1,54R). Il picco dello stop mobile no (vedi trailing).
        p.mfe_price = max(p.mfe_price, s * (uscita - p.entry_fill))
        p.mae_price = max(p.mae_price, s * (p.entry_fill - uscita))
        stop = self._stop_effettivo(p, distanza)
        if s * (uscita - stop) <= 0:
            prezzo = uscita - s * slit
            p.mae_price = max(p.mae_price, s * (p.entry_fill - prezzo))
            await self._chiudi(book, p, prezzo, self._motivo_stop(p), adesso)
            return
        if s * (uscita - plan.target) >= 0:
            p.mfe_price = max(p.mfe_price, s * (plan.target - p.entry_fill))
            await self._chiudi(book, p, plan.target, "TP", adesso)
            return
        if invalidata is not None:
            await self._chiudi(book, p, uscita, "INVALIDATION", adesso)
            return
        if adesso >= plan.expires_at:
            await self._chiudi(book, p, uscita, "TIME", adesso)
            return
        strumento = await self._strumenti.by_broker_symbol(plan.broker_symbol)
        lordo = await self._v.profit(
            strumento, Direction(plan.direction), p.volume, p.entry_fill, uscita
        )
        p.unrealized_pnl = round(lordo - p.commission + p.swap, 2)

    # ------------------------------------------------------------------
    # Stop mobile (reale se acceso per il conto) e misura in ombra
    # ------------------------------------------------------------------
    @staticmethod
    def _ombre_nuove() -> dict[str, Ombra]:
        """Ombre create all'apertura: una posizione aperta prima della misura (o senza
        il suo picco dall'inizio) non ne ha e resta fuori dal confronto."""
        return {nome: Ombra() for nome in REGOLE}

    @staticmethod
    def _stop_effettivo(p: PaperPosition, distanza: float) -> float:
        if p.stop_r is None or p.entry_fill is None:
            return p.plan.stop
        return prezzo_stop(p.entry_fill, p.plan.verso, distanza, p.stop_r)

    @staticmethod
    def _motivo_stop(p: PaperPosition) -> str:
        return "TRAIL" if p.stop_r is not None else "SL"

    @staticmethod
    def _nuovo_picco(p: PaperPosition, favorevole_r: float) -> None:
        """Picco su barra chiusa: sposta gli stop delle ombre e, se acceso, quello vero."""
        if favorevole_r <= p.picco_r:
            return
        p.picco_r = favorevole_r
        for nome, o in p.ombre.items():
            if o.esito_r is None:
                o.stop_r = nuovo_stop_r(REGOLE[nome], p.picco_r, o.stop_r)
        if p.trailing_rule in REGOLE:
            p.stop_r = nuovo_stop_r(REGOLE[p.trailing_rule], p.picco_r, p.stop_r)

    @staticmethod
    def _ombre_barra(
        p: PaperPosition, b: Barra, spread: float, slit: float, distanza: float
    ) -> None:
        assert p.entry_fill is not None
        if distanza <= 0:
            return
        for o in p.ombre.values():
            if o.esito_r is not None or o.stop_r is None:
                # Con lo stop iniziale l'ombra coincide col trade vero: la chiude _chiudi.
                continue
            o.esito_r = esito_barra(
                p.plan.verso,
                p.entry_fill,
                distanza,
                prezzo_stop(p.entry_fill, p.plan.verso, distanza, o.stop_r),
                p.plan.target,
                open_=b.open,
                high=b.high,
                low=b.low,
                spread=spread,
                slittamento=slit,
            )

    @staticmethod
    def _chiudi_ombre(
        p: PaperPosition, prezzo: float, slit: float, distanza: float
    ) -> dict[str, float]:
        """Esito di ogni regola: quello già avvenuto, oppure l'uscita del trade vero.

        Se il trade vero esce oltre lo stop di un'ombra (es. stop iniziale preso sul
        tick), il prezzo è passato prima da quello stop: l'ombra esce lì.
        """
        assert p.entry_fill is not None
        if distanza <= 0:
            return {}
        s = p.plan.verso
        esiti: dict[str, float] = {}
        for nome, o in p.ombre.items():
            if o.esito_r is None:
                if o.stop_r is not None:
                    livello = prezzo_stop(p.entry_fill, s, distanza, o.stop_r)
                    if s * (prezzo - livello) <= 0:
                        o.esito_r = round(s * (livello - s * slit - p.entry_fill) / distanza, 3)
                if o.esito_r is None:
                    o.esito_r = round(s * (prezzo - p.entry_fill) / distanza, 3)
            esiti[nome] = o.esito_r
        return esiti

    async def _innesca(
        self, book: PaperBook, p: PaperPosition, barre: list[tuple[Barra, float]]
    ) -> None:
        plan = p.plan
        adesso = self._clock.now()
        invalidata = await self._invalidazione(p, plan.created_at) if plan.invalidations else None
        info = (await self._specifiche.get(plan.broker_symbol)).value
        slit = self._cfg.slippage_points * info.point
        s, livello = plan.verso, plan.entry_price
        for b, spread in barre:
            p.checked_until = b.fine
            if invalidata is not None and invalidata <= b.inizio:
                self._annulla(book, p, "invalidato prima dell'ingresso", b.fine)
                return
            if b.inizio >= plan.valid_until:
                self._annulla(book, p, "scaduto senza innesco", b.fine)
                return
            ask_alto, ask_basso, ask_open = b.high + spread, b.low + spread, b.open + spread
            prezzo: float | None = None
            if plan.entry_type == "STOP":
                if s > 0 and ask_alto >= livello:
                    prezzo = max(livello, ask_open) + slit
                elif s < 0 and b.low <= livello:
                    prezzo = min(livello, b.open) - slit
            elif plan.entry_type == "LIMIT":
                if s > 0 and ask_basso <= livello:
                    prezzo = min(livello, ask_open)
                elif s < 0 and b.high >= livello:
                    prezzo = max(livello, b.open)
            if prezzo is None:
                continue
            ctx = await self._contesto(plan.broker_symbol)
            # Il gate non deve contare questo stesso ordine fra quelli aperti.
            indice = book.positions.index(p)
            book.positions.remove(p)
            try:
                volume, rischio, motivi = await self._dimensiona(book, plan, ctx, prezzo)
            finally:
                book.positions.insert(indice, p)
            if motivi:
                self._annulla(
                    book, p, "innescato ma rifiutato dal gate: " + "; ".join(motivi), b.fine
                )
                return
            p.status, p.opened_at, p.entry_fill = "OPEN", b.inizio, prezzo
            p.volume, p.risk_amount = volume, rischio
            p.spread_at_entry, p.slippage_entry = spread, slit if plan.entry_type == "STOP" else 0.0
            p.commission = self._cfg.commission_per_lot * volume
            p.trailing_rule = self._regola_mobile(plan.book)
            p.ombre = self._ombre_nuove()
            self.ledger.evento(
                "OPEN",
                b.inizio,
                book=plan.book,
                symbol=plan.canonical,
                direction=plan.direction,
                price=prezzo,
                volume=volume,
                type=plan.entry_type,
                strategy=plan.strategy_version,
            )
            return
        if adesso >= plan.valid_until:
            self._annulla(book, p, "scaduto senza innesco", adesso)

    async def chiudi_manuale(self, libro: str, position_id: str) -> str:
        """Chiusura (o annullamento, se in attesa) chiesta dall'utente. Ai prezzi correnti."""
        async with self.ledger.lock:
            book = self.ledger.books[libro]
            p = next((x for x in book.positions if x.position_id == position_id), None)
            if p is None:
                return "non trovata (forse già chiusa)"
            adesso = self._clock.now()
            if p.status == "PENDING":
                self._annulla(book, p, "annullato dall'utente", adesso)
                esito = "ordine annullato"
            else:
                tick = (await self._m.get_tick(p.plan.broker_symbol)).value
                info = (await self._specifiche.get(p.plan.broker_symbol)).value
                slit = self._cfg.slippage_points * info.point
                prezzo = tick.bid - slit if p.plan.verso > 0 else tick.ask + slit
                if p.entry_fill is not None:
                    risultato = p.plan.verso * (prezzo - p.entry_fill)
                    p.mfe_price = max(p.mfe_price, risultato)
                    p.mae_price = max(p.mae_price, -risultato)
                await self._chiudi(book, p, prezzo, "MANUAL", adesso)
                esito = f"chiusa a {prezzo:g}"
            self.ledger.salva()
            return esito

    def _annulla(self, book: PaperBook, p: PaperPosition, motivo: str, quando: datetime) -> None:
        book.positions.remove(p)
        self.ledger.evento(
            "CANCELLED", quando, book=p.plan.book, symbol=p.plan.canonical, reason=motivo
        )

    async def _swap(self, p: PaperPosition) -> None:
        """Swap a ogni mezzanotte broker attraversata da posizione aperta."""
        ora = self.ora_broker()
        scarto = self._scarto()
        if ora is None or scarto is None or p.opened_at is None or p.entry_fill is None:
            return
        info = (await self._specifiche.get(p.plan.broker_symbol)).value
        giorno = (p.opened_at + scarto).date() + timedelta(days=1)
        strumento = await self._strumenti.by_broker_symbol(p.plan.broker_symbol)
        while giorno <= ora.date():
            chiave = giorno.isoformat()
            if chiave not in p.swap_days:
                p.swap_days.append(chiave)
                if info.swap_mode == 1:
                    notte = giorno - timedelta(days=1)
                    giorno_mt5 = (notte.weekday() + 1) % 7
                    moltiplica = 3 if giorno_mt5 == info.swap_rollover3days else 1
                    punti = info.swap_long if p.plan.verso > 0 else info.swap_short
                    p.swap += round(
                        await self._valore(
                            strumento, p.volume, p.entry_fill, punti * info.point * moltiplica
                        ),
                        2,
                    )
                elif info.swap_mode != 0:
                    p.notes.append(f"Swap in modalità {info.swap_mode} non simulato.")
            giorno += timedelta(days=1)

    async def _chiudi(
        self,
        book: PaperBook,
        p: PaperPosition,
        prezzo: float,
        motivo: str,
        quando: datetime,
        *,
        ambigua: bool = False,
    ) -> None:
        plan = p.plan
        assert p.entry_fill is not None and p.opened_at is not None
        strumento = await self._strumenti.by_broker_symbol(plan.broker_symbol)
        lordo = await self._v.profit(
            strumento, Direction(plan.direction), p.volume, p.entry_fill, prezzo
        )
        commissioni = p.commission + self._cfg.commission_per_lot * p.volume
        netto = round(lordo - commissioni + p.swap, 2)
        info = (await self._specifiche.get(plan.broker_symbol)).value
        slit = self._cfg.slippage_points * info.point
        slit_uscita = slit if motivo in ("SL", "TRAIL") else 0.0
        distanza = abs(p.entry_fill - plan.stop) or plan.atr_h1
        ombre = self._chiudi_ombre(p, prezzo, slit, distanza)
        trade = PaperTrade(
            position_id=p.position_id,
            plan=plan,
            opened_at=p.opened_at,
            closed_at=quando,
            entry_fill=p.entry_fill,
            exit_price=prezzo,
            exit_reason=motivo,  # type: ignore[arg-type]
            volume=p.volume,
            risk_amount=p.risk_amount,
            gross_pnl=round(lordo, 2),
            commission=round(commissioni, 2),
            swap=round(p.swap, 2),
            spread_cost=round(
                await self._valore(strumento, p.volume, p.entry_fill, p.spread_at_entry), 2
            ),
            slippage_cost=round(
                await self._valore(
                    strumento, p.volume, p.entry_fill, p.slippage_entry + slit_uscita
                ),
                2,
            ),
            net_pnl=netto,
            r_multiple=round(netto / p.risk_amount, 3) if p.risk_amount > 0 else 0.0,
            mae_r=round(p.mae_price / distanza, 3),
            mfe_r=round(p.mfe_price / distanza, 3),
            ambiguous_bar=ambigua,
            notes=tuple(p.notes),
            trailing_rule=p.trailing_rule,
            price_r=round(plan.verso * (prezzo - p.entry_fill) / distanza, 3),
            shadow_r=ombre,
        )
        book.positions.remove(p)
        book.trades.append(trade)
        book.balance = round(book.balance + netto, 2)
        self.ledger.evento(
            "CLOSE",
            quando,
            book=plan.book,
            symbol=plan.canonical,
            reason=motivo,
            price=prezzo,
            net=netto,
            r=trade.r_multiple,
        )
        logger.info(
            "paper.chiusura",
            libro=plan.book,
            simbolo=plan.canonical,
            motivo=motivo,
            netto=netto,
            r=trade.r_multiple,
        )
