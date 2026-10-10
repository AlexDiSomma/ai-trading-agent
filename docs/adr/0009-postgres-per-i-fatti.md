# ADR 0009 — PostgreSQL per i fatti, non per ogni tick

**Stato:** accettata · **Data:** 2026-10-04

## Contesto

La specifica elenca una tabella `market_data`. Presa alla lettera — salvare tutte
le candele di dieci strumenti su sei timeframe, più i tick — significa decine di
milioni di righe l'anno su un database che serve anche le query operative. Le
scritture di serie storiche finirebbero per rallentare la lettura delle posizioni
aperte.

C'è anche un punto di correttezza: MT5 **è già** l'archivio autorevole dello
storico prezzi e lo ricostruisce dal server del broker.

## Decisione

Il database conserva i **fatti e le decisioni**, non la materia prima.

Su PostgreSQL: eventi di sistema, versioni di configurazione, contesti globali e
macro, decisioni AI con il loro contesto, piani operativi, verdetti di rischio,
ordini, posizioni, operazioni chiuse, performance giornaliere, backtest.

Dal broker, su richiesta: candele e tick, con cache in memoria e su Redis per la
durata della loro validità.

Su file Parquet, solo quando serve: le serie storiche per il backtest, scaricate
una volta e versionate.

Se in futuro servirà conservare serie lunghe su database, la scelta sarà
TimescaleDB o il partizionamento mensile — e sarà una nuova ADR, motivata dalla
misura del volume reale.

## Conseguenze

Positive: database piccolo, query operative veloci, nessuna duplicazione di ciò
che il broker già conserva.

Negative: un'analisi storica richiede di riscaricare i dati; e se il broker
revisiona lo storico, due backtest a distanza di mesi possono differire. Per
questo i dati di backtest, una volta scaricati, si congelano su file.
