"""Fase 4.5: fonti esterne, calendario, forza valutaria a grafo, discordanza, persistenza.

I parser si testano su file **reali** registrati il 2026-10-05 in `tests/dati/`
(Forex Factory, CBOE, Tesoro USA): nessun dato inventato, nessuna rete.
"""

from __future__ import annotations

import ast
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.calendar.risk import rischio_calendario
from backend.config.global_universe import CROSS_FX, VALUTE
from backend.core.enums import (
    DataCategory,
    EventImportance,
    GlobalDataQuality,
    GlobalRegime,
    PersistenceRequirement,
    ProviderKind,
    ProviderState,
    StrengthLabel,
    UncertaintyLevel,
    VolatilityLevel,
)
from backend.core.errors import PersistenceUnavailableError
from backend.global_market.currency_strength import stima_barra, stima_barra_robusta
from backend.global_market.engine import DatiEsterni, costruisci
from backend.global_market.external_dims import DXY_COSTANTE, DXY_PESI, tassi, volatilita_implicita
from backend.global_market.risk import sentiment
from backend.providers.base import (
    NotConfiguredCalendar,
    NotConfiguredMacro,
    NotConfiguredNews,
    ProviderError,
)
from backend.providers.cboe import analizza_csv as analizza_vix
from backend.providers.forexfactory import analizza_feed
from backend.providers.us_treasury import analizza_csv as analizza_tesoro
from backend.schemas.external import ProviderStatus
from backend.schemas.global_market import DimensionQuality, RiskEvidence
from backend.tests.scenari_globali import AS_OF, FINE, scenario

DATI = Path(__file__).parents[1] / "dati"
LETTO = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def calendario():  # type: ignore[no-untyped-def]
    return analizza_feed(
        (DATI / "forexfactory_2026-10-05.json").read_text(encoding="utf-8"), retrieved_at=LETTO
    )


@pytest.fixture(scope="module")
def vix():  # type: ignore[no-untyped-def]
    return analizza_vix((DATI / "cboe_vix_2026-10-05.csv").read_text(), retrieved_at=LETTO)


@pytest.fixture(scope="module")
def curva():  # type: ignore[no-untyped-def]
    return analizza_tesoro([(DATI / "treasury_2026.csv").read_text()], retrieved_at=LETTO)


# ======================================================================
# Parser sui dati reali
# ======================================================================
class TestCalendarioForexFactory:
    def test_eventi_reali_letti(self, calendario) -> None:  # type: ignore[no-untyped-def]
        assert calendario.event_count == 79
        alti = [e for e in calendario.events if e.importance is EventImportance.HIGH]
        assert {(e.currency, e.title) for e in alti} == {
            ("JPY", "BOJ Gov Ueda Speaks"),
            ("USD", "FOMC Meeting Minutes"),
            ("CAD", "Employment Change"),
            ("CAD", "Unemployment Rate"),
        }
        assert all(e.scheduled_at.tzinfo is UTC for e in calendario.events)
        # 14:00 ora di New York (EDT, UTC-4) = 18:00 UTC.
        fomc = next(e for e in alti if e.currency == "USD")
        assert fomc.scheduled_at == datetime(2026, 10, 7, 18, 0, tzinfo=UTC)

    def test_copertura_dichiarata_e_nessun_actual(self, calendario) -> None:  # type: ignore[no-untyped-def]
        assert calendario.coverage_start == datetime(2026, 10, 4, 4, 0, tzinfo=UTC)
        assert calendario.coverage_end == datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
        assert calendario.has_actuals is False
        assert all(e.actual is None for e in calendario.events)

    @pytest.mark.parametrize("testo", ["", "{}", "[]", "non json", '[{"title": "x"}]'])
    def test_formato_inatteso_e_un_errore_non_un_calendario_vuoto(self, testo: str) -> None:
        with pytest.raises(ProviderError):
            analizza_feed(testo, retrieved_at=LETTO)


