"""Costruzione del client LLM dalla configurazione (`AI_PROVIDER`, `AI_MODEL`)."""

from __future__ import annotations

from backend.ai.providers.base import LlmClient, NullLlmClient
from backend.config.settings import AISettings


def costruisci_client(cfg: AISettings) -> LlmClient:
    """Client del provider scelto; `NullLlmClient` se manca la chiave o il provider."""
    if cfg.provider == "openai" and cfg.openai_api_key is not None:
        from backend.ai.providers.openai_client import OpenAIClient

        return OpenAIClient(
            api_key=cfg.openai_api_key.get_secret_value(),
            model=cfg.model,
            timeout=cfg.timeout_seconds,
        )
    if cfg.provider == "anthropic" and cfg.anthropic_api_key is not None:
        from backend.ai.providers.anthropic_client import AnthropicClient

        return AnthropicClient(
            api_key=cfg.anthropic_api_key.get_secret_value(),
            model=cfg.model,
            timeout=cfg.timeout_seconds,
        )
    return NullLlmClient()
