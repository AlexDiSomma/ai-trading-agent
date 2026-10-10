# ADR 0013 — Lo spread si giudica in rapporto all'ATR, non in punti

**Stato:** accettata · **Data:** 2026-10-04 · **Origine:** misura sul conto XM

## Contesto

`RISK_MAX_SPREAD_POINTS` era un limite in punti, con valore iniziale 35 — ragionevole
pensando al forex. Gli spread reali su XM:

| Strumento | spread (punti) | in prezzo | sul prezzo |
|-----------|----------------|-----------|------------|
| EURUSD | 20 | 0.00020 | 0.018% |
| GOLD | 53 | 0.53 | 0.013% |
| US100Cash | 616 | 6.16 | 0.020% |
| US30Cash | 600 | 6.00 | 0.012% |

Un punto è l'ultima cifra del prezzo, e il prezzo ha un numero di cifre diverso
per ogni strumento: 35 punti sono 3,5 pip su EURUSD e un ventesimo di punto indice
su US100Cash. Con quel limite **GOLD, SILVER e tutti gli indici risultavano
intrattabili**, pur avendo spread normali. E il sintomo sarebbe stato il peggiore
possibile: il sistema non avrebbe mai proposto nulla su quegli strumenti, senza
errori, senza messaggi.

## Decisione

Il controllo primario è il rapporto **spread / ATR**: adimensionale, quindi
confrontabile fra strumenti. `RISK_MAX_SPREAD_ATR_RATIO`, default 0.30 — lo
spread non deve mangiare più di un terzo del movimento tipico di una barra.

`RISK_MAX_SPREAD_POINTS` resta come tetto di sicurezza, con valore largo (1000), e
si usa solo quando l'ATR non è calcolabile per storia insufficiente.

**L'ATR di riferimento è quello del timeframe operativo (H1), non di quello di
esecuzione (M5).** Scoperto misurando alla chiusura del venerdì: con l'ATR di M5
il rapporto di EURUSD risultava 0.74 e quello di USDJPY 1.46, perché l'ATR a
cinque minuti collassa quando il mercato rallenta, e tutti gli strumenti
sarebbero risultati troppo cari proprio nei momenti di calma. Lo spread va
confrontato con il movimento che l'operazione si aspetta di catturare, e quel
movimento ha l'orizzonte della tesi operativa.

Con H1, misurato sul conto a mercato chiuso: GOLD 0.027, US30Cash 0.043,
US100Cash 0.060, EURUSD 0.124, SILVER 0.188, USDJPY 0.192. Tutti sotto 0.30, con
margine.

La misura del rapporto viene calcolata **sempre**, anche quando lo stato della
quotazione è deciso da altro (mercato chiuso, dato vecchio): una misura non deve
dipendere dal ramo preso dalla logica, altrimenti manca proprio quando serve per
confrontare le condizioni fra strumenti.

## Conseguenze

Positive: un unico limite ha senso su tutti gli strumenti, e nessun gruppo viene
escluso in silenzio. Il rapporto è leggibile nel contesto
(`quote.spread_atr_ratio`) e nel messaggio ("spread 53 punti, 3% dell'ATR").

Negative: la soglia 0.30 è **euristica non validata**, come i pesi della
classificazione di tendenza. Va misurata in walk-forward (fase 9): il valore
giusto dipende da quanto movimento le strategie si aspettano di catturare, e
quello si saprà solo quando ci saranno strategie.
