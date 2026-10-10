"""Lettore dell'universo globale sul terminale finto: stati delle serie e degrado.

Il terminale finto etichetta in ora broker (UTC+3) come XM e permette di simulare
mercato chiuso, flusso fermo e simboli assenti.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.brokers.mt5.connector import MT5Connector
from backend.brokers.mt5.mock import FakeMT5Terminal
from backend.config.global_universe import UNIVERSO, StrumentoDiRiferimento
from backend.core.enums import (
    GlobalDataQuality,
    MarketPhase,
    MarketStatus,
    ReferenceGroup,
    SeriesStatus,
    Timeframe,
)
from backend.global_market.engine import costruisci
from backend.global_market.reader import GlobalDataReader
from backend.market_data.broker_time import BrokerClock
from backend.market_data.instruments import InstrumentRegistry
from backend.market_data.specifications import SpecificationRepository


def lettore(connettore, clock, orologio, universo=UNIVERSO):  # type: ignore[no-untyped-def]
    registro = InstrumentRegistry(SpecificationRepository(connettore, clock=clock), clock=clock)
    return GlobalDataReader(
        connettore,
        registro,
        orologio,
        max_tick_age_seconds=15.0,
        clock=clock,
        universe=universo,
        attesa_rilettura_secondi=0.0,
    )


def per_nome(lettura, nome):  # type: ignore[no-untyped-def]
    return next(i for i in lettura.ingressi if i.canonical == nome)


class TestStatiDelleSerie:
    async def test_mercato_aperto_serie_fresche(
        self, connettore: MT5Connector, clock, orologio_broker: BrokerClock
    ) -> None:
        lettura = await lettore(connettore, clock, orologio_broker).leggi()
        eurusd = per_nome(lettura, "EURUSD")
        assert eurusd.market_status is MarketStatus.OPEN_CONFIRMED
        assert set(eurusd.stati.values()) == {SeriesStatus.FRESH}
        assert lettura.limite_broker == clock.now() + timedelta(hours=3)
        assert lettura.simulated is True

    async def test_tutto_l_universo_e_letto(
        self, connettore: MT5Connector, clock, orologio_broker: BrokerClock
    ) -> None:
        lettura = await lettore(connettore, clock, orologio_broker).leggi()
        assert len(lettura.ingressi) == len(UNIVERSO) == 38
        assert all(i.stati for i in lettura.ingressi)
        assert not [i.canonical for i in lettura.ingressi if i.reason.startswith("Simbolo")]

    async def test_mercato_chiuso(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock, orologio_broker
    ) -> None:  # type: ignore[no-untyped-def]
        terminale.simula_mercato_chiuso(30 * 3_600)
        lettura = await lettore(connettore, clock, orologio_broker).leggi()
        oro = per_nome(lettura, "XAUUSD")
        assert oro.market_status is MarketStatus.CLOSED_INFERRED
        assert set(oro.stati.values()) == {SeriesStatus.CLOSED}
        _, c = costruisci(
            lettura.ingressi,
            as_of=lettura.as_of,
            limite_broker=lettura.limite_broker,
            simulated=lettura.simulated,
        )
        assert c.market_phase is MarketPhase.CLOSED_INFERRED
        assert c.usable_for_decisions is False

    async def test_barre_ferme_a_mercato_non_chiuso_sono_stale(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock, orologio_broker
    ) -> None:  # type: ignore[no-untyped-def]
        """Tick vivi ma barre ferme da tre ore: il flusso delle candele è rotto."""
        terminale.bar_lag_seconds = 3 * 3_600
        lettura = await lettore(connettore, clock, orologio_broker).leggi()
        eurusd = per_nome(lettura, "EURUSD")
        assert eurusd.stati[Timeframe.M15] is SeriesStatus.STALE
        assert eurusd.stati[Timeframe.H1] is SeriesStatus.STALE
        assert "ferme" in eurusd.note[Timeframe.M15]

    async def test_ora_broker_ignota_serie_non_verificate(
        self, connettore: MT5Connector, clock
    ) -> None:  # type: ignore[no-untyped-def]
        lettura = await lettore(connettore, clock, BrokerClock()).leggi()
        eurusd = per_nome(lettura, "EURUSD")
        assert set(eurusd.stati.values()) == {SeriesStatus.UNVERIFIED}
        assert eurusd.market_status is MarketStatus.UNKNOWN
        assert lettura.limite_broker is None
        _, c = costruisci(lettura.ingressi, as_of=lettura.as_of, limite_broker=None)
        assert c.data_quality is not GlobalDataQuality.GOOD
        assert c.usable_for_decisions is False


class TestRiletturaSerieFerma:
    async def test_storico_non_sincronizzato_recuperato_alla_seconda_lettura(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock, orologio_broker
    ) -> None:  # type: ignore[no-untyped-def]
        """Misurato su XM il 2026-10-05: alla riapertura MT5 restituì USDCAD H4 fermo
        a venerdì, poi sincronizzato. Una rilettura recupera la serie."""
        originale = connettore.get_candles
        chiamate = {"n": 0}

        async def prima_ferma(simbolo, tf, quante):  # type: ignore[no-untyped-def]
            if simbolo == "USDCAD" and tf is Timeframe.H4:
                chiamate["n"] += 1
                terminale.bar_lag_seconds = 3 * 86_400 if chiamate["n"] == 1 else 0.0
            try:
                return await originale(simbolo, tf, quante)
            finally:
                terminale.bar_lag_seconds = 0.0

        connettore.get_candles = prima_ferma  # type: ignore[method-assign]
        universo = tuple(u for u in UNIVERSO if u.canonical == "USDCAD")
        lettura = await lettore(connettore, clock, orologio_broker, universo).leggi()
        usdcad = per_nome(lettura, "USDCAD")
        assert chiamate["n"] == 2
        assert usdcad.stati[Timeframe.H4] is SeriesStatus.FRESH


class TestSimboliMancanti:
    async def test_simbolo_inesistente_dichiarato(
        self, connettore: MT5Connector, clock, orologio_broker
    ) -> None:  # type: ignore[no-untyped-def]
        universo = (
            *UNIVERSO[:3],
            StrumentoDiRiferimento(
                "GER40", "DAX_INESISTENTE", ReferenceGroup.EQUITY_INDEX, "EUR", "GER40"
            ),
        )
        lettura = await lettore(connettore, clock, orologio_broker, universo).leggi()
        dax = per_nome(lettura, "GER40")
        assert set(dax.stati.values()) == {SeriesStatus.MISSING}
        assert "non leggibile" in dax.reason

    async def test_identita_diversa_esclusa_non_sostituita(
        self, connettore: MT5Connector, clock, orologio_broker
    ) -> None:  # type: ignore[no-untyped-def]
        """Se il simbolo dichiarato è un altro strumento, non lo si usa al suo posto."""
        universo = (StrumentoDiRiferimento("XAUUSD", "SILVER", ReferenceGroup.METAL, "USD", "XAU"),)
        lettura = await lettore(connettore, clock, orologio_broker, universo).leggi()
        oro = per_nome(lettura, "XAUUSD")
        assert set(oro.stati.values()) == {SeriesStatus.MISSING}
        assert "XAGUSD" in oro.reason

    async def test_errore_del_broker_sulle_candele(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock, orologio_broker
    ) -> None:  # type: ignore[no-untyped-def]
        terminale.break_always("copy_rates_from_pos")
        lettura = await lettore(connettore, clock, orologio_broker, UNIVERSO[:2]).leggi()
        assert all(set(i.stati.values()) == {SeriesStatus.MISSING} for i in lettura.ingressi)


class TestTickValutatoAllOraDiLettura:
    async def test_lettura_lenta_non_produce_tick_dal_futuro(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock, orologio_broker
    ) -> None:  # type: ignore[no-untyped-def]
        """Regressione dal conto reale: 37 strumenti letti in sequenza.

        Il tick si valutava contro l'istante d'inizio lettura; quelli letti dopo
        risultavano "dal futuro" e incoerenti. Qui l'orologio avanza di 5 minuti
        durante la lettura.
        """
        originale = connettore.get_tick

        async def tick_lento(simbolo):  # type: ignore[no-untyped-def]
            clock.advance(seconds=10)
            orologio_broker.seed(10_800, clock.now())
            return await originale(simbolo)

        connettore.get_tick = tick_lento  # type: ignore[method-assign]
        lettura = await lettore(connettore, clock, orologio_broker).leggi()
        stati = {i.quote_status for i in lettura.ingressi}
        assert "INCONSISTENT" not in {str(s) for s in stati}


@pytest.mark.parametrize("modulo", ["global_market", "intermarket", "macro", "calendar"])
def test_l_intelligence_globale_non_tocca_esecuzione_ai_o_calcolatori(modulo: str) -> None:
    """Fase 4: nessun LLM, nessun ordine, nessun TradeIntent, nessun calcolo monetario."""
    import ast

    from backend.config.paths import BACKEND_ROOT

    vietati = (
        "backend.ai",
        "backend.execution",
        "backend.trade_planner",
        "backend.valuation",
        "backend.risk",
    )
    file = sorted((BACKEND_ROOT / modulo).rglob("*.py"))
    assert file, f"{modulo} vuoto: il controllo passerebbe a vuoto"
    for percorso in file:
        albero = ast.parse(percorso.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.ImportFrom) and nodo.module:
                assert not nodo.module.startswith(vietati), f"{percorso.name} importa {nodo.module}"
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    assert not alias.name.startswith(vietati), (
                        f"{percorso.name} importa {alias.name}"
                    )
            if isinstance(nodo, ast.Attribute):
                assert nodo.attr not in {"order_send", "order_calc_profit", "order_calc_margin"}
