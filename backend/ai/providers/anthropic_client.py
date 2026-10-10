"""Client Anthropic (tool use forzato con lo schema come `input_schema`).

**Non verificato dal vivo**: nel `.env` non c'è una chiave Anthropic. L'interfaccia è
la stessa del client OpenAI; il primo uso reale va verificato prima di fidarsene.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.core.errors import AIProviderError


class AnthropicClient:
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, timeout: float) -> None:
        from anthropic import AsyncAnthropic

        self.model = model
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)

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
        from anthropic import AnthropicError

        from backend.ai.providers.base import RispostaLlm

        inizio = time.perf_counter()
        try:
            risposta = await self._client.messages.create(
                model=self.model,
                system=sistema,
                messages=[{"role": "user", "content": utente}],
                tools=[
                    {
                        "name": nome_schema,
                        "description": "Restituisce l'analisi strutturata.",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": nome_schema},
                temperature=temperature,
                max_tokens=max_output_tokens,
            )
        except AnthropicError as errore:
            raise AIProviderError(f"Anthropic: {errore.__class__.__name__}: {errore}") from errore
        blocco = next((b for b in risposta.content if b.type == "tool_use"), None)
        if blocco is None:
            raise AIProviderError(
                f"Anthropic: nessuna chiamata allo strumento ({risposta.stop_reason})."
            )
        return RispostaLlm(
            testo=json.dumps(blocco.input, ensure_ascii=False),
            model_snapshot=risposta.model,
            request_id=risposta.id,
            input_tokens=risposta.usage.input_tokens,
            output_tokens=risposta.usage.output_tokens,
            latency_ms=round((time.perf_counter() - inizio) * 1000, 1),
        )
