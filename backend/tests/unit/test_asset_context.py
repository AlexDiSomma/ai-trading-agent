"""Test del contesto di mercato: composizione, multi-timeframe, utilizzabilità.

La proprietà più importante verificata qui è `usable_for_decisions`. È il fail
closed applicato al contesto: un contesto con dati scarsi, quotazione non valida o
prezzi simulati dice **no** da sé, senza che il consumatore debba dedurlo dai
valori. Nella fase 7 sarà la differenza fra un agente che rifiuta di decidere e
uno che decide su numeri inventati.

Il contesto inoltre **non contiene proposte operative**: un test lo verifica sui
campi del modello, perché la tentazione di aggiungere "direzione suggerita" qui
arriverà, e aggirerebbe il Risk Engine.
"""

from __future__ import annotations

import pytest

from backend.core.enums import (
    AssetClass,
    DataQuality,
    QuoteState,
    Timeframe,
)
from backend.core.errors import StaleDataError
from backend.schemas.context import AssetMarketContext
from backend.services.container import ApplicationContainer
from backend.tests.conftest import crea_settings


@pytest.fixture
async def contenitore(tmp_path, clock):  # type: ignore[no-untyped-def]
    settings = crea_settings(
        kill_switch_file=tmp_path / "KILL_SWITCH",
        allowed_symbols="EURUSD,GOLD,US100Cash",
    )
    container = ApplicationContainer(settings, clock=clock)
    await container.startup()
    yield container
    await container.shutdown()


@pytest.fixture
async def contesto(contenitore) -> AssetMarketContext:  # type: ignore[no-untyped-def]
    return await contenitore.require_analyst().analyse("GOLD")


class TestComposizione:
    async def test_tutti_i_timeframe_della_gerarchia(self, contesto: AssetMarketContext) -> None:
        assert set(contesto.timeframes) == {
            Timeframe.D1,
            Timeframe.H4,
            Timeframe.H1,
            Timeframe.M15,
            Timeframe.M5,
        }

    async def test_ruoli_dei_timeframe(self, contesto: AssetMarketContext) -> None:
        """La gerarchia del progetto, leggibile nel contesto."""
        ruoli = {tf: a.role for tf, a in contesto.timeframes.items()}
        assert ruoli[Timeframe.D1] == "structure"
        assert ruoli[Timeframe.H4] == "structure"
        assert ruoli[Timeframe.H1] == "trend"
        assert ruoli[Timeframe.M15] == "setup"
        assert ruoli[Timeframe.M5] == "execution"

    async def test_classe_di_attivita_dal_broker(self, contesto: AssetMarketContext) -> None:
        assert contesto.asset_class is AssetClass.METAL

    async def test_indicatori_popolati(self, contesto: AssetMarketContext) -> None:
        indicatori = contesto.timeframes[Timeframe.H1].indicators
        for campo in ("ema20", "ema50", "ema200", "rsi14", "atr14", "adx14"):
            assert getattr(indicatori, campo) is not None
        assert indicatori.bars_used > 200

    async def test_valori_derivati_coerenti(self, contesto: AssetMarketContext) -> None:
        indicatori = contesto.timeframes[Timeframe.H1].indicators
        assert indicatori.atr_pct is not None
        assert indicatori.atr_pct > 0
        assert indicatori.ema_alignment in (-1, 0, 1)
        if indicatori.bollinger_position is not None:
            # Il prezzo può uscire dalle bande: la posizione non è limitata a [0,1].
            assert isinstance(indicatori.bollinger_position, float)

    async def test_struttura_e_livelli_presenti(self, contesto: AssetMarketContext) -> None:
        analisi = contesto.timeframes[Timeframe.H1]
        assert analisi.structure.confirmed_swings >= 0
        for livello in analisi.levels:
            assert livello.distance_atr is not None

    async def test_solo_gli_ultimi_swing_nel_contesto(self, contesto: AssetMarketContext) -> None:
        """La serie completa gonfierebbe il contesto persistito senza aggiungere nulla."""
        assert len(contesto.timeframes[Timeframe.H1].swings) <= 8

    async def test_valore_monetario_dal_broker(self, contesto: AssetMarketContext) -> None:
        assert contesto.contract.value_per_price_unit is not None
        assert contesto.contract.value_source == "broker_calculator"

    async def test_supporti_sotto_e_resistenze_sopra(self, contesto: AssetMarketContext) -> None:
        analisi = contesto.timeframes[Timeframe.H1]
        prezzo = analisi.indicators.close
        if analisi.nearest_support is not None:
            assert analisi.nearest_support < prezzo
        if analisi.nearest_resistance is not None:
            assert analisi.nearest_resistance > prezzo


