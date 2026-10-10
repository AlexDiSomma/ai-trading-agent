# ADR 0023 — AI Trade Thesis in shadow mode, tre gruppi, esiti senza hindsight (fase 5B)

**Stato:** accettata · **Data:** 2026-10-05 · **Origine:** richiesta dell'utente (fase 5B)
· **Collegate:** ADR 0004 (sola lettura), 0022 (AI analyst)

## Contesto

Dopo la fase 5A l'AI sa analizzare il contesto. La domanda ora è un'altra: **le sue
decisioni operative aggiungono valore a una regola quantitativa?** Per rispondere
servono decisioni registrate *prima* di conoscere l'esito, un termine di confronto
fisso e una misura dell'esito che non dipenda da chi la legge. Niente di tutto questo
deve poter toccare il conto.

## Decisione

### 1. Tre gruppi, sempre separati

| Gruppo | Cosa decide | Da dove |
|---|---|---|
| **A — QUANT_ONLY** | baseline deterministica | `shadow/quant_baseline.py` |
| **B — QUANT_AI_FILTER** | piano di A, tenuto solo se l'AI lo approva | `FilterVerdict` |
| **C — AI_THESIS** | tesi dell'AI, costruita **senza vedere** A | `TradeThesis` |

Il sistema non aveva un Quant Engine che proponesse operazioni (lo scanner è la fase
5). La baseline A è quindi **nuova e dichiarata come tale**: continuazione della
tendenza multi-timeframe (bias D1+H4 = bias H1, |allineamento| ≥ 0,35, nessun
conflitto), ingresso MARKET, stop 1,5 ATR H1, target 2R, 24 ore. Non è ottimizzata e
non deve esserlo: deve essere fissa e versionata (`quant-baseline-1.0.0`).

Due chiamate indipendenti per decisione: la tesi (C) e, solo se A propone
un'operazione, il filtro (B). La tesi non vede il piano di A, così C non è contaminato.

### 2. TradeThesis: ogni claim con evidenze, ogni condizione tipizzata

