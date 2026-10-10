"""Primo filtro, deterministico: di cosa parla la notizia e quali asset tocca.

Una notizia senza alcun tema di mercato e senza alcun asset riconosciuto viene
scartata (es. consigli di risparmio, cronaca). Le parole chiave sono intere (confini
di parola), in inglese: le fonti scrivono in inglese. L'AI, dopo, affina impatto e
sintesi; questo filtro decide solo cosa vale la pena farle leggere.
"""

from __future__ import annotations

import re
from typing import Literal

Impatto = Literal["LOW", "MEDIUM", "HIGH"]

#: Asset (etichette usate in tutto il sistema notizie) → parole che li richiamano.
ASSET: dict[str, tuple[str, ...]] = {
    "USD": (
        "fed",
        "fomc",
        "powell",
        "federal reserve",
        "dollar",
        "greenback",
        "treasury yields",
        "us inflation",
        "us cpi",
        "nonfarm",
        "payrolls",
        "us economy",
        "wall street",
        "trump",
        "white house",
        "us jobs",
        "us gdp",
        "us tariffs",
        "washington",
    ),
    "EUR": (
        "ecb",
        "lagarde",
        "eurozone",
        "euro area",
        "euro",
        "germany",
        "german",
        "france",
        "french",
        "italy",
        "bund",
        "european union",
        "eu ",
    ),
    "GBP": (
        "boe",
        "bank of england",
        "bailey",
        "sterling",
        "pound",
        "uk economy",
        "britain",
        "british",
        "gilts",
        "uk inflation",
        "reeves",
    ),
    "JPY": ("boj", "bank of japan", "ueda", "yen", "japan", "japanese", "jgb"),
    "CHF": ("snb", "swiss national bank", "swiss franc", "switzerland"),
    "AUD": ("rba", "reserve bank of australia", "aussie", "australia", "australian"),
    "NZD": ("rbnz", "new zealand", "kiwi"),
    "CAD": ("bank of canada", "boc", "loonie", "canada", "canadian"),
    "XAU": ("gold", "bullion", "precious metals"),
    "XAG": ("silver",),
    "OIL": ("oil", "crude", "opec", "brent", "wti"),
    "EQUITY": (
        "stocks",
        "equities",
        "nasdaq",
        "s&p 500",
        "dow jones",
        "stock market",
        "shares",
        "sell-off",
        "selloff",
    ),
}

TEMI: dict[str, tuple[str, ...]] = {
    "POLITICA_MONETARIA": (
        "rate decision",
        "interest rate",
        "interest rates",
        "rate cut",
        "rate hike",
        "rate cuts",
        "rate hikes",
        "monetary policy",
        "central bank",
        "fomc",
        "ecb",
        "boe",
        "boj",
        "rba",
        "snb",
        "rbnz",
        "policy meeting",
        "minutes",
        "hawkish",
        "dovish",
        "quantitative",
    ),
    "INFLAZIONE": ("inflation", "cpi", "pce", "consumer prices", "price pressures"),
    "LAVORO": (
        "payrolls",
        "jobs report",
        "unemployment",
        "jobless",
        "labor market",
        "labour market",
        "employment",
    ),
    "CRESCITA": ("gdp", "recession", "economic growth", "pmi", "retail sales", "slowdown"),
    "GEOPOLITICA": (
        "war",
        "attack",
        "missile",
        "missiles",
        "invasion",
        "sanctions",
        "ceasefire",
        "military",
        "airstrike",
        "airstrikes",
        "troops",
        "nuclear",
        "conflict",
        "drone",
        "escalation",
        "coup",
    ),
    "COMMERCIO": (
        "tariff",
        "tariffs",
        "trade war",
        "trade deal",
        "export controls",
        "import duties",
    ),
    "ENERGIA": ("opec", "oil prices", "crude", "energy prices", "gas prices", "pipeline"),
    "CRISI": ("default", "bank run", "bailout", "collapse", "crisis", "contagion", "downgrade"),
}

#: Temi che, insieme, rendono una notizia potenzialmente ad alto impatto.
_FORTI = re.compile(
    r"\b(rate (decision|cut|hike)s?|war|invasion|missile|nuclear|default|emergency|"
    r"surprise|unexpected|shock|collapse|escalat\w*)\b",
    re.IGNORECASE,
)


def _regex(parole: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        r"(?<![\w])(" + "|".join(re.escape(p.strip()) for p in parole) + r")(?![\w])", re.IGNORECASE
    )


_RE_ASSET = {k: _regex(v) for k, v in ASSET.items()}
_RE_TEMI = {k: _regex(v) for k, v in TEMI.items()}


def classifica(
    testo: str, *, ufficiale: bool, asset_fissi: tuple[str, ...] = ()
) -> tuple[tuple[str, ...], tuple[str, ...], Impatto | None]:
    """(asset, temi, impatto di regola). Impatto None = notizia non rilevante."""
    asset = sorted({k for k, r in _RE_ASSET.items() if r.search(testo)} | set(asset_fissi))
    temi = sorted(k for k, r in _RE_TEMI.items() if r.search(testo))
    if not temi:
        return tuple(asset), (), None
    if not asset and "GEOPOLITICA" not in temi:
        return (), tuple(temi), None
    if ufficiale and "POLITICA_MONETARIA" in temi:
        impatto: Impatto = "HIGH" if _FORTI.search(testo) else "MEDIUM"
    elif _FORTI.search(testo) and (
        "GEOPOLITICA" in temi or "POLITICA_MONETARIA" in temi or "CRISI" in temi
    ):
        impatto = "HIGH"
    elif len(temi) >= 2 or "POLITICA_MONETARIA" in temi or "INFLAZIONE" in temi:
        impatto = "MEDIUM"
    else:
        impatto = "LOW"
    return tuple(asset), tuple(temi), impatto
