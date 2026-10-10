"""Test di non-regressione sulle scoperte fatte sul conto XM demo reale.

Ogni classe di questo file corrisponde a **un'assunzione sbagliata** scoperta
collegandosi davvero al broker il 2026-10-04. Non sono test di unità ordinari:
sono il contratto che impedisce a quelle scoperte di andare perdute.

La regola, se uno di questi test diventa rosso: **non adattare il test.**
Significa che una proprietà verificata sul broker vero è stata persa. Si corregge
il codice, oppure — se il broker è cambiato davvero — si ricontrolla sul conto,
si aggiorna `DECISIONI.md` e solo allora il test.

Il terminale finto riproduce le specifiche reali di XM (nomi, digits, contract
size, tick value, valute, stops level), quindi questi test girano senza MT5 e
senza Windows: vedi `backend/brokers/mt5/mock.py`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.brokers.mt5.connector import MT5Connector
from backend.brokers.mt5.mapping import to_account_snapshot
from backend.brokers.mt5.mock import DEFAULT_SYMBOLS, FakeMT5Terminal
from backend.config.risk_limits import RiskLimits
from backend.core.enums import MarketStatus, QuoteQualityStatus, QuoteState
from backend.core.errors import StaleDataError, SymbolNotFoundError
from backend.market_data.broker_time import BrokerClock
from backend.market_data.quote_quality import QuoteQualityEvaluator
from backend.valuation.service import BrokerValuationService


class TestNomiDeiSimboli:
    """Scoperta 1: su XM i nomi canonici non esistono.

    L'oro si chiama `GOLD`, non `XAUUSD`. Gli indici `US30Cash`, `US100Cash`,
    `US500Cash`, non `US30`/`NAS100`/`SP500`. E la grafia conta: `US30CASH` in
    maiuscolo **non esiste** sul broker, quindi normalizzare i nomi in maiuscolo
    renderebbe la whitelist inutilizzabile proprio sugli indici.
    """

    def test_la_grafia_mista_viene_conservata(self) -> None:
        limiti = RiskLimits(_env_file=None, RISK_ALLOWED_SYMBOLS="EURUSD,GOLD,US30Cash")
        assert limiti.allowed_symbols == ("EURUSD", "GOLD", "US30Cash")
        # Se qualcuno reintroducesse .upper(), questa asserzione cade.
        assert "US30CASH" not in limiti.allowed_symbols

    def test_il_confronto_ignora_le_maiuscole_ma_invia_la_grafia_del_broker(self) -> None:
        limiti = RiskLimits(_env_file=None, RISK_ALLOWED_SYMBOLS="US100Cash")
        assert limiti.is_symbol_allowed("us100cash") is True
        assert limiti.canonical_symbol("US100CASH") == "US100Cash"

    async def test_un_nome_inesistente_non_viene_sostituito_in_silenzio(
        self, connettore: MT5Connector
    ) -> None:
        """XAUUSD non esiste: il sistema deve dirlo, non ripiegare su GOLD."""
        with pytest.raises(SymbolNotFoundError) as errore:
            await connettore.get_symbol("XAUUSD")
        assert "XAUUSD" in str(errore.value)

    def test_il_terminale_finto_non_offre_i_nomi_canonici(self) -> None:
        """Il mock deve mentire come mente il broker, non essere più gentile."""
        nomi = {simbolo.name for simbolo in DEFAULT_SYMBOLS}
        assert "GOLD" in nomi
        assert "US100Cash" in nomi
        assert "XAUUSD" not in nomi
        assert "US30" not in nomi
        assert "NAS100" not in nomi


class TestValoreDelTick:
    """Scoperta 2: il valore del tick dipende dalla valuta del conto.

    Su un conto in EUR, `trade_tick_value` di EURUSD è **0.8887**, non 1.0; su
    USDJPY è 0.5629; su SILVER è 5.0 perché il contratto è di 5.000 once. Una
    costante nel codice al posto di questi numeri significa una size sbagliata.
    """

    async def test_il_tick_value_non_e_mai_uno_per_definizione(
        self, connettore: MT5Connector
    ) -> None:
        eurusd = await connettore.get_symbol("EURUSD")
        assert eurusd.tick_value == pytest.approx(0.8887)
        assert eurusd.tick_value != 1.0

    async def test_strumenti_diversi_hanno_contratti_diversi(
        self, connettore: MT5Connector, strumento
    ) -> None:
        eurusd = await connettore.get_symbol("EURUSD")
        gold = await connettore.get_symbol("GOLD")
        silver = await connettore.get_symbol("SILVER")
        indice = await connettore.get_symbol("US100Cash")

        assert eurusd.contract_size == 100_000.0
        assert gold.contract_size == 100.0
        assert silver.contract_size == 5_000.0
        assert indice.contract_size == 1.0

        # Il passo di volume degli indici è dieci volte quello del forex: una
        # size di 0.01 lotti su US100Cash viene rifiutata dal broker.
        assert eurusd.volume_step == 0.01
        assert indice.volume_step == 0.1
        # Normalizzazione dal servizio di valutazione (ADR 0014): unico punto.
        us100 = await strumento("US100Cash")
        assert BrokerValuationService.normalize_volume(us100, 0.05).volume == 0.0

    async def test_il_valore_di_un_tick_in_valuta_conto_si_calcola_dal_broker(
        self, valutazione: BrokerValuationService, strumento
    ) -> None:
        """Perdita per 1 lotto su 100 tick di GOLD: 88.87 EUR, non 100.

        Fino al 2026-10-04 questo test moltiplicava `tick_value` e otteneva 100:
        il numero giusto in **dollari**, sbagliato in valuta del conto. La
        versione corretta passa dal servizio di valutazione (ADR 0014).
        """
        from backend.core.enums import Direction

        gold = await strumento("GOLD")
        assert gold.contract.tick_size == 0.01
        stima = await valutazione.loss_at_stop(gold, Direction.LONG, 1.0, 4_140.0, 4_139.0)
        assert stima.loss == pytest.approx(88.87, abs=0.05)
        assert stima.account_currency == "EUR"


class TestDistanzaMinimaDiStop:
    """Scoperta 3: XM dichiara `trade_stops_level = 0` su tutti gli strumenti.

    Significa che il broker **non** impone una distanza minima fra prezzo e stop
    loss. Un controllo di rischio che si appoggiasse a quel campo non
    proteggerebbe da nulla: il minimo in ATR definito dall'utente
    (`RISK_MIN_STOP_DISTANCE_ATR`) è l'unica difesa contro uno stop troppo
    vicino, e deve restare obbligatorio.
    """

    async def test_il_broker_non_impone_una_distanza_minima(self, connettore: MT5Connector) -> None:
        for nome in ("EURUSD", "GOLD", "US100Cash"):
            info = await connettore.get_symbol(nome)
            assert info.stops_level_points == 0

    def test_il_limite_in_atr_resta_definito_dall_utente(self) -> None:
        limiti = RiskLimits(_env_file=None, RISK_ALLOWED_SYMBOLS="GOLD")
        assert limiti.min_stop_distance_atr > 0
        assert limiti.max_stop_distance_atr > limiti.min_stop_distance_atr


class TestStatoDelleQuotazioni:
    """Scoperta 4: bid e ask a zero, e quotazioni vecchie di 31 ore.

    `US100Cash` e `US500Cash` restituiscono bid/ask a 0.0 finché non sono stati
    attivati nel Market Watch. E a mercato chiuso (verificato di sabato) l'ultima
    quotazione di tutti gli strumenti era vecchia di più di trenta ore.

    Sono tre situazioni distinte che non vanno confuse: nessuna quotazione, dato
    vecchio, quotazione valida.
    """

    async def test_bid_a_zero_non_e_un_prezzo(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal
    ) -> None:
        terminale.symbols_without_quotes.add("US500Cash")
        with pytest.raises(StaleDataError) as errore:
            await connettore.get_tick("US500Cash")
        assert "Nessuna quotazione" in str(errore.value)

    async def test_quotazione_vecchia_restituita_ma_etichettata(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock
    ) -> None:
        """Trentuno ore: è l'età reale misurata di sabato sul conto.

        Il connettore restituisce il dato; è la valutazione di qualità a dire
        che il mercato è **dedotto** chiuso (mai "chiuso" come certezza).
        """
        terminale.simula_mercato_chiuso(31 * 3_600)
        info = await connettore.get_symbol("GOLD")
        tick = await connettore.get_tick("GOLD")
        assert tick.bid > 0
        qualita = QuoteQualityEvaluator(max_tick_age_seconds=15.0).evaluate(
            info, tick, now=clock.now(), broker_clock=BrokerClock()
        )
        assert qualita.status is QuoteQualityStatus.MARKET_CLOSED
        assert qualita.market_status is MarketStatus.CLOSED_INFERRED
        assert qualita.is_inference is True

    async def test_quotazione_valida_passa(
        self, connettore: MT5Connector, orologio_broker: BrokerClock, clock
    ) -> None:
        info = await connettore.get_symbol("GOLD")
        tick = await connettore.get_tick("GOLD")
        qualita = QuoteQualityEvaluator(max_tick_age_seconds=15.0).evaluate(
            info, tick, now=clock.now(), broker_clock=orologio_broker
        )
        assert qualita.status is QuoteQualityStatus.VALID
        assert qualita.market_status is MarketStatus.OPEN_CONFIRMED
        assert tick.ask > tick.bid > 0

    async def test_tick_fermo_da_quasi_tre_ore_non_e_valido(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock
    ) -> None:
        """Scoperta 11 (fail-open, ADR 0015): ora broker UTC+3 letta come UTC.

        Un tick fermo da 2h59m ha un'etichetta solo un minuto "nel futuro"
        rispetto all'UTC di sistema: il vecchio controllo lo dava per fresco.
        Con lo scarto noto è vecchio; con lo scarto ignoto non è comunque valido.
        """
        terminale.tick_lag_seconds = 2 * 3_600 + 59 * 60
        info = await connettore.get_symbol("GOLD")
        tick = await connettore.get_tick("GOLD")
        valutatore = QuoteQualityEvaluator(max_tick_age_seconds=15.0)

        noto = BrokerClock()
        noto.seed(3 * 3_600, clock.now())
        for orologio in (noto, BrokerClock()):
            qualita = valutatore.evaluate(info, tick, now=clock.now(), broker_clock=orologio)
            assert qualita.status is not QuoteQualityStatus.VALID
            assert qualita.market_status is not MarketStatus.OPEN_CONFIRMED


class TestTipoDiConto:
    """Scoperta 5: il server demo di XM non contiene la parola "demo".

    Il conto demo sta su `XMGlobalMU-MT5 2`. L'euristica sul nome del server lo
    annunciava come **CONTO REALE**: un errore che fa prendere alla leggera un
    conto vero, o ignorare l'avviso su uno finto.
    """

    class _Conto:
        login = 169_444_760
        server = "XMGlobalMU-MT5 2"
        name = "Demo Account"
        company = "XM International MU Limited"
        currency = "EUR"
        leverage = 1_000
        balance = 9_849.66
        equity = 9_856.21
        margin = 0.0
        margin_free = 9_856.21
        margin_level = 0.0
        profit = 6.55
        trade_allowed = True
        trade_expert = True
        trade_mode = 0

    def test_il_nome_del_server_non_dice_se_e_demo(self) -> None:
        assert "demo" not in self._Conto.server.lower()

    def test_il_tipo_viene_dal_campo_trade_mode(self, clock) -> None:  # type: ignore[no-untyped-def]
        conto = to_account_snapshot(self._Conto(), as_of=clock.now())
        assert conto.account_type == "DEMO"
        assert conto.is_demo is True
        assert conto.is_real_money is False

    def test_in_dubbio_si_assume_denaro_vero(self, clock) -> None:  # type: ignore[no-untyped-def]
        class Ignoto(TestTipoDiConto._Conto):
            trade_mode = 77

        conto = to_account_snapshot(Ignoto(), as_of=clock.now())
        assert conto.account_type == "UNKNOWN"
        assert conto.is_real_money is True


class TestValuteDichiarateDalBroker:
    """Scoperta 6: XM dichiara GOLD come USD/USD/USD, non XAU/USD.

    Conseguenza sul modello di esposizione: raggruppare solo per valuta mette
    l'oro e gli indici americani nello stesso secchiello "USD", e due posizioni
    che non hanno nulla in comune sembrano rischio concentrato. Serve una
    dimensione per **classe di attività** accanto a quella per valuta
    (vedi `portfolio/exposure.py` e DECISIONI.md).
    """

    async def test_oro_dichiarato_in_dollari(self, connettore: MT5Connector) -> None:
        gold = await connettore.get_symbol("GOLD")
        assert gold.currency_base == "USD"
        assert gold.currency_profit == "USD"
        # Non è XAU: se un giorno lo diventasse, va rivisto il raggruppamento.
        assert gold.currency_base != "XAU"

    async def test_forex_dichiara_due_valute_distinte(self, connettore: MT5Connector) -> None:
        eurusd = await connettore.get_symbol("EURUSD")
        assert (eurusd.currency_base, eurusd.currency_profit) == ("EUR", "USD")


class TestValoreMonetarioDiUnMovimento:
    """Scoperta 7: `trade_tick_value` non è sempre nella valuta del conto.

    Misurato sul conto XM in EUR confrontando `order_calc_profit` del broker con
    il valore derivato da `tick_value / tick_size`:

    | strumento  | rapporto broker / derivato |
    |------------|----------------------------|
    | EURUSD     | 1.0000  (tick_value in EUR) |
    | USDJPY     | 1.0004  (tick_value in EUR) |
    | GOLD       | 0.8887  (tick_value in USD) |
    | SILVER     | 0.8887  (tick_value in USD) |
    | US100Cash  | 0.8890  (tick_value in USD) |

    0.8887 è esattamente 1/EURUSD. Derivare il valore di un movimento da
    `tick_value` sbaglia del **12,5%** su metalli e indici, senza che nessun
    controllo se ne accorga: i numeri a valle sono coerenti fra loro e sbagliati
    insieme.

    **Verso dell'errore, corretto il 2026-10-04.** Fino a quella data qui si
    diceva che la size sarebbe stata *sovrastimata*. Misurato: su questo conto in
    EUR la perdita per lotto risulta sovrastimata (dollari letti come euro, che
    valgono di più), quindi la size è **sottostimata** dell'11% e il nozionale
    sovrastimato del 12,5%. È un errore prudente *qui*, e diventa un fail-open su
    un conto in una valuta che vale meno del dollaro (FOUNDATION_VALIDATION §5).

    Da qui l'uso di `order_calc_profit` (ADR 0012), e il terminale finto riproduce
    l'incoerenza di proposito.
    """

    async def test_forex_tick_value_in_valuta_del_conto(
        self, connettore: MT5Connector, valutazione: BrokerValuationService, strumento
    ) -> None:
        info = await connettore.get_symbol("EURUSD")
        scarto = await valutazione.tick_value_gap(await strumento("EURUSD"), info, 1.12501)
        assert scarto.gap_pct == pytest.approx(0.0, abs=0.1)

    async def test_oro_tick_value_non_in_valuta_del_conto(
        self, connettore: MT5Connector, valutazione: BrokerValuationService, strumento
    ) -> None:
        """Il caso che rompe il dimensionamento se si usa tick_value."""
        info = await connettore.get_symbol("GOLD")
        scarto = await valutazione.tick_value_gap(await strumento("GOLD"), info, 4_140.07)
        assert scarto.from_broker == pytest.approx(scarto.from_tick_value * 0.8887, rel=0.001)
        assert scarto.gap_pct == pytest.approx(12.5, abs=0.2)

    async def test_indice_stesso_scarto(
        self, connettore: MT5Connector, valutazione: BrokerValuationService, strumento
    ) -> None:
        info = await connettore.get_symbol("US100Cash")
        scarto = await valutazione.tick_value_gap(await strumento("US100Cash"), info, 30_791.56)
        assert scarto.gap_pct == pytest.approx(12.5, abs=0.3)

    def test_verso_dell_errore_sul_conto_reale_in_eur(self) -> None:
        """Con i numeri misurati sul conto: tick_value sottostima la size dell'11%.

        GOLD, stop a 10 dollari, budget 100 EUR. Da `tick_value` (1.0 USD letto
        come EUR) la perdita per lotto è 1000 e la size 0.10; il broker dice
        888.88 EUR e la size giusta è 0.11. Errore prudente su un conto in EUR, ma
        è un caso: su un conto in valuta più debole del dollaro si invertirebbe.
        """
        import json
        from pathlib import Path

        reale = json.loads(
            (Path(__file__).parents[1] / "dati" / "xm_demo_2026-10-04.json").read_text(
                encoding="utf-8"
            )
        )["strumenti"]["GOLD"]
        spec = reale["specifica"]
        perdita_da_tick_value = reale["distanza_stop"] / spec["tick_size"] * spec["tick_value"]
        perdita_dal_broker = reale["profitto"]["servizio_perdita_long"]
        assert perdita_da_tick_value == pytest.approx(1_000.0)
        assert perdita_dal_broker == pytest.approx(888.88, abs=0.05)
        assert perdita_da_tick_value > perdita_dal_broker  # perdita sovrastimata...
        size_da_tick_value = int(100.0 / perdita_da_tick_value / 0.01) * 0.01
        assert size_da_tick_value == pytest.approx(0.10)
        assert reale["size"]["volume"] == pytest.approx(0.11)  # ...quindi size sottostimata

    async def test_il_calcolatore_e_disponibile_e_non_invia_ordini(
        self, connettore: MT5Connector
    ) -> None:
        """`order_calc_profit` calcola e non agisce: deve restare disponibile."""
        assert await connettore.calculate_profit("GOLD", 1.0, 4_140.0, 4_141.0) != 0
        assert not hasattr(connettore, "send_order")


class TestSpreadConfrontabileFraStrumenti:
    """Scoperta 8: un limite di spread in punti non è confrontabile.

    Spread reali su XM: EURUSD 20 punti, GOLD 53, US100Cash 616. Con un limite
    assoluto di 35 punti — valore plausibile pensando al forex — oro e indici
    risultano **intrattabili**, benché i loro spread siano normali.

    Il controllo primario è quindi il rapporto spread/ATR, l'unico confrontabile:
    misurato sul conto, vale 0.09 su EURUSD e 0.03 su GOLD e US100Cash.
    """

    async def test_spread_in_punti_non_confrontabili(self, connettore: MT5Connector) -> None:
        eurusd = await connettore.get_symbol("EURUSD")
        indice = await connettore.get_symbol("US100Cash")
        assert eurusd.spread_points == 20
        assert indice.spread_points == 616
        # In prezzo la differenza si capovolge rispetto all'ordine dei punti
        # relativo al valore dello strumento.
        assert eurusd.spread_price == pytest.approx(0.0002, abs=0.00001)
        assert indice.spread_price == pytest.approx(6.16, abs=0.01)

    def test_il_limite_relativo_e_il_controllo_primario(self) -> None:
        limiti = RiskLimits(_env_file=None, RISK_ALLOWED_SYMBOLS="EURUSD,GOLD")
        assert limiti.max_spread_atr_ratio > 0
        # Il tetto in punti resta largo: un valore stretto escluderebbe in
        # silenzio interi gruppi di strumenti.
        assert limiti.max_spread_points >= 500


class TestSessioniNonEsposteDaMt5:
    """Scoperta 9: l'API Python di MT5 non espone gli orari di sessione.

    `symbol_info_sessions_quote` e `symbol_info_sessions_trade` esistono solo in
    MQL5. Non c'è nemmeno una funzione per l'ora corrente del server. E
    `session_open` / `session_close` di `symbol_info` sono **prezzi**, non orari —
    un nome che invita all'errore.

    Conseguenza architetturale (ADR 0011): "mercato chiuso" è una **deduzione**
    nostra, marcata come tale, non un dato del broker.
    """

    def test_il_pacchetto_non_offre_le_sessioni(self) -> None:
        import importlib.util

        if importlib.util.find_spec("MetaTrader5") is None:
            pytest.skip("Pacchetto MetaTrader5 assente: gira solo su Windows")
        import MetaTrader5

        assert not hasattr(MetaTrader5, "symbol_info_sessions_quote")
        assert not hasattr(MetaTrader5, "symbol_info_sessions_trade")

    def test_il_protocollo_non_promette_le_sessioni(self) -> None:
        """Il nostro protocollo non deve dichiarare ciò che non si può avere."""
        from backend.brokers.mt5.terminal import MT5Terminal

        assert not hasattr(MT5Terminal, "symbol_info_sessions_quote")

    async def test_mercato_chiuso_e_marcato_come_deduzione(
        self, connettore: MT5Connector, terminale: FakeMT5Terminal, clock
    ) -> None:  # type: ignore[no-untyped-def]
        from backend.market_data.sessions import QuoteStateDetector

        terminale.simula_mercato_chiuso(31 * 3_600)
        info = await connettore.get_symbol("EURUSD")
        tick = await connettore.get_tick("EURUSD")
        detector = QuoteStateDetector(max_tick_age_seconds=15.0, max_spread_points=1_000.0)
        esito = detector.assess(
            info,
            tick,
            now=clock.now(),
            last_bar_time=clock.now() - timedelta(hours=31),
            atr=0.002,
        )
        assert esito.state is QuoteState.MARKET_CLOSED
        assert esito.is_inference is True


class TestLinearitaDelCalcolatore:
    """Scoperta 10: `order_calc_profit` non è lineare nel delta di prezzo.

    Il broker converte il profitto nella valuta del conto usando il prezzo di
    **chiusura**. Per una coppia che ha la valuta del conto come base — EURUSD o
    EURJPY su un conto in EUR — quel prezzo *è* il tasso di conversione, quindi
    chiedere il profitto per un movimento grande fa convertire al cambio
    sbagliato.

    Misurato sul conto reale: chiedendo il profitto da 1.12501 a 2.12501 il broker
    ha restituito 47.056 EUR per unità di prezzo invece di 88.810. Un errore del
    47% sull'ingresso principale del dimensionamento.

    Rimedio: cento tick di movimento e divisione. Il terminale finto riproduce la
    non linearità, altrimenti il difetto non sarebbe verificabile.
    """

    async def test_valore_indipendente_dal_delta_usato(self, connettore: MT5Connector) -> None:
        """Dieci, cento o mille tick devono dare lo stesso valore per unità."""
        info = await connettore.get_symbol("EURUSD")
        prezzo = 1.12501
        valori = []
        for tick in (10, 100, 1_000):
            delta = info.tick_size * tick
            profitto = await connettore.calculate_profit("EURUSD", 1.0, prezzo, prezzo + delta)
            valori.append(profitto / delta)
        assert valori[0] == pytest.approx(valori[1], rel=0.01)
        assert valori[1] == pytest.approx(valori[2], rel=0.01)

    async def test_un_delta_di_un_intero_falsa_il_risultato(
        self, connettore: MT5Connector, valutazione: BrokerValuationService, strumento
    ) -> None:
        """Il difetto originale, riprodotto: 1.0 di movimento su EURUSD."""
        prezzo = 1.12501
        sbagliato = await connettore.calculate_profit("EURUSD", 1.0, prezzo, prezzo + 1.0)
        corretto = await valutazione.value_per_price_unit(await strumento("EURUSD"), prezzo)
        # Il valore con delta 1.0 è circa la metà di quello corretto.
        assert sbagliato < corretto * 0.6

    async def test_valore_corretto_per_eurusd(
        self, valutazione: BrokerValuationService, strumento
    ) -> None:
        valore = await valutazione.value_per_price_unit(await strumento("EURUSD"), 1.12501)
        assert valore == pytest.approx(88_870.0, rel=0.01)

    async def test_oro_non_soffre_del_problema(
        self, valutazione: BrokerValuationService, strumento
    ) -> None:
        """Il prezzo dell'oro non è un tasso di conversione: resta lineare."""
        valore = await valutazione.value_per_price_unit(await strumento("GOLD"), 4_140.07)
        assert valore == pytest.approx(88.87, rel=0.01)
