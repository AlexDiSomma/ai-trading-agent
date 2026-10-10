"""Servizio notizie: raccolta, filtro, conferma, classificazione, freno di rischio.

Ciclo (ogni `NEWS_INTERVAL_SECONDS`):

1. legge i feed di `sources.FONTI` (solo tramite `providers/http.py`);
2. scarta le notizie vecchie (> `FINESTRA_ORE`) e quelle senza temi di mercato
   (`taxonomy.classifica`);
3. raggruppa le notizie che parlano dello stesso fatto (titoli simili entro 12 ore):
   una notizia è **confermata** se viene da una fonte ufficiale o se la riportano almeno
   due editori indipendenti (con almeno una testata o fonte ufficiale);
4. le nuove notizie rilevanti passano, a lotti, al classificatore AI (facoltativo);
5. salva lo stato su file (scrittura atomica).

Usi:
* **freno di rischio**: notizia ad alto impatto, confermata, recente, su una valuta o un
  asset dello strumento → nessuna nuova apertura su quello strumento per
  `NEWS_BRAKE_MINUTES`. Le notizie non aprono mai operazioni da sole;
* **evidenze per l'AI**: le notizie recenti e confermate entrano nella tesi come
  `news.<id>`, citabili come le altre evidenze;
* **lettura per l'utente** nella dashboard.

È anche il `NewsProvider` del contesto globale: con il servizio attivo la dimensione
NEWS risulta configurata e le limitazioni dichiarate lo riflettono.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.core.clock import Clock
from backend.core.enums import DataFrequency, IntendedUse, ProviderKind, SourceDomain
from backend.core.logging import get_logger
from backend.news.feed import leggi_feed
from backend.news.sources import FONTI, Fonte
from backend.news.taxonomy import classifica
from backend.providers.base import ProviderInfo, StatoProvider
from backend.providers.http import scarica

logger = get_logger(__name__)

FINESTRA_ORE = 48
CONSERVA_ORE = 72
SOGLIA_SIMILITUDINE = 0.34
_PAROLE = re.compile(r"[a-z]{4,}")
_VUOTE = frozenset(
    [
        "that",
        "this",
        "with",
        "from",
        "after",
        "over",
        "their",
        "what",
        "will",
        "have",
        "been",
        "says",
        "said",
        "year",
        "amid",
    ]
)

Classificatore = Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]]


class NewsItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    url: str
    source: str
    publisher: str
    level: str
    published_at: datetime
    seen_at: datetime
    excerpt: str = ""
    assets: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    impact_rule: str = "LOW"
    #: Dal classificatore AI, se disponibile.
    impact_ai: str | None = None
    summary_it: str | None = None
    why: str | None = None
    unverified: bool | None = None
    cluster: str = ""
    publishers: tuple[str, ...] = ()
    confirmed: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def impact(self) -> str:
        return self.impact_ai or self.impact_rule


def _parole(titolo: str) -> frozenset[str]:
    return frozenset(p for p in _PAROLE.findall(titolo.lower()) if p not in _VUOTE)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class NewsService:
    def __init__(
        self,
        cartella: Path,
        *,
        clock: Clock,
        classificatore: Classificatore | None = None,
        max_ai: int = 20,
        freno_minuti: int = 60,
        fonti: tuple[Fonte, ...] = FONTI,
    ) -> None:
        self._file = cartella / "news.json"
        self._clock = clock
        self._classifica = classificatore
        self._max_ai = max_ai
        self._freno = timedelta(minutes=freno_minuti)
        self._fonti = fonti
        self.items: dict[str, NewsItem] = {}
        self.stato_fonti: dict[str, dict[str, Any]] = {}
        self.scartate = 0
        self.info = ProviderInfo(
            name="RSS: banche centrali + testate",
            kind=ProviderKind.NEWS,
            official=False,
            requires_key=False,
            free=True,
            coverage=f"{len(fonti)} feed: banche centrali, testate, siti forex",
            license_note="Titoli ed estratti pubblici dei feed RSS, solo uso interno.",
            domain=SourceDomain.NEWS,
            frequency=DataFrequency.INTRADAY_BARS,
            publication_delay="minuti",
            intended_use=IntendedUse.EVENT_RISK,
        )
        self._stato = StatoProvider(self.info)
        if self._file.exists():
            dati = json.loads(self._file.read_text(encoding="utf-8"))
            self.items = {i["id"]: NewsItem.model_validate(i) for i in dati.get("items", [])}

    # ------------------------------------------------------------------ provider
    def status(self):  # type: ignore[no-untyped-def]
        return self._stato.status()

    async def headlines(self, since: datetime) -> tuple[NewsItem, ...]:
        return tuple(i for i in self.items.values() if i.published_at >= since)

    # ------------------------------------------------------------------ raccolta
    async def raccogli(self) -> dict[str, int]:
        adesso = self._clock.now()
        limite = adesso - timedelta(hours=FINESTRA_ORE)
        nuove: list[NewsItem] = []
        riuscite = 0
        for f in self._fonti:
            try:
                voci = leggi_feed(await scarica(f.url, limite_secondi=20))
                self.stato_fonti[f.nome] = {
                    "ok": True,
                    "at": adesso.isoformat(),
                    "items": len(voci),
                    "level": f.livello,
                }
                riuscite += 1
            except Exception as e:
                self.stato_fonti[f.nome] = {
                    "ok": False,
                    "at": adesso.isoformat(),
                    "error": f"{type(e).__name__}: {e}"[:200],
                    "level": f.livello,
                }
                continue
            for v in voci:
                if v.pubblicata < limite or v.pubblicata > adesso + timedelta(minutes=10):
                    continue
                ident = hashlib.sha256((v.link or v.titolo).encode("utf-8")).hexdigest()[:14]
                if ident in self.items:
                    continue
                asset, temi, impatto = classifica(
                    f"{v.titolo}. {v.estratto}",
                    ufficiale=f.livello == "UFFICIALE",
                    asset_fissi=f.asset_fissi,
                )
                if impatto is None:
                    self.scartate += 1
                    continue
                n = NewsItem(
                    id=ident,
                    title=v.titolo,
                    url=v.link,
                    source=f.nome,
                    publisher=f.editore,
                    level=f.livello,
                    published_at=v.pubblicata,
                    seen_at=adesso,
                    excerpt=v.estratto,
                    assets=asset,
                    themes=temi,
                    impact_rule=impatto,
                )
                self.items[ident] = n
                nuove.append(n)
        if riuscite:
            self._stato.successo(
                adesso, max((i.published_at for i in self.items.values()), default=None)
            )
        else:
            self._stato.errore("Nessun feed di notizie raggiungibile.")
        self._raggruppa()
        await self._classifica_ai()
        self._pulisci(adesso)
        self._salva()
        logger.info("notizie.raccolta", nuove=len(nuove), totali=len(self.items), fonti_ok=riuscite)
        return {"new": len(nuove), "total": len(self.items), "sources_ok": riuscite}

    def _raggruppa(self) -> None:
        """Raggruppa per fatto (titoli simili entro 12 ore) e decide la conferma."""
        ordinate = sorted(self.items.values(), key=lambda i: i.published_at)
        gruppi: list[tuple[frozenset[str], datetime, list[NewsItem]]] = []
        for n in ordinate:
            parole = _parole(n.title)
            for i, (rif, quando, membri) in enumerate(gruppi):
                if (
                    abs((n.published_at - quando).total_seconds()) <= 12 * 3600
                    and _jaccard(parole, rif) >= SOGLIA_SIMILITUDINE
                ):
                    membri.append(n)
                    gruppi[i] = (rif | parole, quando, membri)
                    break
            else:
                gruppi.append((parole, n.published_at, [n]))
        for _, _, membri in gruppi:
            editori = sorted({m.publisher for m in membri})
            forti = any(m.level in ("UFFICIALE", "TESTATA") for m in membri)
            confermata = any(m.level == "UFFICIALE" for m in membri) or (
                len(editori) >= 2 and forti
            )
            cluster = membri[0].id
            for m in membri:
                m.cluster, m.publishers, m.confirmed = cluster, tuple(editori), confermata

    async def _classifica_ai(self) -> None:
        """Classifica le notizie ancora senza valutazione AI: prima le più importanti per
        regola, poi le più recenti, al massimo `max_ai` per ciclo. Quelle che restano
        fuori passano al ciclo successivo."""
        if self._classifica is None:
            return
        limite = self._clock.now() - timedelta(hours=24)
        in_attesa = [
            n for n in self.items.values() if n.impact_ai is None and n.published_at >= limite
        ]
        if not in_attesa:
            return
        ordine = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        scelte = sorted(
            in_attesa, key=lambda n: (ordine[n.impact_rule], -n.published_at.timestamp())
        )[: self._max_ai]
        esito = await self._classifica(
            [
                {
                    "id": n.id,
                    "source": n.source,
                    "level": n.level,
                    "title": n.title,
                    "excerpt": n.excerpt[:400],
                }
                for n in scelte
            ]
        )
        for ident, v in esito.items():
            n = self.items.get(ident)
            if n is not None:
                n.impact_ai, n.summary_it, n.why, n.unverified = (
                    v.impact,
                    v.summary_it,
                    v.why,
                    v.unverified,
                )
                n.assets = tuple(sorted(set(n.assets) | set(v.assets)))

    def _pulisci(self, adesso: datetime) -> None:
        limite = adesso - timedelta(hours=CONSERVA_ORE)
        self.items = {k: v for k, v in self.items.items() if v.published_at >= limite}

    def _salva(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"items": [i.model_dump(mode="json") for i in self.items.values()]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self._file)

    # ------------------------------------------------------------------ usi
    def recenti(
        self,
        *,
        ore: int = 24,
        asset: set[str] | None = None,
        solo_confermate: bool = False,
        impatto_minimo: str = "LOW",
    ) -> list[NewsItem]:
        livelli = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        limite = self._clock.now() - timedelta(hours=ore)
        scelte = [
            i
            for i in self.items.values()
            if i.published_at >= limite
            and (not solo_confermate or i.confirmed)
            and livelli[i.impact] >= livelli[impatto_minimo]
            and (
                not asset
                or set(i.assets) & asset
                or ("GEOPOLITICA" in i.themes and i.impact == "HIGH")
            )
        ]
        # Un solo rappresentante per fatto: il più autorevole, poi il più recente.
        peso = {"UFFICIALE": 0, "TESTATA": 1, "SPECIALIZZATA": 2}
        per_fatto: dict[str, NewsItem] = {}
        for i in sorted(scelte, key=lambda x: (peso[x.level], -x.published_at.timestamp())):
            per_fatto.setdefault(i.cluster or i.id, i)
        return sorted(per_fatto.values(), key=lambda x: x.published_at, reverse=True)

    def freni(self, asset: set[str]) -> list[str]:
        """Motivi per non aprire su uno strumento con questi asset (vuoto = nessun freno)."""
        adesso = self._clock.now()
        motivi = []
        for i in self.recenti(ore=6, asset=asset, solo_confermate=True, impatto_minimo="HIGH"):
            if adesso - i.published_at <= self._freno and not i.unverified:
                motivi.append(
                    f"Notizia ad alto impatto ({i.source}, {i.published_at:%H:%M} UTC): "
                    f"{i.summary_it or i.title}"
                )
        return motivi[:2]

    def per_tesi(self, asset: set[str], n: int = 8) -> list[dict[str, Any]]:
        return [
            {
                "ref": f"news.{i.id}",
                "published_utc": i.published_at.isoformat(),
                "source": i.source,
                "level": i.level,
                "confirmed": i.confirmed,
                "unverified": i.unverified,
                "impact": i.impact,
                "assets": list(i.assets),
                "title": i.title,
                "summary_it": i.summary_it,
            }
            for i in self.recenti(ore=12, asset=asset, impatto_minimo="MEDIUM")[:n]
        ]


def asset_strumento(base: str, quotazione: str, classe: str) -> set[str]:
    """Etichette notizie di uno strumento: le sue valute, più EQUITY per gli indici."""
    tag = {x for x in (base, quotazione) if x and len(x) == 3}
    if "INDEX" in classe.upper():
        tag.add("EQUITY")
    return tag
