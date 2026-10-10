# ADR 0005 — Il Risk Engine non si può aggirare

**Stato:** accettata · **Data:** 2026-10-04 · **Implementazione:** fasi 6 e 11

## Contesto

"Il Risk Engine è indipendente dall'AI" è facile da scrivere e facile da
tradire: basta un modulo che costruisce un ordine e lo invia senza passare dal
controllo. Nessun test lo noterebbe, perché il Risk Engine continuerebbe a
funzionare correttamente — solo, nessuno lo chiamerebbe.

C'è poi il problema dell'idempotenza: MT5 può andare in timeout **dopo** avere
eseguito un ordine. Un rinvio automatico raddoppia la posizione, e il rischio
dell'utente risulta violato senza che nessun limite sia stato cambiato.

## Decisione

**L'approvazione è un tipo, non un valore booleano.** Il Risk Engine è l'unico
modulo che produce `RiskApproval`, che contiene l'impronta del piano approvato.
`OrderWriter.send_order` richiede `(ApprovedTradePlan, RiskApproval)`: non esiste
un modo di chiamarlo con un piano non approvato, perché mancherebbe un argomento
di quel tipo.

**La verifica si ripete all'esecuzione.** L'Execution Engine ricalcola l'impronta
del piano e la confronta con quella dell'approvazione: un piano modificato dopo
l'approvazione viene rifiutato. L'approvazione ha una scadenza breve — una
valutazione di rischio di dieci minuti prima descrive un altro mercato.

**Esecuzione al massimo una volta.** Prima di ogni invio viene scritto un
*intento di esecuzione* su database con un identificativo nostro, riportato nel
`magic` e nel `comment` dell'ordine. Dopo un timeout non si rinvia: si riconcilia
leggendo posizioni e storico del broker e cercando quell'intento.

**Nessun import incrociato.** `risk/` non importa `ai/` e viceversa; un test
sull'AST lo verifica già adesso, mentre i due package sono vuoti e la regola è
facile da rispettare. L'agente riceve il contesto di rischio come dato di sola
lettura, mai il motore.

## Conseguenze

Positive: la garanzia è a livello di tipo e viene controllata da mypy, non dalla
memoria di chi scrive. L'idempotenza elimina la causa più comune di posizione
doppia.

Negative: più cerimonia per aprire una posizione e una tabella in più da
mantenere. Accettato: il caso che previene costa molto più della cerimonia.
