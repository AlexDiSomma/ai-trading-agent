# ADR 0004 — Sola lettura per costruzione, non per configurazione

**Stato:** accettata · **Data:** 2026-10-04

## Contesto

Fino alla fase 11 il sistema non deve poter inviare ordini. Il modo usuale è un
flag, `ENABLE_TRADING=false`. Un flag però protegge soltanto finché nessuno lo
inverte, e non dice nulla su cosa accada quando viene invertito: il codice di
esecuzione non è mai stato eseguito né testato.

## Decisione

La capacità di scrivere è **assente dal codice**, non disattivata.

1. **Protocolli separati.** `MarketDataReader` e `AccountDataReader` per la
   lettura, `OrderWriter` per la scrittura. Nessuna implementazione di
   `OrderWriter` esiste nel repository.
2. **Il protocollo del terminale elenca solo letture.** `MT5Terminal` non
   dichiara `order_send`: chi riceve un `MT5Terminal` non può inviare ordini,
   perché il metodo non esiste.
3. **Un test legge il sorgente.** `test_read_only_guarantee.py` analizza l'AST
   dei file in `brokers/` e fallisce se compare una chiamata di scrittura verso
   MT5, a meno che il file non sia elencato in `MODULI_AUTORIZZATI` — oggi vuoto.
4. **Nessun endpoint HTTP di scrittura sul conto**, verificato da un test che
   ispeziona le rotte registrate nell'applicazione.
5. **Il mock non è una scorciatoia:** anche `FakeMT5Terminal` è priva di
   `order_send`.

## Conseguenze

Positive: l'affermazione "non può operare" è verificabile, non è una promessa.
Il giorno della fase 11 quel test fallirà, e il fallimento obbliga a dichiarare
l'intenzione invece di scivolarci dentro.

Negative: un test che analizza il sorgente è inusuale e va spiegato a chi lo
incontra. È il motivo per cui il suo docstring è più lungo del test.
