"""Test del servizio dati di mercato.

Verifica la cosa più importante di questo strato: che il perimetro deciso
dall'utente valga anche per la semplice lettura. Se il sistema può *guardare*
uno strumento non autorizzato, prima o poi lo includerà in un'analisi.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.brokers.mt5.connector import MT5Connector
from backend.config.risk_limits import RiskLimits
from backend.core.clock import FrozenClock
from backend.core.enums import Timeframe
from backend.core.errors import StaleDataError, SymbolNotAllowedError
from backend.market_data.reader import TIMEFRAME_HIERARCHY, MarketDataService


@pytest.fixture
def servizio(connettore: MT5Connector, clock: FrozenClock) -> MarketDataService:
    limiti = RiskLimits(_env_file=None, RISK_ALLOWED_SYMBOLS="EURUSD,GOLD")
    return MarketDataService(connettore, limiti, clock=clock)


class TestPerimetro:
    def test_la_watchlist_e_quella_dell_utente(self, servizio: MarketDataService) -> None:
        assert servizio.watchlist == ("EURUSD", "GOLD")

    async def test_strumento_fuori_whitelist_rifiutato(self, servizio: MarketDataService) -> None:
        """US100Cash esiste sul broker ma l'utente non l'ha autorizzato."""
        with pytest.raises(SymbolNotAllowedError) as errore:
            await servizio.get_symbol("US100Cash")
        assert "RISK_ALLOWED_SYMBOLS" in str(errore.value)

    async def test_rifiuto_anche_per_candele_e_tick(self, servizio: MarketDataService) -> None:
        with pytest.raises(SymbolNotAllowedError):
            await servizio.get_tick("US100Cash")
        with pytest.raises(SymbolNotAllowedError):
            await servizio.get_candles("US100Cash", Timeframe.H1, 20)

    async def test_nome_non_sensibile_alle_maiuscole(self, servizio: MarketDataService) -> None:
        info = await servizio.get_symbol("eurusd")
        assert info.value.name == "EURUSD"


class TestFreschezza:
    async def test_ogni_lettura_dichiara_quando_e_stata_prodotta(
        self, servizio: MarketDataService, clock: FrozenClock
    ) -> None:
        fresh = await servizio.get_candles("EURUSD", Timeframe.H1, 30)
        assert fresh.as_of == clock.now()
        assert fresh.source.startswith("MetaTrader5")
        assert fresh.is_stale(clock.now()) is False

    async def test_un_dato_invecchiato_non_si_puo_usare_per_decidere(
        self, servizio: MarketDataService, clock: FrozenClock
    ) -> None:
        fresh = await servizio.get_candles("EURUSD", Timeframe.M5, 30)
        dopo = clock.now() + timedelta(minutes=10)
        with pytest.raises(StaleDataError):
            fresh.require_fresh(dopo)

    async def test_ttl_proporzionato_al_timeframe(self, servizio: MarketDataService) -> None:
        """Una lettura D1 resta valida molto più di una M5."""
        m5 = await servizio.get_candles("EURUSD", Timeframe.M5, 30)
        d1 = await servizio.get_candles("EURUSD", Timeframe.D1, 30)
        assert d1.ttl_seconds > m5.ttl_seconds * 10


class TestMultiTimeframe:
    async def test_gerarchia_dal_lento_al_veloce(self, servizio: MarketDataService) -> None:
        """Si parte dalla struttura e si scende: non il contrario."""
        assert TIMEFRAME_HIERARCHY[0] is Timeframe.D1
        assert TIMEFRAME_HIERARCHY[-1] is Timeframe.M5

    async def test_lettura_su_piu_timeframe(self, servizio: MarketDataService) -> None:
        serie = await servizio.get_multi_timeframe(
            "GOLD", (Timeframe.H4, Timeframe.H1, Timeframe.M15), count=40
        )
        assert set(serie) == {Timeframe.H4, Timeframe.H1, Timeframe.M15}
        for timeframe, fresh in serie.items():
            assert fresh.value.timeframe is timeframe
            assert len(fresh.value.closed) == 40

    async def test_multi_timeframe_rispetta_la_whitelist(self, servizio: MarketDataService) -> None:
        with pytest.raises(SymbolNotAllowedError):
            await servizio.get_multi_timeframe("US100Cash")


class TestCandeleChiuse:
    async def test_la_candela_in_formazione_resta_fuori_dai_calcoli(
        self, servizio: MarketDataService
    ) -> None:
        fresh = await servizio.get_candles("EURUSD", Timeframe.H1, 100)
        serie = fresh.value
        assert len(serie.candles) == len(serie.closed) + 1
        assert len(serie.closes) == len(serie.closed)

    async def test_has_enough_protegge_dagli_indicatori_senza_dati(
        self, servizio: MarketDataService
    ) -> None:
        fresh = await servizio.get_candles("EURUSD", Timeframe.H1, 30)
        assert fresh.value.has_enough(30) is True
        assert fresh.value.has_enough(300) is False
