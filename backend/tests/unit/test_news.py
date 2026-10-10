"""Notizie: lettura sicura dei feed, filtro, conferma fra editori, freno, AI, tesi.

Nessuna rete: i feed sono testi costruiti qui, serviti da una `scarica` sostituita.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.ai.news_classifier import NewsClassifier
from backend.ai.providers.base import RispostaLlm
from backend.core.clock import FrozenClock
from backend.news import service as servizio
from backend.news.feed import leggi_feed
from backend.news.service import NewsService, asset_strumento
from backend.news.sources import Fonte
from backend.news.taxonomy import classifica

ADESSO = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def rss(*voci: tuple[str, str, datetime]) -> str:
    righe = "".join(
        f"<item><title>{t}</title><link>{link}</link><description>{t}</description>"
        f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item>"
        for t, link, d in voci
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{righe}</channel></rss>'
    )


class TestFeed:
    def test_rss_e_atom(self) -> None:
        voci = leggi_feed(rss(("Fed holds rates", "https://a/1", ADESSO)))
        assert voci[0].titolo == "Fed holds rates" and voci[0].pubblicata == ADESSO
        atom = (
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>ECB &amp; rates</title>'
            '<link href="https://b/2"/><updated>2026-10-06T11:00:00Z</updated>'
            "<summary>&lt;p&gt;testo&lt;/p&gt;</summary></entry></feed>"
        )
        v = leggi_feed(atom)[0]
        assert (v.titolo, v.link, v.estratto) == ("ECB & rates", "https://b/2", "testo")

    def test_voci_senza_data_scartate(self) -> None:
        assert (
            leggi_feed("<rss><channel><item><title>senza data</title></item></channel></rss>") == []
        )

    def test_xml_malevolo_rifiutato(self) -> None:
        malevolo = (
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
            "<rss><channel><item><title>&b;</title></item></channel></rss>"
        )
        with pytest.raises(Exception):  # noqa: B017 — defusedxml solleva EntitiesForbidden
            leggi_feed(malevolo)


class TestFiltro:
    def test_rilevanti_e_irrilevanti(self) -> None:
        assert classifica("How to save for your toddler's pension", ufficiale=False)[2] is None
        asset, temi, imp = classifica(
            "Fed signals surprise rate hike as inflation jumps", ufficiale=False
        )
        assert "USD" in asset and "POLITICA_MONETARIA" in temi and imp == "HIGH"

    def test_parole_intere(self) -> None:
        # "warns" non è "war", "europe" non è "euro"
        asset, temi, _ = classifica("Chancellor warns on Europe outlook", ufficiale=False)
        assert "GEOPOLITICA" not in temi and "EUR" not in asset

    def test_decisione_ufficiale_alto_impatto(self) -> None:
        asset, _, imp = classifica(
            "Monetary policy decision: rate hike of 25 basis points",
            ufficiale=True,
            asset_fissi=("GBP",),
        )
        assert asset == ("GBP",) and imp == "HIGH"

    def test_geopolitica_senza_asset_resta(self) -> None:
        _, temi, imp = classifica(
            "Missile attack escalates conflict in the region", ufficiale=False
        )
        assert "GEOPOLITICA" in temi and imp == "HIGH"

    def test_asset_degli_strumenti(self) -> None:
        assert asset_strumento("EUR", "USD", "FX") == {"EUR", "USD"}
        assert asset_strumento("XAU", "USD", "METAL") == {"XAU", "USD"}
        assert asset_strumento("US100", "USD", "INDEX") == {"USD", "EQUITY"}


FONTI = (
    Fonte("Fed", "https://fed", "federalreserve.gov", "UFFICIALE", ("USD",)),
    Fonte("BBC", "https://bbc", "bbc.co.uk", "TESTATA"),
    Fonte("Guardian", "https://guardian", "theguardian.com", "TESTATA"),
    Fonte("FXStreet", "https://fxs", "fxstreet.com", "SPECIALIZZATA"),
)


async def _servizio(tmp_path, feed: dict[str, str], monkeypatch, classificatore=None):  # type: ignore[no-untyped-def]
    async def finta_scarica(url: str, *, limite_secondi: float) -> str:
        if url not in feed:
            raise RuntimeError("feed non raggiungibile")
        return feed[url]

    monkeypatch.setattr(servizio, "scarica", finta_scarica)
    s = NewsService(tmp_path, clock=FrozenClock(ADESSO), classificatore=classificatore, fonti=FONTI)
    await s.raccogli()
    return s


class TestServizio:
    async def test_conferma_fra_editori_indipendenti(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        t = ADESSO - timedelta(minutes=20)
        s = await _servizio(
            tmp_path,
            {
                "https://bbc": rss(
                    (
                        "US imposes sweeping tariffs on Chinese imports, dollar jumps",
                        "https://bbc/1",
                        t,
                    )
                ),
                "https://guardian": rss(
                    (
                        "Dollar jumps as US imposes sweeping tariffs on China imports",
                        "https://g/1",
                        t,
                    )
                ),
                "https://fxs": rss(
                    ("Gold slips as dollar firms on inflation data", "https://fxs/1", t)
                ),
            },
            monkeypatch,
        )
        per_titolo = {i.publisher: i for i in s.items.values()}
        assert per_titolo["bbc.co.uk"].confirmed and per_titolo["theguardian.com"].confirmed
        assert per_titolo["bbc.co.uk"].cluster == per_titolo["theguardian.com"].cluster
        assert not per_titolo["fxstreet.com"].confirmed  # una sola fonte specializzata
        assert s.stato_fonti["Fed"]["ok"] is False and s.status().state.value == "AVAILABLE"
        # Un solo rappresentante per fatto nell'elenco.
        assert len([i for i in s.recenti() if "tariffs" in i.title.lower()]) == 1

    async def test_vecchie_e_irrilevanti_scartate(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        s = await _servizio(
            tmp_path,
            {
                "https://bbc": rss(
                    (
                        "Fed cuts rates in surprise move",
                        "https://bbc/old",
                        ADESSO - timedelta(days=4),
                    ),
                    ("Ten tips for a better garden", "https://bbc/g", ADESSO),
                ),
            },
            monkeypatch,
        )
        assert s.items == {} and s.scartate == 1

    async def test_freno_solo_su_alto_impatto_confermato_recente(
        self, tmp_path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        s = await _servizio(
            tmp_path,
            {
                "https://fed": rss(
                    (
                        "FOMC statement: surprise rate hike of 50 basis points",
                        "https://fed/1",
                        ADESSO - timedelta(minutes=15),
                    )
                ),
                "https://fxs": rss(
                    (
                        "Rumours of emergency rate cut in Japan as yen collapses",
                        "https://fxs/2",
                        ADESSO - timedelta(minutes=5),
                    )
                ),
            },
            monkeypatch,
        )
        motivi = s.freni({"EUR", "USD"})
        assert len(motivi) == 1 and "Fed" in motivi[0]
        assert s.freni({"JPY", "CHF"}) == []  # fonte specializzata sola: non confermata
        assert s.freni({"AUD", "NZD"}) == []
        # Passata un'ora, il freno si spegne.
        s._clock = FrozenClock(ADESSO + timedelta(hours=1))
        assert s.freni({"USD"}) == []

    async def test_ai_affina_e_le_voci_non_frenano(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        class Valutazione:
            def __init__(self, **kw: Any) -> None:
                self.__dict__.update(kw)

        async def classificatore(lotto: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                n["id"]: Valutazione(
                    impact="HIGH",
                    assets=("USD",),
                    summary_it="Sintesi in italiano.",
                    why="conta",
                    unverified=True,
                )
                for n in lotto
            }

        s = await _servizio(
            tmp_path,
            {
                "https://fed": rss(
                    (
                        "FOMC statement on monetary policy",
                        "https://fed/1",
                        ADESSO - timedelta(minutes=5),
                    )
                ),
            },
            monkeypatch,
            classificatore,
        )
        i = next(iter(s.items.values()))
        assert (i.impact, i.summary_it, i.unverified) == ("HIGH", "Sintesi in italiano.", True)
        assert s.freni({"USD"}) == []  # voce non verificata: nessun freno
        refs = s.per_tesi({"USD"})
        assert refs and refs[0]["ref"] == f"news.{i.id}"

    async def test_stato_salvato_e_riletto(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        s = await _servizio(
            tmp_path,
            {
                "https://bbc": rss(
                    (
                        "ECB raises interest rates again",
                        "https://bbc/9",
                        ADESSO - timedelta(hours=1),
                    )
                ),
            },
            monkeypatch,
        )
        r = NewsService(tmp_path, clock=FrozenClock(ADESSO), fonti=FONTI)
        assert set(r.items) == set(s.items)


class _Client:
    name = "finto"
    model = "finto"

    def __init__(self, testo: str) -> None:
        self.testo = testo

    async def completa_json(self, **kw: Any) -> RispostaLlm:
        return RispostaLlm(
            testo=self.testo,
            model_snapshot="f",
            request_id="r",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


async def test_classificatore_scarta_id_e_asset_inventati() -> None:
    risposta = {
        "items": [
            {
                "id": "a1",
                "impact": "HIGH",
                "assets": ["USD", "BTC"],
                "summary_it": "La Fed alza i tassi.",
                "why": "tassi",
                "unverified": False,
            },
            {
                "id": "inventato",
                "impact": "LOW",
                "assets": [],
                "summary_it": "Non esiste qui.",
                "why": "",
                "unverified": False,
            },
        ]
    }
    esito = await NewsClassifier(_Client(json.dumps(risposta))).classifica(
        [{"id": "a1", "source": "Fed", "level": "UFFICIALE", "title": "x", "excerpt": "y"}]
    )
    assert set(esito) == {"a1"} and esito["a1"].assets == ("USD",)
    assert await NewsClassifier(_Client("non json")).classifica([{"id": "a1"}]) == {}


def test_il_gate_ferma_su_notizia() -> None:
    from backend.config.risk_limits import RiskLimits
    from backend.risk.gate import RichiestaIngresso, valuta_ingresso

    r = RichiestaIngresso(
        simbolo="EURUSD",
        rr=2.0,
        stop_atr=1.5,
        spread_atr=0.05,
        rischio=40.0,
        equity=10_000,
        posizioni_aperte=0,
        simboli_aperti=frozenset(),
        rischio_aperto=0.0,
        pnl_oggi=0.0,
        picco_equity=10_000,
        ora_broker=datetime(2026, 10, 6, 11),
        kill_switch=False,
        finestra_evento=False,
        dati_validi=True,
        motivi_notizie=("Notizia ad alto impatto (Fed)",),
    )
    assert valuta_ingresso(r, RiskLimits(_env_file=None)) == ["Notizia ad alto impatto (Fed)"]  # type: ignore[call-arg]
