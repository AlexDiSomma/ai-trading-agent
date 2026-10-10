# Database

PostgreSQL conserva **fatti e decisioni**, non la materia prima dei prezzi
(ADR 0009). Lo schema cresce con le fasi: una tabella nasce insieme al modulo
che la scrive, perché una tabella creata in anticipo prende la forma di
un'ipotesi invece di quella dell'uso reale.

Migrazioni con Alembic, in `backend/database/migrations/versions/`.

---

## Esistenti (migrazione `0001_fondazione`)

### `system_events` — audit trail
Ogni evento del bus, per sempre. Non è un log: i log si ruotano e si perdono.
Serve a rispondere settimane dopo a domande come *"il 12 marzo il kill switch era
attivo?"*.

Colonne chiave: `event_type`, `occurred_at`, `severity`, `mode`,
`correlation_id`, `payload` (JSON). Indici su `(event_type, occurred_at)` e su
`correlation_id`.

`mode` non è ridondante: un evento letto senza sapere se era PAPER o LIVE porta a
conclusioni sbagliate su cosa è accaduto al conto.

### `config_versions` — quali limiti erano attivi
Fotografia immutabile dei limiti di rischio, con un'impronta del contenuto: se i
limiti non cambiano non nasce una versione a ogni riavvio. I piani operativi
faranno riferimento alla versione attiva, così una perdita non viene mai
giudicata con i limiti di oggi se era stata decisa con quelli di ieri.

### `accounts` — storico dello stato del conto
Serie temporale di equity, saldo e margine. Drawdown e perdita settimanale si
misurano sull'equity nel tempo, e dopo un riavvio l'unico modo di averla è
averla scritta. La colonna `simulated` separa i dati dal terminale finto: una
statistica che mescola equity simulata e reale non significa nulla.

### `symbols` — specifiche di contratto nel tempo
XM può cambiare spread, stop level o volume minimo. Se cambiano, una size
calcolata ieri non è valida oggi, e senza storico non si capirebbe perché un
ordine è stato rifiutato.

---

## Previste, fase per fase

### Fase 4 — il mondo
- `global_contexts` — ogni `GlobalMarketContext` con `as_of`, regime, confidence
  e sintesi.
- `macro_events` — calendario: timestamp, paese, valuta, evento, importanza,
  `previous`, `forecast`, `actual`. La **sorpresa** si calcola, non si salva.
- `news` — notizie normalizzate e deduplicate, con sentiment, asset coinvolti e
  numero di fonti indipendenti.

### ✅ Fase 3 — analisi (migrazione `0002_contesto_mercato`)
- `asset_market_contexts` — l'`AssetMarketContext` completo. Colonne per ciò su
  cui si interroga (simbolo, regime dominante, allineamento, stato quotazione,
  qualità, simulato), JSON per la ricostruzione integrale. La colonna
  `quote_is_inference` conserva la distinzione fra quello che il broker ha
  dichiarato e quello che abbiamo dedotto (ADR 0011): senza di essa, rileggendo lo
  storico, le due cose sarebbero indistinguibili.
- `symbols` arricchita con `path`, `asset_class`, `trade_mode` e
  `value_per_price_unit`. L'ultima è conservata perché cambia con il cambio ed è
  l'ingresso del dimensionamento: una size contestata va rivista con il valore di
  allora, non con quello di oggi.

### Fase 6 — rischio
- `risk_decisions` — ogni verdetto con il limite che ha deciso l'esito.
  Comprende i rifiuti: la domanda *"perché non ha operato"* è tanto importante
  quanto *"perché ha operato"*.
- `risk_budget_state` — stato persistente di budget e circuit breaker. Senza
  persistenza un riavvio azzererebbe il limite giornaliero (ANALISI §1.5).

### Fase 7 — decisione
- `ai_decisions` — la `MarketDecision` completa, confidence inclusa, legata al
  contesto che l'ha prodotta.
- `ai_requests` — *non nella specifica, necessaria*: prompt (impronta), modello,
  token, costo, latenza, esito. Serve al limite di costo giornaliero e alla cache
  che rende riproducibili i backtest (ADR 0008).
- `prompt_versions` — *non nella specifica*: versione del prompt, per invalidare
  la cache in modo esplicito.
- `trade_plans` — piani operativi, salvati **prima** dell'esecuzione.

### Fase 8 — esecuzione e gestione
- `execution_intents` — *non nella specifica, necessaria*: scritta prima
  dell'invio, è ciò che garantisce l'esecuzione al massimo una volta (ADR 0005).
- `orders`, `positions`, `trades`, `position_events` — ciclo di vita completo,
  con il motivo di ogni modifica.

### Fase 9 — apprendimento
- `performance_daily`, `performance_strategy` — metriche su finestre mobili.
- `calibration` — *non nella specifica*: confidence dichiarata contro esito
  reale, per correggere la scala del rischio sui dati e non sull'intuizione.
- `backtests` — configurazione, periodo, metriche, versione del codice e dei
  prompt usati.

---

## Convenzioni

**Tutti i timestamp sono `timezone=True`.** Una colonna naive su dati di trading
significa, prima o poi, un confronto fra ora broker e ora UTC che sembra
funzionare e sposta tutto di due ore (ADR 0010).

**`as_of` / `occurred_at` ≠ `created_at`.** Il primo è il momento del fatto, il
secondo quello della registrazione. Tenerli separati permette di accorgersi di un
ritardo di scrittura, che è a sua volta un sintomo da indagare.

**Convenzione di nomi sui vincoli** (`backend/models/base.py`): senza di essa
PostgreSQL inventa i nomi degli indici e Alembic genera migrazioni che non sanno
cosa rinominare.

**Niente cancellazioni sui dati storici.** Le decisioni sbagliate restano: sono
il materiale su cui si misura il sistema. Una scelta superata si supera con una
riga nuova, non cancellando quella vecchia — la stessa regola di `DECISIONI.md`.

---

## Comandi

```bash
# applicare le migrazioni
alembic upgrade head

# vedere lo SQL senza eseguirlo
alembic upgrade head --sql

# nuova migrazione (dopo avere aggiunto o importato un modello in models/__init__.py)
alembic revision --autogenerate -m "descrizione"

# tornare indietro di una
alembic downgrade -1
```

Un modello non importato in `backend/models/__init__.py` **non esiste** per
Alembic: l'autogenerazione lo ignora e la tabella manca in produzione.
