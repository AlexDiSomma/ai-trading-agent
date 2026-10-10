"""Agente della fase 5B: una chiamata per la tesi (C), una per il filtro (B).

Il flusso è quello della fase 5A: ingresso versionato con hash → chiamata con output
strutturato a temperatura 0 → validazione Pydantic → validazione contestuale →
record immutabile. Qualsiasi difetto produce `REJECTED` o `UNAVAILABLE`, e da quei
record non nasce nessun piano attivo (fail closed).

Le due chiamate sono **indipendenti**: la tesi non vede il piano quant, così il
gruppo C non è contaminato dal gruppo A.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.ai.contracts import LlmCall
from backend.ai.providers.base import LlmClient, schema_rigoroso
from backend.ai.thesis.contracts import AiDecisionRecord, FilterVerdict, ThesisInput, TradeThesis
from backend.ai.thesis.prompts import FILTER_SYSTEM_PROMPT, THESIS_SYSTEM_PROMPT, USER_TEMPLATE
from backend.ai.thesis.validation import problemi_filtro, problemi_tesi
from backend.core.clock import Clock, SystemClock
from backend.core.errors import AIProviderError
from backend.core.logging import get_logger
from backend.shadow.evidence import CatalogoEvidenze

logger = get_logger(__name__)


class ThesisAgent:
    def __init__(
        self,
        client: LlmClient,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 8_000,
        clock: Clock | None = None,
    ) -> None:
        self._client = client
        self._temperatura = temperature
        self._max_token = max_output_tokens
        self._clock = clock or SystemClock()
        self._schema_tesi = schema_rigoroso(TradeThesis)
        self._schema_filtro = schema_rigoroso(FilterVerdict)

    async def _chiama(
        self, ingresso: ThesisInput, sistema: str, schema: dict[str, Any], nome: str
    ) -> tuple[dict[str, Any], str | None, list[str]]:
        chiamata: dict[str, Any] = {
            "provider": self._client.name,
            "model_requested": self._client.model,
            "temperature": self._temperatura,
            "max_output_tokens": self._max_token,
            "started_at": self._clock.now(),
        }
        try:
            r = await self._client.completa_json(
                sistema=sistema,
                utente=USER_TEMPLATE.format(
                    payload=json.dumps(ingresso.payload, ensure_ascii=False, indent=1)
                ),
                schema=schema,
                nome_schema=nome,
                temperature=self._temperatura,
                max_output_tokens=self._max_token,
            )
        except AIProviderError as errore:
            chiamata["error"] = str(errore)[:500]
            return chiamata, None, [f"Provider AI non disponibile: {errore}"]
        chiamata.update(
            model_snapshot=r.model_snapshot,
            request_id=r.request_id,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            latency_ms=r.latency_ms,
        )
        return chiamata, r.testo, []

    @staticmethod
    def _analizza(modello: type[BaseModel], grezzo: str | None) -> tuple[Any, list[str]]:
        if grezzo is None:
            return None, []
        try:
            return modello.model_validate_json(grezzo), []
        except ValidationError as errore:
            return None, [
                "Output non conforme allo schema: "
                + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errore.errors()[:8]
                )
            ]

    async def tesi(
        self,
        ingresso: ThesisInput,
        catalogo: CatalogoEvidenze,
        *,
        ultime_chiusure: Mapping[tuple[str, str], float],
        qualita_globale: str,
    ) -> AiDecisionRecord:
        chiamata, grezzo, motivi = await self._chiama(
            ingresso, THESIS_SYSTEM_PROMPT, self._schema_tesi, "trade_thesis"
        )
        tesi, errori_schema = self._analizza(TradeThesis, grezzo)
        motivi += errori_schema
        regole: list[str] = []
        non_sostenute: list[str] = []
        if tesi is not None:
            regole, non_sostenute = problemi_tesi(
                tesi,
                ingresso,
                catalogo,
                ultime_chiusure=ultime_chiusure,
                qualita_globale=qualita_globale,
            )
        return self._registra(ingresso, chiamata, grezzo, motivi, regole, non_sostenute, tesi=tesi)

    async def filtro(self, ingresso: ThesisInput) -> AiDecisionRecord:
        chiamata, grezzo, motivi = await self._chiama(
            ingresso, FILTER_SYSTEM_PROMPT, self._schema_filtro, "ai_filter"
        )
        verdetto, errori_schema = self._analizza(FilterVerdict, grezzo)
        motivi += errori_schema
        regole: list[str] = []
        non_sostenute: list[str] = []
        if verdetto is not None:
            regole, non_sostenute = problemi_filtro(verdetto, ingresso)
        return self._registra(
            ingresso, chiamata, grezzo, motivi, regole, non_sostenute, verdetto=verdetto
        )

    def _registra(
        self,
        ingresso: ThesisInput,
        chiamata: dict[str, Any],
        grezzo: str | None,
        motivi: list[str],
        regole: list[str],
        non_sostenute: list[str],
        *,
        tesi: TradeThesis | None = None,
        verdetto: FilterVerdict | None = None,
    ) -> AiDecisionRecord:
        if motivi or (tesi is None and verdetto is None):
            stato = "UNAVAILABLE"
        elif regole:
            stato = "REJECTED"
        else:
            stato = "VALID"
        record = AiDecisionRecord(
            created_at=chiamata["started_at"],
            input=ingresso,
            call=LlmCall(**chiamata),
            raw_output=grezzo,
            status=stato,  # type: ignore[arg-type]
            failure_reasons=tuple(motivi + regole),
            unsupported_claims=tuple(non_sostenute),
            # Anche una tesi REJECTED resta leggibile nel record: serve a capire perché
            # è stata scartata. Il piano però nasce solo da VALID.
            thesis=tesi,
            verdict=verdetto,
        )
        logger.info(
            "ai.tesi" if tesi is not None or ingresso.kind == "TRADE_THESIS" else "ai.filtro",
            stato=stato,
            strumento=ingresso.canonical,
            record=record.record_id,
            motivi=len(record.failure_reasons),
        )
        return record
