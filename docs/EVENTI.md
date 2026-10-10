# Eventi interni

Catalogo in `backend/core/events.py` (enum `EventType`). I moduli non si chiamano
a catena: pubblicano eventi. Tre motivi, tutti pratici:

- un modulo lento (news, macro) non blocca quelli veloci (prezzi, posizioni);
- il **Decision Journal** registra tutto iscrivendosi al bus, senza che nessun
  modulo sappia della sua esistenza — così la riga che manca non è mai quella
  che serviva;
- il **backtest** ripubblica gli stessi eventi in ordine storico e riusa gli
  stessi consumatori, senza una seconda implementazione della pipeline.

Ogni evento porta `occurred_at`, `source` e `correlation_id`. Il
`correlation_id` lega tutti gli eventi nati dalla stessa osservazione di
mercato: è ciò che permette di ricostruire un trade dalla candela che l'ha fatto
notare fino alla chiusura.

---

## Dati di mercato

| Evento | Quando | Chi lo ascolta |
|--------|--------|----------------|
| `market.tick.received` | nuova quotazione rilevante | position manager, breaker spread |
| `market.candle.closed` | chiusura di una barra su un timeframe | quant, regime, scanner |
| `market.data.stale` | dato oltre il TTL | circuit breaker (blocca), health |

`market.data.stale` è un evento e non solo un errore: significa che il sistema
sta operando alla cieca, e deve risultare nel diario.

## Cosa succede nel mondo

| Evento | Quando |
|--------|--------|
| `macro.release.published` | pubblicato un dato macro con il valore effettivo |
| `calendar.event.imminent` | evento ad alto impatto entro la finestra di blocco |
| `news.batch.ingested` | nuovo lotto di notizie normalizzato e deduplicato |
| `intermarket.context.updated` | ricalcolate correlazioni e forza relativa |
| `world.context.updated` | nuovo `GlobalMarketContext` disponibile |
| `world.regime.changed` | il regime globale è cambiato |

`world.regime.changed` è l'unico evento del ciclo lento che interrompe l'attesa
del ciclo veloce: le posizioni aperte vanno rivalutate subito, non al prossimo
giro programmato.

## Cosa succede sui mercati

| Evento | Quando |
|--------|--------|
| `asset.regime.changed` | il regime di un singolo strumento è cambiato |
| `scanner.ranking.updated` | nuova graduatoria delle opportunità |

## Decisione

| Evento | Quando |
|--------|--------|
| `ai.decision.produced` | l'agente ha prodotto una `MarketDecision` valida (anche WAIT) |
| `ai.provider.unavailable` | il provider LLM non risponde o risponde non valido |
| `plan.created` | `TradePlan` salvato su database |
| `plan.expired` | piano scaduto senza essere eseguito |

WAIT genera un evento come LONG e SHORT. Una decisione di non operare è
un'informazione, e senza registrarla non si può misurare quante occasioni il
sistema scarta e perché.

## Rischio e portafoglio

| Evento | Quando | Severità nel diario |
|--------|--------|---------------------|
| `portfolio.exposure.updated` | ricalcolata l'esposizione | INFO |
| `risk.plan.approved` | piano approvato | INFO |
| `risk.plan.reduced` | approvato con size ridotta | WARNING |
| `risk.plan.rejected` | respinto, con il limite violato | WARNING |
| `risk.budget.exhausted` | budget giornaliero o settimanale esaurito | CRITICAL |
| `risk.circuit_breaker.tripped` | breaker scattato | CRITICAL |

## Esecuzione

| Evento | Quando |
|--------|--------|
| `execution.intent.created` | intento scritto **prima** dell'invio (idempotenza) |
| `execution.order.sent` | ordine inviato al broker |
| `execution.order.filled` | ordine eseguito |
| `execution.order.rejected` | rifiutato dal broker |
| `position.opened` | posizione aperta e riconciliata |
| `position.modified` | SL/TP modificati, con il motivo |
| `position.partially_closed` | chiusura parziale |
| `position.closed` | posizione chiusa |
| `position.thesis_invalidated` | le condizioni di invalidazione del piano si sono verificate |

`execution.intent.created` precede sempre `execution.order.sent`. Se c'è il primo
e non il secondo, la riconciliazione all'avvio sa cosa cercare presso il broker.

## Apprendimento

| Evento | Quando |
|--------|--------|
| `performance.trade.analysed` | analisi post-trade completata |
| `performance.daily.computed` | statistiche giornaliere calcolate |
| `performance.strategy.degradation` | profit factor recente molto sotto lo storico |

## Piattaforma

| Evento | Quando | Severità |
|--------|--------|----------|
| `broker.connection.lost` | MT5 non raggiungibile | CRITICAL |
| `broker.connection.restored` | connessione ripristinata | INFO |
| `system.kill_switch.engaged` | kill switch attivato | CRITICAL |
| `system.kill_switch.released` | rilasciato, con il motivo | INFO |
| `system.mode.changed` | modalità operativa cambiata | INFO |
| `system.started` / `system.stopped` | avvio e arresto | INFO |

---

## Regole del bus

**Un handler che solleva non interrompe gli altri.** Il journal non deve poter
far cadere l'esecuzione, né viceversa. Gli errori degli handler vengono
raccolti e loggati con il nome dell'handler.

**Gli handler girano insieme.** `EventBus.publish` li lancia in parallelo: un
handler lento non mette in coda quelli veloci.

**Pubblicare non è registrare.** Il journal si iscrive con `subscribe_all` e
scrive su `system_events`; se quella scrittura fallisce, la tracciabilità è
compromessa e System Health lo dichiara (componente "Diario di sistema").

**Nessun evento è una richiesta.** Un evento dice cos'è accaduto, non cosa fare.
Se un modulo deve ordinare qualcosa a un altro, la dipendenza è esplicita e il
flusso è quello di `DATA_FLOW.md` — non il bus.
