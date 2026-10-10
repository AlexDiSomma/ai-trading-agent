"""Stop mobile (ombra e reale), MFE sul tick, posizioni correlate, salute dell'AI.

Percorsi costruiti a mano sul piano EURUSD di `test_paper`: ingresso 1.1000, stop
1.0980 (1R = 0.0020), target 1.1040 (2R), slittamento zero.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.ai.health import ClientMonitorato, SaluteAI, classifica_errore
from backend.ai.providers.base import RispostaLlm
from backend.core.clock import FrozenClock
from backend.core.errors import AIOutputInvalidError, AIProviderError
from backend.paper.ledger import PaperLedger
from backend.paper.models import PaperPosition
from backend.paper.performance import correlati, performance, stop_mobile_ombra
from backend.paper.trailing import REGOLE, nuovo_stop_r
from backend.services.controls import Controlli
from backend.tests.unit.test_paper import T0, _apri, _percorso, esecutore, m1, piano

__all__ = ["esecutore"]  # fixture importata da test_paper

#: Sale a +1,2R su barra chiusa, poi crolla sotto lo stop iniziale.
SU_E_GIU = [(1.1000, 1.1024, 1.0999, 1.1020), (1.1020, 1.1021, 1.0975, 1.0978)]


class TestRegole:
    def test_lo_stop_non_arretra(self) -> None:
        r = REGOLE["trailing_1r"]
        assert nuovo_stop_r(r, 0.8, None) is None
        assert nuovo_stop_r(r, 1.5, None) == pytest.approx(0.5)
        assert nuovo_stop_r(r, 1.2, 0.5) == pytest.approx(0.5)  # picco più basso: resta

    def test_regole_fisse(self) -> None:
        assert REGOLE["pareggio_1r"].stop_r(1.3) == 0.0
        assert REGOLE["blocca_05r"].stop_r(1.0) == 0.5
        assert REGOLE["blocca_05r"].stop_r(0.99) is None


class TestOmbra:
    async def test_stop_iniziale_con_ombre_che_salvano_il_guadagno(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano())
        _percorso(ex, m1(T0, SU_E_GIU))
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert (t.exit_reason, t.price_r, t.trailing_rule) == ("SL", -1.0, None)
        assert t.shadow_r == pytest.approx(
            {"pareggio_1r": 0.0, "blocca_05r": 0.5, "trailing_1r": 0.2}, abs=1e-3
        )

    async def test_trade_al_target_ombre_uguali(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano())
        _percorso(ex, m1(T0, [SU_E_GIU[0], (1.1020, 1.1045, 1.1015, 1.1040)]))
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert t.exit_reason == "TP"
        assert list(t.shadow_r.values()) == pytest.approx([2.0, 2.0, 2.0], abs=1e-3)

    async def test_stop_preso_sul_tick_oltre_lo_stop_ombra(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano())
        _percorso(ex, m1(T0, SU_E_GIU[:1]), bid=1.0970)
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert t.exit_reason == "SL" and t.price_r == pytest.approx(-1.5, abs=1e-3)
        # Il prezzo è passato dagli stop ombra prima di arrivare a 1.0970.
        assert t.shadow_r["pareggio_1r"] == pytest.approx(0.0, abs=1e-3)
        assert t.shadow_r["blocca_05r"] == pytest.approx(0.5, abs=1e-3)

    def test_confronto_sugli_stessi_trade(self) -> None:
        class T:  # basta la forma usata dal confronto
            def __init__(self, vero: float, ombra: dict[str, float]) -> None:
                self.price_r, self.shadow_r, self.trailing_rule = vero, ombra, None

        trades = [
            T(-1.0, {"pareggio_1r": 0.0, "blocca_05r": 0.5, "trailing_1r": 0.2}),
            T(2.0, {"pareggio_1r": 0.0, "blocca_05r": 0.5, "trailing_1r": 1.1}),
        ]
        esito = stop_mobile_ombra(trades)  # type: ignore[arg-type]
        assert esito["trades"] == 2 and esito["actual_r"] == 1.0
        pareggio = esito["rules"]["pareggio_1r"]
        assert (pareggio["difference_r"], pareggio["better"], pareggio["worse"]) == (-1.0, 1, 1)


class TestStopMobileReale:
    async def test_regola_accesa_esce_in_guadagno(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        pos = _apri(ledger, piano())
        pos.trailing_rule = "blocca_05r"
        _percorso(ex, m1(T0, SU_E_GIU))
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert (t.exit_reason, t.trailing_rule) == ("TRAIL", "blocca_05r")
        assert t.exit_price == pytest.approx(1.1010) and t.net_pnl > 0
        assert t.price_r == pytest.approx(0.5, abs=1e-3)

    async def test_la_regola_si_fissa_all_apertura(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, _, _ = esecutore
        scelta = {"QUANT": "trailing_1r"}
        ex._stop_mobile = lambda libro: scelta.get(libro)
        assert ex._regola_mobile("QUANT") == "trailing_1r"
        assert ex._regola_mobile("AI") is None
        scelta["QUANT"] = "inesistente"
        assert ex._regola_mobile("QUANT") is None

    def test_comando_persistente_e_regola_sconosciuta(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        f = tmp_path / "controls.json"
        c = Controlli(f, ("EURUSD",))
        assert c.regola_mobile("QUANT") is None  # spento di default
        c.stop_mobile("QUANT", "blocca_05r", T0)
        assert Controlli(f, ("EURUSD",)).regola_mobile("QUANT") == "blocca_05r"
        with pytest.raises(ValueError):
            c.stop_mobile("AI", "inventata", T0)
        c.stop_mobile("QUANT", None, T0)
        assert Controlli(f, ("EURUSD",)).regola_mobile("QUANT") is None
        assert "spento" in c.stato["log"][-1]["action"]


class TestMfeSulTick:
    async def test_target_preso_sul_tick_registra_il_massimo(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        _apri(ledger, piano())
        _percorso(ex, [], bid=1.1045)
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert t.exit_reason == "TP"
        # Prima della correzione: MFE 0 con un trade chiuso a +2R.
        assert t.mfe_r >= 2.0

    async def test_posizione_aperta_aggiorna_mfe_dal_tick(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        p = _apri(ledger, piano())
        _percorso(ex, [], bid=1.1016)
        await ex.gestisci()
        assert p.mfe_price == pytest.approx(0.0016)
        assert p.picco_r == 0.0  # il picco dello stop mobile solo da barre chiuse


class TestCorrelazione:
    def test_stessa_scommessa_aperta_dopo(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        ledger = PaperLedger(tmp_path)
        ledger.inizializza(10_000.0, "EUR", T0)
        b = ledger.books["QUANT"]

        def pos(pid: str, simbolo: str, verso: str, minuti: int) -> PaperPosition:
            return PaperPosition(
                position_id=pid,
                plan=piano(canonical=simbolo, broker_symbol=simbolo, direction=verso)
                if verso == "LONG"
                else piano(
                    canonical=simbolo,
                    broker_symbol=simbolo,
                    direction=verso,
                    stop=1.1020,
                    target=1.0960,
                ),
                status="OPEN",
                opened_at=T0 + timedelta(minutes=minuti),
                entry_fill=1.1,
            )

        b.positions = [
            pos("a", "US100", "LONG", 0),
            pos("b", "US500", "LONG", 5),  # stessa scommessa, dopo: correlato
            pos("c", "US30", "SHORT", 6),  # verso opposto: copertura, non correlato
            pos("d", "EURUSD", "LONG", 7),  # altro gruppo
            pos("e", "GBPUSD", "LONG", 8),  # dollaro debole come EURUSD: correlato
        ]
        assert correlati(b) == {"b", "e"}
        assert performance(b)["correlation"]["groups"]


class TestSaluteAI:
    def test_classificazione_dei_guasti(self) -> None:
        credito = (
            "OpenAI: RateLimitError: Error code: 429 - {'error': {'type': "
            "'insufficient_quota', 'code': 'credit_balance_exhausted'}}"
        )
        assert classifica_errore(credito)[0] == "CREDITO"  # prima del 429 generico
        assert classifica_errore("OpenAI: RateLimitError: Error code: 429")[0] == "LIMITE"
        assert classifica_errore("OpenAI: AuthenticationError: 401")[0] == "CHIAVE"
        assert classifica_errore("OpenAI: APITimeoutError: timeout")[0] == "RETE"
        assert classifica_errore("boh")[0] == "ERRORE"

    async def test_monitor_passaggi_e_consumo(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        orologio = FrozenClock(T0)
        salute = SaluteAI(orologio, tmp_path / "ai_usage.json")
        passaggi: list[tuple[bool, str | None]] = []
        salute.al_cambio = lambda ok, tipo, _: passaggi.append((ok, tipo))

        class Finto:
            name, model = "finto", "m"
            esito: Exception | None = None

            async def completa_json(self, **_: object) -> RispostaLlm:
                if self.esito:
                    raise self.esito
                return RispostaLlm("{}", "m", "r", 100, 20, 1.0)

        finto = Finto()
        client = ClientMonitorato(finto, salute)
        await client.completa_json()
        finto.esito = AIProviderError("insufficient_quota")
        for _ in range(3):
            with pytest.raises(AIProviderError):
                await client.completa_json()
        stato = salute.stato()
        assert (stato["ok"], stato["kind"], stato["consecutive_failures"]) == (False, "CREDITO", 3)
        assert "insufficient" not in (stato["message"] or "")  # niente testo grezzo
        finto.esito = AIOutputInvalidError("json rotto")
        with pytest.raises(AIOutputInvalidError):
            await client.completa_json()
        assert salute.stato()["consecutive_failures"] == 3  # risposta non valida ≠ guasto
        finto.esito = None
        await client.completa_json()
        # Un evento per passaggio, non per ogni chiamata fallita.
        assert passaggi == [(True, None), (False, "CREDITO"), (True, None)]
        uso = SaluteAI(orologio, tmp_path / "ai_usage.json").stato()["usage_today"]
        assert (uso["calls"], uso["failures"], uso["input"], uso["output"]) == (5, 3, 200, 40)


async def test_posizione_senza_ombre_resta_fuori_dal_confronto(esecutore) -> None:  # type: ignore[no-untyped-def]
    # Aperta prima della misura: il suo picco dall'apertura non è noto.
    ex, ledger, _ = esecutore
    _apri(ledger, piano()).ombre = {}
    _percorso(ex, m1(T0, SU_E_GIU))
    await ex.gestisci()
    t = ledger.books["QUANT"].trades[0]
    assert t.shadow_r == {} and stop_mobile_ombra([t])["trades"] == 0


class TestCorrezioniGestione:
    async def test_stop_nella_barra_d_innesco(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        p = piano(entry_type="LIMIT", entry_price=1.0990, stop=1.0970, target=1.1030)
        ledger.books["QUANT"].positions.append(
            PaperPosition(position_id="o1", plan=p, status="PENDING", checked_until=T0)
        )

        async def gate_ok(*_: object) -> tuple[float, float, list[str]]:
            return 0.5, 100.0, []

        ex._dimensiona = gate_ok  # type: ignore[method-assign]
        # Scende al limite (riempito a 1.0990) e nella stessa barra fino a 1.0965.
        _percorso(ex, m1(T0, [(1.1000, 1.1002, 1.0965, 1.0968)]), bid=1.0975)
        await ex.gestisci()
        t = ledger.books["QUANT"].trades[0]
        assert (t.exit_reason, t.exit_price, t.ambiguous_bar) == ("SL", 1.0970, True)

    async def test_dopo_un_fermo_si_leggono_tutte_le_barre(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, ledger, _ = esecutore
        pos = _apri(ledger, piano())
        pos.checked_until = T0 - timedelta(hours=10)
        richieste: list[int] = []
        _percorso(ex, m1(T0, [(1.1000, 1.1005, 1.0995, 1.1000)]))
        originale = ex._barre

        async def conta(simbolo: str, tf: object, n: int):  # type: ignore[no-untyped-def]
            richieste.append(n)
            return await originale(simbolo, tf, n)

        ex._barre = conta  # type: ignore[method-assign]
        await ex.gestisci()
        assert richieste[0] >= 10 * 60  # non più le sole ultime 4 ore
        # Le barre disponibili partono dopo l'ultimo controllo: il buco è dichiarato.
        assert any("non verificate" in n for n in pos.notes)


async def test_blocchi_del_conto_prima_della_tesi_ai(esecutore) -> None:  # type: ignore[no-untyped-def]
    """Gli stessi motivi del gate, senza un piano: servono a non pagare tesi inutili."""
    ex, ledger, _ = esecutore
    assert not any("posizioni" in m for m in ex.blocchi_conto("QUANT"))
    for i, simbolo in enumerate(("EURUSD", "GBPUSD", "USDJPY")):
        pos = _apri(ledger, piano(canonical=simbolo, broker_symbol=simbolo))
        pos.position_id = f"p{i}"
    assert any("Già 3 posizioni" in m for m in ex.blocchi_conto("QUANT"))
    assert not any("posizioni" in m for m in ex.blocchi_conto("AI"))
    ex._kill = lambda: True
    assert any("Kill switch" in m for m in ex.blocchi_conto("AI"))


async def test_validazione_trade_con_le_nuove_misure(esecutore) -> None:  # type: ignore[no-untyped-def]
    from backend.paper.validation import problemi_trade

    ex, ledger, _ = esecutore
    pos = _apri(ledger, piano())
    pos.trailing_rule = "blocca_05r"
    _percorso(ex, m1(T0, SU_E_GIU))
    await ex.gestisci()
    t = ledger.books["QUANT"].trades[0]
    assert problemi_trade(t) == []
    rotto = t.model_copy(update={"trailing_rule": None, "mfe_r": 0.1})
    assert set(problemi_trade(rotto)) >= {
        "uscita a stop mobile senza regola attiva",
        "uscita oltre il massimo favorevole registrato",
    }


class TestInversione:
    def test_piano_speculare(self) -> None:
        from backend.paper.planner import inverti

        p = inverti(piano())  # LONG 1.1000, stop 1.0980, target 1.1040
        assert (p.direction, p.entry_type, p.entry_price) == ("SHORT", "MARKET", 1.1000)
        assert p.stop == pytest.approx(1.1020) and p.target == pytest.approx(1.0960)
        assert p.rr == piano().rr and p.invalidations == ()
        assert p.strategy_version.endswith("|inverso")
        assert inverti(piano(entry_type="LIMIT")).entry_type == "STOP"

    def test_comando_persistente(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        f = tmp_path / "controls.json"
        c = Controlli(f, ("EURUSD",))
        assert not c.invertito("QUANT")  # spento di default
        c.inversione("QUANT", True, T0)
        assert Controlli(f, ("EURUSD",)).invertito("QUANT")
        assert not Controlli(f, ("EURUSD",)).invertito("AI")
        assert "INVERTITI" in c.stato["log"][-1]["action"]


class TestTargetQuant:
    def test_target_a_13r(self) -> None:
        from backend.paper.planner import con_target, inverti

        p = con_target(piano(), 1.3)  # stop a 0.0020 → target a 0.0026
        assert p.target == pytest.approx(1.1026) and p.rr == pytest.approx(1.3)
        assert p.strategy_version.endswith("|tp1.3")
        q = inverti(p)  # le due cose si combinano
        assert q.direction == "SHORT" and q.target == pytest.approx(1.0974)

    def test_rr_minimo_solo_per_quant(self, esecutore) -> None:  # type: ignore[no-untyped-def]
        ex, _, _ = esecutore
        assert ex._limiti_conto("QUANT").minimum_rr == ex._limiti.minimum_rr
        ex._rr_minimo = lambda libro: 1.3 if libro == "QUANT" else None
        assert ex._limiti_conto("QUANT").minimum_rr == 1.3
        assert ex._limiti_conto("AI").minimum_rr == ex._limiti.minimum_rr
        ex._rr_minimo = lambda libro: 5.0  # mai più severo del limite generale da qui
        assert ex._limiti_conto("QUANT").minimum_rr == ex._limiti.minimum_rr

    def test_comando_con_limiti(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        f = tmp_path / "controls.json"
        c = Controlli(f, ("EURUSD",))
        assert c.target_quant is None
        c.imposta_target_quant(1.3, T0)
        assert Controlli(f, ("EURUSD",)).target_quant == 1.3
        with pytest.raises(ValueError):
            c.imposta_target_quant(0.5, T0)
