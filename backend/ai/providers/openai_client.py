"""Client OpenAI (Responses API, output strutturato con schema JSON "strict").

Verificato dal vivo il 2026-10-05 con `gpt-5.4-mini` (snapshot
`gpt-5.4-mini-2026-03-17`): schema strict e `temperature=0` accettati.
"""

from __future__ import annotations

import time
from typing import Any

from backend.core.errors import AIProviderError


class OpenAIClient:
    name = "openai"

    def __init__(self, *, api_key: str, model: str, timeout: float) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=1)

    async def completa_json(
        self,
        *,
        sistema: str,
        utente: str,
        schema: dict[str, Any],
        nome_schema: str,
        temperature: float,
        max_output_tokens: int,
    ):  # type: ignore[no-untyped-def]
        from openai import OpenAIError

        from backend.ai.providers.base import RispostaLlm

        inizio = time.perf_counter()
        try:
            risposta = await self._client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": utente},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": nome_schema,
                        "schema": schema,
                        "strict": True,
                    }
                },
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        except OpenAIError as errore:
            raise AIProviderError(f"OpenAI: {errore.__class__.__name__}: {errore}") from errore
        if risposta.status != "completed":
            dettaglio = getattr(risposta, "incomplete_details", None)
            raise AIProviderError(f"OpenAI: risposta {risposta.status} ({dettaglio}).")
        uso = risposta.usage
        return RispostaLlm(
            testo=risposta.output_text,
            model_snapshot=risposta.model,
            request_id=risposta.id,
            input_tokens=getattr(uso, "input_tokens", None),
            output_tokens=getattr(uso, "output_tokens", None),
            latency_ms=round((time.perf_counter() - inizio) * 1000, 1),
        )
