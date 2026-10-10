"""Classificazione AI delle notizie: impatto, asset toccati, sintesi in italiano.

Il modello riceve solo titolo ed estratto delle notizie già passate dal filtro di
regola, a lotti. Non deve aggiungere fatti: se il testo riporta voci o dichiarazioni
non confermate, lo dice. Output validato: id inesistenti e asset fuori elenco sono
scartati; una risposta non valida lascia le notizie con la sola classificazione di
regola (fail closed sull'AI, non sulla notizia).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.ai.providers.base import LlmClient, schema_rigoroso
from backend.core.errors import AIProviderError
from backend.core.logging import get_logger

logger = get_logger(__name__)

PROMPT_VERSION = "news-classifier-prompt-1.0.0"
ASSET_AMMESSI = (
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "AUD",
    "NZD",
    "CAD",
    "XAU",
    "XAG",
    "OIL",
    "EQUITY",
)

_RIGIDO = ConfigDict(frozen=True, extra="forbid")


class Valutazione(BaseModel):
    model_config = _RIGIDO

    id: str
    impact: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        description="Impatto atteso su forex, oro e indici nelle prossime ore."
    )
    assets: tuple[str, ...] = Field(description=f"Sottoinsieme di {', '.join(ASSET_AMMESSI)}.")
    summary_it: str = Field(
        min_length=10, max_length=320, description="Sintesi in italiano, fedele al testo."
    )
    why: str = Field(max_length=200, description="Perché conta (o non conta) per i mercati.")
    unverified: bool = Field(
        description="Vero se il testo riporta voci, indiscrezioni o fonti anonime."
    )


class Lotto(BaseModel):
    model_config = _RIGIDO

    items: tuple[Valutazione, ...]


SISTEMA = f"""\
Sei un analista che filtra notizie per un sistema di trading su forex, oro e indici.
Per ogni notizia ricevi id, fonte, titolo ed estratto. Restituisci una valutazione per ogni id.
Regole:
- Usa solo le informazioni del testo. Non aggiungere fatti, numeri o contesto che non ci sono.
- impact HIGH solo per: decisioni o sorprese di banche centrali, dati macro molto diversi dalle
  attese, escalation militari o geopolitiche rilevanti, crisi finanziarie, misure commerciali
  importanti. MEDIUM per notizie che possono muovere i prezzi in modo limitato. LOW per il resto.
- assets: solo fra {", ".join(ASSET_AMMESSI)}; EQUITY = indici azionari.
- unverified = true se il testo riporta voci, indiscrezioni, fonti anonime o affermazioni non
  confermate; in quel caso dillo anche nella sintesi.
- summary_it: una o due frasi in italiano, neutre. why: perché conta per il mercato.
Rispondi solo con l'oggetto dello schema."""


class NewsClassifier:
    def __init__(self, client: LlmClient, *, max_output_tokens: int = 4000) -> None:
        self._client = client
        self._max = max_output_tokens
        self._schema = schema_rigoroso(Lotto)

    async def classifica(self, notizie: list[dict[str, Any]]) -> dict[str, Valutazione]:
        if not notizie:
            return {}
        try:
            r = await self._client.completa_json(
                sistema=SISTEMA,
                utente=json.dumps(notizie, ensure_ascii=False),
                schema=self._schema,
                nome_schema="news_batch",
                temperature=0.0,
                max_output_tokens=self._max,
            )
            lotto = Lotto.model_validate_json(r.testo)
        except (AIProviderError, ValidationError) as e:
            logger.warning("notizie.classificazione_fallita", motivo=str(e)[:200])
            return {}
        validi = {n["id"] for n in notizie}
        esito: dict[str, Valutazione] = {}
        for v in lotto.items:
            if v.id not in validi:
                continue
            asset = tuple(a for a in v.assets if a in ASSET_AMMESSI)
            esito[v.id] = v.model_copy(update={"assets": asset})
        return esito
