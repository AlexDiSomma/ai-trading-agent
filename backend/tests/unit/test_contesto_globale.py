"""Motore del contesto globale: regimi, conflitti, qualità, determinismo, look-ahead.

Gli scenari sono sintetici (`tests/scenari_globali.py`): la forza "vera" di ogni
valuta e la direzione di ogni gruppo sono note per costruzione, quindi si può
verificare che il motore le ritrovi.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.core.enums import (
    ConflictSeverity,
    GlobalDataQuality,
    GlobalRegime,
    IntermarketState,
    MarketPhase,
    MarketStatus,
    RelationshipState,
    SeriesStatus,
    StrengthLabel,
    Timeframe,
    UncertaintyLevel,
    VolatilityTrend,
)
from backend.global_market.engine import costruisci
from backend.global_market.features import SerieDiRiferimento, taglia
from backend.intermarket.engine import relazione
from backend.tests.scenari_globali import AS_OF, FINE, con_barre_future, scenario

#: Scenari coerenti in TUTTE le dimensioni (fase 4.5): un risk-off con volatilità
#: che cresce, un risk-on con volatilità che si è calmata. Senza, la volatilità
#: azionaria bassa del rumore sintetico contraddiceva il resto e il regime era,
#: correttamente, MIXED.
RISK_OFF = {
    "valute": {"JPY": 0.0006, "CHF": 0.0004, "USD": 0.0002, "AUD": -0.0006, "NZD": -0.0005},
    "gruppi": {"azioni": -0.0008, "oro": 0.0005},
    "profilo": ((120, 0, 1.3),),
}
RISK_ON = {
    "valute": {"JPY": -0.0006, "CHF": -0.0004, "AUD": 0.0006, "NZD": 0.0005},
    "gruppi": {"azioni": 0.0008},
    "profilo": ((250, 120, 1.4),),
}


def contesto(ingressi=None, *, limite=FINE, **kw):  # type: ignore[no-untyped-def]
    ingressi = ingressi if ingressi is not None else scenario(**kw)
    return costruisci(
        ingressi,
        as_of=AS_OF,
        limite_broker=limite,
        broker_offset_seconds=10_800,
        broker_time_source="MEASURED",
    )


def riga(c, etichetta: str) -> str:  # type: ignore[no-untyped-def]
    return next(r.value for r in c.summary if r.label == etichetta)


# ======================================================================
# Regimi
# ======================================================================
class TestRegimi:
    def test_risk_off(self) -> None:
        _, c = contesto(**RISK_OFF)
        assert c.regime is GlobalRegime.RISK_OFF
        assert c.risk_sentiment.score is not None and c.risk_sentiment.score < 0
        assert c.equities.label is StrengthLabel.WEAK
        assert c.usable_for_decisions is True

    def test_risk_on(self) -> None:
        _, c = contesto(**RISK_ON)
        assert c.regime is GlobalRegime.RISK_ON
        assert c.equities.label is StrengthLabel.STRONG

    def test_mercato_piatto_non_produce_una_direzione(self) -> None:
        """Nessuna deriva: il motore non inventa né risk-on né risk-off.

        Può essere UNCERTAIN (segnali deboli) o MIXED (rumore che spinge dimensioni
        diverse in versi opposti): mai una direzione.
        """
        _, c = contesto()
        assert c.regime in (GlobalRegime.UNCERTAIN, GlobalRegime.MIXED)

    def test_transizione_fra_timeframe_lenti_e_veloci(self) -> None:
        off = (RISK_OFF["valute"], RISK_OFF["gruppi"])
        on = (RISK_ON["valute"], RISK_ON["gruppi"])
        _, c = contesto(
            per_timeframe={
                Timeframe.D1: off,
                Timeframe.H4: off,
                Timeframe.H1: on,
                Timeframe.M15: on,
            }
        )
        assert c.regime is GlobalRegime.TRANSITION

    def test_shock_di_volatilita_e_event_driven(self) -> None:
        _, c = contesto(shock=("*",), **RISK_OFF)
        assert c.volatility.shock is True
        assert c.regime is GlobalRegime.EVENT_DRIVEN


# ======================================================================
# Evidenze in conflitto
# ======================================================================
class TestConflitti:
    def test_evidenze_discordi_danno_mixed(self) -> None:
        """Azioni su con yen e franco forti e valute cicliche deboli: non è risk-on."""
        _, c = contesto(
            valute={"JPY": 0.0006, "CHF": 0.0005, "AUD": -0.0005, "NZD": -0.0005},
            gruppi={"azioni": 0.0009},
        )
        assert c.regime in (GlobalRegime.MIXED, GlobalRegime.UNCERTAIN)
        assert c.regime is not GlobalRegime.RISK_ON
        codici = {k.code for k in c.conflicts}
        assert "AZIONI_E_RIFUGI_FORTI" in codici

    def test_l_esempio_dell_utente_e_rappresentabile(self) -> None:
        """Risk MIXED, USD STRONG, Gold STRONG, Equities WEAK, Volatility EXPANDING,
        Macro uncertainty HIGH — senza che un punteggio unico lo appiattisca."""
        _, c = contesto(
            valute={"USD": 0.0006, "JPY": -0.0004, "AUD": 0.0003, "NZD": 0.0003},
            gruppi={"azioni": -0.0008, "oro": 0.0006},
            profilo=((220, 180, 1.6), (10, 0, 1.7)),
        )
        assert c.risk_sentiment.label is GlobalRegime.MIXED
        assert c.currency_strength.of("USD").label is StrengthLabel.STRONG  # type: ignore[union-attr]
        assert riga(c, "Gold") == "STRONG"
        assert c.equities.label is StrengthLabel.WEAK
        assert c.volatility.trend is VolatilityTrend.EXPANDING
        assert c.macro_event_risk.market_implied_uncertainty is UncertaintyLevel.HIGH
        assert {k.code for k in c.conflicts} >= {"ORO_E_DOLLARO_FORTI"}

    def test_conflitto_grave_trasforma_risk_off_in_mixed(self) -> None:
        from backend.global_market.risk import regime
        from backend.schemas.global_market import Conflict

        _, c = contesto(**RISK_OFF)
        grave = Conflict(
            code="TEST",
            dimensions=("intermarket",),
            severity=ConflictSeverity.HIGH,
            description="x",
        )
        stato, motivi = regime(c.risk_sentiment, c.volatility, [grave], False)
        assert stato is GlobalRegime.MIXED
        assert "TEST" in motivi[0]


# ======================================================================
# Forza delle valute
# ======================================================================
class TestForzaValute:
    def test_ritrova_l_ordine_vero(self) -> None:
        derive = {"USD": 0.0006, "EUR": 0.0003, "GBP": 0.0001, "JPY": -0.0006}
        _, c = contesto(valute=derive)
        classifica = [x.currency for x in c.currency_strength.currencies]
        assert classifica[0] == "USD"
        assert classifica[-1] == "JPY"
        assert classifica.index("EUR") < classifica.index("GBP")
        assert all(x.crosses_used == 7 for x in c.currency_strength.currencies)

    def test_con_pochi_cross_la_valuta_resta_stimabile(self) -> None:
        """Il grafo usa tutte le equazioni: tre cross bastano a collocare NZD."""
        mancanti = ("NZDUSD", "EURNZD", "GBPNZD", "NZDJPY")
        _, c = contesto(mancanti=mancanti, valute={"NZD": 0.001})
        nzd = c.currency_strength.of("NZD")
        assert nzd is not None and nzd.crosses_used == 3
        assert nzd.label is StrengthLabel.STRONG and nzd.rank == 1

    def test_valuta_senza_cross_non_stimabile_le_altre_si(self) -> None:
        nzd_tutti = ("NZDUSD", "EURNZD", "GBPNZD", "NZDJPY", "NZDCHF", "AUDNZD", "NZDCAD")
        _, c = contesto(mancanti=nzd_tutti, valute={"USD": 0.0006})
        nzd = c.currency_strength.of("NZD")
        assert nzd is not None and nzd.score is None and nzd.label is StrengthLabel.UNKNOWN
        assert c.currency_strength.of("USD").rank == 1  # type: ignore[union-attr]


# ======================================================================
# Qualità dei dati
# ======================================================================
class TestQualita:
    def test_dati_buoni_ma_calendario_assente_degradato(self) -> None:
        _, c = contesto(**RISK_OFF)
        assert c.data_quality is GlobalDataQuality.DEGRADED
        assert c.macro_event_risk.calendar_available is False
        assert c.macro_event_risk.calendar_level is UncertaintyLevel.UNKNOWN
        qualita = {d.dimension: d.quality for d in c.dimension_quality}
        assert qualita["currency_strength"] is GlobalDataQuality.GOOD

    def test_simboli_mancanti_dichiarati(self) -> None:
        snap, c = contesto(mancanti=("US500", "GER40"), **RISK_OFF)
        assert set(snap.missing) == {"US500", "GER40"}
        equities = next(d for d in c.dimension_quality if d.dimension == "equities")
        assert equities.quality is GlobalDataQuality.DEGRADED
        assert any("US500" in a for a in c.warnings)

    def test_quasi_tutto_mancante_insufficiente(self) -> None:
        from backend.config.global_universe import CROSS_FX

        _, c = contesto(mancanti=CROSS_FX[:20], **RISK_OFF)
        assert c.data_quality is GlobalDataQuality.INSUFFICIENT
        assert c.regime is GlobalRegime.UNCERTAIN
        assert c.usable_for_decisions is False

    def test_dati_fermi_esclusi_e_contesto_degradato(self) -> None:
        stati = dict.fromkeys(("US500", "US100", "US30", "GER40"), SeriesStatus.STALE)
        snap, c = contesto(stati_speciali=stati, **RISK_OFF)
        assert c.equities.quality.quality is GlobalDataQuality.INSUFFICIENT
        us500 = next(i for i in snap.instruments if i.canonical == "US500")
        assert us500.usable_timeframes == ()

    def test_tutto_fermo_fail_closed(self) -> None:
        _, c = contesto(stato=SeriesStatus.STALE, **RISK_OFF)
        assert c.data_quality is GlobalDataQuality.INSUFFICIENT
        assert c.regime is GlobalRegime.UNCERTAIN
        assert c.usable_for_decisions is False

    def test_storia_insufficiente(self) -> None:
        snap, c = contesto(barre=50, **RISK_OFF)
        assert all(
            s.status is SeriesStatus.INSUFFICIENT_HISTORY
            for i in snap.instruments
            for s in i.series
        )
        assert c.data_quality is GlobalDataQuality.INSUFFICIENT
        assert c.regime is GlobalRegime.UNCERTAIN

    def test_storia_corta_per_i_percentili(self) -> None:
        _, c = contesto(barre=120, **RISK_OFF)
        assert c.volatility.median_atr_percentile[Timeframe.H1] is None
        assert c.macro_event_risk.market_implied_uncertainty is UncertaintyLevel.UNKNOWN

    def test_mercato_chiuso_dichiarato_e_non_utilizzabile(self) -> None:
        _, c = contesto(stato=SeriesStatus.CLOSED, mercato=MarketStatus.CLOSED_INFERRED, **RISK_OFF)
        assert c.market_phase is MarketPhase.CLOSED_INFERRED
        assert "MERCATO CHIUSO" in c.market_phase_reason
        assert c.data_quality is not GlobalDataQuality.GOOD
        assert c.usable_for_decisions is False
        assert riga(c, "Mercato") == "CLOSED_INFERRED"

    def test_ora_broker_ignota_mai_good(self) -> None:
        _, c = contesto(
            stato=SeriesStatus.UNVERIFIED, mercato=MarketStatus.UNKNOWN, limite=None, **RISK_OFF
        )
        assert c.data_quality is not GlobalDataQuality.GOOD
        assert c.usable_for_decisions is False
        assert any("Ora del broker non misurata" in a for a in c.warnings)

    def test_dati_simulati_mai_utilizzabili(self) -> None:
        _, c = costruisci(scenario(**RISK_OFF), as_of=AS_OF, limite_broker=FINE, simulated=True)
        assert c.simulated is True
        assert c.usable_for_decisions is False
        assert any("SIMULATI" in a for a in c.warnings)


# ======================================================================
# Riproducibilità e look-ahead
# ======================================================================
class TestDeterminismo:
    def test_stesso_ingresso_stesso_contesto(self) -> None:
        s1, c1 = contesto(**RISK_OFF)
        s2, c2 = contesto(**RISK_OFF)
        assert c1.model_dump_json() == c2.model_dump_json()
        assert s1.input_fingerprint == s2.input_fingerprint

    def test_ingresso_diverso_impronta_diversa(self) -> None:
        s1, _ = contesto(**RISK_OFF)
        s2, _ = contesto(seme=8, **RISK_OFF)
        assert s1.input_fingerprint != s2.input_fingerprint

    def test_ordine_degli_strumenti_irrilevante(self) -> None:
        ingressi = scenario(**RISK_OFF)
        _, c1 = contesto(ingressi)
        _, c2 = contesto(list(reversed(ingressi)))
        assert c1.model_dump_json() == c2.model_dump_json()

    def test_ricostruibile_dal_payload(self) -> None:
        from backend.schemas.global_market import GlobalMarketContext

        _, c = contesto(**RISK_OFF)
        assert GlobalMarketContext.model_validate(c.model_dump(mode="json")) == c


class TestNessunLookAhead:
    def test_barre_future_non_cambiano_il_contesto(self) -> None:
        """Il broker fornisce barre oltre l'istante di analisi: non devono contare."""
        ingressi = scenario(**RISK_OFF)
        s1, c1 = contesto(ingressi)
        s2, c2 = contesto(con_barre_future(ingressi))
        assert s1.input_fingerprint == s2.input_fingerprint
        assert c1.model_dump_json() == c2.model_dump_json()

    def test_il_taglio_esclude_la_barra_non_ancora_chiusa(self) -> None:
        serie = scenario()[0].serie[Timeframe.H1]
        ultima = serie.tempi[-1]
        # Limite a metà dell'ultima barra: l'ultima non è ancora chiusa.
        assert len(taglia(serie, ultima + timedelta(minutes=30))) == len(serie) - 1
        # Limite esattamente alla sua fine: è chiusa e conta.
        assert len(taglia(serie, ultima + timedelta(hours=1))) == len(serie)

    def test_spostare_l_istante_indietro_ignora_le_barre_successive(self) -> None:
        ingressi = scenario(**RISK_OFF)
        prima = FINE - timedelta(days=2)
        tagliati = [
            replace(i, serie={tf: taglia(s, prima) for tf, s in i.serie.items()}) for i in ingressi
        ]
        _, c1 = contesto(ingressi, limite=prima)
        _, c2 = contesto(tagliati, limite=prima)
        assert c1.input_fingerprint == c2.input_fingerprint
        assert c1.model_dump_json() == c2.model_dump_json()