class TestVixCboe:
    def test_serie_reale(self, vix) -> None:  # type: ignore[no-untyped-def]
        assert vix.category is DataCategory.REFERENCE_INSTRUMENT
        ultimo = vix.observations[-1]
        assert (str(ultimo.day), ultimo.close) == ("2026-10-02", 15.31)

    def test_la_chiusura_di_oggi_non_e_disponibile_oggi(self, vix) -> None:  # type: ignore[no-untyped-def]
        """Nessun look-ahead: la chiusura del 2/10 vale dalla mezzanotte di NY del 3/10."""
        ultimo = vix.observations[-1]
        sera = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)  # 19:00 a New York
        assert ultimo not in vix.available(sera)
        assert ultimo in vix.available(datetime(2026, 10, 3, 4, 0, tzinfo=UTC))

    def test_intestazione_sbagliata(self) -> None:
        with pytest.raises(ProviderError):
            analizza_vix("A,B\n1,2\n", retrieved_at=LETTO)


class TestTesoroUsa:
    def test_curva_reale(self, curva) -> None:  # type: ignore[no-untyped-def]
        ultimo = curva.points[-1]
        assert str(ultimo.day) == "2026-10-02"
        assert ultimo.yields["10Y"] == 5.28 and ultimo.yields["2Y"] == 4.83

    def test_colonne_mancanti(self) -> None:
        with pytest.raises(ProviderError):
            analizza_tesoro(['Date,"1 Mo"\n10/02/2026,4.0\n'], retrieved_at=LETTO)


# ======================================================================
# Rischio da calendario
# ======================================================================
class TestRischioCalendario:
    def test_finestra_attiva_attorno_al_fomc(self, calendario) -> None:  # type: ignore[no-untyped-def]
        r = rischio_calendario(
            calendario,
            as_of=datetime(2026, 10, 7, 17, 30, tzinfo=UTC),
            prima_minuti=60,
            dopo_minuti=30,
        )
        assert r.level is UncertaintyLevel.HIGH
        assert [w.currency for w in r.active_windows] == ["USD"]

    def test_finestra_rispetta_i_limiti_dell_utente(self, calendario) -> None:  # type: ignore[no-untyped-def]
        """60 minuti prima, 30 dopo: alle 18:31 la finestra del FOMC (18:00) è chiusa."""
        dopo = rischio_calendario(
            calendario,
            as_of=datetime(2026, 10, 7, 18, 31, tzinfo=UTC),
            prima_minuti=60,
            dopo_minuti=30,
        )
        assert not dopo.active_windows

    def test_evento_nelle_prossime_24_ore(self, calendario) -> None:  # type: ignore[no-untyped-def]
        r = rischio_calendario(
            calendario,
            as_of=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
            prima_minuti=60,
            dopo_minuti=30,
        )
        assert r.level is UncertaintyLevel.MODERATE
        assert r.next_high_impact["USD"].title == "FOMC Meeting Minutes"

    def test_oltre_la_copertura_e_unknown_non_low(self, calendario) -> None:  # type: ignore[no-untyped-def]
        """Venerdì sera: il calendario finisce sabato notte, le 24 ore non sono note."""
        r = rischio_calendario(
            calendario,
            as_of=datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
            prima_minuti=60,
            dopo_minuti=30,
        )
        assert r.level is UncertaintyLevel.UNKNOWN
        assert r.covers_next_24h is False

    def test_senza_calendario_unknown(self) -> None:
        r = rischio_calendario(None, as_of=LETTO, prima_minuti=60, dopo_minuti=30)
        assert r.level is UncertaintyLevel.UNKNOWN

    def test_evento_usd_rende_il_regime_event_driven(self, calendario) -> None:  # type: ignore[no-untyped-def]
        as_of = datetime(2026, 10, 7, 17, 45, tzinfo=UTC)
        _, c = costruisci(
            scenario(valute={"JPY": 0.0006}, gruppi={"azioni": -0.0008}),
            as_of=as_of,
            limite_broker=FINE,
            esterni=DatiEsterni(
                calendario=calendario, stati=(_stato(ProviderKind.ECONOMIC_CALENDAR),)
            ),
        )
        assert c.regime is GlobalRegime.EVENT_DRIVEN
        assert c.macro_event_risk.calendar_level is UncertaintyLevel.HIGH

    def test_evento_cad_non_cambia_il_regime_globale(self, calendario) -> None:  # type: ignore[no-untyped-def]
        """L'occupazione canadese apre una finestra su CAD, non sul mondo."""
        as_of = datetime(2026, 10, 9, 12, 15, tzinfo=UTC)
        _, c = costruisci(
            scenario(valute={"JPY": 0.0006}, gruppi={"azioni": -0.0008}),
            as_of=as_of,
            limite_broker=FINE,
            esterni=DatiEsterni(
                calendario=calendario, stati=(_stato(ProviderKind.ECONOMIC_CALENDAR),)
            ),
        )
        assert c.regime is not GlobalRegime.EVENT_DRIVEN
        assert {w.currency for w in c.macro_event_risk.active_windows} == {"CAD"}


