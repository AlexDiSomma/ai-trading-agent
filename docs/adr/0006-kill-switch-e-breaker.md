# ADR 0006 — Kill switch su file, circuit breaker fail-closed

**Stato:** accettata · **Data:** 2026-10-04

## Contesto

Serve un modo di fermare tutto che funzioni **anche quando il sistema è rotto**.
Un interruttore in memoria non serve se il processo è bloccato; un endpoint HTTP
non serve se l'API non risponde.

Questione separata: quali guasti devono bloccare l'operatività, e cosa accade
alle posizioni già aperte quando il blocco scatta.

## Decisione

**Il kill switch è un file.** Se esiste, nessun ordine parte. Si crea da
terminale, da Esplora risorse o dall'API. Tre proprietà:

- *persistente*: sopravvive al riavvio e non si disarma da solo;
- *fail-closed*: se il file non è leggibile il blocco si considera **attivo**,
  perché non sapere equivale a non operare;
- *con rilascio consapevole*: disattivarlo richiede un motivo, che finisce nel
  diario di sistema.

**Il kill switch blocca le aperture, non chiude le posizioni.** Chiudere tutto
d'ufficio in un momento di panico realizza perdite che il mercato stava magari
rientrando, e trasforma un problema tecnico in una perdita certa. Lo svuotamento
del portafoglio (`flatten`) è un comando separato, esplicito e volontario.

**Il circuit breaker decide in base alla natura dell'errore**, non al tipo
concreto: ogni errore in `core/errors.py` dichiara `transient` e
`blocks_trading`, così aggiungere un errore nuovo non richiede di toccare il
breaker. Condizioni di blocco: perdita giornaliera o settimanale raggiunta,
drawdown massimo, MT5 disconnesso, dati di mercato scaduti, database non
disponibile, diario che non scrive, spread o slippage anomali, AI non disponibile
con `AI_REQUIRED=true`, stato di esecuzione inatteso.

**Lo stato del breaker è persistente.** Un breaker scattato non deve ripartire
chiuso dopo un riavvio: sarebbe un modo di azzerare il limite giornaliero
riavviando il processo.

## Conseguenze

Positive: esiste sempre un modo di fermare il sistema, e il comportamento di
default davanti all'ignoto è non operare.

Negative: un kill switch persistente può restare attivo per dimenticanza. Per
questo System Health lo mostra in evidenza, con il motivo per cui è scattato.
