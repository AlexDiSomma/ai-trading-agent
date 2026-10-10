"""Shadow mode (fase 5B): esiti senza hindsight, tesi validate, fail closed, isolamento.

Nessuna rete: il modello è un `ModelloFinto`; i prezzi delle prove di valutazione
sono barre costruite qui, quelli dei contesti vengono dal terminale finto.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.ai.providers.base import RispostaLlm
from backend.ai.thesis.agent import ThesisAgent
from backend.ai.thesis.contracts import ThesisInput, TradeThesis
from backend.ai.thesis.validation import problemi_tesi
from backend.core.enums import DataQuality, Timeframe
from backend.core.errors import AIProviderError
from backend.shadow.conditions import Barra, PriceCondition
from backend.shadow.evaluator import valuta
from backend.shadow.evidence import CatalogoEvidenze, costruisci_catalogo
from backend.shadow.groups import piano_filtro, piano_tesi
from backend.shadow.journal import ShadowJournal, ShadowJournalError
from backend.shadow.metrics import report
from backend.shadow.plans import ShadowTradePlan
from backend.shadow.quant_baseline import piano_quant
from backend.shadow.records import PIPELINE_VERSION, DecisionSnapshot, OutcomeSnapshot

T0 = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
M5 = timedelta(minutes=5)


# ======================================================================
# Utilità
# ======================================================================
def barre(inizio: datetime, ohlc: list[tuple[float, float, float, float]], passo=M5) -> list[Barra]:  # type: ignore[no-untyped-def]
    return [
        Barra(inizio + i * passo, inizio + (i + 1) * passo, o, h, lo, c)
        for i, (o, h, lo, c) in enumerate(ohlc)
    ]


def piano(**kw: Any) -> ShadowTradePlan:
    base: dict[str, Any] = {
        "group": "A_QUANT_ONLY",
        "canonical": "XAUUSD",
        "decided_at": T0,
        "mode": "REPLAY",
        "decision": "LONG",
        "status": "ACTIVE",
        "entry_type": "MARKET",
        "entry_price": 100.0,
        "entry_valid_hours": 1,
        "stop_loss": 99.0,
        "take_profit": 102.0,
        "horizon_hours": 4,
        "reference_price": 100.0,
        "atr_h1": 1.0,
        "spread_price": 0.1,
    }
    return ShadowTradePlan(**(base | kw))


def cond(**kw: Any) -> PriceCondition:
    base: dict[str, Any] = {
        "subject": "XAUUSD",
        "timeframe": "M5",
        "measure": "CLOSE",
        "operator": "BELOW",
        "level": 99.5,
        "evidence_ids": ("asset.XAUUSD.H1.level.S1",),
        "description": "Chiusura sotto il supporto.",
    }
    return PriceCondition(**(base | kw))


DOPO = T0 + timedelta(days=1)


# ======================================================================
# OutcomeEvaluator
# ======================================================================
class TestEsiti:
    def test_tp_con_r_netto_mae_mfe(self) -> None:
        p = piano()
        s = {("XAUUSD", "M5"): barre(T0, [(100, 100.5, 99.6, 100.4), (100.4, 102.2, 100.3, 102)])}
        o = valuta(p, s, adesso=DOPO)
        assert (o.status, o.exit_reason, o.tp_hit) == ("CLOSED", "TP", True)
        assert o.entry_fill == 100 and o.r_gross == 2.0
        assert o.cost_r == 0.1 and o.simulated_r == 1.9
        assert o.mae_r == 0.4 and o.mfe_r == 2.0
        assert o.time_to_outcome_hours == pytest.approx(10 / 60, abs=0.01)

    def test_sl_e_short_simmetrico(self) -> None:
        p = piano(decision="SHORT", stop_loss=101.0, take_profit=98.0)
        s = {("XAUUSD", "M5"): barre(T0, [(100, 100.4, 99.5, 100.2), (100.2, 101.3, 100.1, 101.2)])}
        o = valuta(p, s, adesso=DOPO)
        assert (o.exit_reason, o.sl_hit, o.r_gross) == ("SL", True, -1.0)
        assert o.mfe_r == 0.5

    def test_sl_e_tp_nella_stessa_barra_vince_lo_sl(self) -> None:
        s = {("XAUUSD", "M5"): barre(T0, [(100, 102.5, 98.5, 101)])}
        o = valuta(piano(), s, adesso=DOPO)
        assert o.exit_reason == "SL" and o.ambiguous_bar

    def test_nessun_hindsight_le_barre_prima_della_decisione_non_contano(self) -> None:
        prima = barre(T0 - 2 * M5, [(100, 105, 95, 100), (100, 105, 95, 100)])
        dopo = barre(T0, [(100, 100.2, 99.8, 100)] * 3)
        o = valuta(piano(horizon_hours=1), {("XAUUSD", "M5"): prima + dopo}, adesso=DOPO)
        assert o.exit_reason == "TIME" and not o.tp_hit and not o.sl_hit
        assert o.bars_used == 3

    def test_pending_finche_l_orizzonte_non_e_trascorso(self) -> None:
        s = {("XAUUSD", "M5"): barre(T0, [(100, 100.5, 99.8, 100.2)])}
        o = valuta(piano(), s, adesso=T0 + timedelta(minutes=10))
        assert o.status == "PENDING" and o.triggered and not o.horizon_elapsed

    def test_stop_entry_non_innescato_scade(self) -> None:
        p = piano(entry_type="STOP", entry_price=101.0, take_profit=103.0, stop_loss=100.0)
        s = {("XAUUSD", "M5"): barre(T0, [(100, 100.6, 99.8, 100.2)] * 24)}
        o = valuta(p, s, adesso=DOPO)
        assert (o.status, o.not_triggered_reason) == ("NOT_TRIGGERED", "EXPIRED")

    def test_stop_entry_con_gap_entra_all_apertura(self) -> None:
        p = piano(entry_type="STOP", entry_price=101.0, take_profit=104.0, stop_loss=100.0)
        s = {("XAUUSD", "M5"): barre(T0, [(100, 100.5, 99.9, 100.4), (101.5, 101.8, 101.4, 101.6)])}
        o = valuta(p, s, adesso=DOPO)
        assert o.triggered and o.entry_fill == 101.5

    def test_invalidazione_prima_dell_ingresso_annulla(self) -> None:
        p = piano(
            entry_type="STOP",
            entry_price=101.0,
            take_profit=103.0,
            stop_loss=100.0,
            invalidation_conditions=(cond(level=99.5),),
        )
        s = {("XAUUSD", "M5"): barre(T0, [(100, 100.2, 99.3, 99.4), (99.4, 101.5, 99.4, 101.2)])}
        o = valuta(p, s, adesso=DOPO)
        assert (o.status, o.not_triggered_reason) == ("NOT_TRIGGERED", "INVALIDATED_BEFORE_ENTRY")

    def test_invalidazione_dopo_l_ingresso_chiude_alla_chiusura(self) -> None:
        p = piano(stop_loss=98.0, take_profit=104.0, invalidation_conditions=(cond(level=99.5),))
        s = {("XAUUSD", "M5"): barre(T0, [(100, 100.3, 99.7, 100.1), (100.1, 100.2, 99.2, 99.3)])}
        o = valuta(p, s, adesso=DOPO)
        assert (o.exit_reason, o.exit_price, o.invalidation_hit) == ("INVALIDATION", 99.3, True)
        assert o.r_gross == pytest.approx(-0.35)

    def test_condizione_d_ingresso_su_un_altro_strumento(self) -> None:
        p = piano(
            entry_valid_hours=2,
            entry_conditions=(cond(subject="US500", timeframe="H1", operator="ABOVE", level=6000),),
        )
        s = {
            ("XAUUSD", "M5"): barre(T0, [(100, 100.2, 99.9, 100)] * 12 + [(100, 102.1, 99.9, 102)]),
            ("US500", "H1"): barre(T0, [(5990, 6010, 5980, 6005)], passo=timedelta(hours=1)),
        }
        o = valuta(p, s, adesso=DOPO)
        assert o.triggered and o.triggered_at == T0 + timedelta(hours=1)

    def test_condizione_senza_barre_dichiarata_non_verificabile(self) -> None:
        p = piano(invalidation_conditions=(cond(subject="GER40", timeframe="H1"),))
        o = valuta(p, {("XAUUSD", "M5"): barre(T0, [(100, 100.1, 99.9, 100)])}, adesso=DOPO)
        assert not o.conditions[0].verifiable
        assert any("non verificabile" in n for n in o.notes)

    def test_wait_misura_comunque_il_mercato(self) -> None:
        p = piano(decision="WAIT", status="WAIT", entry_type=None, stop_loss=None, take_profit=None)
        s = {("XAUUSD", "M5"): barre(T0, [(100, 101, 99.5, 100.8), (100.8, 102, 100.5, 101.5)])}
        o = valuta(p, s, adesso=DOPO)
        assert o.status == "NOT_APPLICABLE"
        assert o.market_move and o.market_move.max_up_atr == 2.0
        assert o.market_move.close_change_atr == 1.5


# ======================================================================
# Validazione della tesi
# ======================================================================
S1, R1, TREND, MTF = (
    "asset.XAUUSD.H1.level.S1",
    "asset.XAUUSD.H1.level.R1",
    "asset.XAUUSD.H1.trend",
    "asset.XAUUSD.mtf",
)
CATALOGO = CatalogoEvidenze(
    canonical="XAUUSD",
    voci={S1: {}, R1: {}, TREND: {}, MTF: {}},
    prezzo=2000.0,
    atr_h1=10.0,
    spread_prezzo=0.3,
    cifre=3,
    qualita="GOOD",
)
INGRESSO = ThesisInput(
    kind="TRADE_THESIS",
    canonical="XAUUSD",
    context_as_of=T0,
    mode="REPLAY",
    output_schema_version="t",
    prompt_version="t",
    global_input_sha256="0",
    payload={},
    allowed_evidence_ids=(*CATALOGO.ids, "risk_sentiment"),
    universe=("US500", "XAUUSD"),
    known_limitations=(),
)
CHIUSURE = {("XAUUSD", "H1"): 2000.0, ("XAUUSD", "M5"): 2000.0, ("US500", "H1"): 6000.0}


def tesi(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "canonical": "XAUUSD",
        "decision": "LONG",
        "scenario": {
            "narrative": "Tendenza H1 rialzista con supporto vicino.",
            "evidence_ids": [TREND],
        },
        "setup": {
            "type": "PULLBACK",
            "description": "Ritracciamento sul supporto H1.",
            "evidence_ids": [S1],
        },
        "entry": {"mode": "NOW", "price": None, "valid_for_hours": 2, "evidence_ids": [MTF]},
        "entry_conditions": [],
        "stop_loss": {"price": 1985.0, "evidence_ids": [S1]},
        "take_profit": {"price": 2030.0, "evidence_ids": [R1]},
        "invalidation_conditions": [
            {
                "subject": "XAUUSD",
                "timeframe": "H1",
                "measure": "CLOSE",
                "operator": "BELOW",
                "level": 1988.0,
                "evidence_ids": [S1],
                "description": "Chiusura H1 sotto il supporto.",
            }
        ],
        "target_conditions": [],
        "supporting_evidence": [
            {"statement": "Trend H1 rialzista, fattori concordi.", "evidence_ids": [TREND]},
            {"statement": "Allineamento multi-timeframe positivo.", "evidence_ids": [MTF]},
        ],
        "contradictory_evidence": [
            {
                "statement": "Sentiment di rischio misto sul globale.",
                "evidence_ids": ["risk_sentiment"],
            }
        ],
        "uncertainties": [
            {
                "description": "Calendario solo settimanale.",
                "missing_information": "calendario completo",
                "impact": "LOW",
            }
        ],
        "expected_horizon_hours": 24,
        "confidence": "MEDIUM",
        "wait_reason": None,
    }
    return base | kw


def attesa(**kw: Any) -> dict[str, Any]:
    return tesi(
        decision="WAIT",
        entry=None,
        stop_loss=None,
        take_profit=None,
        invalidation_conditions=[],
        wait_reason="Evidenze miste e nessun livello vicino.",
        **kw,
    )


def esamina(dati: dict[str, Any], qualita: str = "GOOD") -> tuple[list[str], list[str]]:
    return problemi_tesi(
        TradeThesis.model_validate(dati),
        INGRESSO,
        CATALOGO,
        ultime_chiusure=CHIUSURE,
        qualita_globale=qualita,
    )


def _inval(**kw: Any) -> list[dict[str, Any]]:
    c = tesi()["invalidation_conditions"][0]
    return [c | kw]


class TestValidazioneTesi:
    def test_tesi_valida(self) -> None:
        assert esamina(tesi()) == ([], [])

    def test_wait_valido(self) -> None:
        assert esamina(attesa())[0] == []

    @pytest.mark.parametrize(
        ("modifica", "motivo"),
        [
            (
                {
                    "scenario": {
                        "narrative": "Scenario senza appigli nei dati.",
                        "evidence_ids": ["asset.XAUUSD.H1.vwap"],
                    }
                },
                "evidenze inesistenti",
            ),
            (
                {
                    "setup": {
                        "type": "BREAKOUT",
                        "description": "Rottura senza citazioni.",
                        "evidence_ids": [],
                    }
                },
                "nessuna evidenza",
            ),
            ({"stop_loss": {"price": 2005.0, "evidence_ids": [S1]}}, "parte sbagliata"),
            (
                {
                    "entry": {
                        "mode": "AT_LEVEL",
                        "price": None,
                        "valid_for_hours": 2,
                        "evidence_ids": [MTF],
                    }
                },
                "AT_LEVEL senza prezzo",
            ),
            (
                {
                    "entry": {
                        "mode": "AT_LEVEL",
                        "price": 2050.0,
                        "valid_for_hours": 2,
                        "evidence_ids": [MTF],
                    }
                },
                "ATR H1 dal prezzo",
            ),
            ({"stop_loss": {"price": 1999.0, "evidence_ids": [S1]}}, "sotto il minimo"),
            ({"invalidation_conditions": _inval(level=2010.0)}, "già vera"),
            ({"invalidation_conditions": _inval(subject="GER40")}, "non verificabile"),
            ({"invalidation_conditions": []}, "senza condizioni di invalidazione"),
            (
                {
                    "supporting_evidence": [
                        {
                            "statement": "Trend H1 rialzista, fattori concordi.",
                            "evidence_ids": [TREND],
                        }
                    ]
                },
                "evidenze a favore",
            ),
            ({"confidence": "HIGH"}, "HIGH"),
            (
                {
                    "scenario": {
                        "narrative": "Comprare 2 lotti con leva massima.",
                        "evidence_ids": [TREND],
                    }
                },
                "size, leva",
            ),
        ],
    )
    def test_violazioni(self, modifica: dict[str, Any], motivo: str) -> None:
        qualita = "DEGRADED" if modifica.get("confidence") == "HIGH" else "GOOD"
        errori, _ = esamina(tesi(**modifica), qualita)
        assert any(motivo in e for e in errori), errori

    def test_evidenza_contraria_inventata_marcata_non_rifiutata(self) -> None:
        errori, non_sostenute = esamina(
            tesi(
                contradictory_evidence=[
                    {"statement": "Flussi istituzionali in uscita.", "evidence_ids": ["cot.gold"]}
                ]
            )
        )
        assert errori == [] and "cot.gold" in non_sostenute[0]

    def test_wait_con_livelli_incoerente(self) -> None:
        errori, _ = esamina(attesa() | {"stop_loss": {"price": 1985.0, "evidence_ids": [S1]}})
        assert any("WAIT con" in e for e in errori)

    def test_lo_schema_non_ha_campi_di_volume(self) -> None:
        testo = json.dumps(TradeThesis.model_json_schema()).lower()
        for vietato in ("volume", "lots", "size", "leverage", "margin"):
            assert f'"{vietato}' not in testo


# ======================================================================
# Agente, gruppi, fail closed
# ======================================================================
class ModelloFinto:
    name = "finto"
    model = "finto-1"

    def __init__(self, *testi: str | None, errore: str | None = None) -> None:
        self.testi = list(testi)
        self.errore = errore
        self.chiamate = 0

    async def completa_json(self, **kw: Any) -> RispostaLlm:
        self.chiamate += 1
        if self.errore:
            raise AIProviderError(self.errore)
        return RispostaLlm(
            testo=self.testi.pop(0) or "",
            model_snapshot="finto-1-2026",
            request_id="r",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


async def _tesi_rec(testo: str | None, errore: str | None = None):  # type: ignore[no-untyped-def]
    agente = ThesisAgent(ModelloFinto(testo, errore=errore))
    return await agente.tesi(INGRESSO, CATALOGO, ultime_chiusure=CHIUSURE, qualita_globale="GOOD")


def _c(rec):  # type: ignore[no-untyped-def]
    return piano_tesi(rec, reference_price=2000.0, atr_h1=10.0, spread_price=0.3)


class TestGruppi:
    async def test_tesi_valida_diventa_piano_attivo(self) -> None:
        rec = await _tesi_rec(json.dumps(tesi()))
        p = _c(rec)
        assert rec.status == "VALID" and p.status == "ACTIVE" and p.computed_rr == 2.0
        assert p.entry_type == "MARKET"
        assert p.ai_confidence_label == "MEDIUM" and p.thesis_record_id == rec.record_id

    async def test_tesi_rifiutata_nessuna_operazione(self) -> None:
        rec = await _tesi_rec(json.dumps(tesi(stop_loss={"price": 2005.0, "evidence_ids": [S1]})))
        assert rec.status == "REJECTED" and rec.thesis is not None
        assert _c(rec).status == "REJECTED"

    @pytest.mark.parametrize("testo", ["", "{non json", json.dumps({"decision": "LONG"})])
    async def test_output_invalido_fail_closed(self, testo: str) -> None:
        rec = await _tesi_rec(testo)
        assert rec.status == "UNAVAILABLE" and _c(rec).status == "REJECTED"

    async def test_provider_assente_fail_closed(self) -> None:
        rec = await _tesi_rec(None, errore="timeout")
        assert rec.status == "UNAVAILABLE" and rec.call.error == "timeout"

    async def test_wait_valido(self) -> None:
        p = _c(await _tesi_rec(json.dumps(attesa())))
        assert (p.status, p.decision) == ("WAIT", "WAIT")

    @staticmethod
    def _verdetto(**kw: Any) -> str:
        contro = {
            "statement": "Sentiment globale contrario al piano.",
            "evidence_ids": ["risk_sentiment"],
        }
        favore = {"statement": "Trend H1 nella direzione del piano.", "evidence_ids": [TREND]}
        base = {
            "canonical": "XAUUSD",
            "plan_direction_read": "LONG",
            "points_for": [favore],
            "points_against": [contro],
            "key_risks": [],
            "verdict": "REJECT",
            "confidence": "LOW",
        }
        return json.dumps(base | kw)

    async def _filtra(self, testo: str) -> ShadowTradePlan:
        quant = piano(canonical="XAUUSD")
        ing = INGRESSO.model_copy(
            update={"kind": "AI_FILTER", "payload": {"quant_plan": {"decision": "LONG"}}}
        )
        return piano_filtro(quant, await ThesisAgent(ModelloFinto(testo)).filtro(ing))

    async def test_filtro_respinge_e_approva(self) -> None:
        respinto = await self._filtra(self._verdetto())
        approvato = await self._filtra(self._verdetto(verdict="APPROVE"))
        assert (respinto.status, respinto.decision) == ("FILTERED", "WAIT")
        assert (approvato.status, approvato.stop_loss) == ("ACTIVE", 99.0)
        assert respinto.plan_id != approvato.plan_id

    @pytest.mark.parametrize(
        "modifica",
        [
            # trovato dal vivo: piano SHORT letto come "long"
            {"plan_direction_read": "SHORT"},
            # trovato dal vivo: REJECT con motivi tutti a favore del piano
            {"points_against": []},
            {
                "verdict": "APPROVE",
                "points_for": [
                    {"statement": "Motivo con id inventato qui.", "evidence_ids": ["nessuno"]}
                ],
            },
        ],
    )
    async def test_filtro_incoerente_fail_closed(self, modifica: dict[str, Any]) -> None:
        assert (await self._filtra(self._verdetto(**modifica))).status == "REJECTED"

    def test_nella_tesi_la_decisione_segue_le_evidenze(self) -> None:
        campi = list(TradeThesis.model_fields)
        assert campi.index("decision") > campi.index("contradictory_evidence")
        from backend.ai.thesis.contracts import FilterVerdict

        campi = list(FilterVerdict.model_fields)
        assert campi.index("verdict") > campi.index("points_against")

    def test_quant_wait_resta_wait_nel_gruppo_b(self) -> None:
        q = piano(decision="WAIT", status="WAIT", entry_type=None, stop_loss=None, take_profit=None)
        b = piano_filtro(q, None)
        assert (b.group, b.status) == ("B_QUANT_AI_FILTER", "WAIT")


# ======================================================================
# Baseline quant su un contesto vero del terminale finto
# ======================================================================
@pytest.fixture
async def contesto_gold(tmp_path, clock):  # type: ignore[no-untyped-def]
    from backend.services.container import ApplicationContainer
    from backend.tests.conftest import crea_settings

    c = ApplicationContainer(
        crea_settings(kill_switch_file=tmp_path / "KS", allowed_symbols="EURUSD,GOLD,US100Cash"),
        clock=clock,
    )
    await c.startup()
    ctx = await c.require_analyst().analyse("GOLD")
    yield c, ctx
    await c.shutdown()


def _forza(ctx, bias: int, allineamento: float):  # type: ignore[no-untyped-def]
    tfs = {
        tf: a.model_copy(update={"quality": DataQuality.GOOD}) for tf, a in ctx.timeframes.items()
    }
    mtf = ctx.multi_timeframe.model_copy(
        update={
            "structural_bias": bias,
            "operational_bias": bias,
            "alignment": allineamento,
            "structure_execution_conflict": False,
        }
    )
    return ctx.model_copy(update={"timeframes": tfs, "multi_timeframe": mtf})


class TestBaseline:
    async def test_dati_simulati_wait(self, contesto_gold) -> None:  # type: ignore[no-untyped-def]
        _, ctx = contesto_gold
        p = piano_quant(ctx, costruisci_catalogo(ctx, "XAUUSD"), decided_at=T0, mode="LIVE")
        assert p.status == "WAIT" and "non GOOD" in p.reasons[0]

    @pytest.mark.parametrize("bias", [1, -1])
    async def test_tendenza_concorde_piano_attivo(self, contesto_gold, bias: int) -> None:  # type: ignore[no-untyped-def]
        _, ctx = contesto_gold
        ctx = _forza(ctx, bias, 0.6 * bias)
        cat = costruisci_catalogo(ctx, "XAUUSD")
        p = piano_quant(ctx, cat, decided_at=T0, mode="LIVE")
        assert p.status == "ACTIVE" and p.verso == bias and p.computed_rr == 2.0
        assert p.stop_loss == pytest.approx(cat.prezzo - bias * 1.5 * cat.atr_h1, rel=1e-6)  # type: ignore[operator]

    async def test_allineamento_debole_wait(self, contesto_gold) -> None:  # type: ignore[no-untyped-def]
        _, ctx = contesto_gold
        p = piano_quant(
            _forza(ctx, 1, 0.2), costruisci_catalogo(ctx, "XAUUSD"), decided_at=T0, mode="LIVE"
        )
        assert p.status == "WAIT"

    async def test_catalogo_ha_id_stabili(self, contesto_gold) -> None:  # type: ignore[no-untyped-def]
        _, ctx = contesto_gold
        cat = costruisci_catalogo(ctx, "XAUUSD")
        for i in (
            "asset.XAUUSD.mtf",
            "asset.XAUUSD.H1.trend",
            "asset.XAUUSD.D1.ema",
            "asset.XAUUSD.H4.structure",
            "asset.XAUUSD.M15.momentum",
        ):
            assert i in cat.ids
        assert not any(".M5." in i for i in cat.ids)

    async def test_replay_non_vede_barre_successive(self, contesto_gold) -> None:  # type: ignore[no-untyped-def]
        from backend.services.shadow_runner import _taglia_candele

        c, _ = contesto_gold
        assert c.ingestion is not None
        complete = {
            tf: await c.ingestion.fetch_one("GOLD", tf, 400) for tf in (Timeframe.H1, Timeframe.M5)
        }
        ultima = complete[Timeframe.H1].series.closed[-1].time
        limite = ultima - timedelta(hours=10)
        tagliate = {tf: _taglia_candele(tc, limite) for tf, tc in complete.items()}
        for tf, tc in tagliate.items():
            durata = timedelta(minutes=tf.minutes)
            assert tc.series.closed and all(x.time + durata <= limite for x in tc.series.closed)
        ctx = await c.require_analyst().analyse_replay("GOLD", tagliate, as_of=limite)
        assert not ctx.usable_for_decisions and ctx.quote.is_inference
        assert ctx.timeframes[Timeframe.H1].last_closed_at + timedelta(hours=1) <= limite


# ======================================================================
# Diario, metriche, isolamento
# ======================================================================
def _decisione(gruppi: dict[str, ShadowTradePlan], t: datetime = T0) -> DecisionSnapshot:
    return DecisionSnapshot(
        pipeline_version=PIPELINE_VERSION,
        canonical="XAUUSD",
        broker_symbol="GOLD",
        decided_at=t,
        mode="REPLAY",
        selection_reason="test",
        asset_context={},
        evidence_catalog={},
        reference={},
        global_regime="MIXED",
        global_data_quality="GOOD",
        plans=tuple(gruppi[g] for g in ("A_QUANT_ONLY", "B_QUANT_AI_FILTER", "C_AI_THESIS")),
    )


def test_diario_immutabile_e_verificato(tmp_path) -> None:  # type: ignore[no-untyped-def]
    a = piano()
    d = _decisione(
        {
            "A_QUANT_ONLY": a,
            "B_QUANT_AI_FILTER": a.model_copy(update={"group": "B_QUANT_AI_FILTER"}),
            "C_AI_THESIS": a.model_copy(update={"group": "C_AI_THESIS"}),
        }
    )
    j = ShadowJournal(tmp_path)
    percorso = j.scrivi_decisione(d)
    with pytest.raises(ShadowJournalError):
        j.scrivi_decisione(d)
    assert j.decisioni()[0].integrity_sha256 == d.integrity_sha256
    percorso.write_text(
        percorso.read_text(encoding="utf-8").replace("99.0", "98.0"), encoding="utf-8"
    )
    with pytest.raises(ShadowJournalError):
        j.decisioni()


def test_metriche_separano_i_gruppi_e_dichiarano_il_campione() -> None:
    decisioni, esiti = [], {}
    sorgente_tp = {("XAUUSD", "M5"): barre(T0, [(100, 102.5, 99.8, 102)])}
    sorgente_sl = {("XAUUSD", "M5"): barre(T0, [(100, 100.2, 98.5, 98.8)])}
    for i, sorgente in enumerate((sorgente_tp, sorgente_sl, sorgente_tp)):
        t = T0 + timedelta(days=i)
        s = {
            k: barre(t, [(b.open, b.high, b.low, b.close) for b in v]) for k, v in sorgente.items()
        }
        a = piano(decided_at=t)
        b = a.model_copy(
            update={"group": "B_QUANT_AI_FILTER", "status": "FILTERED" if i == 1 else "ACTIVE"}
        )
        c = a.model_copy(
            update={
                "group": "C_AI_THESIS",
                "status": "WAIT",
                "decision": "WAIT",
                "ai_confidence_label": "LOW",
            }
        )
        d = _decisione({"A_QUANT_ONLY": a, "B_QUANT_AI_FILTER": b, "C_AI_THESIS": c}, t)
        decisioni.append(d)
        esiti[d.decision_id] = OutcomeSnapshot(
            decision_id=d.decision_id,
            decision_integrity_sha256=d.integrity_sha256,
            canonical="XAUUSD",
            decided_at=t,
            evaluated_at=t + timedelta(days=1),
            bars_m5=(),
            bars_sha256="",
            outcomes=tuple(valuta(p, s, adesso=t + timedelta(days=1)) for p in d.plans),
        )
    r = report(decisioni, esiti)
    a, b, c = r.groups
    assert (a.closed, a.tp, a.sl) == (3, 2, 1)
    assert (b.closed, b.filtered) == (2, 1)
    assert c.closed == 0 and c.wait == 3
    assert r.filter_rejected_r.mean == pytest.approx(-1.1)
    assert r.filter_verdict == r.thesis_verdict == "INSUFFICIENT_SAMPLE"
    assert r.thesis_vs_quant.n == 3


def test_shadow_e_thesis_non_raggiungono_broker_ne_esecuzione() -> None:
    from backend.config.paths import BACKEND_ROOT

    vietati = (
        "backend.brokers",
        "backend.risk",
        "backend.execution",
        "backend.trade_planner",
        "backend.valuation",
        "backend.portfolio",
        "backend.services",
        "backend.schemas.intent",
    )
    file = sorted((BACKEND_ROOT / "shadow").rglob("*.py")) + sorted(
        (BACKEND_ROOT / "ai" / "thesis").rglob("*.py")
    )
    assert len(file) >= 14
    for percorso in file:
        for nodo in ast.walk(ast.parse(percorso.read_text(encoding="utf-8"))):
            moduli = []
            if isinstance(nodo, ast.ImportFrom) and nodo.module:
                moduli = [nodo.module]
            elif isinstance(nodo, ast.Import):
                moduli = [a.name for a in nodo.names]
            for m in moduli:
                assert not m.startswith(vietati), f"{percorso.name} importa {m}"
            if isinstance(nodo, ast.Attribute):
                assert nodo.attr not in ("order_send", "order_check", "send_order")


def test_il_piano_ombra_non_ha_volume_ne_conto() -> None:
    campi = set(ShadowTradePlan.model_fields)
    assert not campi & {
        "volume",
        "lots",
        "size",
        "margin",
        "account",
        "risk_budget",
        "expected_loss",
    }


def test_versioni_precedenti_contate_non_rilette(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cartella = tmp_path / "decisions"
    cartella.mkdir()
    # Un record del primo giro: filtro con `reasons`, schema che oggi non esiste più.
    (cartella / "vecchio.json").write_text(
        json.dumps({"canonical": "X", "filter_record": {"verdict": {"reasons": []}}}),
        encoding="utf-8",
    )
    j = ShadowJournal(tmp_path)
    assert j.decisioni() == []
    assert sum(j.altre_versioni().values()) == 1


@pytest.mark.parametrize(
    ("verso", "livello", "atteso"),
    [
        (1, None, "MARKET"),
        (1, 2000.5, "MARKET"),
        (1, 2010.0, "STOP"),
        (1, 1990.0, "LIMIT"),
        (-1, 1990.0, "STOP"),
        (-1, 2010.0, "LIMIT"),
    ],
)
def test_tipo_d_ingresso_deciso_dal_codice(verso: int, livello: float | None, atteso: str) -> None:
    from backend.paper.geometry import tipo_ingresso

    assert tipo_ingresso(verso, livello, 2000.0, 10.0) == atteso


async def test_tesi_al_livello_diventa_stop_o_limit_dal_codice() -> None:
    entry = {"mode": "AT_LEVEL", "price": 2012.0, "valid_for_hours": 2, "evidence_ids": [MTF]}
    p = _c(
        await _tesi_rec(
            json.dumps(tesi(entry=entry, stop_loss={"price": 1997.0, "evidence_ids": [S1]}))
        )
    )
    assert (p.entry_type, p.entry_price, p.computed_rr) == ("STOP", 2012.0, 1.2)