def _stato(tipo: ProviderKind, nome: str = "registrato") -> ProviderStatus:
    from backend.core.enums import IntendedUse, SourceDomain

    dominio = {
        ProviderKind.ECONOMIC_CALENDAR: (SourceDomain.CALENDAR, IntendedUse.EVENT_RISK),
        ProviderKind.MARKET_REFERENCE: (
            SourceDomain.MARKET_REFERENCE,
            IntendedUse.STRUCTURAL_CONTEXT,
        ),
        ProviderKind.RATES: (SourceDomain.MACRO, IntendedUse.STRUCTURAL_CONTEXT),
    }[tipo]
    return ProviderStatus(
        name=nome,
        kind=tipo,
        state=ProviderState.AVAILABLE,
        domain=dominio[0],
        intended_use=dominio[1],
    )


# ======================================================================
# VIX, tassi, DXY
# ======================================================================
class TestDimensioniEsterne:
    def test_vix_reale(self, vix) -> None:  # type: ignore[no-untyped-def]
        v = volatilita_implicita(vix.available(LETTO), as_of=LETTO, fonte="cboe")
        assert v.value == 15.31
        assert v.percentile_1y is not None and 0 <= v.percentile_1y <= 1
        assert v.quality.quality is GlobalDataQuality.GOOD

    def test_vix_vecchio_degradato(self, vix) -> None:  # type: ignore[no-untyped-def]
        tardi = LETTO + timedelta(days=10)
        v = volatilita_implicita(vix.available(tardi), as_of=tardi, fonte="cboe")
        assert v.quality.quality is GlobalDataQuality.DEGRADED

    def test_vix_assente_insufficiente(self) -> None:
        v = volatilita_implicita((), as_of=LETTO, fonte="nessuna", assente="non configurato")
        assert v.quality.quality is GlobalDataQuality.INSUFFICIENT
        assert v.level is VolatilityLevel.UNKNOWN

    def test_tassi_reali(self, curva) -> None:  # type: ignore[no-untyped-def]
        t = tassi(curva.available(LETTO), as_of=LETTO, fonte="us_treasury")
        assert t.slope_2s10s_bp == pytest.approx(45.0)
        assert t.curve_inverted is False
        assert t.ten_year_trend in ("RISING", "FALLING", "STABLE")
        assert t.quality.quality is GlobalDataQuality.GOOD
        assert t.change_20d_bp["10Y"] is not None

    def test_formula_dxy(self) -> None:
        """Con tutti i cambi a 1 il DXY vale la costante ICE."""
        assert math.isclose(
            DXY_COSTANTE * math.prod(1.0**p for p in DXY_PESI.values()), 50.14348112
        )
        assert sum(abs(p) for p in DXY_PESI.values()) == pytest.approx(1.0)

    def test_dxy_concorda_con_la_forza_usd(self) -> None:
        _, c = costruisci(scenario(valute={"USD": 0.0006}), as_of=AS_OF, limite_broker=FINE)
        dxy = c.dollar_index
        assert dxy is not None and dxy.label is StrengthLabel.STRONG
        assert dxy.agrees_with_usd_strength is True
        assert (
            dxy.correlation_with_usd_strength is not None
            and dxy.correlation_with_usd_strength > 0.5
        )

    def test_fonti_dichiarate_con_categoria(self) -> None:
        snap, _ = costruisci(scenario(), as_of=AS_OF, limite_broker=FINE)
        per_id = {s.id: s for s in snap.sources}
        assert per_id["EURUSD"].category is DataCategory.REFERENCE_INSTRUMENT
        assert per_id["VIX"].category is DataCategory.REFERENCE_INSTRUMENT
        assert per_id["UST10Y"].category is DataCategory.MACRO_SERIES
        assert per_id["VIX"].state is ProviderState.NOT_CONFIGURED
        assert per_id["DXY"].source.startswith("derivato")


