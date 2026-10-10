# ADR 0008 — L'AI deve essere riproducibile, altrimenti il backtest non vale

**Stato:** accettata · **Data:** 2026-10-04 · **Implementazione:** fasi 7 e 9

## Contesto

La specifica chiede di confrontare tre varianti: sistema quantitativo senza AI,
sistema quantitativo con AI, agente AI autonomo. Un confronto del genere non
significa nulla se la variante con AI dà risultati diversi a ogni esecuzione: non
si saprebbe se la differenza viene dalla strategia o dal campionamento del
modello.

Secondo problema: la `confidence` dichiarata da un modello linguistico **non è
una probabilità calibrata**. Usarla direttamente per dimensionare le posizioni
significa dare peso a un numero di cui non si conosce il significato.

## Decisione

**Riproducibilità.** `temperature=0`. Ogni risposta va in cache con chiave
`hash(prompt + versione_prompt + modello)` e viene salvata su database: il
backtest riusa le risposte registrate invece di richiamare il modello, e due
esecuzioni dello stesso backtest danno lo stesso risultato. Il prompt è
versionato, così cambiarlo invalida la cache in modo esplicito e non silenzioso.

**La confidence si calibra, non si crede.** La scala `RISK_CONFIDENCE_LADDER`
traduce la confidence in una *quota del rischio massimo dell'utente*, a scaglioni
discreti, e può solo ridurre. In parallelo si registra la coppia (confidence
dichiarata, esito) e si misura la calibrazione con il Brier score: se il modello
dichiara 0.9 e vince il 55% delle volte, la scala va corretta sui dati e non
sull'intuizione.

**Nessun output non valido diventa un'operazione.** La risposta è validata con
Pydantic; se non passa, la decisione è WAIT. Nessun tentativo di "aggiustare" un
JSON malformato: un modello che risponde male è un modello che non ha capito il
contesto.

**Nessuna invenzione in caso di guasto.** Con `AI_REQUIRED=true`, provider non
disponibile significa NO TRADE. Non esiste un percorso di riserva basato sui soli
indicatori: sarebbe un cambio di strategia non dichiarato, nel momento peggiore.

## Conseguenze

Positive: i backtest sono riproducibili e l'A/B test misura la strategia e non il
rumore; la cache abbatte il costo delle rielaborazioni.

Negative: `temperature=0` riduce la varietà delle analisi, e la cache aggiunge
una tabella più una possibile fonte di risposte obsolete se il versioning del
prompt viene dimenticato. Per questo la versione del prompt entra nella chiave.
