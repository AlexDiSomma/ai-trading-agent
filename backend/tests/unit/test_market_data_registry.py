"""Test di registro simboli, specifiche, stato quotazioni e acquisizione candele.

Il test che conta più degli altri è
`test_un_alias_non_sostituisce_il_simbolo_richiesto`: se un giorno il sistema
cominciasse a operare su `GOLD` perché l'utente ha scritto `XAUUSD`, starebbe
operando su uno strumento non autorizzato. La whitelist è il contratto, e un
suggerimento non è un'autorizzazione.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.brokers.mt5.connector import MT5Connector
from backend.brokers.mt5.mock import FakeMT5Terminal
from backend.core.clock import FrozenClock
from backend.core.enums import (
    AssetClass,
    DataQuality,
    MarketPhase,
    MarketStatus,
    QuoteQualityStatus,
    QuoteState,
    Timeframe,
)
from backend.market_data.ingestion import CandleIngestion, peggiore
from backend.market_data.reader import MarketDataService
from backend.market_data.registry import (
    MatchKind,
    SymbolRegistry,
    classifica_asset,
    normalizza,
)
from backend.market_data.sessions import (
    BrokerClock,
    QuoteStateDetector,
    fase_di_mercato,
)
from backend.market_data.specifications import SpecificationRepository, impronta
from backend.schemas.market import Tick

ADESSO = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)
#: Le etichette di MT5 sono in ora del broker (XM: UTC+3), anche nel mock.
SCARTO_XM = 3 * 3_600
ORA_BROKER = ADESSO + timedelta(seconds=SCARTO_XM)


def orologio_xm() -> BrokerClock:
    """Scarto dell'ora broker già stabilito, come dopo l'avvio del contenitore."""
    orologio = BrokerClock()
    orologio.seed(SCARTO_XM, ADESSO)
    return orologio


@pytest.fixture
async def registro(connettore: MT5Connector, clock: FrozenClock) -> SymbolRegistry:
    registry = SymbolRegistry(connettore, clock=clock)
    await registry.refresh()
    return registry


class TestNormalizzazione:
    @pytest.mark.parametrize(
        ("ingresso", "atteso"),
        [
            ("US30Cash", "US30"),
            ("us100cash", "US100"),
            ("EURUSD", "EURUSD"),
            ("EUR/USD", "EURUSD"),
            ("GOLD", "GOLD"),
            ("US500Cash", "US500"),
        ],
    )
    def test_forme_confrontabili(self, ingresso: str, atteso: str) -> None:
        assert normalizza(ingresso) == atteso

    def test_un_suffisso_non_divora_il_nome(self) -> None:
        """Rimuovere troppo renderebbe nomi diversi indistinguibili."""
        assert normalizza("CASH") == "CASH"


class TestClassificazioneAsset:
    @pytest.mark.parametrize(
        ("percorso", "nome", "atteso"),
        [
            (r"Forex\Standard\Majors\EURUSD", "EURUSD", AssetClass.FX),
            (r"Derivatives\Spot Metals\GOLD", "GOLD", AssetClass.METAL),
            (r"Derivatives\Cash\Cash Indices\US100Cash", "US100Cash", AssetClass.INDEX),
            (r"Cryptocurrencies\BTCUSD", "BTCUSD", AssetClass.CRYPTO),
            (r"Stocks\US\AAPL", "AAPL", AssetClass.STOCK),
        ],
    )
    def test_dal_percorso_del_broker(self, percorso: str, nome: str, atteso: AssetClass) -> None:
        """Il percorso viene dal broker: è un dato, non una congettura."""
        assert classifica_asset(percorso, nome) is atteso

    def test_oro_classificato_come_metallo_nonostante_le_valute_usd(self) -> None:
        """XM dichiara GOLD come USD/USD: senza il percorso finirebbe fra i cambi."""
        assert classifica_asset(r"Derivatives\Spot Metals\GOLD", "GOLD") is AssetClass.METAL

    def test_ripiego_sul_nome_se_il_percorso_manca(self) -> None:
        assert classifica_asset("", "GOLD") is AssetClass.METAL
        assert classifica_asset("", "EURUSD") is AssetClass.FX
        assert classifica_asset("", "QUALCOSA") is AssetClass.OTHER


class TestRisoluzioneDeiNomi:
    async def test_corrispondenza_esatta(self, registro: SymbolRegistry) -> None:
        esito = registro.resolve("GOLD")
        assert esito.match is MatchKind.EXACT
        assert esito.canonical == "GOLD"
        assert esito.usable is True

    async def test_differenza_di_sole_maiuscole(self, registro: SymbolRegistry) -> None:
        esito = registro.resolve("us100cash")
        assert esito.match is MatchKind.CASE_INSENSITIVE
        assert esito.canonical == "US100Cash"
        assert esito.usable is True
        assert "grafia" in esito.note

    async def test_un_alias_non_sostituisce_il_simbolo_richiesto(
        self, registro: SymbolRegistry
    ) -> None:
        """Il test più importante del file.

        `XAUUSD` non esiste su XM. Il sistema deve dire che non esiste e suggerire
        `GOLD`, **senza** usarlo: operare su uno strumento che l'utente non ha
        autorizzato violerebbe il contratto della whitelist.
        """
        esito = registro.resolve("XAUUSD")
        assert esito.match is MatchKind.ALIAS_SUGGESTION
        assert esito.canonical is None
        assert esito.usable is False
        assert "GOLD" in esito.candidates
        assert "non viene usato automaticamente" in esito.note

    async def test_suffisso_del_broker_riconosciuto_dai_dati(
        self, registro: SymbolRegistry
    ) -> None:
        """`US30` → `US30Cash` si deduce dai nomi del broker, senza tabelle."""
        esito = registro.resolve("US30")
        assert esito.match is MatchKind.ALIAS_SUGGESTION
        assert "US30Cash" in esito.candidates

    async def test_nome_inventato_senza_candidati(self, registro: SymbolRegistry) -> None:
        esito = registro.resolve("ZZZQQQ")
        assert esito.match is MatchKind.NOT_FOUND
        assert esito.candidates == ()

    async def test_registro_non_caricato(self, connettore: MT5Connector) -> None:
        vuoto = SymbolRegistry(connettore)
        assert vuoto.resolve("GOLD").match is MatchKind.NOT_FOUND
        assert "non caricato" in vuoto.resolve("GOLD").note

    async def test_ricerca_libera(self, registro: SymbolRegistry) -> None:
        assert "GOLD" in registro.search("gol")

    async def test_scadenza_del_catalogo(
        self, connettore: MT5Connector, clock: FrozenClock
    ) -> None:
        registry = SymbolRegistry(connettore, clock=clock, ttl_seconds=60)
        await registry.refresh()
        assert registry.is_stale is False
        clock.advance(seconds=120)
        assert registry.is_stale is True


class TestControlloWhitelist:
    async def test_whitelist_corretta_nessun_problema(self, registro: SymbolRegistry) -> None:
        assert registro.check_whitelist(("EURUSD", "GOLD", "US100Cash")) == ()

    async def test_nome_inesistente_e_un_errore_con_candidati(
        self, registro: SymbolRegistry
    ) -> None:
        problemi = registro.check_whitelist(("EURUSD", "XAUUSD"))
        assert len(problemi) == 1
        assert problemi[0].severity == "errore"
        assert problemi[0].suggestions == ("GOLD",)

    async def test_grafia_sbagliata_e_solo_un_avviso(self, registro: SymbolRegistry) -> None:
        problemi = registro.check_whitelist(("us100cash",))
        assert problemi[0].severity == "avviso"
        assert problemi[0].suggestions == ("US100Cash",)


class TestArchivioSpecifiche:
    async def test_lettura_e_cache(self, connettore: MT5Connector, clock: FrozenClock) -> None:
        repository = SpecificationRepository(connettore, clock=clock, ttl_seconds=3_600)
        primo = await repository.get("GOLD")
        secondo = await repository.get("GOLD")
        # Stesso istante di produzione: la seconda lettura viene dalla cache.
        assert secondo.as_of == primo.as_of
        assert secondo.source.startswith("cache")

    async def test_force_rilegge_sempre(self, connettore: MT5Connector, clock: FrozenClock) -> None:
        repository = SpecificationRepository(connettore, clock=clock)
        await repository.get("GOLD")
        clock.advance(seconds=5)
        fresco = await repository.get("GOLD", force=True)
        assert not fresco.source.startswith("cache")

    async def test_cambio_di_specifica_rilevato(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock: FrozenClock
    ) -> None:
        repository = SpecificationRepository(connettore, clock=clock)
        await repository.get("GOLD")
        terminale.symbols["GOLD"].volume_min = 0.05
        await repository.refresh("GOLD")
        cambiamenti = [c for c in repository.changes if c.field == "volume_min"]
        assert cambiamenti
        assert cambiamenti[0].previous == 0.01
        assert cambiamenti[0].current == 0.05

    async def test_lo_spread_non_e_un_cambio_di_specifica(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock: FrozenClock
    ) -> None:
        """Lo spread cambia a ogni tick: includerlo renderebbe il rilevamento inutile."""
        repository = SpecificationRepository(connettore, clock=clock)
        prima = await repository.get("GOLD")
        terminale.symbols["GOLD"].spread = 999
        await repository.refresh("GOLD")
        assert repository.changes == ()
        assert impronta(prima.value) == impronta(repository.cached("GOLD"))  # type: ignore[arg-type]

    async def test_persistenza_chiamata_solo_sui_cambiamenti(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock: FrozenClock
    ) -> None:
        salvati: list[str] = []

        async def salva(info, classe) -> None:  # type: ignore[no-untyped-def]
            salvati.append(info.name)

        repository = SpecificationRepository(connettore, clock=clock, persist=salva)
        await repository.refresh("GOLD")
        await repository.refresh("GOLD")  # nessun cambiamento
        assert salvati == ["GOLD"]

        # Il campo del broker si chiama `trade_contract_size`: usare
        # `contract_size` creerebbe un attributo che nessuno legge, e il test
        # passerebbe senza aver cambiato niente.
        terminale.symbols["GOLD"].trade_contract_size = 50.0
        await repository.refresh("GOLD")
        assert salvati == ["GOLD", "GOLD"]

    async def test_classe_di_attivita_dal_percorso(
        self, connettore: MT5Connector, clock: FrozenClock
    ) -> None:
        repository = SpecificationRepository(connettore, clock=clock)
        await repository.get("GOLD")
        await repository.get("EURUSD")
        assert repository.asset_class("GOLD") is AssetClass.METAL
        assert repository.asset_class("EURUSD") is AssetClass.FX


class TestOraDelBroker:
    def test_senza_misura_non_si_inventa(self) -> None:
        stima = BrokerClock().estimate(now=ADESSO)
        assert stima.source == "UNAVAILABLE"
        assert stima.server_time is None
        assert stima.reliable is False

    def test_misura_da_due_tick_concordi(self) -> None:
        """Scarto di due ore (XM d'inverno), confermato da due strumenti."""
        clock = BrokerClock()
        tick = [
            Tick(symbol=s, time=ADESSO + timedelta(hours=2), bid=1.1, ask=1.1001)
            for s in ("EURUSD", "USDJPY")
        ]
        assert clock.observe_many(tick, now=ADESSO) is True
        stima = clock.estimate(now=ADESSO)
        assert stima.offset_seconds == 7_200
        assert stima.server_time == ADESSO + timedelta(hours=2)
        assert stima.reliable is True
        assert stima.corroborating_ticks == 2

    def test_un_tick_solo_non_stabilisce_lo_scarto(self) -> None:
        """Regressione ADR 0015: stabilire lo scarto dal tick da valutare è circolare.

        Un tick fermo da tre ore esatte, su un server a UTC+0, sembrerebbe fresco
        con scarto +0 se bastasse lui a fissare lo scarto.
        """
        clock = BrokerClock()
        tick = Tick(symbol="EURUSD", time=ADESSO + timedelta(hours=2), bid=1.1, ask=1.1001)
        assert clock.observe(tick, now=ADESSO) is False
        assert clock.observe_many([tick], now=ADESSO) is False
        assert clock.estimate(now=ADESSO).source == "UNAVAILABLE"

    def test_observe_conferma_uno_scarto_esistente(self) -> None:
        clock = orologio_xm()
        tick = Tick(symbol="EURUSD", time=ORA_BROKER, bid=1.1, ask=1.1001)
        assert clock.observe(tick, now=ADESSO) is True
        altro = Tick(symbol="EURUSD", time=ADESSO, bid=1.1, ask=1.1001)
        # Un tick che suggerisce un altro scarto non lo cambia.
        assert clock.observe(altro, now=ADESSO) is False
        assert clock.estimate(now=ADESSO).offset_seconds == SCARTO_XM

    def test_un_tick_vecchio_non_misura(self) -> None:
        """A mercato chiuso lo scarto non è misurabile: non si indovina.

        Trentuno ore e' un multiplo esatto di mezz'ora: la prima versione del
        codice lo accettava come scarto di fuso orario. Ora c'e' un limite di
        plausibilita' di 14 ore.
        """
        clock = BrokerClock()
        vecchio = Tick(
            symbol="EURUSD",
            time=ADESSO - timedelta(hours=31),
            bid=1.1,
            ask=1.1001,
        )
        assert clock.observe_many([vecchio, vecchio], now=ADESSO) is False
        assert clock.estimate(now=ADESSO).source == "UNAVAILABLE"

    def test_misura_invecchiata_resta_usabile_per_dodici_ore(self) -> None:
        clock = BrokerClock()
        clock.seed(7_200, ADESSO)
        assert clock.estimate(now=ADESSO + timedelta(hours=6)).reliable is True
        # Oltre le dodici ore potrebbe esserci stato un cambio di ora legale.
        assert clock.estimate(now=ADESSO + timedelta(hours=20)).reliable is False

    def test_scarto_arrotondato_al_quarto_dora(self) -> None:
        clock = BrokerClock()
        tick = [
            Tick(symbol=s, time=ADESSO + timedelta(hours=2, seconds=7), bid=1.1, ask=1.1001)
            for s in ("EURUSD", "GOLD")
        ]
        clock.observe_many(tick, now=ADESSO)
        assert clock.estimate(now=ADESSO).offset_seconds == 7_200


def detector(**override) -> QuoteStateDetector:  # type: ignore[no-untyped-def]
    base = {
        "max_tick_age_seconds": 15.0,
        "max_spread_points": 1_000.0,
        "broker_clock": orologio_xm(),
    }
    base.update(override)
    return QuoteStateDetector(**base)  # type: ignore[arg-type]


class TestStatoQuotazione:
    async def test_quotazione_valida(self, connettore: MT5Connector) -> None:
        info = await connettore.get_symbol("EURUSD")
        tick = await connettore.get_tick("EURUSD")
        esito = detector().assess(info, tick, now=ADESSO, last_bar_time=ORA_BROKER, atr=0.002)
        assert esito.state is QuoteState.TRADEABLE
        assert esito.is_inference is False
        assert esito.allows_new_positions is True

    async def test_strumento_disabilitato_dal_broker(self, connettore: MT5Connector) -> None:
        """Fatto del broker, non deduzione nostra."""
        info = (await connettore.get_symbol("EURUSD")).model_copy(update={"trade_mode": 0})
        esito = detector().assess(info, None, now=ADESSO, quote_missing=True)
        assert esito.state is QuoteState.NOT_TRADEABLE
        assert esito.is_inference is False

    async def test_solo_chiusura(self, connettore: MT5Connector) -> None:
        info = (await connettore.get_symbol("EURUSD")).model_copy(update={"trade_mode": 3})
        esito = detector().assess(info, None, now=ADESSO, quote_missing=True)
        assert esito.state is QuoteState.CLOSE_ONLY
        assert esito.allows_new_positions is False

    async def test_nessuna_quotazione(self, connettore: MT5Connector) -> None:
        info = await connettore.get_symbol("US500Cash")
        esito = detector().assess(info, None, now=ADESSO, quote_missing=True)
        assert esito.state is QuoteState.NO_QUOTE
        assert esito.is_inference is False

    async def test_mercato_chiuso_e_una_deduzione(self, connettore: MT5Connector) -> None:
        """Due prove concordi: quotazione molto vecchia e nessuna barra nuova."""
        info = await connettore.get_symbol("EURUSD")
        tick = Tick(symbol="EURUSD", time=ORA_BROKER - timedelta(hours=31), bid=1.1, ask=1.1001)
        esito = detector().assess(
            info,
            tick,
            now=ADESSO,
            last_bar_time=ORA_BROKER - timedelta(hours=31),
            atr=0.002,
        )
        assert esito.state is QuoteState.MARKET_CLOSED
        assert esito.is_inference is True
        assert "MT5 non espone gli orari di sessione" in esito.reason

    async def test_dato_vecchio_ma_barre_recenti_e_stale_non_chiuso(
        self, connettore: MT5Connector
    ) -> None:
        """Flusso prezzi in ritardo e mercato aperto sono guasti diversi."""
        info = await connettore.get_symbol("EURUSD")
        tick = Tick(symbol="EURUSD", time=ORA_BROKER - timedelta(seconds=60), bid=1.1, ask=1.1001)
        esito = detector().assess(
            info, tick, now=ADESSO, last_bar_time=ORA_BROKER - timedelta(seconds=30), atr=0.002
        )
        assert esito.state is QuoteState.STALE
        assert esito.is_inference is False

    async def test_spread_giudicato_in_rapporto_all_atr(self, connettore: MT5Connector) -> None:
        """35 punti sono molto su EURUSD e nulla su US100Cash: serve il relativo."""
        eurusd = await connettore.get_symbol("EURUSD")
        indice = await connettore.get_symbol("US100Cash")
        tick_eur = await connettore.get_tick("EURUSD")
        tick_idx = await connettore.get_tick("US100Cash")

        rigoroso = detector(max_spread_atr_ratio=0.05)
        # EURUSD: 20 punti = 0.0002 contro ATR 0.002 -> 10%, oltre il 5%.
        assert (
            rigoroso.assess(eurusd, tick_eur, now=ADESSO, last_bar_time=ORA_BROKER, atr=0.002).state
            is QuoteState.WIDE_SPREAD
        )
        # US100Cash: 616 punti = 6.16 contro ATR 240 -> 2.6%, accettabile.
        assert (
            rigoroso.assess(indice, tick_idx, now=ADESSO, last_bar_time=ORA_BROKER, atr=240.0).state
            is QuoteState.TRADEABLE
        )

    async def test_senza_atr_si_usa_il_tetto_assoluto(self, connettore: MT5Connector) -> None:
        indice = await connettore.get_symbol("US100Cash")
        tick = await connettore.get_tick("US100Cash")
        esito = detector(max_spread_points=100.0).assess(
            indice, tick, now=ADESSO, last_bar_time=ORA_BROKER, atr=None
        )
        assert esito.state is QuoteState.WIDE_SPREAD
        assert "tetto assoluto" in esito.reason


class TestFaseDiMercato:
    """La fase si basa sullo **stato di mercato** delle quotazioni, non sull'operatività."""

    def valutazione(self, mercato: MarketStatus, nome: str = "X"):  # type: ignore[no-untyped-def]
        from backend.market_data.sessions import QuoteAssessment
        from backend.schemas.quote import QuoteQuality

        dedotto = mercato is MarketStatus.CLOSED_INFERRED
        stato_dato = {
            MarketStatus.OPEN_CONFIRMED: QuoteQualityStatus.VALID,
            MarketStatus.CLOSED_INFERRED: QuoteQualityStatus.MARKET_CLOSED,
            MarketStatus.UNKNOWN: QuoteQualityStatus.STALE,
        }[mercato]
        qualita = QuoteQuality(
            broker_symbol=nome,
            status=stato_dato,
            market_status=mercato,
            is_inference=dedotto,
            max_tick_age_seconds=15.0,
            evaluated_at=ADESSO,
        )
        return QuoteAssessment(
            symbol=nome,
            state=QuoteState.TRADEABLE,
            reason="",
            is_inference=dedotto,
            quality=qualita,
        )

    def test_tutti_quotabili_mercato_aperto(self) -> None:
        fase, motivo = fase_di_mercato([self.valutazione(MarketStatus.OPEN_CONFIRMED)] * 3)
        assert fase is MarketPhase.OPEN_CONFIRMED
        assert "3 strumenti su 3" in motivo

    def test_tutti_fermi_mercato_chiuso(self) -> None:
        fase, motivo = fase_di_mercato([self.valutazione(MarketStatus.CLOSED_INFERRED)] * 5)
        assert fase is MarketPhase.CLOSED_INFERRED
        # Il messaggio non esclude la connessione caduta: sono indistinguibili.
        assert "connessione" in motivo

    def test_alcuni_aperti_alcuni_chiusi(self) -> None:
        """Su XM gli indici chiudono mentre il forex continua."""
        valutazioni = [
            self.valutazione(MarketStatus.OPEN_CONFIRMED, "EURUSD"),
            self.valutazione(MarketStatus.CLOSED_INFERRED, "US100Cash"),
            self.valutazione(MarketStatus.CLOSED_INFERRED, "US500Cash"),
        ]
        fase, _ = fase_di_mercato(valutazioni)
        assert fase is MarketPhase.PARTIALLY_OPEN

    def test_nessuno_strumento(self) -> None:
        assert fase_di_mercato([])[0] is MarketPhase.UNKNOWN

    def test_stato_ignoto_non_diventa_chiuso(self) -> None:
        """Quotazioni di freschezza non verificabile: né aperto né chiuso."""
        fase, _ = fase_di_mercato([self.valutazione(MarketStatus.UNKNOWN)] * 4)
        assert fase is MarketPhase.UNKNOWN


class TestAcquisizioneCandele:
    @pytest.fixture
    def ingestione(self, connettore: MT5Connector, clock: FrozenClock) -> CandleIngestion:
        from backend.config.risk_limits import RiskLimits

        limiti = RiskLimits(_env_file=None, RISK_ALLOWED_SYMBOLS="EURUSD,GOLD,US100Cash")
        servizio = MarketDataService(connettore, limiti, clock=clock)
        return CandleIngestion(servizio, clock=clock)

    async def test_gerarchia_completa(self, ingestione: CandleIngestion) -> None:
        dati = await ingestione.fetch_all("EURUSD")
        assert list(dati) == [
            Timeframe.D1,
            Timeframe.H4,
            Timeframe.H1,
            Timeframe.M15,
            Timeframe.M5,
        ]

    async def test_qualita_simulata_sul_terminale_finto(self, ingestione: CandleIngestion) -> None:
        dati = await ingestione.fetch_one("EURUSD", Timeframe.H1)
        assert dati.quality is DataQuality.SIMULATED
        assert "simulati" in dati.note

    async def test_storia_corta_segnalata(self, ingestione: CandleIngestion) -> None:
        """Sotto 210 barre la EMA200 non esiste: l'analisi è parziale e va detto."""
        dati = await ingestione.fetch_one("EURUSD", Timeframe.H1, count=100)
        assert dati.quality is DataQuality.SHORT_HISTORY
        assert "EMA200" in dati.note

    async def test_storia_insufficiente(self, ingestione: CandleIngestion) -> None:
        dati = await ingestione.fetch_one("EURUSD", Timeframe.H1, count=20)
        assert dati.quality is DataQuality.INSUFFICIENT
        assert dati.usable is False

    async def test_un_timeframe_rotto_non_ferma_gli_altri(
        self, ingestione: CandleIngestion, terminale: FakeMT5Terminal
    ) -> None:
        terminale.break_always("copy_rates_from_pos")
        assert await ingestione.fetch_all("EURUSD") == {}

    def test_la_qualita_peggiore_vince(self) -> None:
        assert (
            peggiore([DataQuality.SIMULATED, DataQuality.INSUFFICIENT, DataQuality.GOOD])
            is DataQuality.INSUFFICIENT
        )
        assert peggiore([]) is DataQuality.GOOD
