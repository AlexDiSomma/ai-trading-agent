"""Test di integrazione dell'API.

Verificano tre cose che valgono più di cento test unitari:

1. l'applicazione si avvia e risponde anche con database e Redis spenti;
2. quando non può operare, **lo dice**, in italiano e con il motivo;
3. nessuna rotta permette di toccare il conto.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.brokers.mt5.mock import FakeMT5Terminal, FakePosition


class TestStato:
    async def test_health_risponde_anche_senza_database(self, client_anonimo: AsyncClient) -> None:
        """Senza PostgreSQL il sistema è fuori uso, ma deve poterlo dichiarare."""
        risposta = await client_anonimo.get("/api/health")
        assert risposta.status_code == 503
        corpo = risposta.json()
        assert corpo["status"] == "UNHEALTHY"
        assert corpo["can_trade"] is False
        motivi = " ".join(corpo["blocking_reasons"])
        assert "PostgreSQL" in motivi

    async def test_health_elenca_i_componenti_con_il_motivo(
        self, client_anonimo: AsyncClient
    ) -> None:
        corpo = (await client_anonimo.get("/api/health")).json()
        componenti = {c["name"]: c for c in corpo["components"]}
        assert "PostgreSQL" in componenti
        assert "Redis" in componenti
        assert any("MetaTrader5" in nome for nome in componenti)
        # Ogni componente spiega il proprio stato: niente stati muti.
        for componente in componenti.values():
            assert componente["detail"]

    async def test_health_avvisa_che_i_dati_sono_simulati(
        self, client_anonimo: AsyncClient
    ) -> None:
        corpo = (await client_anonimo.get("/api/health")).json()
        avvisi = " ".join(corpo["warnings"])
        assert "mock" in avvisi.lower()

    async def test_modalita_dichiarata_ed_effettiva(self, client_anonimo: AsyncClient) -> None:
        corpo = (await client_anonimo.get("/api/health")).json()
        assert corpo["declared_mode"] == "PAPER"
        assert corpo["effective_mode"] == "PAPER"

    async def test_liveness_non_dipende_da_nulla(self, client_anonimo: AsyncClient) -> None:
        risposta = await client_anonimo.get("/api/health/live")
        assert risposta.status_code == 200
        assert risposta.json()["stato"] == "attivo"


class TestAutenticazione:
    async def test_senza_token_le_rotte_sono_chiuse(self, client_anonimo: AsyncClient) -> None:
        for percorso in ("/api/account", "/api/market/watchlist", "/api/system/risk-limits"):
            risposta = await client_anonimo.get(percorso)
            assert risposta.status_code == 401, percorso

    async def test_con_token_le_rotte_rispondono(self, client: AsyncClient) -> None:
        risposta = await client.get("/api/market/watchlist")
        assert risposta.status_code == 200

    async def test_health_resta_pubblico(self, client_anonimo: AsyncClient) -> None:
        """Il docker healthcheck non ha un token, e deve poter interrogare."""
        assert (await client_anonimo.get("/api/health/live")).status_code == 200


class TestConto:
    async def test_lettura_conto(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/account")).json()
        assert corpo["login"] == 169_444_760
        assert corpo["currency"] == "EUR"
        assert corpo["equity"] > 0

    async def test_posizioni_vuote(self, client: AsyncClient) -> None:
        assert (await client.get("/api/account/positions")).json() == []

    async def test_posizioni_popolate(
        self, client: AsyncClient, terminale_api: FakeMT5Terminal
    ) -> None:
        terminale_api.positions.append(
            FakePosition(
                ticket=1,
                symbol="GOLD",
                type=0,
                volume=0.02,
                price_open=2320.0,
                price_current=2330.0,
                sl=2300.0,
                profit=20.0,
            )
        )
        corpo = (await client.get("/api/account/positions")).json()
        assert len(corpo) == 1
        assert corpo[0]["side"] == "BUY"
        assert corpo[0]["stop_loss"] == 2300.0

    async def test_portafoglio(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/account/portfolio")).json()
        assert corpo["open_positions_count"] == 0
        assert "account" in corpo

    async def test_conto_non_leggibile_diventa_503_con_il_motivo(
        self, client: AsyncClient, terminale_api: FakeMT5Terminal
    ) -> None:
        terminale_api.break_always("account_info")
        risposta = await client.get("/api/account")
        assert risposta.status_code == 503
        assert "terminale" in risposta.json()["detail"].lower()


class TestMercato:
    async def test_watchlist(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/market/watchlist")).json()
        assert corpo["strumenti"] == ["EURUSD", "GOLD"]
        assert corpo["dati_simulati"] is True

    async def test_specifiche_simbolo(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/market/symbols/GOLD")).json()
        assert corpo["simbolo"]["contract_size"] == 100.0
        assert corpo["eta_secondi"] >= 0

    async def test_simbolo_fuori_whitelist_vietato(self, client: AsyncClient) -> None:
        """US100Cash esiste sul broker: è l'utente che non l'ha autorizzato."""
        risposta = await client.get("/api/market/symbols/US100Cash")
        assert risposta.status_code == 403
        assert "RISK_ALLOWED_SYMBOLS" in risposta.json()["detail"]

    async def test_tick_dichiara_la_propria_eta(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/market/tick/EURUSD")).json()
        assert corpo["tick"]["ask"] > corpo["tick"]["bid"]
        assert "eta_secondi" in corpo
        assert corpo["scaduto"] is False

    async def test_tick_vecchio_viene_etichettato_non_nascosto(
        self, client: AsyncClient, terminale_api: FakeMT5Terminal
    ) -> None:
        terminale_api.tick_lag_seconds = 600
        corpo = (await client.get("/api/market/tick/EURUSD")).json()
        assert corpo["scaduto"] is True

    async def test_candele_dichiarano_se_l_ultima_e_chiusa(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/market/candles/EURUSD?timeframe=H1&count=50")).json()
        assert corpo["numero_candele"] == 51
        assert corpo["numero_chiuse"] == 50
        assert corpo["ultima_chiusa"] is False
        assert corpo["ruolo_timeframe"] == "trend"

    async def test_timeframe_non_valido_rifiutato(self, client: AsyncClient) -> None:
        risposta = await client.get("/api/market/candles/EURUSD?timeframe=Q1")
        assert risposta.status_code == 422

    async def test_count_fuori_scala_rifiutato(self, client: AsyncClient) -> None:
        assert (await client.get("/api/market/candles/EURUSD?count=99999")).status_code == 422


class TestLimitiDiRischio:
    async def test_i_limiti_sono_leggibili(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/system/risk-limits")).json()
        assert corpo["perimetro"]["strumenti"] == ["EURUSD", "GOLD"]
        assert corpo["perdite_massime"]["giornaliera_pct"] > 0
        assert "riavvio" in corpo["nota"]

    async def test_non_esiste_un_modo_di_modificarli_via_api(self, app) -> None:  # type: ignore[no-untyped-def]
        """I limiti si cambiano in .env, consapevolmente, non con una chiamata.

        Letto dallo schema OpenAPI: `app.routes` non espande i router inclusi, e
        un controllo su quella lista passerebbe a vuoto. L'asserzione positiva
        sulla rotta di lettura impedisce che accada di nuovo.
        """
        schema = app.openapi()
        percorsi = schema.get("paths", {})
        assert "/api/system/risk-limits" in percorsi, (
            "La rotta dei limiti non e' nello schema: il controllo non sta guardando niente."
        )
        metodi = {m.lower() for m in percorsi["/api/system/risk-limits"]}
        assert metodi == {"get"}, f"I limiti sono modificabili via API: {sorted(metodi)}"


class TestKillSwitch:
    async def test_stato_iniziale_libero(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/system/kill-switch")).json()
        assert corpo["attivo"] is False

    async def test_attivazione_e_rilascio(self, client: AsyncClient) -> None:
        attivazione = await client.post(
            "/api/system/kill-switch/engage", json={"motivo": "verifica manuale"}
        )
        assert attivazione.status_code == 200
        assert attivazione.json()["attivo"] is True

        stato = (await client.get("/api/system/kill-switch")).json()
        assert stato["attivo"] is True
        assert "verifica manuale" in stato["motivo"]

        rilascio = await client.post(
            "/api/system/kill-switch/release", json={"motivo": "fine verifica"}
        )
        assert rilascio.status_code == 200
        assert rilascio.json()["attivo"] is False

    async def test_il_rilascio_richiede_un_motivo(self, client: AsyncClient) -> None:
        """Rilasciare il blocco non deve poter essere un clic distratto."""
        risposta = await client.post("/api/system/kill-switch/release", json={})
        assert risposta.status_code == 422

    async def test_kill_switch_attivo_blocca_l_operativita(
        self, client: AsyncClient, client_anonimo: AsyncClient
    ) -> None:
        await client.post("/api/system/kill-switch/engage", json={"motivo": "test di blocco"})
        corpo = (await client_anonimo.get("/api/health")).json()
        assert corpo["kill_switch_engaged"] is True
        assert corpo["can_trade"] is False
        assert "test di blocco" in " ".join(corpo["blocking_reasons"])


class TestRadice:
    async def test_pagina_radice_orienta(self, client_anonimo: AsyncClient) -> None:
        corpo = (await client_anonimo.get("/")).json()
        assert corpo["modalita"] == "PAPER"
        assert corpo["stato"] == "/api/health"

    async def test_openapi_disponibile(self, client_anonimo: AsyncClient) -> None:
        risposta = await client_anonimo.get("/openapi.json")
        assert risposta.status_code == 200
        assert "AI Trading Agent" in risposta.json()["info"]["title"]


class TestRegistroSimboli:
    async def test_whitelist_verificata_contro_il_catalogo(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/analysis/symbols")).json()
        assert corpo["simboli_presso_il_broker"] > 0
        assert [v["richiesto"] for v in corpo["whitelist"]] == ["EURUSD", "GOLD"]
        assert all(v["utilizzabile"] for v in corpo["whitelist"])
        assert corpo["problemi"] == []

    async def test_la_nota_dichiara_che_gli_alias_non_sostituiscono(
        self, client: AsyncClient
    ) -> None:
        corpo = (await client.get("/api/analysis/symbols")).json()
        assert "suggerimenti" in corpo["nota"]


class TestSpecificheEValoreMonetario:
    async def test_scarto_fra_broker_e_tick_value_esposto(self, client: AsyncClient) -> None:
        """Il numero che giustifica l'uso del calcolatore del broker (ADR 0012)."""
        corpo = (await client.get("/api/analysis/specifications/GOLD")).json()
        assert corpo["classe_attivita"] == "METAL"
        assert "Spot Metals" in corpo["percorso_broker"]
        assert corpo["valore_unita_prezzo"] > 0
        assert corpo["scarto_percentuale"] == pytest.approx(12.5, abs=0.2)

    async def test_forex_scarto_trascurabile(self, client: AsyncClient) -> None:
        """Sul forex i due valori quasi coincidono, su metalli e indici no.

        Un piccolo scarto resta anche sul conto reale (misurato: +0,08% su
        EURUSD), perche' il `tick_value` dichiarato dal broker e' convertito a un
        cambio leggermente diverso dal prezzo corrente. Quello che conta e'
        l'ordine di grandezza: frazioni di punto percentuale contro il 12,5% dei
        metalli.
        """
        corpo = (await client.get("/api/analysis/specifications/EURUSD")).json()
        assert abs(corpo["scarto_percentuale"]) < 2.0

    async def test_strumento_fuori_whitelist_vietato(self, client: AsyncClient) -> None:
        assert (await client.get("/api/analysis/specifications/SILVER")).status_code == 403


