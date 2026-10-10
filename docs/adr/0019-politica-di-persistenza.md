# ADR 0019 — Persistenza: best effort per osservare, obbligatoria per operare

**Stato:** accettata · **Data:** 2026-10-05 · **Origine:** fase 4.5, richiesta
dell'utente

## Contesto

Nella fase 4, con PostgreSQL spento, le scritture dei contesti venivano saltate per
30 secondi dopo un guasto, così che l'osservazione del mercato non aspettasse un
database assente. È giusto per l'intelligence read-only. Non lo sarà per lo stato
del rischio (budget giornaliero, posizioni approvate) e per gli intenti di esecuzione
(idempotenza degli ordini): uno stato che vive solo in memoria si azzera al riavvio,
e il limite dell'utente raddoppia in silenzio (ANALISI §1.5).

## Decisione

Due requisiti espliciti (`PersistenceRequirement`):

* **`BEST_EFFORT`** — intelligence read-only, contesti, specifiche. Senza database si
  degrada: le scritture falliscono o si saltano, vengono contate, System Health le
  mostra come componente **non critico** ("Persistenza contesti (best effort)").
  L'osservazione continua.
* **`REQUIRED`** — stato del rischio (fase 6) ed esecuzione (fase 11). Prima di ogni
  operazione si chiama `MarketPersistence.require(REQUIRED, operazione)`, che solleva
  `PersistenceUnavailableError` (`blocks_trading = True`) se la persistenza non è
  affidabile. **Fail closed.**

"Affidabile" = almeno una scrittura riuscita e nessun guasto dopo di essa. Un
database mai provato **non** è affidabile: "nessun errore finora" non è una prova.

Già oggi PostgreSQL è un componente **critico** in System Health: con il database
giù `can_trade` è falso. La guardia `REQUIRED` è il punto in cui le fasi 6 e 11 lo
faranno rispettare operazione per operazione.

## Conseguenze

* Test: con database mai raggiunto `REQUIRED` solleva e `BEST_EFFORT` no; dopo una
  scrittura riuscita `REQUIRED` passa; dopo un nuovo guasto torna a sollevare.
* Il Risk Engine della fase 6 dovrà chiamare la guardia prima di aggiornare il
  budget; l'Execution Engine della fase 11 prima di registrare un intento.
