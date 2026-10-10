# ADR 0011 — Lo stato del mercato è una deduzione, non un dato del broker

**Stato:** accettata · **Data:** 2026-10-04 · **Origine:** scoperta sul conto XM

## Contesto

Il piano prevedeva una rilevazione dello stato di mercato *basata sul broker*:
sapere dagli orari di sessione se uno strumento è negoziabile adesso. Verificando
sul conto reale, quell'informazione **non è disponibile**:

* `symbol_info_sessions_quote` e `symbol_info_sessions_trade` esistono in MQL5 ma
  **non** nel pacchetto Python `MetaTrader5` (verificato: `dir(mt5)` non contiene
  nessuna funzione con "session" nel nome);
* non esiste nemmeno una funzione per l'**ora corrente del server**;
* i campi `session_open` e `session_close` di `symbol_info` sono **prezzi**, non
  orari. Il nome invita all'errore: chi li usasse come orari otterrebbe 4177.33.

Quindi l'assunzione di partenza era sbagliata. Le alternative erano tre: fingere
che l'informazione ci sia e dedurla senza dirlo; rinunciare alla rilevazione; o
dedurla dichiarando che è una deduzione.

## Decisione

Lo stato della quotazione separa **fatti del broker** da **deduzioni nostre**, e
ogni valutazione porta `is_inference`.

Fatti del broker, accertati:

| Stato | Fonte |
|-------|-------|
| `NOT_TRADEABLE` | `trade_mode == 0` |
| `CLOSE_ONLY` | `trade_mode == 3` |
| `NO_QUOTE` | bid o ask a zero |
| `STALE` | età del tick oltre il limite configurato |
| `WIDE_SPREAD` | spread oltre il limite (ADR 0013) |

Deduzione, dichiarata come tale:

| Stato | Prove richieste |
|-------|-----------------|
| `MARKET_CLOSED` | tick più vecchio di 15 minuti **e** nessuna barra nuova |

Le due prove devono concordare. Con la sola età del tick si confonderebbe un
flusso prezzi interrotto con un mercato chiuso, e sono guasti diversi: il primo
richiede attenzione, il secondo è normale di notte.

La **fase complessiva** del mercato si deduce dall'insieme degli strumenti, non da
uno: su XM gli indici chiudono mentre il forex continua, quindi "US100Cash fermo"
non significa "mercato chiuso". Serve l'insieme anche per distinguere un mercato
chiuso da una connessione caduta: nel primo caso qualcosa resta aperto quasi
sempre, nel secondo si fermano tutti insieme — e quando si fermano tutti, il
messaggio dice entrambe le possibilità invece di scegliere.

**L'ora del broker** si misura da una quotazione fresca (il suo timestamp è in
ora-broker, la differenza con l'ora di sistema è lo scarto) e si conserva con la
sua età. Se non è mai stata misurata, si dichiara sconosciuta: non si inventa.
Due limiti dichiarati: lo scarto deve essere plausibile come fuso orario (±14 ore,
altrimenti è un tick vecchio travestito), e una misura di più di dodici ore non è
affidabile perché potrebbe stare dall'altra parte di un cambio di ora legale.

## Conseguenze

Positive: nessuna affermazione sul mercato viene presentata come certezza quando
è una deduzione. Chi legge un `QuoteStatus` sa sempre di che tipo di informazione
si tratta, e il database conserva la distinzione (`quote_is_inference`).

Negative: la deduzione ha una latenza. A mercato appena chiuso servono quindici
minuti perché lo stato passi da `STALE` a `MARKET_CLOSED`. È accettabile perché
entrambi gli stati impediscono di operare: il fail closed non dipende dalla
precisione della deduzione.

**Resta da fare (fase 4):** ricavare il calendario settimanale osservando quali
ore hanno barre negli ultimi mesi. È l'unico modo di avere orari di sessione
senza l'API, e permetterebbe di anticipare una chiusura invece di dedurla dopo.