# ======================================================================
# Forza valutaria a grafo
# ======================================================================
def _cross(valori: dict[str, float], esclusi: set[str] = frozenset()) -> dict[str, float]:  # type: ignore[assignment]
    return {c: valori[c[:3]] - valori[c[3:]] for c in CROSS_FX if c not in esclusi}


valuta_r = st.floats(min_value=-0.01, max_value=0.01, allow_nan=False)


class TestGrafoValutario:
    @settings(max_examples=100, deadline=None)
    @given(st.lists(valuta_r, min_size=8, max_size=8))
    def test_dati_coerenti_ritrovati_esattamente(self, rendimenti: list[float]) -> None:
        """Con cross coerenti il modello ritrova i rendimenti (a meno della media)."""
        valori = dict(zip(VALUTE, rendimenti, strict=True))
        media = sum(rendimenti) / 8
        stima = stima_barra(_cross(valori))
        assert stima is not None
        x, residuo = stima
        assert residuo < 1e-12
        for v, xi in zip(VALUTE, x, strict=True):
            assert xi == pytest.approx(valori[v] - media, abs=1e-12)

    @settings(max_examples=60, deadline=None)
    @given(
        st.lists(valuta_r, min_size=8, max_size=8),
        st.sets(st.sampled_from([c for c in CROSS_FX if "USD" in c]), max_size=4),
    )
    def test_togliere_coppie_del_dollaro_non_sposta_le_stime(
        self, rendimenti: list[float], esclusi: set[str]
    ) -> None:
        """Nessun sovrappeso: il dollaro non conta di più perché ha più coppie."""
        valori = dict(zip(VALUTE, rendimenti, strict=True))
        completo = stima_barra(_cross(valori))
        ridotto = stima_barra(_cross(valori, esclusi))
        assert completo is not None and ridotto is not None
        for a, b in zip(completo[0], ridotto[0], strict=True):
            assert a == pytest.approx(b, abs=1e-12)

    def test_cross_incoerente_rimosso_come_outlier(self) -> None:
        """Un prezzo sbagliato su un cross non sposta le stime delle valute."""
        valori = {v: 0.001 * i for i, v in enumerate(VALUTE)}
        pulito = stima_barra(_cross(valori))
        cross = _cross(valori)
        cross["EURUSD"] += 0.01
        esito = stima_barra_robusta(cross)
        assert esito is not None and esito.outlier == ["EURUSD"]
        assert pulito is not None
        for a, b in zip(esito.x, pulito[0], strict=True):
            assert a == pytest.approx(b, abs=1e-12)

    @settings(max_examples=60, deadline=None)
    @given(
        st.lists(valuta_r, min_size=8, max_size=8),
        st.sampled_from(CROSS_FX),
        st.floats(min_value=5e-4, max_value=0.05) | st.floats(min_value=-0.05, max_value=-5e-4),
    )
    def test_un_outlier_qualsiasi_non_sposta_le_stime(
        self, rendimenti: list[float], corrotto: str, errore: float
    ) -> None:
        valori = dict(zip(VALUTE, rendimenti, strict=True))
        pulito = stima_barra(_cross(valori))
        cross = _cross(valori)
        cross[corrotto] += errore
        esito = stima_barra_robusta(cross)
        assert esito is not None and corrotto in esito.outlier
        for a, b in zip(esito.x, pulito[0], strict=True):  # type: ignore[index]
            assert a == pytest.approx(b, abs=1e-9)

    def test_pesi_per_volatilita_con_rumore_diverso(self) -> None:
        """Rumore grande su GBPJPY e piccolo altrove: il peso 1/varianza lo contiene."""
        import random

        rng = random.Random(3)  # noqa: S311 - dati di test
        valori = {v: 0.0005 * i for i, v in enumerate(VALUTE)}
        media = sum(valori.values()) / 8
        errori_pesati, errori_uguali = [], []
        for _ in range(200):
            cross = {
                c: r + rng.gauss(0, 2e-4 if c in ("GBPJPY", "GBPNZD", "EURNZD") else 2e-5)
                for c, r in _cross(valori).items()
            }
            pesi = {
                c: 1 / ((2e-4 if c in ("GBPJPY", "GBPNZD", "EURNZD") else 2e-5) ** 2) for c in cross
            }
            m = sum(pesi.values()) / len(pesi)
            pesi = {c: w / m for c, w in pesi.items()}
            # Con soglia outlier alta per isolare l'effetto dei pesi.
            x_p = stima_barra(cross, pesi=pesi)[0]  # type: ignore[index]
            x_u = stima_barra(cross)[0]  # type: ignore[index]
            gbp = VALUTE.index("GBP")
            errori_pesati.append(abs(x_p[gbp] - (valori["GBP"] - media)))  # type: ignore[operator]
            errori_uguali.append(abs(x_u[gbp] - (valori["GBP"] - media)))  # type: ignore[operator]
        assert sum(errori_pesati) < sum(errori_uguali)

    def test_cross_fermo_escluso_dal_timeframe(self) -> None:
        """Una quotazione bloccata (rendimenti nulli) non entra nella stima."""
        from dataclasses import replace

        from backend.core.enums import Timeframe
        from backend.global_market.currency_strength import serie_valute

        serie = {
            (c, tf): s
            for ing in scenario(valute={"USD": 0.0005})
            for tf, s in ing.serie.items()
            if (c := ing.canonical) in CROSS_FX
        }
        bloccata = serie[("EURGBP", Timeframe.H1)]
        serie[("EURGBP", Timeframe.H1)] = replace(
            bloccata, chiusure=tuple(bloccata.chiusure[0] for _ in bloccata.chiusure)
        )
        stima = serie_valute(serie, Timeframe.H1)
        assert "EURGBP" in stima.esclusi
        assert "EURGBP" not in stima.usati
        assert len(stima.valute["USD"]) > 300

    def test_troppi_pochi_cross_per_barra(self) -> None:
        valori = dict.fromkeys(VALUTE, 0.0)
        pochi = dict(list(_cross(valori).items())[:10])
        assert stima_barra(pochi) is None