class TestMultiTimeframe:
    async def test_bias_per_ruolo(self, contesto: AssetMarketContext) -> None:
        vista = contesto.multi_timeframe
        for valore in (
            vista.structural_bias,
            vista.operational_bias,
            vista.setup_bias,
            vista.execution_bias,
        ):
            assert valore in (-1, 0, 1)

    async def test_allineamento_nei_limiti(self, contesto: AssetMarketContext) -> None:
        assert -1.0 <= contesto.multi_timeframe.alignment <= 1.0
        assert contesto.multi_timeframe.total_timeframes == 5

    async def test_conflitto_dichiarato_quando_le_direzioni_divergono(
        self, contesto: AssetMarketContext
    ) -> None:
        vista = contesto.multi_timeframe
        veloce = vista.execution_bias or vista.setup_bias
        atteso = bool(vista.structural_bias and veloce and vista.structural_bias != veloce)
        assert vista.structure_execution_conflict is atteso

    async def test_regime_dominante_dal_timeframe_strutturale(
        self, contesto: AssetMarketContext
    ) -> None:
        assert (
            contesto.multi_timeframe.dominant_regime
            is contesto.timeframes[Timeframe.D1].trend.regime
        )

    async def test_un_solo_timeframe_richiesto(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        contesto = await contenitore.require_analyst().analyse("GOLD", (Timeframe.H1,))
        assert contesto.multi_timeframe.total_timeframes == 1
        assert contesto.multi_timeframe.structural_bias == 0


class TestUtilizzabilita:
    async def test_dati_simulati_rendono_il_contesto_inutilizzabile(
        self, contesto: AssetMarketContext
    ) -> None:
        """Il fail closed applicato al contesto."""
        assert contesto.simulated is True
        assert contesto.usable_for_decisions is False
        assert any("simulati" in m.lower() for m in contesto.blocking_reasons)

    async def test_motivi_del_blocco_elencati(self, contesto: AssetMarketContext) -> None:
        assert contesto.blocking_reasons

    async def test_qualita_segnalata(self, contesto: AssetMarketContext) -> None:
        assert contesto.quality is DataQuality.SIMULATED

    async def test_quotazione_assente_non_blocca_l_analisi(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        """Senza tick l'analisi storica resta possibile, ma lo stato lo dichiara."""
        terminale = contenitore.broker._terminal
        terminale.symbols_without_quotes.add("GOLD")
        contesto = await contenitore.require_analyst().analyse("GOLD")
        assert contesto.quote.state is QuoteState.NO_QUOTE
        assert contesto.usable_for_decisions is False
        assert contesto.timeframes  # l'analisi delle candele c'è comunque

    async def test_mercato_chiuso_dichiarato_come_deduzione(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        terminale = contenitore.broker._terminal
        # Tick **e** barre vecchi: e' la situazione reale di un mercato chiuso.
        # Con i soli tick vecchi lo stato corretto e' STALE, non MARKET_CLOSED.
        terminale.simula_mercato_chiuso(31 * 3_600)
        contesto = await contenitore.require_analyst().analyse("GOLD")
        assert contesto.quote.state is QuoteState.MARKET_CLOSED
        assert contesto.quote.is_inference is True


class TestNessunaProposta:
    async def test_il_contesto_non_contiene_direzioni_da_prendere(
        self, contesto: AssetMarketContext
    ) -> None:
        """Entry, stop e direzioni arrivano con la fase 7, non da qui.

        Il contesto descrive il mercato. Aggiungere qui una "direzione suggerita"
        aprirebbe una strada che aggira il Trade Planner e, di conseguenza, il
        Risk Engine.
        """
        campi = set(AssetMarketContext.model_fields)
        vietati = {"entry", "stop_loss", "take_profit", "decision", "direction", "signal"}
        assert campi & vietati == set()

    async def test_i_fattori_della_tendenza_sono_leggibili(
        self, contesto: AssetMarketContext
    ) -> None:
        tendenza = contesto.timeframes[Timeframe.H1].trend
        assert tendenza.factors
        assert tendenza.reasons


class TestPersistibilita:
    async def test_serializzabile_in_json(self, contesto: AssetMarketContext) -> None:
        """Deve poter finire su database per la ricostruzione a mesi di distanza."""
        payload = contesto.model_dump(mode="json")
        assert payload["symbol"] == "GOLD"
        assert "timeframes" in payload
        import json

        assert json.loads(json.dumps(payload))["asset_class"] == "METAL"

    async def test_ricostruibile_dal_payload(self, contesto: AssetMarketContext) -> None:
        copia = AssetMarketContext.model_validate(contesto.model_dump(mode="json"))
        assert copia.symbol == contesto.symbol
        assert copia.multi_timeframe.alignment == contesto.multi_timeframe.alignment

    async def test_immutabile(self, contesto: AssetMarketContext) -> None:
        with pytest.raises(Exception):  # noqa: B017  (ValidationError di pydantic)
            contesto.symbol = "ALTRO"  # type: ignore[misc]


class TestIstantaneaDiMercato:
    async def test_istantanea_completa(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        snap = await contenitore.require_snapshots().build()
        assert snap.observed_assets == 3
        assert snap.account.currency == "EUR"
        assert snap.exposure is not None
        assert snap.market_phase is not None

    async def test_un_solo_istante_per_tutto(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        """Conto, esposizione e contesti devono essere della stessa fotografia."""
        snap = await contenitore.require_snapshots().build()
        assert snap.exposure.as_of == snap.account.as_of
        for contesto in snap.assets:
            assert contesto.as_of == snap.as_of

    async def test_whitelist_sbagliata_segnalata_e_strumento_escluso(self, tmp_path, clock) -> None:  # type: ignore[no-untyped-def]
        settings = crea_settings(
            kill_switch_file=tmp_path / "KILL",
            allowed_symbols="EURUSD,XAUUSD",
        )
        container = ApplicationContainer(settings, clock=clock)
        await container.startup()
        try:
            snap = await container.require_snapshots().build()
            assert [p.requested for p in snap.whitelist_problems] == ["XAUUSD"]
            assert snap.whitelist_problems[0].suggestions == ("GOLD",)
            # Escluso, non sostituito con GOLD.
            assert [a.symbol for a in snap.assets] == ["EURUSD"]
            assert any("esclusi" in a for a in snap.warnings)
        finally:
            await container.shutdown()

    async def test_un_asset_rotto_non_ferma_gli_altri(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        terminale = contenitore.broker._terminal
        terminale.break_always("copy_rates_from_pos")
        snap = await contenitore.require_snapshots().build()
        # Senza candele i contesti sono vuoti, ma la lettura non esplode.
        assert snap.account is not None

    async def test_dati_simulati_dichiarati(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        snap = await contenitore.require_snapshots().build()
        assert snap.simulated is True
        assert snap.usable_assets == ()
        assert any("simulat" in a.lower() for a in snap.warnings)

    async def test_ora_del_broker_non_misurabile_e_dichiarata(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        """A mercato chiuso lo scarto non si può stabilire, e lo si dichiara.

        All'avvio il contenitore lo misura (mercato aperto nel mock); qui lo si
        dimentica e si chiude il mercato: nessuna coppia di quotazioni fresche
        può ristabilirlo.
        """
        terminale = contenitore.broker._terminal
        terminale.simula_mercato_chiuso(31 * 3_600)
        contenitore.broker_clock.reset()
        snap = await contenitore.require_snapshots().build()
        assert snap.broker_time_source in ("UNAVAILABLE", "CACHED")
        assert any("Ora del server" in a for a in snap.warnings)


class TestSimboloFuoriPerimetro:
    async def test_strumento_non_autorizzato_rifiutato(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        from backend.core.errors import SymbolNotAllowedError

        with pytest.raises(SymbolNotAllowedError):
            await contenitore.require_analyst().analyse("SILVER")

    async def test_errori_del_broker_propagati(self, contenitore) -> None:  # type: ignore[no-untyped-def]
        terminale = contenitore.broker._terminal
        terminale.break_always("symbol_info")
        with pytest.raises((StaleDataError, Exception)):
            await contenitore.require_analyst().analyse("GOLD")
