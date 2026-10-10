"""Test del connettore MT5 contro il terminale finto.

Coprono soprattutto i modi in cui MetaTrader 5 **sbaglia**: restituisce `None`
invece di sollevare, resta aperto su un altro conto, serve un tick vecchio di
mezz'ora. Un connettore testato solo sul percorso felice è un connettore che
scopre questi casi in produzione.
"""

from __future__ import annotations

import pytest

from backend.brokers.mt5.connector import MT5Connector
from backend.brokers.mt5.mock import FakeAccount, FakeMT5Terminal, FakePosition
from backend.config.settings import MT5Settings, Settings
from backend.core.clock import FrozenClock
from backend.core.enums import HealthStatus, PositionSide, Timeframe
from backend.core.errors import (
    BrokerError,
    BrokerNotConnectedError,
    BrokerTimeoutError,
    StaleDataError,
    SymbolNotFoundError,
)


def crea_connettore(
    settings: Settings, terminale: FakeMT5Terminal, clock: FrozenClock
) -> MT5Connector:
    return MT5Connector(
        settings.mt5, terminale, clock=clock, simulated=True, reference_symbol="EURUSD"
    )


class TestConnessione:
    async def test_connessione_riuscita(self, connettore: MT5Connector) -> None:
        assert connettore.connected is True
        assert connettore.is_simulated is True
        assert "simulato" in connettore.name

    async def test_initialize_fallito_solleva_con_il_codice_errore(
        self, settings: Settings, terminale: FakeMT5Terminal, clock: FrozenClock
    ) -> None:
        terminale.break_always("initialize", code=-10003, message="IPC initialize failed")
        connector = crea_connettore(settings, terminale, clock)
        with pytest.raises(BrokerNotConnectedError) as errore:
            await connector.connect()
        assert errore.value.context["mt5_error_code"] == -10003
        assert "terminale" in str(errore.value).lower()

    async def test_conto_diverso_da_quello_configurato_viene_rifiutato(
        self, settings: Settings, clock: FrozenClock
    ) -> None:
        """Un terminale lasciato aperto su un altro conto non deve essere usato."""
        terminale = FakeMT5Terminal(clock=clock, account=FakeAccount(login=999_999_999))
        # Il mock non sovrascrive il login se initialize non riceve credenziali:
        # qui le riceve, quindi si forza il disallineamento dopo l'inizializzazione.
        terminale.initialize = lambda **kwargs: True  # type: ignore[method-assign]
        connector = crea_connettore(settings, terminale, clock)
        with pytest.raises(BrokerError) as errore:
            await connector.connect()
        assert "conto diverso" in str(errore.value)
        assert errore.value.context["actual_login"] == 999_999_999

    async def test_terminale_disconnesso_e_unhealthy(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        terminale.terminal.connected = False
        stato = await connettore.health()
        assert stato.status is HealthStatus.UNHEALTHY
        assert stato.connected is False

    async def test_health_non_solleva_mai(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        """Deve poter raccontare il guasto, non propagarlo."""
        terminale.break_always("terminal_info")
        stato = await connettore.health()
        assert stato.status is HealthStatus.UNHEALTHY
        assert stato.detail

    async def test_dati_simulati_degradano_lo_stato(self, connettore: MT5Connector) -> None:
        """Con il mock attivo lo stato non è mai "sano": i prezzi sono inventati."""
        stato = await connettore.health()
        assert stato.status is HealthStatus.DEGRADED
        assert "NON sono quelli del broker" in stato.detail


class TestConto:
    async def test_lettura_conto(self, connettore: MT5Connector) -> None:
        conto = await connettore.get_account()
        assert conto.login == 169_444_760
        assert conto.currency == "EUR"
        assert conto.equity > 0
        assert conto.account_type == "DEMO"
        assert conto.is_demo is True
        assert conto.is_real_money is False

    async def test_conto_non_disponibile_solleva(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        terminale.break_always("account_info")
        with pytest.raises(BrokerNotConnectedError):
            await connettore.get_account()

    async def test_nessuna_posizione_non_e_un_errore(self, connettore: MT5Connector) -> None:
        """MT5 restituisce None sia per "nessuna posizione" sia per errore."""
        assert await connettore.get_positions() == ()

    async def test_errore_sulle_posizioni_viene_distinto_dal_vuoto(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        terminale.break_always("positions_get", code=-10004, message="No IPC connection")
        with pytest.raises(BrokerError, match="posizioni aperte"):
            await connettore.get_positions()

    async def test_lettura_posizioni(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        terminale.positions.append(
            FakePosition(
                ticket=1,
                symbol="GOLD",
                type=1,
                volume=0.05,
                price_open=2330.0,
                price_current=2325.0,
                sl=2340.0,
                tp=2300.0,
                profit=25.0,
                magic=777,
                comment="piano-1",
            )
        )
        posizioni = await connettore.get_positions()
        assert len(posizioni) == 1
        posizione = posizioni[0]
        assert posizione.side is PositionSide.SELL
        assert posizione.stop_loss == 2340.0
        assert posizione.has_protection is True
        assert posizione.magic == 777

    async def test_stop_a_zero_diventa_assente(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        """0.0 in MT5 significa "non impostato", non "stop a prezzo zero"."""
        terminale.positions.append(
            FakePosition(
                ticket=2,
                symbol="EURUSD",
                type=0,
                volume=0.01,
                price_open=1.08,
                price_current=1.081,
                sl=0.0,
                tp=0.0,
            )
        )
        posizione = (await connettore.get_positions())[0]
        assert posizione.stop_loss is None
        assert posizione.has_protection is False

    async def test_portafoglio_coerente(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        terminale.positions.append(
            FakePosition(
                ticket=3,
                symbol="EURUSD",
                type=0,
                volume=0.02,
                price_open=1.08,
                price_current=1.085,
                profit=10.0,
            )
        )
        portafoglio = await connettore.get_portfolio()
        assert portafoglio.open_positions_count == 1
        assert portafoglio.unrealized_pnl == 10.0
        assert portafoglio.symbols() == ("EURUSD",)
        assert len(portafoglio.unprotected_positions) == 1


class TestStrumenti:
    async def test_specifiche_di_contratto_lette_dal_broker(self, connettore: MT5Connector) -> None:
        """Il pip value non è una costante nostra: viene dal broker."""
        info = await connettore.get_symbol("GOLD")
        assert info.name == "GOLD"
        assert info.digits == 2
        assert info.contract_size == 100.0
        assert info.volume_min == 0.01
        assert info.tick_value > 0
        assert info.trade_allowed is True

    async def test_simbolo_inesistente(self, connettore: MT5Connector) -> None:
        with pytest.raises(SymbolNotFoundError, match="BTCUSD"):
            await connettore.get_symbol("BTCUSD")

    async def test_normalizzazione_del_volume_arrotonda_per_difetto(
        self, connettore: MT5Connector
    ) -> None:
        """Arrotondare per eccesso rischierebbe più del calcolato."""
        from backend.market_data.instruments import costruisci_strumento
        from backend.valuation.service import BrokerValuationService

        # La normalizzazione vive solo nel servizio di valutazione (ADR 0014).
        strumento = costruisci_strumento(await connettore.get_symbol("EURUSD"))
        normalizza = BrokerValuationService.normalize_volume
        assert normalizza(strumento, 0.123).volume == 0.12
        assert normalizza(strumento, 0.005).volume == 0.0  # sotto il minimo
        assert normalizza(strumento, 999).volume == strumento.contract.volume_max

    async def test_elenco_simboli(self, connettore: MT5Connector) -> None:
        simboli = await connettore.list_symbols()
        assert "EURUSD" in simboli
        assert "GOLD" in simboli


class TestQuotazioni:
    async def test_tick(self, connettore: MT5Connector) -> None:
        tick = await connettore.get_tick("EURUSD")
        assert tick.ask > tick.bid
        assert tick.spread > 0
        assert tick.bid < tick.mid < tick.ask

    async def test_il_connettore_non_giudica_la_freschezza(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        """Regressione ADR 0015: il vecchio `require_fresh` era un fail-open.

        Confrontava l'etichetta del tick (ora broker, UTC+3) con l'ora UTC del
        sistema: un tick fermo da 2h59m risultava "fresco". Il connettore ora
        restituisce il dato e basta; la freschezza si giudica in `quote_quality`.
        """
        import inspect

        assert "require_fresh" not in inspect.signature(connettore.get_tick).parameters
        terminale.tick_lag_seconds = 120
        tick = await connettore.get_tick("EURUSD")
        # Etichetta in ora broker: tre ore avanti rispetto all'UTC, meno il ritardo.
        scarto = (tick.time - terminale._clock.now()).total_seconds()
        assert scarto == pytest.approx(3 * 3_600 - 120, abs=1)

    async def test_assenza_di_quotazione_non_diventa_un_prezzo_zero(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        """MT5 dà bid/ask a zero quando non ha ancora quotazioni per il simbolo.

        Trovato provando sul conto XM reale: alcuni strumenti della whitelist
        rispondono così a mercato chiuso. È assenza di dato, non un prezzo.
        """
        terminale.symbols_without_quotes.add("US100Cash")
        with pytest.raises(StaleDataError, match="Nessuna quotazione"):
            await connettore.get_tick("US100Cash")


class TestCandele:
    async def test_serie_contiene_la_candela_in_formazione(self, connettore: MT5Connector) -> None:
        """Il punto più delicato: l'ultima barra non è chiusa e va dichiarata."""
        serie = await connettore.get_candles("EURUSD", Timeframe.H1, 50)
        assert len(serie) == 51
        assert serie.last_candle_closed is False
        assert len(serie.closed) == 50

    async def test_candele_ordinate_e_senza_duplicati(self, connettore: MT5Connector) -> None:
        serie = await connettore.get_candles("GOLD", Timeframe.M15, 100)
        tempi = [c.time for c in serie.candles]
        assert tempi == sorted(tempi)
        assert len(set(tempi)) == len(tempi)

    async def test_ohlc_coerente(self, connettore: MT5Connector) -> None:
        serie = await connettore.get_candles("US100Cash", Timeframe.H4, 30)
        for candela in serie.candles:
            assert candela.low <= candela.open <= candela.high
            assert candela.low <= candela.close <= candela.high

    async def test_serie_deterministica(self, connettore: MT5Connector) -> None:
        """Due letture consecutive devono dare lo stesso storico."""
        prima = await connettore.get_candles("EURUSD", Timeframe.H1, 40)
        seconda = await connettore.get_candles("EURUSD", Timeframe.H1, 40)
        assert [c.close for c in prima.candles] == [c.close for c in seconda.candles]

    async def test_timeframe_diversi_descrivono_lo_stesso_mercato(
        self, connettore: MT5Connector
    ) -> None:
        """H1 e H4 non devono raccontare due mercati diversi."""
        h1 = await connettore.get_candles("EURUSD", Timeframe.H1, 60)
        h4 = await connettore.get_candles("EURUSD", Timeframe.H4, 60)
        media_h1 = sum(h1.closes) / len(h1.closes)
        media_h4 = sum(h4.closes) / len(h4.closes)
        # Ordine di grandezza compatibile: lo stesso processo di prezzo.
        assert abs(media_h1 - media_h4) < media_h1 * 0.05

    async def test_count_non_valido(self, connettore: MT5Connector) -> None:
        with pytest.raises(ValueError, match="almeno 1"):
            await connettore.get_candles("EURUSD", Timeframe.H1, 0)

    async def test_richiesta_troppo_grande_viene_rifiutata(self, connettore: MT5Connector) -> None:
        with pytest.raises(ValueError, match="massimo per chiamata"):
            await connettore.get_candles("EURUSD", Timeframe.M1, 10_000)

    async def test_assenza_di_storico_solleva(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        terminale.break_always("copy_rates_from_pos")
        with pytest.raises(BrokerError, match="Nessuna candela"):
            await connettore.get_candles("EURUSD", Timeframe.H1, 10)


class TestOraServerBroker:
    async def test_ora_server_dedotta_dal_tick(self, connettore: MT5Connector) -> None:
        ora = await connettore.server_time()
        assert ora.tzinfo is not None


class TestTimeout:
    async def test_chiamata_che_non_risponde_diventa_timeout(
        self, terminale: FakeMT5Terminal, clock: FrozenClock
    ) -> None:
        """Il chiamante non deve restare appeso per sempre."""
        import time

        settings_timeout = MT5Settings(
            _env_file=None, use_mock=True, timeout_ms=1_000, login=1, password="x", server="S"
        )
        terminale.account_info = lambda: time.sleep(5)  # type: ignore[method-assign]
        connector = MT5Connector(settings_timeout, terminale, clock=clock, simulated=True)
        with pytest.raises(BrokerTimeoutError, match="non ha risposto"):
            await connector.get_account()
