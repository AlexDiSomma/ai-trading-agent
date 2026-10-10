# ADR 0017 — Contesto globale quantitativo, a dimensioni separate e riproducibile

**Stato:** accettata · **Data:** 2026-10-04 · **Origine:** fase 4

## Contesto

Prima di analizzare un singolo strumento il sistema deve rispondere a "cosa sta
succedendo complessivamente sui mercati?". Vincoli dell'utente: nessun LLM
decisionale, fail closed, nessun look-ahead, dati stale/mancanti/chiusi che
degradano il contesto, e **nessun punteggio globale unico**: casi come "rischio
MIXED, USD forte, oro forte, azioni deboli, volatilità in espansione, incertezza
alta" devono restare rappresentabili.

## Decisione

### 1. Motore puro, separato dalla lettura

`global_market/engine.costruisci()` riceve serie, stati e istante di analisi e
restituisce `GlobalMarketSnapshot` (ingresso) e `GlobalMarketContext` (lettura).
Niente orologio, niente broker, niente casualità: è riusabile nel backtest della
fase 9 senza modifiche. La lettura dal broker sta in `global_market/reader.py`.

### 2. Dimensioni separate

Sentiment di rischio (con le sue evidenze), volatilità, forza delle valute, forza
relativa, direzione delle azioni, direzione dei metalli, intermarket, rischio
macro/eventi, conflitti, lettura multi-timeframe di ogni strumento. Ognuna ha la
sua qualità (`GOOD`, `DEGRADED`, `INSUFFICIENT`) e il suo dettaglio per timeframe
(D1, H4, H1, M15). Il regime è una conclusione *fra* le altre, non una media.

Unità comune: **momentum normalizzato** — rendimento logaritmico su 20 barre
diviso per la volatilità attesa sullo stesso orizzonte. Confrontabile fra EURUSD e
US100; troncato a ±10.

### 3. Regime per regole, in ordine di precedenza

`UNCERTAIN` (qualità insufficiente o segnali deboli) → `EVENT_DRIVEN` (shock di
volatilità: ≥40% degli strumenti oltre il 97° percentile dell'ATR veloce) →
`TRANSITION` (timeframe lenti e veloci oltre soglia in versi opposti) →
`RISK_ON`/`RISK_OFF` (composito oltre ±0.35 e accordo pesato ≥70%) → `MIXED`.
Un conflitto grave, o due medi, trasforma RISK_ON/RISK_OFF in MIXED.

### 4. Qualità e fail closed

* Serie `STALE`, `MISSING`, `INSUFFICIENT_HISTORY` sono **escluse** dai calcoli;
  `CLOSED` e `UNVERIFIED` sono usate ma impediscono `GOOD`.
* Una dimensione è `INSUFFICIENT` sotto il 50% di copertura.
* Fase di mercato diversa da `OPEN_CONFIRMED` o dati simulati: tutte le dimensioni
  al massimo `DEGRADED`.
* `usable_for_decisions` richiede dati reali, `OPEN_CONFIRMED`, qualità non
  insufficiente, regime diverso da `UNCERTAIN`.
* Il calendario economico non esiste ancora: il rischio da calendario è `UNKNOWN`,
  mai "basso", e il contesto è al massimo `DEGRADED`.

### 5. Riproducibilità e look-ahead

* Ogni serie viene **tagliata** all'istante di analisi in ora broker: restano le
  barre la cui *fine* non lo supera. Un test aggiunge barre future e verifica che
  il contesto non cambi.
* `input_fingerprint` (sha256 delle barre usate più la versione del motore) e
  `engine_version` identificano il contesto; stesso ingresso → stesso JSON, byte
  per byte (testato, anche sui dati reali).
* Valori arrotondati a 6 decimali e iterazioni in ordine fisso: l'uscita non
  dipende dall'ordine degli strumenti in ingresso (testato).

### 6. Freschezza misurata sulla barra in formazione

Una serie è fresca se la barra **corrente** si è aperta da meno di una durata di
barra (più due minuti), con età verificata dall'ora broker (ADR 0015). Il primo
criterio pensato — l'età dell'ultima barra *chiusa* — avrebbe dichiarato ferme le
serie H1/H4 per ore a ogni riapertura del lunedì, perché l'ultima barra chiusa è
quella del venerdì.

## Alternative scartate

* **Un `global_score` 0-100.** Esplicitamente escluso dall'utente, e a ragione: la
  media di "azioni forti" e "yen forte" è "neutro", cioè l'informazione opposta.
* **Un LLM che legge i grafici e dice il regime.** Non deterministico, non
  riproducibile nel backtest, e vietato in questa fase.
* **Relazioni intermarket con segno atteso scritto nel codice** ("oro e dollaro
  sono inversi"). Il segno viene dai dati: sul conto reale USDCAD e petrolio oggi
  non hanno relazione, e l'oro è correlato *positivamente* all'S&P 500.

## Conseguenze

* Tutte le soglie sono in `global_market/parametri.py` e sono euristiche non
  validate: la fase 9 le misurerà. Cambiarne una cambia `VERSIONE_MOTORE`.
* Un contesto costa ~220 letture dal terminale (millisecondi ciascuna): cache di
  60 secondi nel servizio.
* `MarketSnapshot` contiene il contesto globale, calcolato prima degli strumenti, e
  ogni `AssetMarketContext` riceve il suo `global_backdrop`.
