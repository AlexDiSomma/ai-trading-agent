# ADR 0007 — La freschezza del dato è parte del tipo

**Stato:** accettata · **Data:** 2026-10-04

## Contesto

In un sistema con un ciclo lento e uno veloce (ADR 0002) ogni dato che circola è
*vecchio di qualcosa*. La domanda "questo numero è di adesso?" non ha risposta
guardando il numero. Nel progetto precedente era esattamente il problema: non si
capiva se un valore a schermo fosse attuale o di mezz'ora prima.

Le soluzioni abituali — un campo `timestamp` accanto al valore, oppure un
controllo nei chiamanti — hanno lo stesso difetto: sono opzionali.

## Decisione

`Fresh[T]` incapsula valore, `as_of`, TTL e sorgente. Due modi di leggerlo:

- `require_fresh(now)` restituisce il valore **solo** se ancora valido,
  altrimenti solleva `StaleDataError`. È il modo normale per chi decide;
- `.value` restituisce il valore comunque. È una riga visibile, che in una
  revisione si nota e va giustificata.

Ogni lettura di mercato passa da `MarketDataService` e torna come `Fresh[...]`.
I TTL sono proporzionati al timeframe (circa un terzo della barra) e un tick vive
pochi secondi. Le risposte HTTP riportano sempre `as_of` ed `eta_secondi`.

Caso particolare: fuori orario di mercato l'ultimo tick è legittimamente vecchio.
Il dato viene restituito **etichettato come scaduto** e non nascosto: è
un'informazione utile, ed è un motivo valido per non operare.

## Conseguenze

Positive: non si può decidere su un dato vecchio per distrazione; la dashboard
mostra sempre l'età del dato; i test sulla freschezza sono deterministici grazie
all'orologio iniettabile.

Negative: un involucro in più su ogni lettura, e la tentazione di scrivere
`.value` quando si ha fretta. Mitigato dal fatto che `.value` si vede.
