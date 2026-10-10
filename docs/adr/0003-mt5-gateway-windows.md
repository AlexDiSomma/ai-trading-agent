# ADR 0003 — MetaTrader 5 gira sull'host Windows, non in Docker

**Stato:** accettata · **Data:** 2026-10-04

## Contesto

La specifica chiede sia Docker Compose sia il pacchetto Python `MetaTrader5`. Le
due cose non convivono:

- `MetaTrader5` esiste **solo per Windows** e comunica via IPC con il terminale
  grafico, che deve essere installato, aperto e collegato al conto;
- è **sincrono, bloccante e non thread-safe**, e mantiene una sola connessione
  per processo;
- `mt5.initialize()` avvia o si attacca a un'applicazione desktop: in un
  container Linux non c'è nulla a cui attaccarsi.

Soluzioni con Wine in container esistono, ma aggiungono un livello di emulazione
fra il sistema operativo e gli ordini reali. Non è il punto in cui accettare
complessità.

## Decisione

Topologia ibrida.

- **In Docker:** PostgreSQL, Redis e, facoltativamente, API e worker che non
  parlano con il broker. Nel container `MT5_USE_MOCK=true`.
- **Sull'host Windows:** il processo che possiede il terminale MT5. Nelle fasi
  1-2 è lo stesso backend avviato con `uvicorn` fuori da Docker. Dalla fase 7,
  quando i worker si moltiplicano, diventa un **unico** processo `mt5-gateway`
  che espone un'API interna: resta vero che una sola connessione per volta parla
  con il terminale.

Nel codice l'isolamento è già pronto: `MT5Connector` serializza ogni chiamata su
un executor a thread singolo con un lock, e dipende dal protocollo `MT5Terminal`
e non dal modulo globale.

## Conseguenze

Positive: niente emulazione sul percorso degli ordini; il resto del sistema resta
portabile e testabile su qualunque sistema operativo; il collo di bottiglia MT5 è
esplicito e in un punto solo.

Negative: il deployment non è un singolo `docker compose up`. Il PC Windows con
il terminale aperto diventa un componente dell'infrastruttura, e il suo riavvio
va gestito con la riconciliazione delle posizioni all'avvio (fase 8).

**Nota sulla versione di Python:** il pacchetto ha wheel per CPython 3.14
(verificato su questa macchina: `metatrader5-5.0.6231-cp314`). Il vincolo è il
sistema operativo, non la versione dell'interprete.
