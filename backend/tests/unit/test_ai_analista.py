"""AI Market Analyst (fase 5A, READ ONLY): validazione, immutabilità, versioni, isolamento.

Nessuna rete: il modello è un `ModelloFinto` definito qui, che restituisce testo
preparato. I casi reali sono in `report_ai.py`, eseguito sul conto XM.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.ai.agents.market_analyst import MarketAnalyst
from backend.ai.contracts import AnalysisRecord, MarketAnalysis
from backend.ai.input_builder import costruisci_ingresso
from backend.ai.journal import AnalysisJournal, JournalError
from backend.ai.providers.base import NullLlmClient, RispostaLlm, schema_rigoroso
from backend.core.clock import FrozenClock
from backend.core.errors import AIProviderError
from backend.global_market.engine import costruisci
from backend.tests.scenari_globali import AS_OF, FINE, scenario

RISK_OFF = {
    "valute": {"JPY": 0.0006, "CHF": 0.0004, "USD": 0.0002, "AUD": -0.0006, "NZD": -0.0005},
    "gruppi": {"azioni": -0.0008, "oro": 0.0005},
    "profilo": ((120, 0, 1.3),),
}
MIXED = {
    "valute": {"USD": 0.0006, "JPY": -0.0004, "AUD": 0.0003, "NZD": 0.0003},
    "gruppi": {"azioni": -0.0008, "oro": 0.0006},
    "profilo": ((220, 180, 1.6), (10, 0, 1.7)),
}


def contesto(**kw):  # type: ignore[no-untyped-def]
    return costruisci(scenario(**kw), as_of=AS_OF, limite_broker=FINE)[1]


class ModelloFinto:
    """Solo test: restituisce un testo preparato, o solleva."""

    name = "finto"
    model = "finto-1"

    def __init__(self, testo: str | None = None, errore: str | None = None) -> None:
        self.testo, self.errore = testo, errore
        self.chiamate: list[dict[str, Any]] = []

    async def completa_json(self, **kw: Any) -> RispostaLlm:
        self.chiamate.append(kw)
        if self.errore:
            raise AIProviderError(self.errore)
        return RispostaLlm(
            testo=self.testo or "",
            model_snapshot="finto-1-2026",
            request_id="r1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=1.0,
        )


def analisi_valida(ctx, **modifiche) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    refs = costruisci_ingresso(ctx).allowed_refs
    base: dict[str, Any] = {
        "engine_regime_acknowledged": str(ctx.regime),
        "market_summary": "Quadro di avversione al rischio: indici deboli, yen e franco forti.",
        "analyst_view_on_regime": "La lettura coincide con quella del motore.",
        "dominant_themes": [
            {
                "title": "Fuga verso i rifugi",
                "description": "Yen e franco si rafforzano mentre gli indici scendono.",
                "refs": ["currency_strength.JPY", "equities"],
            }
        ],
        "scenarios": [
            {
                "name": "Prosecuzione",
                "narrative": "L'avversione al rischio continua nelle prossime sessioni.",
                "likelihood": 0.6,
                "horizon_hours": 48,
                "confirming_signals": ["Yen ancora forte sui timeframe veloci."],
                "invalidation": [
                    {
                        "description": "Gli indici tornano a salire con ampiezza.",
                        "subject": "US500",
                        "metric": "PRICE",
                        "direction": "ABOVE",
                        "threshold": 7800.0,
                        "horizon_hours": 48,
                    }
                ],
                "key_subjects": ["US500", "JPY"],
            }
        ],
        "supporting_evidence": [
            {"statement": "Il sentiment composito è negativo.", "refs": ["risk_sentiment"]}
        ],
        "contradictory_evidence": [
            {"statement": "Il calendario economico non è disponibile.", "refs": ["calendar"]}
        ],
        "uncertainties": [
            {
                "description": "Rischio da eventi non noto.",
                "missing_information": "Calendario economico.",
                "impact": "MEDIUM",
            }
        ],
        "assets_to_watch": [
            {
                "canonical": "USDJPY",
                "reason": "Termometro della domanda di yen.",
                "watch_for": "Una ripresa sopra i massimi recenti.",
            }
        ],
        "invalidation_conditions": [
            {
                "description": "Il regime del motore torna RISK_ON.",
                "subject": "regime",
                "metric": "REGIME",
                "direction": "CHANGES_TO",
                "threshold": None,
                "target_state": "RISK_ON",
                "horizon_hours": 24,
            }
        ],
        "confidence": "MEDIUM",
    }
    assert all(
        r in refs for r in ("currency_strength.JPY", "equities", "risk_sentiment", "calendar")
    )
    base.update(modifiche)
    return base


async def analizza(ctx, testo=None, errore=None, tmp_path: Path | None = None):  # type: ignore[no-untyped-def]
    diario = AnalysisJournal(tmp_path or Path("var/test_ai_journal_unit"))
    analista = MarketAnalyst(
        ModelloFinto(testo, errore),
        diario,
        clock=FrozenClock(datetime(2026, 3, 10, 9, 1, tzinfo=UTC)),
    )
    return await analista.analizza(ctx), diario


# ======================================================================
# Validazione: output invalido = analisi non disponibile
# ======================================================================
class TestValidazione:
    async def test_analisi_valida_disponibile(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        r, _ = await analizza(ctx, json.dumps(analisi_valida(ctx)), tmp_path=tmp_path)
        assert r.status == "AVAILABLE", r.failure_reasons
        assert r.analysis is not None
        assert r.call.model_snapshot == "finto-1-2026"

    @pytest.mark.parametrize(
        ("modifica", "motivo"),
        [
            ({"engine_regime_acknowledged": "RISK_ON"}, "diverso da quello del motore"),
            (
                {
                    "supporting_evidence": [
                        {"statement": "Prova inventata qui.", "refs": ["vix.daily"]}
                    ]
                },
                "Riferimenti inesistenti",
            ),
            (
                {
                    "assets_to_watch": [
                        {
                            "canonical": "BTCUSD",
                            "reason": "Non è nell'universo.",
                            "watch_for": "Qualsiasi cosa.",
                        }
                    ]
                },
                "fuori dall'universo",
            ),
            (
                {"market_summary": "Conviene buy su USDJPY con stop loss sotto 150 e 0.5 lots."},
                "Contenuto operativo",
            ),
            ({"confidence": "HIGH"}, "Fiducia HIGH"),
            (
                {
                    "uncertainties": [
                        {
                            "description": "Nessun dubbio rilevante.",
                            "missing_information": None,
                            "impact": "LOW",
                        }
                    ]
                },
                "Limitazioni note ignorate",
            ),
        ],
    )
    async def test_violazioni_rendono_l_analisi_non_disponibile(
        self, modifica: dict[str, Any], motivo: str, tmp_path: Path
    ) -> None:
        ctx = contesto(**RISK_OFF)
        r, _ = await analizza(ctx, json.dumps(analisi_valida(ctx, **modifica)), tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"
        assert r.analysis is None
        assert any(motivo in m for m in r.failure_reasons), r.failure_reasons
        assert r.raw_output  # la risposta grezza resta registrata

    async def test_mixed_senza_evidenze_contrarie_rifiutato(self, tmp_path: Path) -> None:
        ctx = contesto(**MIXED)
        assert str(ctx.regime) == "MIXED"
        testo = json.dumps(analisi_valida(ctx, contradictory_evidence=[]))
        r, _ = await analizza(ctx, testo, tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"
        assert any("senza evidenze contraddittorie" in m for m in r.failure_reasons)

    @pytest.mark.parametrize("testo", ["", "non è json", '{"market_summary": "troppo poco"}'])
    async def test_json_rotto_o_incompleto(self, testo: str, tmp_path: Path) -> None:
        r, _ = await analizza(contesto(**RISK_OFF), testo, tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"
        assert any("schema" in m for m in r.failure_reasons)

    async def test_campo_operativo_aggiunto_rifiutato_dallo_schema(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        dati = analisi_valida(ctx)
        dati["entry_price"] = 1.1234  # il contratto non ha questo campo
        r, _ = await analizza(ctx, json.dumps(dati), tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"

    async def test_probabilita_oltre_uno(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        dati = analisi_valida(ctx)
        dati["scenarios"] = [
            dict(dati["scenarios"][0], likelihood=0.7, name=n) for n in ("A1", "B1")
        ]
        r, _ = await analizza(ctx, json.dumps(dati), tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"

    async def test_provider_non_raggiungibile(self, tmp_path: Path) -> None:
        r, _ = await analizza(contesto(**RISK_OFF), errore="timeout", tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"
        assert r.call.error == "timeout"

    async def test_nessun_provider_nessuna_risposta_finta(self, tmp_path: Path) -> None:
        analista = MarketAnalyst(NullLlmClient(), AnalysisJournal(tmp_path))
        r = await analista.analizza(contesto(**RISK_OFF))
        assert r.status == "UNAVAILABLE" and r.raw_output is None


# ======================================================================
# Condizioni di invalidazione verificabili da un programma (fase 9 senza hindsight)
# ======================================================================
def _condizione(**kw: Any) -> dict[str, Any]:
    base = {
        "description": "Condizione di prova.",
        "subject": "regime",
        "metric": "REGIME",
        "direction": "CHANGES_TO",
        "threshold": None,
        "target_state": "RISK_ON",
        "horizon_hours": 24,
    }
    return base | kw


class TestVerificabilita:
    @pytest.mark.parametrize(
        "modifica",
        [
            {"target_state": None},  # "cambia" senza dire verso cosa (trovato dal vivo)
            {"target_state": "SALE"},  # stato inesistente
            {"metric": "PRICE", "subject": "US500", "direction": "ABOVE", "target_state": None},
            {"metric": "PRICE", "subject": "BTCUSD", "direction": "ABOVE", "threshold": 1.0},
            {"metric": "CURRENCY_STRENGTH", "subject": "XAU", "direction": "BELOW", "threshold": 0},
            # correlazione su un conflitto (trovato dal vivo): non è una correlazione
            {
                "metric": "CORRELATION",
                "subject": "conflicts.INDIZI_RISCHIO_DISCORDI",
                "direction": "BELOW",
                "threshold": 0.41,
            },
            {"metric": "EVENT", "subject": "calendar", "direction": "OCCURS"},
        ],
    )
    async def test_condizioni_ambigue_rifiutate(
        self, modifica: dict[str, Any], tmp_path: Path
    ) -> None:
        ctx = contesto(**RISK_OFF)
        dati = analisi_valida(ctx, invalidation_conditions=[_condizione(**modifica)])
        r, _ = await analizza(ctx, json.dumps(dati), tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"
        assert any("non verificabile" in m for m in r.failure_reasons), r.failure_reasons

    @pytest.mark.parametrize(
        "modifica",
        [
            {},
            {"metric": "PRICE", "subject": "US500", "direction": "BELOW", "threshold": 7000.0},
            {"metric": "CURRENCY_STRENGTH", "subject": "JPY", "target_state": "WEAK"},
            # riferimento non ambiguo alla valuta (scritto così dal modello reale)
            {
                "metric": "CURRENCY_STRENGTH",
                "subject": "currency_strength.USD",
                "target_state": "NEUTRAL",
            },
            # "lo scenario fallisce se il regime NON diventa RISK_ON entro l'orizzonte"
            {"direction": "NOT_REACHED"},
            {"metric": "CURRENCY_STRENGTH", "subject": "USD", "direction": "ABOVE", "threshold": 1},
            {
                "metric": "VOLATILITY",
                "subject": "volatility",
                "target_state": "EXPANDING",
            },
        ],
    )
    async def test_condizioni_verificabili_accettate(
        self, modifica: dict[str, Any], tmp_path: Path
    ) -> None:
        ctx = contesto(**RISK_OFF)
        dati = analisi_valida(ctx, invalidation_conditions=[_condizione(**modifica)])
        r, _ = await analizza(ctx, json.dumps(dati), tmp_path=tmp_path)
        assert r.status == "AVAILABLE", r.failure_reasons

    async def test_anche_le_condizioni_degli_scenari_sono_controllate(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        dati = analisi_valida(ctx)
        dati["scenarios"][0]["invalidation"] = [_condizione(target_state=None)]
        r, _ = await analizza(ctx, json.dumps(dati), tmp_path=tmp_path)
        assert r.status == "UNAVAILABLE"


# ======================================================================
# Immutabilità e versioni
# ======================================================================
class TestImmutabilitaEVersioni:
    async def test_record_immutabile(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        r, _ = await analizza(ctx, json.dumps(analisi_valida(ctx)), tmp_path=tmp_path)
        with pytest.raises(ValidationError):
            r.status = "AVAILABLE"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            r.analysis.confidence = "HIGH"  # type: ignore[union-attr,misc]

    async def test_diario_non_sovrascrive(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        r, diario = await analizza(ctx, json.dumps(analisi_valida(ctx)), tmp_path=tmp_path)
        with pytest.raises(JournalError):
            diario.scrivi(r)

    async def test_manomissione_rilevata(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        r, diario = await analizza(ctx, json.dumps(analisi_valida(ctx)), tmp_path=tmp_path)
        percorso = diario.percorso(r)
        assert AnalysisJournal.leggi(percorso).record_id == r.record_id
        dati = json.loads(percorso.read_text(encoding="utf-8"))
        dati["analysis"]["confidence"] = "HIGH"
        percorso.write_text(json.dumps(dati), encoding="utf-8")
        with pytest.raises(JournalError, match="integrità"):
            AnalysisJournal.leggi(percorso)

    def test_ingresso_deterministico_e_versionato(self) -> None:
        a = costruisci_ingresso(contesto(**RISK_OFF))
        b = costruisci_ingresso(contesto(**RISK_OFF))
        c = costruisci_ingresso(contesto(**MIXED))
        assert a.input_sha256 == b.input_sha256
        assert a.input_sha256 != c.input_sha256
        assert a.prompt_version.startswith("market-analyst-prompt-")
        assert a.output_schema_version.startswith("market-analysis-output-")
        assert a.context_as_of == AS_OF

    def test_l_ingresso_contiene_solo_il_passato(self) -> None:
        """Nessun dato successivo ad as_of: l'impronta del contesto è quella tagliata."""
        ctx = contesto(**RISK_OFF)
        ingresso = costruisci_ingresso(ctx)
        assert ingresso.context_fingerprint == ctx.input_fingerprint
        assert ingresso.payload["context_as_of"] == AS_OF.isoformat()

    async def test_scadenze_di_valutazione_dagli_orizzonti(self, tmp_path: Path) -> None:
        ctx = contesto(**RISK_OFF)
        r, _ = await analizza(ctx, json.dumps(analisi_valida(ctx)), tmp_path=tmp_path)
        scadenze = r.evaluation_due()
        assert scadenze["Prosecuzione"] == AS_OF.replace() + __import__("datetime").timedelta(
            hours=48
        )

    def test_limitazioni_e_interpretazioni_nell_ingresso(self) -> None:
        ingresso = costruisci_ingresso(contesto(**RISK_OFF))
        assert ingresso.known_limitations
        prove = ingresso.payload["risk_sentiment"]["evidence"]
        assert all("points_to" in p for p in prove)


