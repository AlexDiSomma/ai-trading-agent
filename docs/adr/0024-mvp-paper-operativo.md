# ADR 0024 — MVP operativo in PAPER: scheduler, piano deterministico, esecuzione simulata

**Stato:** accettata · **Data:** 2026-10-05 · **Origine:** richiesta dell'utente (MVP
end-to-end) · **Collegate:** ADR 0002, 0004, 0019, 0023

## Decisione

**Catena.** XM → contesto globale → contesto per strumento → scanner → piano quant
(conto QUANT) e tesi AI (conto AI) → `TradePlan` deterministico → gate di rischio →
ingresso paper → gestione (SL/TP/invalidazione/tempo/swap) → uscita → P/L in R e
valuta → dashboard.

**Scheduler.** Tre cicli asincroni nel processo del backend (`services/autopilot.py`),
mai dentro una richiesta HTTP: scansione (`PAPER_SCAN_INTERVAL_SECONDS`, 300),
gestione (`PAPER_MANAGE_INTERVAL_SECONDS`, 20), analisi AI del contesto (3600). Un
errore in un ciclo è registrato e mostrato in dashboard; il ciclo riprende.

**Decisioni tolte al modello (definitive).**
* RR: sempre calcolato dai prezzi (`paper.geometry.rr`). Il contratto della tesi 2.0
  non ha più `expected_rr`.
* MARKET/STOP/LIMIT: il modello indica solo NOW o un livello (`AT_LEVEL`); il tipo lo
  decide `geometry.tipo_ingresso` da direzione, livello e prezzo (entro 0,1 ATR H1 →
  MARKET).

**Ambiguità dentro la barra (definitiva, una sola regola).** `geometry.risolvi_barra`:
apertura oltre un livello → quel livello (stop all'apertura, peggiore); altrimenti
stop e target nella stessa barra → stop, marcato `ambiguous`. La gestione paper usa
barre **M1** (più fini delle M5 dello shadow) e poi il tick corrente; lo stesso codice
è usato dall'evaluator shadow.

**Costi paper.** Spread (LONG entra all'ask ed esce al bid, SHORT il contrario; sulle
barre bid lo spread della barra si aggiunge al lato ask), slittamento
(`PAPER_SLIPPAGE_POINTS`, ingressi a mercato/STOP e uscite allo stop), commissioni
(`PAPER_COMMISSION_PER_LOT` per lato), swap dal broker (`swap_long/short`, modalità in
punti, triplo nel giorno dichiarato) a ogni mezzanotte broker. Profitto e size dal
broker (`BrokerValuationService`, `TradeIntentBuilder`): nessuna formula nuova.

**Gate di rischio** (`risk/gate.py`, puro, senza import dell'AI): kill switch, dati e
quotazione validi, sessioni e giorni, finestra di evento, RR minimo, stop in ATR,
spread/ATR, una posizione per strumento, massimo posizioni, rischio per trade e
aperto, perdita giornaliera, drawdown massimo.

**Persistenza.** File JSON con scrittura atomica (`os.replace`) più diario eventi
in aggiunta (`var/paper/`). PostgreSQL resta facoltativo: un conto che apre posizioni
non può dipendere da un servizio spesso spento. Unico scrittore: lo scheduler, sotto
lock.

**Esperimenti senza blocchi.** Ogni `TradePlan` porta `strategy_version`
(`quant-baseline-1.0.0`, `ai-thesis|trade-thesis-prompt-2.0.0`); la performance è
divisa per versione, ma nessuna versione blocca l'uso della piattaforma.

**LIVE.** Resta impossibile: nessun codice d'invio ordini, rotte senza scrittura sul
conto (test sulla superficie OpenAPI), esecuzione solo nel registro paper.
