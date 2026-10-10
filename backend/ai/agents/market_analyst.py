"""AI Market Analyst — fase 5A, READ ONLY.

Riceve il `GlobalMarketContext` e produce **solo analisi strutturata**: sintesi,
temi dominanti, scenari, evidenze a favore e contrarie, incertezze, strumenti da
osservare, condizioni di invalidazione. Nessun ordine, nessun `TradeIntent`, nessuna
size, nessuna esecuzione: il contratto di output non ha i campi per esprimerli, e
il package non importa né il broker né il rischio né l'esecuzione (verificato
sull'AST).

Flusso, sempre registrato per intero:

1. ingresso compatto e versionato (`input_builder`), con hash;
2. chiamata al modello con output strutturato e temperatura 0;
3. validazione Pydantic, poi validazione contestuale (`validation`);
4. **output non valido = analisi non disponibile**, con i motivi;
5. registrazione immutabile nel diario (file) e, se possibile, nel database.

Il modello è un analista, non un decisore: il regime lo calcola il motore
deterministico, e l'analisi lo ripete e lo commenta.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from backend.ai.contracts import AnalysisRecord, LlmCall, MarketAnalysis
from backend.ai.input_builder import costruisci_ingresso
from backend.ai.journal import AnalysisJournal
from backend.ai.prompts import SYSTEM_PROMPT, USER_TEMPLATE
from backend.ai.providers.base import LlmClient, schema_rigoroso
from backend.ai.validation import problemi
from backend.core.clock import Clock, SystemClock
from backend.core.errors import AIProviderError
from backend.core.logging import get_logger
from backend.schemas.global_market import GlobalMarketContext

logger = get_logger(__name__)

NOME_SCHEMA = "market_analysis"


class MarketAnalyst:
    def __init__(
        self,
        client: LlmClient,
        journal: AnalysisJournal,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 6_000,
        clock: Clock | None = None,
        persist: Callable[[AnalysisRecord], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._diario = journal
        self._temperatura = temperature
        self._max_token = max_output_tokens
        self._clock = clock or SystemClock()
        self._salva = persist
        self._schema = schema_rigoroso(MarketAnalysis)

    async def analizza(self, contesto: GlobalMarketContext) -> AnalysisRecord:
        ingresso = costruisci_ingresso(contesto)
        inizio = self._clock.now()
        chiamata = {
            "provider": self._client.name,
            "model_requested": self._client.model,
            "temperature": self._temperatura,
            "max_output_tokens": self._max_token,
            "started_at": inizio,
        }
        grezzo: str | None = None
        motivi: list[str] = []
        analisi: MarketAnalysis | None = None
        try:
            risposta = await self._client.completa_json(
                sistema=SYSTEM_PROMPT,
                utente=USER_TEMPLATE.format(
                    payload=json.dumps(ingresso.payload, ensure_ascii=False, indent=1)
                ),
                schema=self._schema,
                nome_schema=NOME_SCHEMA,
                temperature=self._temperatura,
                max_output_tokens=self._max_token,
            )
            grezzo = risposta.testo
            chiamata.update(
                model_snapshot=risposta.model_snapshot,
                request_id=risposta.request_id,
                input_tokens=risposta.input_tokens,
                output_tokens=risposta.output_tokens,
                latency_ms=risposta.latency_ms,
            )
        except AIProviderError as errore:
            chiamata["error"] = str(errore)[:500]
            motivi.append(f"Provider AI non disponibile: {errore}")

        if grezzo is not None:
            try:
                analisi = MarketAnalysis.model_validate_json(grezzo)
            except ValidationError as errore:
                motivi.append(
                    "Output non conforme allo schema: "
                    + "; ".join(
                        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                        for e in errore.errors()[:8]
                    )
                )
            if analisi is not None:
                motivi.extend(problemi(analisi, ingresso))

        valida = analisi is not None and not motivi
        record = AnalysisRecord(
            created_at=inizio,
            input=ingresso,
            call=LlmCall(**chiamata),  # type: ignore[arg-type]
            raw_output=grezzo,
            status="AVAILABLE" if valida else "UNAVAILABLE",
            failure_reasons=tuple(motivi),
            analysis=analisi if valida else None,
        )
        percorso = self._diario.scrivi(record)
        logger.info(
            "ai.analisi",
            stato=record.status,
            record=record.record_id,
            regime=str(contesto.regime),
            modello=record.call.model_snapshot or record.call.model_requested,
            motivi=len(motivi),
            file=str(percorso),
        )
        if self._salva is not None:
            await self._salva(record)
        return record
