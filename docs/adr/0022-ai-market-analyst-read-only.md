# ADR 0022 — AI Market Analyst in sola lettura (fase 5A)

**Stato:** accettata · **Data:** 2026-10-05 · **Origine:** richiesta dell'utente
(fase 5A) · **Collegata:** ADR 0008 (determinismo AI)

## Contesto

Prima di dare all'AI qualsiasi ruolo operativo bisogna sapere se le sue analisi
valgono qualcosa. Serve quindi un analista che **non può agire** e le cui analisi
siano **misurabili dopo, senza hindsight**.

## Decisione

### 1. Solo analisi, per costruzione

`backend/ai/` riceve un `GlobalMarketContext` e restituisce un `AnalysisRecord`. Il
package **non importa** broker, rischio, esecuzione o ordini (test AST). Il contratto
`MarketAnalysis` (`extra="forbid"`, frozen) contiene solo: sintesi, lettura del
regime, temi dominanti, scenari con probabilità, evidenze a favore e contrarie,
incertezze con l'informazione mancante, strumenti da osservare, condizioni di
invalidazione, confidence. Non ha campi per prezzi d'ingresso, stop, size, direzione
d'ordine; un campo in più rende l'output invalido. Una regex rifiuta anche contenuto
operativo nel testo libero ("buy", "stop loss", "lotti", …). Gli endpoint
`/api/ai/market-analysis` sono solo POST (analizza) e GET (diario).

### 2. Output invalido = analisi non disponibile

Il JSON viene validato da Pydantic e poi da `ai/validation.py`:

- `engine_regime_acknowledged` uguale al regime del motore (l'AI non lo sostituisce);
- ogni `refs` esiste negli `allowed_refs` dell'ingresso (niente fonti inventate);
- strumenti solo dell'universo;
- regime `MIXED` / `TRANSITION` / `UNCERTAIN` ⇒ almeno un'evidenza contraria;
- con limitazioni note, almeno un'incertezza che nomina l'informazione mancante;
- confidence `HIGH` solo con dati `GOOD`; regime `UNCERTAIN` ⇒ confidence `LOW`;
- somma delle probabilità degli scenari ≤ 1;
- **condizioni di invalidazione verificabili da un programma** (sotto).

Se una regola fallisce: `status = UNAVAILABLE`, `analysis = None`, motivi elencati,
risposta grezza conservata. Nessun ripiego "meglio di niente"; senza provider
(`NullLlmClient`) nessuna analisi finta.

### 3. Condizioni di invalidazione verificabili

La fase 9 dovrà dire se un'analisi era giusta. Una condizione in prosa ("se il quadro
si chiarisce") si giudica solo rileggendola col senno di poi. Ogni condizione ha
quindi campi meccanici `subject` / `metric` / `direction` / `threshold` /
`target_state` / `horizon_hours`, con combinazioni ammesse:

| metric | subject | direzioni |
|---|---|---|
| PRICE | strumento dell'universo | ABOVE / BELOW + soglia |
| CURRENCY_STRENGTH | valuta (o `currency_strength.X`) | soglia, o stato STRONG/NEUTRAL/WEAK/MIXED |
| REGIME | `regime`, `risk_sentiment` | stato `GlobalRegime` |
| VOLATILITY | `implied_volatility`, `volatility`, strumento | soglia, o livello/tendenza |
| CORRELATION | riferimento `intermarket.…` dell'ingresso | soglia in [−1, 1], o `RelationshipState` |
| EVENT | riferimento `calendar.events.…` dell'ingresso | fase `EventPhase` |

Semantica: `ABOVE`/`BELOW` = soglia superata almeno una volta entro l'orizzonte;
`CHANGES_TO`/`OCCURS` = stato osservato almeno una volta entro l'orizzonte;
`NOT_REACHED` = alla fine dell'orizzonte lo stato è diverso da `target_state`.

**Trovato dal vivo.** Con il primo contratto il modello scriveva "CORRELATION BELOW
0.41" su un *conflitto* e "REGIME CHANGES_TO" senza destinazione. Aggiunto
`target_state` e la tabella sopra (prompt 1.2.0). Il modello ha poi scritto, per lo
scenario risk-on, l'invalidazione `REGIME CHANGES_TO RISK_ON` con la descrizione "non
si sposta verso risk-on": il contratto non sapeva dire "se **non** succede". Aggiunta
la direzione `NOT_REACHED` (prompt 1.3.0, output 1.2.0).

### 4. Versionato e immutabile

Ogni record contiene: ingresso completo (payload, `allowed_refs`, universo,
limitazioni, `input_sha256`), versioni di ingresso, output e prompt, modello richiesto
e snapshot effettivo, temperatura, token, latenza, risposta grezza, esito,
`integrity_sha256` sull'intero record. Il diario (`var/ai_journal`) scrive ogni record
con apertura esclusiva (`"x"`, mai sovrascritto) e alla lettura ne ricalcola
l'integrità; la tabella `ai_analyses` (migrazione 0004) ne tiene una copia
interrogabile, best effort (ADR 0019). `evaluation_due()` dà, per scenario, l'istante
dopo il quale l'esito è giudicabile.

### 5. Provider

OpenAI Responses API con `json_schema` strict (`gpt-5.4-mini`, snapshot registrato,
temperatura 0), verificato dal vivo. Client Anthropic (tool use) pronto ma non
verificato: nel `.env` manca la chiave. Lo schema viene reso "strict"
(`additionalProperties: false`, tutti i campi richiesti) senza toccare i nomi dei
campi (un campo `title` era stato cancellato per errore: 400 dal provider).

## Alternative scartate

- **Testo libero valutato da una persona**: non misurabile in modo ripetibile.
- **Riparare l'output invalido** (togliere refs inventati, abbassare la confidence):
  produrrebbe analisi che il modello non ha scritto.
- **Far calcolare all'AI il regime**: il regime resta del motore deterministico;
  l'AI lo commenta e può dissentire solo a parole (`analyst_view_on_regime`).

## Conseguenze

- Un'analisi può essere UNAVAILABLE per un dettaglio formale: è voluto. La frequenza
  delle UNAVAILABLE è essa stessa una misura del modello.
- La fase 9 potrà valutare scenari e invalidazioni con i soli dati successivi
  all'`as_of`, leggendo i campi meccanici.
