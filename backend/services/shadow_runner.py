"""Orchestrazione della shadow mode (fase 5B): dati → contesti → tre gruppi → diario.

Unico punto della 5B che parla col broker, e solo in lettura (`ReadOnlyBroker`). Il
risultato sono snapshot su file: nessun piano raggiunge un servizio di esecuzione,
perché nel sistema non ne esiste uno.

**Una sola lettura, molti istanti.** I dati si leggono una volta (più barre del
solito); ogni istante di decisione `T` vede solo le barre **chiuse entro T** — stessa
regola di taglio del motore globale (`features.taglia`) — e i dati esterni con
`available_at <= T`. Le barre successive servono solo all'`OutcomeEvaluator`.

Selezione degli strumenti: quelli richiesti, più uno scelto dall'agente fra gli altri
autorizzati con una regola dichiarata e calcolata **all'istante T** (massimo
allineamento multi-timeframe in valore assoluto, dati GOOD).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from backend.ai.factory import costruisci_client
from backend.ai.input_builder import costruisci_ingresso
from backend.ai.thesis.agent import ThesisAgent
from backend.ai.thesis.input_builder import costruisci_ingresso_tesi
from backend.config.paths import resolve
from backend.core.enums import DataQuality, ProviderKind, ProviderState, Timeframe
from backend.core.errors import TradingAgentError
from backend.core.logging import get_logger
from backend.global_market.backdrop import sfondo
from backend.global_market.engine import DatiEsterni, costruisci
from backend.global_market.features import taglia
from backend.global_market.reader import GlobalDataReader, LetturaGlobale
from backend.market_data.ingestion import (
    BARRE_MINIME_ASSOLUTE,
    BARRE_PER_EMA200,
    TIMEFRAMES_ANALISI,
    TimeframeCandles,
)
from backend.schemas.context import AssetMarketContext
from backend.schemas.global_market import GlobalMarketContext
from backend.schemas.market import CandleSeries
from backend.services.container import ApplicationContainer
from backend.shadow.conditions import Barra
from backend.shadow.evaluator import impronta_barre, valuta
from backend.shadow.evidence import costruisci_catalogo
from backend.shadow.groups import piano_filtro, piano_tesi
from backend.shadow.journal import ShadowJournal
from backend.shadow.plans import ShadowTradePlan
from backend.shadow.quant_baseline import piano_quant
from backend.shadow.records import PIPELINE_VERSION, DecisionSnapshot, OutcomeSnapshot

logger = get_logger(__name__)

#: Barre per timeframe nella lettura condivisa: abbastanza da tagliare la storia a
#: una settimana fa e avere ancora la EMA200 su ogni timeframe.
BARRE_LETTURA: dict[Timeframe, int] = {
    Timeframe.D1: 400,
    Timeframe.H4: 900,
    Timeframe.H1: 1200,
    Timeframe.M15: 1500,
    Timeframe.M5: 3000,
}
BARRE_UNIVERSO = 1500
CARTELLA_DIARIO = "var/shadow_journal"


@dataclass(frozen=True)
class DatiCondivisi:
    lettura: LetturaGlobale
    esterni: DatiEsterni
    scarto: timedelta
    #: Candele complete per simbolo broker, in ora broker come le dà MT5.
    candele: dict[str, dict[Timeframe, TimeframeCandles]]
    letto_alle: datetime


def _qualita(chiuse: int) -> DataQuality:
    if chiuse < BARRE_MINIME_ASSOLUTE:
        return DataQuality.INSUFFICIENT
    if chiuse < BARRE_PER_EMA200:
        return DataQuality.SHORT_HISTORY
    return DataQuality.GOOD


def _taglia_candele(tc: TimeframeCandles, limite_broker: datetime) -> TimeframeCandles:
    durata = timedelta(minutes=tc.timeframe.minutes)
    tenute = tuple(c for c in tc.series.closed if c.time + durata <= limite_broker)
    serie = CandleSeries(
        symbol=tc.series.symbol,
        timeframe=tc.timeframe,
        candles=tenute,
        as_of=limite_broker,
        source=tc.series.source,
        last_candle_closed=True,
    )
    q = _qualita(len(tenute))
    nota = (
        "" if q is DataQuality.GOOD else f"{tc.timeframe}: {len(tenute)} barre prima dell'istante."
    )
    return TimeframeCandles(
        timeframe=tc.timeframe, fresh=replace(tc.fresh, value=serie), quality=q, note=nota
    )


def _barre_utc(tc: TimeframeCandles, scarto: timedelta) -> list[Barra]:
    durata = timedelta(minutes=tc.timeframe.minutes)
    return [
        Barra(c.time - scarto, c.time - scarto + durata, c.open, c.high, c.low, c.close)
        for c in tc.series.closed
    ]


class ShadowRunner:
    def __init__(
        self, container: ApplicationContainer, *, agent: ThesisAgent | None = None
    ) -> None:
        self._c = container
        ai = container.settings.ai
        self._agente = agent or ThesisAgent(
            costruisci_client(ai),
            temperature=ai.temperature,
            max_output_tokens=ai.max_output_tokens,
            clock=container.clock,
        )
        self.diario = ShadowJournal(resolve(CARTELLA_DIARIO))

    # ------------------------------------------------------------------
    # Lettura
    # ------------------------------------------------------------------
    async def leggi(self, simboli: tuple[str, ...]) -> DatiCondivisi:
        c = self._c
        await c.ensure_broker_time()
        lettore = GlobalDataReader(
            c.require_broker(),
            c.require_instruments(),
            c.broker_clock,
            max_tick_age_seconds=c.settings.mt5.max_tick_age_seconds,
            clock=c.clock,
            barre=BARRE_UNIVERSO,
        )
        lettura = await lettore.leggi()
        if lettura.broker_offset_seconds is None:
            raise TradingAgentError(
                "Scarto dell'ora broker ignoto: impossibile tagliare la storia senza "
                "rischiare il look-ahead (ADR 0015)."
            )
        esterni = await c.require_global_intelligence()._esterni()
        ingestion = c.ingestion
        assert ingestion is not None
        candele: dict[str, dict[Timeframe, TimeframeCandles]] = {}
        for s in simboli:
            candele[s] = {
                tf: await ingestion.fetch_one(s, tf, BARRE_LETTURA[tf]) for tf in TIMEFRAMES_ANALISI
            }
        return DatiCondivisi(
            lettura=lettura,
            esterni=esterni,
            scarto=timedelta(seconds=lettura.broker_offset_seconds),
            candele=candele,
            letto_alle=lettura.as_of,
        )

    # ------------------------------------------------------------------
    # Contesti all'istante T
    # ------------------------------------------------------------------
    def contesto_globale(
        self, d: DatiCondivisi, as_of: datetime, mode: Literal["LIVE", "REPLAY"]
    ) -> GlobalMarketContext:
        lett = d.lettura
        esterni = d.esterni
        if mode == "REPLAY":
            # Lo stato "STALE" di una fonte giornaliera è relativo ad adesso: all'istante
            # passato l'ultima osservazione disponibile era quella di allora, e il motore
            # filtra comunque per `available_at <= as_of`. Il calendario resta com'è:
            # copre solo la settimana corrente, e il motore lo dichiara.
            stati = tuple(
                s.model_copy(update={"state": ProviderState.AVAILABLE})
                if s.kind is not ProviderKind.ECONOMIC_CALENDAR
                and s.state is not ProviderState.NOT_CONFIGURED
                else s
                for s in esterni.stati
            )
            esterni = replace(esterni, stati=stati)
        _, contesto = costruisci(
            lett.ingressi,
            as_of=as_of,
            limite_broker=as_of + d.scarto,
            broker_offset_seconds=lett.broker_offset_seconds,
            broker_time_source=lett.broker_time_source,
            simulated=lett.simulated,
            source=lett.source,
            esterni=esterni,
        )
        return contesto

    def _ultime_chiusure(
        self, d: DatiCondivisi, as_of: datetime, asset: dict[str, dict[Timeframe, TimeframeCandles]]
    ) -> dict[tuple[str, str], float]:
        limite = as_of + d.scarto
        chiusure: dict[tuple[str, str], float] = {}
        for i in d.lettura.ingressi:
            for tf, serie in i.serie.items():
                if not i.stati.get(tf) or not i.stati[tf].usable:
                    continue
                tagliata = taglia(serie, limite)
                if tagliata.chiusure:
                    chiusure[(i.canonical, str(tf))] = tagliata.chiusure[-1]
        for canonico, per_tf in asset.items():
            for tf, tc in per_tf.items():
                if tc.series.closed:
                    chiusure[(canonico, str(tf))] = tc.series.closed[-1].close
        return chiusure

    async def _contesto_asset(
        self, d: DatiCondivisi, simbolo: str, as_of: datetime, mode: str
    ) -> tuple[AssetMarketContext, dict[Timeframe, TimeframeCandles]]:
        tagliate = {
            tf: _taglia_candele(tc, as_of + d.scarto) for tf, tc in d.candele[simbolo].items()
        }
        analista = self._c.require_analyst()
        if mode == "LIVE":
            return await analista.analyse(simbolo), tagliate
        return await analista.analyse_replay(simbolo, tagliate, as_of=as_of), tagliate

    # ------------------------------------------------------------------
    # Decisione
    # ------------------------------------------------------------------
    async def decidi(
        self,
        d: DatiCondivisi,
        as_of: datetime,
        mode: Literal["LIVE", "REPLAY"],
        *,
        richiesti: tuple[str, ...],
        candidati: tuple[str, ...],
    ) -> list[DecisionSnapshot]:
        globale = self.contesto_globale(d, as_of, mode)
        ingresso_globale = costruisci_ingresso(globale)

        contesti: dict[str, tuple[AssetMarketContext, dict[Timeframe, TimeframeCandles]]] = {}
        for s in (*richiesti, *candidati):
            ctx, tagliate = await self._contesto_asset(d, s, as_of, mode)
            if ctx.instrument is not None:
                ctx = ctx.model_copy(update={"global_backdrop": sfondo(ctx.instrument, globale)})
            contesti[s] = (ctx, tagliate)

        idonei = [s for s in candidati if contesti[s][0].quality is DataQuality.GOOD]
        scelto = max(
            idonei, key=lambda s: abs(contesti[s][0].multi_timeframe.alignment), default=None
        )
        selezione = dict.fromkeys(richiesti, "richiesto dall'utente")
        if scelto:
            valori = ", ".join(
                f"{s} {contesti[s][0].multi_timeframe.alignment:+.2f}" for s in idonei
            )
            selezione[scelto] = (
                "scelto dall'agente: massimo |allineamento multi-timeframe| all'istante "
                f"della decisione fra gli altri autorizzati con dati GOOD ({valori})"
            )

        canonici = {
            s: (contesti[s][0].instrument.canonical if contesti[s][0].instrument else s)
            for s in selezione
        }
        chiusure = self._ultime_chiusure(d, as_of, {canonici[s]: contesti[s][1] for s in selezione})
        risultati: list[DecisionSnapshot] = []
        for s, motivo in selezione.items():
            snap = await self._decidi_asset(
                s,
                canonici[s],
                motivo,
                contesti[s],
                globale,
                ingresso_globale,
                chiusure,
                as_of=as_of,
                mode=mode,
            )
            self.diario.scrivi_decisione(snap)
            risultati.append(snap)
        return risultati

    async def _decidi_asset(
        self,
        simbolo: str,
        canonico: str,
        motivo: str,
        contesto: tuple[AssetMarketContext, dict[Timeframe, TimeframeCandles]],
        globale: GlobalMarketContext,
        ingresso_globale,  # type: ignore[no-untyped-def]
        chiusure: dict[tuple[str, str], float],
        *,
        as_of: datetime,
        mode: Literal["LIVE", "REPLAY"],
    ) -> DecisionSnapshot:
        ctx, tagliate = contesto
        prezzo = None
        if mode == "REPLAY":
            m5 = tagliate.get(Timeframe.M5)
            prezzo = m5.series.closed[-1].close if m5 and m5.series.closed else None
        catalogo = costruisci_catalogo(ctx, canonico, prezzo=prezzo)
        note: list[str] = []
        quant = piano_quant(ctx, catalogo, decided_at=as_of, mode=mode)

        # Gate prima di chiamare il modello: dati non GOOD, o quotazione non valida dal
        # vivo, significano nessuna tesi. Fail closed, senza spendere una chiamata.
        blocchi: list[str] = []
        if catalogo.qualita != "GOOD":
            blocchi.append(f"Dati dello strumento {catalogo.qualita}.")
        if mode == "LIVE" and not ctx.usable_for_decisions:
            blocchi.extend(ctx.blocking_reasons)
        if not catalogo.atr_h1:
            blocchi.append("ATR H1 non disponibile.")

        tesi_rec = filtro_rec = None
        if blocchi:
            note.append("AI non interpellata: " + " ".join(blocchi))
            piano_c = ShadowTradePlan(
                group="C_AI_THESIS",
                canonical=canonico,
                decided_at=as_of,
                mode=mode,
                decision="WAIT",
                status="REJECTED",
                horizon_hours=24,
                reference_price=catalogo.prezzo,
                atr_h1=catalogo.atr_h1,
                spread_price=catalogo.spread_prezzo,
                reasons=tuple(blocchi),
            )
            piano_b = piano_filtro(quant, None)
            if quant.status == "ACTIVE":
                piano_b = piano_b.model_copy(update={"status": "REJECTED", "decision": "WAIT"})
        else:
            ingresso = costruisci_ingresso_tesi(
                ingresso_globale, catalogo, as_of=as_of, mode=mode, ultime_chiusure=chiusure
            )
            tesi_rec = await self._agente.tesi(
                ingresso,
                catalogo,
                ultime_chiusure=chiusure,
                qualita_globale=str(globale.data_quality),
            )
            piano_c = piano_tesi(
                tesi_rec,
                reference_price=catalogo.prezzo,
                atr_h1=catalogo.atr_h1,
                spread_price=catalogo.spread_prezzo,
            )
            if quant.status == "ACTIVE":
                ingresso_f = costruisci_ingresso_tesi(
                    ingresso_globale,
                    catalogo,
                    as_of=as_of,
                    mode=mode,
                    ultime_chiusure=chiusure,
                    quant=quant,
                )
                filtro_rec = await self._agente.filtro(ingresso_f)
            piano_b = piano_filtro(quant, filtro_rec)

        return DecisionSnapshot(
            pipeline_version=PIPELINE_VERSION,
            canonical=canonico,
            broker_symbol=simbolo,
            decided_at=as_of,
            mode=mode,
            selection_reason=motivo,
            asset_context=ctx.model_dump(mode="json"),
            evidence_catalog=catalogo.voci,
            reference={
                "price": catalogo.prezzo,
                "atr_h1": catalogo.atr_h1,
                "spread_price": catalogo.spread_prezzo,
                "asset_quality": catalogo.qualita,
                "last_closes": {f"{k[0]} {k[1]}": v for k, v in sorted(chiusure.items())},
            },
            global_regime=str(globale.regime),
            global_data_quality=str(globale.data_quality),
            thesis_record=tesi_rec,
            filter_record=filtro_rec,
            plans=(quant, piano_b, piano_c),
            notes=tuple(note),
        )

    # ------------------------------------------------------------------
    # Esiti
    # ------------------------------------------------------------------
    def sorgente(self, d: DatiCondivisi) -> dict[tuple[str, str], list[Barra]]:
        barre: dict[tuple[str, str], list[Barra]] = {}
        for i in d.lettura.ingressi:
            for tf, serie in i.serie.items():
                durata = timedelta(minutes=tf.minutes)
                precedente: float | None = None
                lista = []
                for t, h, lo, cl in zip(
                    serie.tempi, serie.massimi, serie.minimi, serie.chiusure, strict=True
                ):
                    # Le serie di riferimento non hanno l'apertura: per le condizioni
                    # (chiusura e tocco) non serve; si usa la chiusura precedente.
                    apertura = precedente if precedente is not None else cl
                    lista.append(
                        Barra(
                            t - d.scarto,
                            t - d.scarto + durata,
                            min(max(apertura, lo), h),
                            h,
                            lo,
                            cl,
                        )
                    )
                    precedente = cl
                barre[(i.canonical, str(tf))] = lista
        # Le candele degli strumenti analizzati hanno anche M5 e l'apertura vera:
        # sostituiscono quelle dell'universo per lo stesso strumento.
        canonici = {i.broker_symbol: i.canonical for i in d.lettura.ingressi}
        for simbolo, per_tf in d.candele.items():
            canonico = canonici.get(simbolo, simbolo)
            for tf, tc in per_tf.items():
                barre[(canonico, str(tf))] = _barre_utc(tc, d.scarto)
        return barre

    def valuta_tutto(self, d: DatiCondivisi) -> list[OutcomeSnapshot]:
        sorgente = self.sorgente(d)
        definitivi = {
            k
            for k, o in self.diario.ultimi_esiti().items()
            if all(x.horizon_elapsed for x in o.outcomes)
        }
        prodotti: list[OutcomeSnapshot] = []
        for dec in self.diario.decisioni():
            if dec.decision_id in definitivi:
                continue
            esiti = tuple(valuta(p, sorgente, adesso=d.letto_alle) for p in dec.plans)
            fine = max(p.scadenza for p in dec.plans)
            m5 = [
                b
                for b in sorgente.get((dec.canonical, "M5"), [])
                if dec.decided_at <= b.inizio < fine
            ]
            snap = OutcomeSnapshot(
                decision_id=dec.decision_id,
                decision_integrity_sha256=dec.integrity_sha256,
                canonical=dec.canonical,
                decided_at=dec.decided_at,
                evaluated_at=d.letto_alle,
                bars_m5=tuple((b.inizio.isoformat(), b.open, b.high, b.low, b.close) for b in m5),
                bars_sha256=impronta_barre(m5),
                outcomes=esiti,
            )
            self.diario.scrivi_esito(snap)
            prodotti.append(snap)
        return prodotti