# ======================================================================
# Nessun NaN, per qualunque scenario
# ======================================================================
deriva = st.floats(min_value=-0.002, max_value=0.002, allow_nan=False)


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    usd=deriva,
    jpy=deriva,
    azioni=deriva,
    oro=deriva,
    rumore=st.floats(min_value=1e-5, max_value=0.01),
    seme=st.integers(0, 1000),
)
def test_nessun_nan_e_correlazioni_nell_intervallo(
    usd: float, jpy: float, azioni: float, oro: float, rumore: float, seme: int
) -> None:
    _, c = contesto(
        valute={"USD": usd, "JPY": jpy},
        gruppi={"azioni": azioni, "oro": oro},
        rumore=rumore,
        seme=seme,
    )
    testo = c.model_dump_json()
    assert "NaN" not in testo and "Infinity" not in testo

    def numeri(x):  # type: ignore[no-untyped-def]
        if isinstance(x, float):
            yield x
        elif isinstance(x, dict):
            for v in x.values():
                yield from numeri(v)
        elif isinstance(x, list):
            for v in x:
                yield from numeri(v)

    assert all(math.isfinite(v) for v in numeri(json.loads(testo)))
    for r in c.intermarket.relationships:
        for v in (r.current_correlation, r.baseline_correlation):
            assert v is None or -1.0 <= v <= 1.0