La tesi contiene decisione LONG/SHORT/**WAIT**, scenario, setup, ingresso
(MARKET/STOP/LIMIT con validità), condizioni d'ingresso, stop, target, condizioni di
invalidazione e di target, RR atteso, evidenze a favore e contrarie, incertezze,
orizzonte, etichetta di confidence. Niente volume, size, leva, margine
(`extra="forbid"` + regex sul testo).

**Evidenze.** Ogni dato del contesto dello strumento ha un `evidence_id` stabile
(`asset.XAUUSD.H1.trend`, `asset.EURUSD.H4.level.R1`, …), unito ai riferimenti globali
della 5A. Le affermazioni **decisive** (scenario, setup, ingresso, stop, target,
condizioni, evidenze a favore di un LONG/SHORT) con id inesistenti o senza id rendono
la tesi `REJECTED`. Quelle **non decisive** (evidenze contrarie, rischi del filtro)
restano nel record **marcate unsupported**. Un LONG/SHORT con meno di due evidenze a
favore sostenute è `REJECTED`.

**Condizioni.** `PriceCondition` = strumento, timeframe (M5…D1), misura (`CLOSE` o
`TOUCH`), operatore, livello, evidenze. Si verificano sulle barre reali di qualsiasi
strumento dell'universo. Un'invalidazione o un target **già veri alla decisione**
sono rifiutati: non descrivono un evento futuro. Le condizioni su indicatori (RSI,
forza valutaria, regime) sono rimandate: richiederebbero di ricalcolare il motore su
ogni barra futura.

**Geometria, controllata sui numeri.** Lati di stop e target; STOP/LIMIT dal lato
giusto del prezzo; stop fra 0,3 e 8 ATR H1 e oltre tre spread; ingresso entro 3 ATR;
target entro 15 ATR; RR dichiarato uguale a quello calcolato (±10%).

### 3. Fail closed

| Situazione | Gruppo B | Gruppo C |
|---|---|---|
| dati dello strumento non GOOD, quotazione non valida dal vivo, ATR assente | AI non interpellata → `REJECTED` | AI non interpellata → `REJECTED` |
| provider assente, JSON non conforme | `REJECTED` | `REJECTED` |
| regole violate (evidenze, geometria, condizioni) | `REJECTED` | `REJECTED` |
| verdetto REJECT | `FILTERED` | — |
| WAIT valido | — | `WAIT` |

Solo `VALID` + APPROVE (B) o `VALID` + LONG/SHORT (C) producono un piano `ACTIVE`.

### 4. ShadowTradePlan: nessuna strada verso l'esecuzione

Il piano ombra è in **prezzi e R**: nessun volume, conto, valuta, budget. Non è un
`TradeIntent` e non passa dal Risk Engine. I package `shadow/` e `ai/thesis/` non
importano broker, rischio, valutazione, trade planner, servizi né esecuzione (test
AST); `backend/execution` è vuoto e `order_send` non esiste (ADR 0004).

### 5. Snapshot immutabili e OutcomeEvaluator

* `DecisionSnapshot` **alla decisione**: contesto dello strumento, catalogo delle
  evidenze, numeri di riferimento, record AI con risposta grezza e hash, i tre piani.
* `OutcomeSnapshot` **dopo**: barre M5 successive alla decisione (con hash) ed esito
  di ogni piano. È un file nuovo a ogni valutazione; la decisione non si tocca.

L'evaluator usa solo barre **iniziate dopo** la decisione e misura: innesco (o motivo
del mancato innesco), TP, SL, invalidazione, MAE, MFE, R lordo, costo dello spread
in R, R netto, tempo all'esito. Regole conservative: SL e TP nella stessa barra →
SL; gap oltre lo stop → uscita all'apertura; l'MFE si ferma al target. Per WAIT e
FILTERED misura comunque il movimento del mercato in ATR: serve a sapere se
un'attesa ha evitato una perdita o mancato un'occasione.

### 6. Replay senza hindsight

I dati si leggono una volta; a ogni istante passato T si costruisce il contesto con
le sole barre chiuse entro T (`features.taglia`, la regola del motore globale) e i dati
esterni con `available_at <= T`. Il contesto di replay ha quotazione `UNKNOWN`
dichiarata come deduzione e `usable_for_decisions = False`: non è mai scambiabile per
un contesto operativo. Il modello (`gpt-5.4-mini`, snapshot 2026-03-17) è anteriore
agli istanti di replay: non può "ricordare" i prezzi di fine settembre 2026.

### 7. Confidence

L'etichetta LOW/MEDIUM/HIGH è registrata e mai usata per decidere né letta come
probabilità. Il report ne calcola soltanto la tabella di calibrazione (n, quota di
R > 0, R medio per etichetta).

### 8. Metriche per decidere se l'AI aggiunge valore

* per gruppo: innesco, TP/SL/invalidazione/tempo, R medio e mediano, R totale,
  profit factor, drawdown in R, MAE/MFE, tempo all'esito;
* **filtro**: R di A approvate contro R di A respinte (l'esito controfattuale è
  noto), differenza con intervallo bootstrap al 95%;
* **tesi**: differenza appaiata C − A per decisione (WAIT = 0 R) con intervallo
  bootstrap, vinte/perse/pari;
* affidabilità: quote VALID/REJECTED/UNAVAILABLE e affermazioni unsupported;
* verdetti solo con almeno 30 operazioni chiuse (filtro) e 50 decisioni appaiate
  (tesi); sotto, `INSUFFICIENT_SAMPLE`.

### 9. Correzioni dopo il primo giro reale (pipeline 1.1 e 1.2)

* **Filtro: verdetto per ultimo.** Nella 1.0 il verdetto era il primo campo: il modello
  si impegnava e poi giustificava (10 REJECT su 11, metà con motivi favorevoli al
  piano, metà leggendo "long" un piano SHORT). Ora `plan_direction_read` (verificato
  contro il piano), `points_for`, `points_against`, poi `verdict`; REJECT richiede un
  punto contro sostenuto, APPROVE uno a favore. Il piano è in testa all'ingresso.
* **Tesi: decisione dopo le evidenze**, per la stessa ragione.
* **Ingresso: percorso = id.** Le sezioni globali stanno al primo livello; sotto un
  contenitore il modello citava `global_context.regime`, che non esiste.
* **`pipeline_version`** su ogni decisione (baseline, ingresso, prompt, schemi). Le
  metriche usano una sola versione; il diario non rilegge le altre con i contratti
  nuovi, le conta. I record precedenti restano, immutati, come prova.

### 10. Primo giro di riferimento (pipeline 1.2, 2026-10-05)

6 istanti di replay (29/9-2/10) + 1 dal vivo × 4 strumenti = 28 decisioni.

| | A quant | B quant + filtro | C tesi AI |
|---|---|---|---|
| piani attivi | 12 | 12 | 2 |
| WAIT / respinti | 16 / 0 | 16 / 0 | 20 / 6 |
| chiusi (TP/SL/inval/tempo) | 10 (4/5/0/1) | 10 (4/5/0/1) | 2 (1/0/1/0) |
| R totale netto | +2,19 | +2,19 | +0,36 |

Filtro: 12 APPROVE su 12 (nella 1.0: 10 REJECT su 11) → B = A. Tesi − quant appaiata:
−0,07 R, intervallo 95% [−0,44; +0,27], n = 26 → `INSUFFICIENT_SAMPLE`. I numeri non
dicono nulla sul valore dell'AI: una settimana, un solo regime (dollaro forte, tutte le
operazioni di A sono SHORT), calendario non disponibile per i replay, baseline non
ottimizzata. Servono a verificare che la catena funzioni, non a giudicarla.

## Alternative scartate

* **Far vedere all'AI il piano di A anche per la tesi**: C diventerebbe una variante
  di B e il confronto C − A perderebbe senso.
* **Riparare una tesi quasi valida** (ricalcolare il RR, spostare lo stop): sarebbe una
  tesi che il modello non ha scritto.
* **Usare la confidence come peso o probabilità**: non è calibrata; lo diventerà solo
  se i dati lo dimostreranno.
* **Simulare con un volume e il P/L in euro**: introdurrebbe la valutazione monetaria
  (ADR 0012) in un package che deve restare lontano dal conto; R è sufficiente.
