"""Lettura dei feed RSS 2.0 e Atom, in modo sicuro (`defusedxml`).

Restituisce titolo, link, estratto in testo semplice e data di pubblicazione in UTC.
Una voce senza data non si usa: non sapremmo se è di oggi o di un anno fa.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from defusedxml import ElementTree

_TAG = re.compile(r"<[^>]+>")
_SPAZI = re.compile(r"\s+")


@dataclass(frozen=True)
class Voce:
    titolo: str
    link: str
    estratto: str
    pubblicata: datetime


def _testo(valore: str | None, limite: int = 600) -> str:
    if not valore:
        return ""
    pulito = _SPAZI.sub(" ", html.unescape(_TAG.sub(" ", valore))).strip()
    return pulito[:limite]


def _data(valore: str | None) -> datetime | None:
    if not valore:
        return None
    valore = valore.strip()
    try:
        d = parsedate_to_datetime(valore)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(valore.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)


def _locale(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def leggi_feed(testo: str) -> list[Voce]:
    radice = ElementTree.fromstring(testo.encode("utf-8") if isinstance(testo, str) else testo)
    voci: list[Voce] = []
    for el in radice.iter():
        nome = _locale(el.tag)
        if nome not in ("item", "entry"):
            continue
        campi: dict[str, str] = {}
        for figlio in el:
            chiave = _locale(figlio.tag)
            if chiave == "link" and figlio.get("href"):
                campi.setdefault("link", figlio.get("href", ""))
            elif figlio.text and chiave not in campi:
                campi[chiave] = figlio.text
        data = _data(
            campi.get("pubDate")
            or campi.get("date")
            or campi.get("published")
            or campi.get("updated")
        )
        titolo = _testo(campi.get("title"), 300)
        if not titolo or data is None:
            continue
        voci.append(
            Voce(
                titolo=titolo,
                link=(campi.get("link") or "").strip(),
                estratto=_testo(
                    campi.get("description") or campi.get("summary") or campi.get("content")
                ),
                pubblicata=data,
            )
        )
    return voci