# ======================================================================
# Isolamento: niente ordini, niente broker, niente rischio
# ======================================================================
def test_lo_schema_di_output_non_ha_campi_operativi() -> None:
    testo = json.dumps(MarketAnalysis.model_json_schema()).lower()
    for vietato in (
        "volume",
        "lot",
        "size",
        "entry",
        "stop_loss",
        "take_profit",
        "order",
        "quantity",
        "leverage",
        '"side"',
    ):
        assert vietato not in testo, vietato


def test_schema_rigoroso_conserva_i_nomi_dei_campi() -> None:
    """Regressione dal vivo: il campo `title` veniva cancellato come parola chiave."""
    s = schema_rigoroso(MarketAnalysis)
    tema = s["$defs"]["Theme"]
    assert "title" in tema["properties"]
    assert tema["required"] == list(tema["properties"])
    assert tema["additionalProperties"] is False


def test_il_package_ai_non_importa_broker_rischio_esecuzione() -> None:
    from backend.config.paths import BACKEND_ROOT

    vietati = (
        "backend.brokers",
        "backend.risk",
        "backend.execution",
        "backend.trade_planner",
        "backend.valuation",
        "backend.portfolio",
    )
    file = sorted((BACKEND_ROOT / "ai").rglob("*.py"))
    assert len(file) >= 8
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
                assert nodo.attr not in ("order_send", "send_order", "calculate_margin")


def test_record_ha_id_e_integrita() -> None:
    from backend.ai.contracts import LlmCall

    ingresso = costruisci_ingresso(contesto(**RISK_OFF))
    r = AnalysisRecord(
        created_at=datetime(2026, 3, 10, 9, 1, tzinfo=UTC),
        input=ingresso,
        call=LlmCall(
            provider="finto",
            model_requested="finto-1",
            max_output_tokens=10,
            started_at=datetime(2026, 3, 10, 9, 1, tzinfo=UTC),
        ),
        status="UNAVAILABLE",
        failure_reasons=("prova",),
    )
    assert len(r.record_id) == 24 and len(r.integrity_sha256) == 64
