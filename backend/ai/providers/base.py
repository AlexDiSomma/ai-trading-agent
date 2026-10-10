"""Interfaccia dei client LLM e schema JSON per l'output strutturato.

Il client restituisce **testo grezzo** e metadati della chiamata: la validazione è
altrove (`ai/validation.py` e Pydantic). Così la risposta originale viene sempre
registrata, anche quando non è valida: è ciò che serve per capire perché.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from backend.core.errors import AIProviderError


@dataclass(frozen=True)
class RispostaLlm:
    testo: str
    model_snapshot: str | None
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float


class LlmClient(Protocol):
    name: str
    model: str

    async def completa_json(
        self,
        *,
        sistema: str,
        utente: str,
        schema: dict[str, Any],
        nome_schema: str,
        temperature: float,
        max_output_tokens: int,
    ) -> RispostaLlm: ...


class NullLlmClient:
    """Nessun provider: ogni richiesta fallisce in modo dichiarato. Mai risposte finte."""

    name = "null"
    model = "nessuno"

    async def completa_json(self, **_: Any) -> RispostaLlm:
        raise AIProviderError("Nessun provider AI configurato (AI_PROVIDER=null).")


#: Parole chiave che lo schema "strict" dei provider non accetta o non garantisce.
#: Vengono tolte dallo schema inviato al modello e verificate comunque da Pydantic.
_NON_SUPPORTATE = {
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "pattern",
    "format",
    "default",
    "title",
}


def schema_rigoroso(modello: type[BaseModel]) -> dict[str, Any]:
    """Schema JSON per l'output strutturato "strict": tutti i campi obbligatori,
    nessuna proprietà aggiuntiva, vincoli numerici e di lunghezza demandati a Pydantic."""
    schema = copy.deepcopy(modello.model_json_schema())

    def pulisci(nodo: Any) -> Any:
        if isinstance(nodo, dict):
            for chiave in list(nodo):
                if chiave in _NON_SUPPORTATE:
                    del nodo[chiave]
            if nodo.get("type") == "object" and "properties" in nodo:
                nodo["additionalProperties"] = False
                nodo["required"] = list(nodo["properties"])
            for chiave, valore in nodo.items():
                if chiave in ("properties", "$defs"):
                    # Mappe di NOMI (campi, definizioni): i nomi non si toccano, si
                    # puliscono solo i loro schemi. Difetto trovato dal vivo: il campo
                    # `Theme.title` veniva cancellato come se fosse la parola chiave.
                    for sotto in valore.values():
                        pulisci(sotto)
                else:
                    pulisci(valore)
        elif isinstance(nodo, list):
            for valore in nodo:
                pulisci(valore)
        return nodo

    return pulisci(schema)