# ======================================================================
# MIXED per discordanza fra dimensioni
# ======================================================================
def test_dimensioni_discordi_danno_mixed_anche_con_composito_forte() -> None:
    from backend.core.enums import Timeframe

    def prova(nome: str, valore: float, peso: float) -> RiskEvidence:
        tf = dict.fromkeys((Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15), valore)
        return RiskEvidence(name=nome, score=valore, by_timeframe=tf, weight=peso, description=nome)

    buona = DimensionQuality(dimension="x", quality=GlobalDataQuality.GOOD, coverage=1.0)
    # Composito nettamente risk-on (tre dimensioni forti), ma una dimensione forte
    # indica risk-off: la media direbbe RISK_ON, la discordanza dice MIXED.
    r = sentiment(
        (
            prova("azioni", 2.0, 1.0),
            prova("yen", 2.0, 0.75),
            prova("cicliche", 2.0, 0.75),
            prova("vix", -0.8, 0.75),
        ),
        (buona,),
    )
    assert r.score is not None and r.score > 0.35
    assert r.label is GlobalRegime.MIXED
    assert r.strong_risk_off == ("vix",)


# ======================================================================
# Provider non configurati e politica di persistenza
# ======================================================================
@pytest.mark.parametrize("classe", [NotConfiguredCalendar, NotConfiguredMacro, NotConfiguredNews])
async def test_famiglie_senza_provider_dichiarate_e_mai_simulate(classe) -> None:  # type: ignore[no-untyped-def]
    p = classe()
    assert p.status().state is ProviderState.NOT_CONFIGURED
    metodo = next(m for m in ("calendar", "macro_series", "headlines") if hasattr(p, m))
    with pytest.raises(ProviderError):
        argomenti = (
            () if metodo == "calendar" else (("X",) if metodo == "macro_series" else (LETTO,))
        )
        await getattr(p, metodo)(*argomenti)


