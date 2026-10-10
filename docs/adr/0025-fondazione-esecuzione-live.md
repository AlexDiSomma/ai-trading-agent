# ADR 0025 — Fondazione dell'esecuzione reale, LIVE impossibile senza tutti i gate

**Stato:** accettata · **Data:** 2026-10-06 · **Origine:** richiesta dell'utente (fase 11)
· **Modifica:** ADR 0004 (sola lettura per costruzione) · **Collegate:** 0005, 0006, 0019, 0024

## Decisione

**Chi può inviare ordini.** Solo `execution/mt5_terminal.py` chiama `order_check` e
`order_send` sul pacchetto `MetaTrader5`, attraverso il thread serializzato del
connettore (`run_serialized`). Solo `execution/gateway.py` chiama il terminale. Il
test di sola lettura ora controlla **tutto** il backend e ammette solo questi due file;
il connettore resta privo di metodi di scrittura e il terminale finto pure.

**Ordine dei controlli nel gateway (fail closed).** Autorizzazione LIVE (tutti i gate,
rivalutati a ogni invio) → kill switch globale → circuit breaker → archivio PostgreSQL
durevole e sano → nessun ordine con esito ignoto → `RiskApproval` valida (emessa da
questo Risk Engine, non scaduta, SL/TP dal lato giusto e oltre lo stops level).
Kill switch e breaker bloccano le aperture, non chiusure e protezioni (ADR 0006).

**Idempotenza.** Chiave deterministica dal piano, registrata `UNIQUE` **prima**
dell'invio e scritta nel commento MT5 (`ata:<chiave>`, magic `26_10_06`). Una chiave già
presente restituisce il record e non invia nulla.

**Esiti.** `DONE`/`DONE_REMAINDER` → volume e prezzo reali (esecuzione parziale gestita);
`REQUOTE`/`PRICE_CHANGED` → nuovo prezzo solo entro lo slittamento approvato e la
validità (30 s), al massimo 3 tentativi; rifiuti → registrati, 3 consecutivi aprono il
breaker; timeout, connessione persa, nessuna risposta → esito **ignoto**: mai reinviare,
si riconcilia con posizioni e storico deal tramite il commento. Broker irraggiungibile
anche in riconciliazione → ordine `UNKNOWN`, breaker aperto, nessuna nuova apertura.

**SL/TP lato server obbligatori.** Inviati nella richiesta; dopo l'esecuzione si
verifica la posizione; se mancano si impostano (`TRADE_ACTION_SLTP`); se non si riesce,
la posizione si chiude per protezione e il breaker scatta.

**Rischio dal conto reale** (`risk/live_engine.py`): equity, rischio aperto (perdita
allo stop dal broker; posizione senza stop = blocco), P/L del giorno e della settimana
broker dallo storico deal più il flottante, picco di equity ricostruito da 90 giorni di
deal. Size dal broker (`size_for_risk`). Nulla in memoria: un riavvio non azzera i limiti.

**Gestione posizioni reali** (`execution/manager.py`): riconciliazione, chiusure lato
server lette dallo storico (`[sl`/`[tp` nel commento), invalidazioni e scadenza →
chiusura, SL/TP verificati a ogni ciclo, posizione del sistema sconosciuta → breaker.

**Circuit breaker** persistente su file, riarmo solo manuale con motivo.

**Gate di attivazione** (`execution/readiness.py`), tutti insieme: ≥30 trade paper chiusi
e **validati** (coerenza di P/L, R, tempi, uscite); zero errori critici nel ciclo
paper; PostgreSQL sano con la migrazione 0005; MT5 reale sano con trading e algo trading
consentiti; Risk Engine in grado di ricostruire stato e budget; esercitazione del kill
switch superata negli ultimi 7 giorni; breaker chiuso; nessun ordine ignoto;
`APP_MODE=LIVE` e `APP_ENABLE_LIVE_TRADING=true`; **conferma dell'utente**: file
`var/LIVE_CONFIRMATION.json` scritto a mano con la frase esatta, il login del conto
connesso e una data entro 24 ore. Nessuna API può crearlo.

## Conseguenze

Con la configurazione attuale (`PAPER`, LIVE disabilitato) il gateway esiste ma ogni
invio è rifiutato prima di toccare il terminale. I test simulano timeout dopo e senza
esecuzione, disconnessione durante la riconciliazione, riavvio con ordini in volo,
requote, esecuzione parziale, rifiuti, SL/TP mancanti, posizioni orfane: in nessun caso
parte un secondo ordine.