# ======================================================================
# Intermarket
# ======================================================================
def _coppia(rendimenti_a, rendimenti_b, tf=Timeframe.H1):  # type: ignore[no-untyped-def]
    from backend.core.enums import ReferenceGroup

    def serie(nome, rend):  # type: ignore[no-untyped-def]
        prezzo, prezzi = 100.0, [100.0]
        for r in rend:
            prezzo *= math.exp(r)
            prezzi.append(prezzo)
        tempi = tuple(FINE - timedelta(hours=len(prezzi) - i) for i in range(len(prezzi)))
        return SerieDiRiferimento(
            nome,
            ReferenceGroup.EQUITY_INDEX,
            tf,
            tempi,
            tuple(prezzi),
            tuple(prezzi),
            tuple(prezzi),
        )

    return {("A", tf): serie("A", rendimenti_a), ("B", tf): serie("B", rendimenti_b)}


def _rumore(n: int, seme: int) -> list[float]:
    import random

    rng = random.Random(seme)  # noqa: S311 - dati di test
    return [rng.gauss(0, 0.01) for _ in range(n)]


class TestIntermarket:
    """Correlazione corrente contro base storica, con campioni e significatività."""

    def test_relazione_coerente_con_la_base(self) -> None:
        comune = _rumore(200, 1)
        b = [c + e * 0.3 for c, e in zip(comune, _rumore(200, 2), strict=True)]
        r = relazione("A", "B", Timeframe.H1, _coppia(comune, b))
        assert r.state is RelationshipState.CONSISTENT
        assert r.baseline_correlation is not None and r.baseline_correlation > 0.8
        assert r.baseline_significant is True
        # Campioni dichiarati e senza sovrapposizione: 40 correnti, il resto di base.
        assert (r.current_sample, r.baseline_sample) == (40, 160)
        assert r.method == "log_returns"

    def test_divergenza_significativa(self) -> None:
        comune = _rumore(200, 1)
        b = list(comune[:-40]) + [-c for c in comune[-40:]]
        r = relazione("A", "B", Timeframe.H1, _coppia(comune, b))
        assert r.state is RelationshipState.DIVERGING
        assert r.current_correlation is not None and r.current_correlation < 0
        assert r.difference_z is not None and r.difference_z < -2

    def test_indebolimento_significativo(self) -> None:
        comune = _rumore(200, 1)
        disturbo = _rumore(200, 9)
        b = list(comune[:-40]) + [
            c * 0.2 + e for c, e in zip(comune[-40:], disturbo[-40:], strict=True)
        ]
        r = relazione("A", "B", Timeframe.H1, _coppia(comune, b))
        assert r.state is RelationshipState.WEAKENING

    def test_relazione_che_cambia_segno_e_instabile(self) -> None:
        comune = _rumore(200, 3)
        b = [c if (i // 40) % 2 else -c for i, c in enumerate(comune)]
        r = relazione("A", "B", Timeframe.H1, _coppia(comune, b))
        assert r.state in (RelationshipState.UNSTABLE, RelationshipState.NO_BASELINE)

    def test_indipendenti_nessuna_base(self) -> None:
        r = relazione("A", "B", Timeframe.H1, _coppia(_rumore(200, 4), _rumore(200, 5)))
        assert r.state is RelationshipState.NO_BASELINE
        assert r.baseline_significant is False

    def test_rendimenti_non_prezzi(self) -> None:
        """Due trend indipendenti correlano sui prezzi, non sui rendimenti."""
        a = [0.002 + e for e in _rumore(200, 6)]
        b = [0.002 + e for e in _rumore(200, 7)]
        serie = _coppia(a, b)
        prezzi_a = serie[("A", Timeframe.H1)].chiusure
        prezzi_b = serie[("B", Timeframe.H1)].chiusure
        from backend.intermarket.correlation import pearson

        assert pearson(prezzi_a, prezzi_b) > 0.8  # i livelli "correlano"
        r = relazione("A", "B", Timeframe.H1, serie)
        assert r.state is RelationshipState.NO_BASELINE  # i rendimenti no

    def test_campione_insufficiente(self) -> None:
        r = relazione("A", "B", Timeframe.H1, _coppia(_rumore(100, 4), _rumore(100, 5)))
        assert r.state is RelationshipState.INSUFFICIENT_DATA

    def test_serie_mancante(self) -> None:
        r = relazione("A", "C", Timeframe.H1, _coppia(_rumore(200, 4), _rumore(200, 5)))
        assert r.state is RelationshipState.INSUFFICIENT_DATA
        assert "C" in r.note

    def test_correlazione_perfetta_e_una_base_significativa(self) -> None:
        """Regressione: con |r| = 1 il t non era definito e la base risultava assente."""
        comune = _rumore(200, 1)
        r = relazione("A", "B", Timeframe.H1, _coppia(comune, comune))
        assert r.baseline_significant is True
        assert r.state is RelationshipState.CONSISTENT

    def test_vista_complessiva_insufficiente_senza_dati(self) -> None:
        _, c = contesto(barre=50)
        assert c.intermarket.state is IntermarketState.INSUFFICIENT


# ======================================================================
# Sfondo per strumento
# ======================================================================
def test_sfondo_di_eurusd_usa_le_due_valute() -> None:
    from datetime import UTC, datetime

    from backend.core.enums import AssetClass
    from backend.global_market.backdrop import sfondo
    from backend.schemas.instrument import ContractSpecification, Instrument

    _, c = contesto(valute={"USD": 0.0006, "EUR": -0.0004})
    eurusd = Instrument(
        canonical="EURUSD",
        broker_symbol="EURUSD",
        asset_class=AssetClass.FX,
        base_currency="EUR",
        quote_currency="USD",
        profit_currency="USD",
        margin_currency="EUR",
        contract=ContractSpecification(
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            contract_size=100_000,
            volume_min=0.01,
            volume_max=50,
            volume_step=0.01,
            stops_level_points=0,
            freeze_level_points=0,
            trade_mode=4,
        ),
        mapping_source="regola_fx",
        as_of=datetime(2026, 3, 10, tzinfo=UTC),
    )
    s = sfondo(eurusd, c)
    assert s.strength_differential is not None and s.strength_differential < 0
    assert s.base_strength.currency == "EUR"  # type: ignore[union-attr]
    assert s.quote_strength.label is StrengthLabel.STRONG  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "nome", ["risk_sentiment", "currency_strength", "volatility", "intermarket"]
)
def test_ogni_dimensione_ha_la_sua_qualita(nome: str) -> None:
    _, c = contesto(**RISK_OFF)
    assert any(d.dimension == nome for d in c.dimension_quality)