class TestContestoDiMercato:
    async def test_analisi_multi_timeframe(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/analysis/asset/GOLD")).json()
        assert corpo["symbol"] == "GOLD"
        assert corpo["asset_class"] == "METAL"
        assert set(corpo["timeframes"]) == {"D1", "H4", "H1", "M15", "M5"}
        h1 = corpo["timeframes"]["H1"]
        assert h1["role"] == "trend"
        assert h1["indicators"]["ema200"] is not None
        assert h1["trend"]["factors"]

    async def test_dichiara_di_non_essere_utilizzabile(self, client: AsyncClient) -> None:
        """Prezzi simulati: il contesto dice no da sé."""
        corpo = (await client.get("/api/analysis/asset/GOLD")).json()
        assert corpo["simulated"] is True
        assert corpo["usable_for_decisions"] is False
        assert corpo["blocking_reasons"]

    async def test_nessuna_proposta_operativa_nella_risposta(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/analysis/asset/GOLD")).json()
        for vietato in ("entry", "stop_loss", "take_profit", "decision", "signal"):
            assert vietato not in corpo

    async def test_timeframe_selezionabili(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/analysis/asset/GOLD?timeframe=H1&timeframe=M5")).json()
        assert set(corpo["timeframes"]) == {"H1", "M5"}

    async def test_fuori_whitelist_vietato(self, client: AsyncClient) -> None:
        risposta = await client.get("/api/analysis/asset/SILVER")
        assert risposta.status_code == 403
        assert "RISK_ALLOWED_SYMBOLS" in risposta.json()["detail"]


class TestIstantaneaDiMercatoApi:
    async def test_istantanea_completa(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/analysis/snapshot")).json()
        assert corpo["observed_assets"] == 2
        assert corpo["market_phase"] in (
            "OPEN_CONFIRMED",
            "PARTIALLY_OPEN",
            "CLOSED_INFERRED",
            "UNKNOWN",
        )
        assert corpo["account"]["currency"] == "EUR"
        assert "exposure" in corpo
        assert corpo["simulated"] is True

    async def test_un_solo_istante_per_tutto(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/analysis/snapshot")).json()
        assert corpo["exposure"]["as_of"] == corpo["account"]["as_of"]


class TestEsposizione:
    async def test_portafoglio_vuoto(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/portfolio/exposure")).json()
        assert corpo["open_positions"] == 0
        assert corpo["risk_is_measurable"] is True

    async def test_posizione_senza_stop_rende_il_rischio_non_misurabile(
        self, client: AsyncClient, terminale_api: FakeMT5Terminal
    ) -> None:
        terminale_api.positions.append(
            FakePosition(
                ticket=7,
                symbol="GOLD",
                type=0,
                volume=0.01,
                price_open=4_140.0,
                price_current=4_145.0,
                sl=0.0,
            )
        )
        corpo = (await client.get("/api/portfolio/exposure")).json()
        assert corpo["risk_is_measurable"] is False
        assert corpo["total_risk_at_stop"] is None
        assert "GOLD" in corpo["positions_without_stop"]

    async def test_viste_per_valuta_e_per_classe(
        self, client: AsyncClient, terminale_api: FakeMT5Terminal
    ) -> None:
        terminale_api.positions.append(
            FakePosition(
                ticket=8,
                symbol="GOLD",
                type=0,
                volume=0.01,
                price_open=4_140.0,
                price_current=4_145.0,
                sl=4_130.0,
            )
        )
        corpo = (await client.get("/api/portfolio/exposure")).json()
        # Coppia economica (ADR 0014): l'oro è XAU contro USD, non USD/USD.
        assert {c["currency"] for c in corpo["by_currency"]} == {"XAU", "USD"}
        assert [c["asset_class"] for c in corpo["by_asset_class"]] == ["METAL"]
        assert corpo["total_risk_at_stop"] is not None


class TestRealizzato:
    async def test_nessuna_operazione(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/portfolio/realized?giorni=7")).json()
        assert corpo["pnl_realizzato"] == 0.0
        assert corpo["chiuse"] == []
        assert corpo["ora_broker_attendibile"] in (True, False)

    async def test_intervallo_limitato(self, client: AsyncClient) -> None:
        assert (await client.get("/api/portfolio/realized?giorni=0")).status_code == 422


class TestIntelligenzaGlobaleApi:
    """Fase 4: il contesto globale via HTTP, con il terminale finto."""

    async def test_contesto_globale(self, client: AsyncClient) -> None:
        risposta = await client.get("/api/global/context")
        assert risposta.status_code == 200
        corpo = risposta.json()
        assert corpo["regime"] in (
            "RISK_ON",
            "RISK_OFF",
            "MIXED",
            "TRANSITION",
            "EVENT_DRIVEN",
            "UNCERTAIN",
        )
        # Dati del terminale finto: dichiarati e mai utilizzabili.
        assert corpo["simulated"] is True
        assert corpo["usable_for_decisions"] is False
        assert corpo["data_quality"] in ("GOOD", "DEGRADED", "INSUFFICIENT")
        assert len(corpo["currency_strength"]["currencies"]) == 8
        assert corpo["macro_event_risk"]["calendar_available"] is False
        for chiave in ("risk_sentiment", "volatility", "equities", "metals", "intermarket"):
            assert chiave in corpo
        assert len(corpo["input_fingerprint"]) == 64

    async def test_snapshot_dichiara_lo_stato_di_ogni_serie(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/global/snapshot")).json()
        assert len(corpo["instruments"]) == 38
        stati = {s["status"] for i in corpo["instruments"] for s in i["series"]}
        assert stati <= {
            "FRESH",
            "UNVERIFIED",
            "CLOSED",
            "STALE",
            "INSUFFICIENT_HISTORY",
            "MISSING",
        }

    async def test_sintesi(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/global/summary")).json()
        for etichetta in (
            "Regime",
            "Risk",
            "USD",
            "Gold",
            "Equities",
            "Volatility",
            "Data quality",
        ):
            assert etichetta in corpo["sintesi"]

    async def test_contesto_deterministico_a_orologio_fermo(self, client: AsyncClient) -> None:
        a = (await client.get("/api/global/context", params={"force": True})).json()
        b = (await client.get("/api/global/context", params={"force": True})).json()
        assert a == b

    async def test_istantanea_calcola_prima_il_globale(self, client: AsyncClient) -> None:
        corpo = (await client.get("/api/analysis/snapshot")).json()
        assert corpo["global_context"] is not None
        for asset in corpo["assets"]:
            assert asset["global_backdrop"] is not None
            assert asset["global_backdrop"]["global_usable_for_decisions"] is False

    async def test_nessuna_rotta_di_scrittura_nel_globale(self, app) -> None:  # type: ignore[no-untyped-def]
        schema = app.openapi()
        globali = {p: m for p, m in schema["paths"].items() if p.startswith("/api/global")}
        assert globali, "nessuna rotta globale: il controllo passerebbe a vuoto"
        assert all(set(m) == {"get"} for m in globali.values())
