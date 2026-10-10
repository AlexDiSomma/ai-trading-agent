"""Fonti di notizie gratuite, verificate raggiungibili il 2026-10-06.

Tre livelli di affidabilità, usati per filtrare e per la conferma:

* `UFFICIALE`     — la fonte primaria del fatto (banca centrale): vale da sola;
* `TESTATA`       — testata generalista riconosciuta: vale se confermata da un'altra
                    testata indipendente;
* `SPECIALIZZATA` — sito di notizie per trader: veloce ma secondario, conta come
                    conferma solo insieme a una testata o a una fonte ufficiale.

Escluse per ora: GDELT (risponde HTTP 429 anche rispettando i 5 secondi richiesti),
Casa Bianca e Tesoro USA (feed non più disponibili o vietati). Fonti a pagamento solo
dopo aver misurato il valore di queste (EXTERNAL_PROVIDERS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Livello = Literal["UFFICIALE", "TESTATA", "SPECIALIZZATA"]


@dataclass(frozen=True)
class Fonte:
    nome: str
    url: str
    editore: str
    livello: Livello
    #: Asset sempre interessati (es. la Fed riguarda sempre il dollaro).
    asset_fissi: tuple[str, ...] = ()


FONTI: tuple[Fonte, ...] = (
    Fonte(
        "Federal Reserve",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "federalreserve.gov",
        "UFFICIALE",
        ("USD",),
    ),
    Fonte(
        "Banca Centrale Europea",
        "https://www.ecb.europa.eu/rss/press.html",
        "ecb.europa.eu",
        "UFFICIALE",
        ("EUR",),
    ),
    Fonte(
        "Bank of England",
        "https://www.bankofengland.co.uk/rss/news",
        "bankofengland.co.uk",
        "UFFICIALE",
        ("GBP",),
    ),
    Fonte(
        "Bank of Japan",
        "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "boj.or.jp",
        "UFFICIALE",
        ("JPY",),
    ),
    Fonte("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "bbc.co.uk", "TESTATA"),
    Fonte("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "bbc.co.uk", "TESTATA"),
    Fonte(
        "Guardian Business",
        "https://www.theguardian.com/business/rss",
        "theguardian.com",
        "TESTATA",
    ),
    Fonte("Guardian World", "https://www.theguardian.com/world/rss", "theguardian.com", "TESTATA"),
    Fonte("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "aljazeera.com", "TESTATA"),
    Fonte(
        "CNBC Economy",
        "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "cnbc.com",
        "TESTATA",
    ),
    Fonte(
        "CNBC World", "https://www.cnbc.com/id/100727362/device/rss/rss.html", "cnbc.com", "TESTATA"
    ),
    Fonte(
        "MarketWatch",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "marketwatch.com",
        "TESTATA",
    ),
    Fonte("FXStreet", "https://www.fxstreet.com/rss/news", "fxstreet.com", "SPECIALIZZATA"),
    Fonte(
        "investingLive", "https://investinglive.com/feed/news", "investinglive.com", "SPECIALIZZATA"
    ),
)
