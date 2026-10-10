# ADR 0002 — Due cicli a frequenza diversa, non una catena sincrona

**Stato:** accettata · **Data:** 2026-10-04

## Contesto

La pipeline della specifica ha diciassette stadi in cascata. Realizzata come
catena di chiamate sincrone avrebbe tre difetti gravi.

1. **Fragilità.** Il provider di news non risponde e non si leggono le posizioni
   aperte: lo stadio più lento e meno affidabile detta il ritmo a quello più
   critico.
2. **Costo.** Rifare analisi macro e news a ogni ciclo operativo significa
   chiamate LLM e HTTP inutili: il quadro globale non cambia ogni cinque minuti.
3. **Latenza nel punto sbagliato.** Una posizione aperta va rivalutata in
   secondi, il quadro macro in ore. Un unico ciclo deve scegliere la frequenza
   peggiore per entrambi.

## Decisione

Due cicli distinti che comunicano tramite **snapshot versionati con scadenza**,
non con chiamate dirette.

**Ciclo lento (minuti/ore)** — `global_market`, `macro`, `calendar`, `news`,
`intermarket`. Produce un `GlobalMarketContext` con `as_of` e TTL, pubblicato su
Redis e scritto su database.

**Ciclo veloce (secondi/minuti)** — prezzi, regime per asset, scanner, gestione
delle posizioni aperte. *Legge* l'ultimo contesto disponibile e ne verifica la
freschezza (ADR 0007). Contesto scaduto significa WAIT, non "procedo come se
andasse bene".

Il bus eventi di `core/events.py` è in-process nella fase 1; il trasporto passa
a Redis Streams quando i worker diventano processi separati, mantenendo la stessa
interfaccia.

## Conseguenze

Positive: un modulo lento non blocca quelli critici; il backtest riproduce gli
stessi eventi in ordine storico riusando gli stessi consumatori; le chiamate LLM
si fanno quando il contesto cambia, non a ogni tick.

Negative: il contesto letto dal ciclo veloce è per definizione *vecchio di
qualcosa*. È accettabile solo perché l'età è dichiarata e verificata: senza il
contratto di freschezza questa architettura sarebbe peggiore della catena
sincrona.