async def test_persistenza_required_fail_closed() -> None:
    from contextlib import asynccontextmanager

    from backend.core.clock import FrozenClock
    from backend.services.persistence import MarketPersistence

    class Db:
        def __init__(self) -> None:
            self.giu = True

        @asynccontextmanager
        async def session(self):  # type: ignore[no-untyped-def]
            if self.giu:
                raise ConnectionRefusedError("giù")
            yield type("S", (), {"add": lambda self, r: None})()

    orologio = FrozenClock(LETTO)
    db = Db()
    p = MarketPersistence(db, clock=orologio)  # type: ignore[arg-type]
    # Mai provato: non affidabile. BEST_EFFORT passa, REQUIRED no.
    p.require(PersistenceRequirement.BEST_EFFORT, "contesto globale")
    with pytest.raises(PersistenceUnavailableError):
        p.require(PersistenceRequirement.REQUIRED, "stato del rischio")
    db.giu = False
    await p._scrivi(object(), "prova")
    p.require(PersistenceRequirement.REQUIRED, "stato del rischio")
    db.giu = True
    orologio.advance(seconds=31)
    await p._scrivi(object(), "prova")
    assert (
        PersistenceUnavailableError(  # blocca l'operatività per costruzione
            "x"
        ).blocks_trading
        is True
    )
    with pytest.raises(PersistenceUnavailableError):
        p.require(PersistenceRequirement.REQUIRED, "intento di esecuzione")


def test_solo_providers_http_esce_in_rete() -> None:
    """Un solo punto di accesso alla rete; le fonti esterne non toccano esecuzione o AI."""
    from backend.config.paths import BACKEND_ROOT

    importano_httpx = []
    for percorso in sorted(BACKEND_ROOT.rglob("*.py")):
        relativo = percorso.relative_to(BACKEND_ROOT).as_posix()
        if relativo.startswith("tests/"):
            continue
        albero = ast.parse(percorso.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            nomi = []
            if isinstance(nodo, ast.Import):
                nomi = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                nomi = [nodo.module]
            if any(n.split(".")[0] in ("httpx", "requests", "urllib3", "aiohttp") for n in nomi):
                importano_httpx.append(relativo)
            if relativo.startswith("providers/"):
                for n in nomi:
                    assert not n.startswith(
                        ("backend.ai", "backend.execution", "backend.trade_planner", "backend.risk")
                    ), f"{relativo} importa {n}"
    assert sorted(set(importano_httpx)) == ["providers/http.py"], importano_httpx


# ======================================================================
# Correzioni pre-fase 5: qualità per dominio, uso previsto, DXY verifica incrociata
# ======================================================================
class TestQualitaPerDominio:
    def test_cinque_domini_e_qualita_complessiva_motivata(self) -> None:
        from backend.core.enums import SourceDomain

        _, c = costruisci(scenario(valute={"USD": 0.0006}), as_of=AS_OF, limite_broker=FINE)
        domini = {q.domain: q for q in c.source_quality}
        assert set(domini) == set(SourceDomain)
        assert domini[SourceDomain.BROKER_DATA].quality is GlobalDataQuality.GOOD
        # Senza calendario la complessiva non può essere GOOD...
        assert c.data_quality is GlobalDataQuality.DEGRADED
        assert any("Calendario" in m for m in c.data_quality_reasons)
        # ...e le assenze per scelta sono limitazioni dichiarate.
        assert domini[SourceDomain.NEWS].configured is False
        assert any("notizie" in lim for lim in c.known_limitations)

    def test_fonte_configurata_che_fallisce_degrada(self, calendario) -> None:  # type: ignore[no-untyped-def]
        guasto = _stato(ProviderKind.MARKET_REFERENCE, "cboe").model_copy(
            update={"state": ProviderState.FAILED, "last_error": "timeout"}
        )
        as_of = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
        _, c = costruisci(
            scenario(valute={"USD": 0.0006}),
            as_of=as_of,
            limite_broker=FINE,
            esterni=DatiEsterni(
                calendario=calendario,
                stati=(_stato(ProviderKind.ECONOMIC_CALENDAR), guasto),
            ),
        )
        assert c.data_quality is not GlobalDataQuality.GOOD
        assert any("cboe" in m for m in c.data_quality_reasons)


def test_vix_strutturale_solo_su_d1(vix) -> None:  # type: ignore[no-untyped-def]
    """Correzione pre-fase 5: un dato giornaliero non entra nei timeframe intraday."""
    from backend.core.enums import IntendedUse, Timeframe

    as_of = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    _, c = costruisci(
        scenario(),
        as_of=as_of,
        limite_broker=FINE,
        esterni=DatiEsterni(vix=vix, stati=(_stato(ProviderKind.MARKET_REFERENCE, "cboe"),)),
    )
    prova = next(e for e in c.risk_sentiment.evidence if e.name == "vix")
    assert prova.by_timeframe[Timeframe.D1] is not None
    for tf in (Timeframe.H4, Timeframe.H1, Timeframe.M15):
        assert prova.by_timeframe[tf] is None
    assert c.implied_volatility is not None
    assert c.implied_volatility.intended_use is IntendedUse.STRUCTURAL_CONTEXT


def test_dxy_e_solo_verifica_incrociata() -> None:
    from backend.core.enums import IntendedUse

    _, c = costruisci(scenario(valute={"USD": 0.0006}), as_of=AS_OF, limite_broker=FINE)
    assert c.dollar_index is not None
    assert c.dollar_index.intended_use is IntendedUse.CROSS_CHECK
    assert c.cross_checks[0].name == "DXY_RICOSTRUITO_VS_USD_GRAFO"
    assert all("DXY" not in k.code for k in c.conflicts)


def test_ogni_fonte_reale_dichiara_i_metadati() -> None:
    from backend.core.enums import DataFrequency, IntendedUse
    from backend.providers.cboe import CboeVix
    from backend.providers.forexfactory import ForexFactoryCalendar
    from backend.providers.us_treasury import UsTreasuryRates

    for classe in (CboeVix, ForexFactoryCalendar, UsTreasuryRates):
        info = classe.info
        assert info.frequency is not DataFrequency.NONE
        assert info.publication_delay
        assert isinstance(info.intended_use, IntendedUse)
    assert CboeVix.info.intended_use is IntendedUse.STRUCTURAL_CONTEXT
    assert UsTreasuryRates.info.intended_use is IntendedUse.STRUCTURAL_CONTEXT
    assert ForexFactoryCalendar.info.intended_use is IntendedUse.EVENT_RISK
