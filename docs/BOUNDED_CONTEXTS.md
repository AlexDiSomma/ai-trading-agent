# Bounded context

Otto contesti. Ognuno ha un proprio vocabolario, un proprio ritmo e un proprio
motivo di esistere. La regola che li tiene separati è una sola: **un contesto
non chiama all'indietro**. Il flusso va dal mondo verso l'esecuzione, e il
ritorno avviene solo tramite eventi.

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. PIATTAFORMA   configurazione · log · orologio · bus · health  │
│                  (sotto tutti gli altri, non dipende da nessuno) │
└──────────────────────────────────────────────────────────────────┘

   2. MONDO                3. MERCATO              4. ANALISI
   macro · calendario  →   dati · prezzi      →    quant · struttura
   news · intermarket      simboli · broker        regime · scanner
   GlobalMarketContext     Fresh[CandleSeries]     OpportunityFactors
        │                       │                       │
        └───────────────────────┴───────────────────────┘
                                ↓
                         5. DECISIONE
                         agenti AI · trade planner
                         MarketDecision · TradePlan
                                ↓
                    6. RISCHIO E PORTAFOGLIO
                    limiti · budget · sizing · esposizione
                    RiskApproval  (unico varco verso l'esecuzione)
                                ↓
                         7. ESECUZIONE
                         ordini · posizioni · riconciliazione
                                ↓
                       8. APPRENDIMENTO
                       journal · performance · backtest
                       (osserva tutto, non comanda nessuno)
```

---

## 1. Piattaforma

**Package:** `core/`, `config/`, `database/`, `api/`, `services/`

**Responsabilità:** configurazione validata, logging strutturato, orologio
iniettabile, bus eventi, kill switch, persistenza, HTTP, stato del sistema.

**Vocabolario:** `Settings`, `RiskLimits`, `Clock`, `Fresh[T]`, `Event`,
`KillSwitch`, `SystemHealth`.

**Regola:** `core/` non importa nulla dal resto del backend. È la base, e una
base con dipendenze verso l'alto non è una base.

---

## 2. Mondo — cosa sta succedendo fuori

**Package:** `global_market/`, `macro/`, `calendar/`, `news/`, `intermarket/`

**Responsabilità:** rispondere a *"cosa sta succedendo complessivamente sui
mercati?"* prima che qualcuno guardi un singolo strumento.

**Vocabolario:** `GlobalMarketContext`, `MacroContext`, `NewsContext`,
`IntermarketContext`, `MacroEvent`, sorpresa rispetto alle attese, risk-on /
risk-off, forza del dollaro.

**Ritmo:** minuti/ore. È il ciclo lento (ADR 0002).

**Confini:** non conosce la whitelist dell'utente, non sa su cosa si opererà,
non vede il portafoglio. Produce un contesto e lo pubblica.

---

## 3. Mercato — i dati

**Package:** `brokers/`, `market_data/`

**Responsabilità:** procurare prezzi, candele, specifiche di contratto, stato
del conto e posizioni; farlo solo dentro il perimetro autorizzato e dichiarando
sempre l'età del dato.

**Vocabolario:** `SymbolInfo`, `Tick`, `Candle`, `CandleSeries`,
`AccountSnapshot`, `PositionSnapshot`, `PortfolioSnapshot`, `DealSnapshot`.

**Confini:** non interpreta. Non esiste una funzione "il trend è rialzista" in
questo contesto. Non sa cosa sia una strategia.

**Nota:** è l'unico contesto che conosce MetaTrader 5, e lo conosce in un solo
file (`brokers/mt5/mapping.py`).

---

## 4. Analisi — cosa dicono i dati

**Package:** `quant/`, `structure/`, `regime/`, `scanner/`

**Responsabilità:** indicatori, struttura di mercato, classificazione del
regime, graduatoria delle opportunità sugli strumenti autorizzati.

**Vocabolario:** `MarketRegime`, break of structure, change of character,
compressione, espansione di volatilità, forza relativa, `OpportunityFactors`.

**Confini:** funzioni **pure** su serie di candele. Nessuna chiamata al broker,
nessuno stato, nessuna decisione operativa. È ciò che rende possibile riusare
esattamente questo codice nel backtest.

**Linguaggio:** tutte le classificazioni sono probabilistiche. "Breakout" vuol
dire "configurazione compatibile con un breakout", non "questo è un breakout".

---

## 5. Decisione — cosa conviene fare

**Package:** `ai/`, `signals/`, `trade_planner/`

**Responsabilità:** valutare il contesto strutturato e produrre LONG, SHORT o
WAIT con entry, stop, target, invalidazioni e motivazione; tradurre la decisione
in un piano operativo con numeri.

**Vocabolario:** `MarketDecision`, `TradePlan`, confidence, scenario atteso,
condizioni di invalidazione, fattori a favore e contrari.

**Confini:** **non importa `risk/`**. Non conosce i limiti dell'utente se non
come contesto di sola lettura, non calcola la size definitiva, non invia nulla.
Il suo prodotto è una proposta.

---

## 6. Rischio e portafoglio — si può fare?

**Package:** `risk/`, `portfolio/`

**Responsabilità:** l'unico contesto autorizzato a dire sì. Verifica i limiti
dell'utente, il budget residuo, l'esposizione aperta e correlata, calcola la
dimensione della posizione con le specifiche reali del simbolo, emette
`APPROVED`, `REDUCED_SIZE`, `REJECTED` o `WAIT`.

**Vocabolario:** `RiskVerdict`, `RiskApproval`, `DailyRiskBudget`,
`CurrencyExposure`, `OpenRisk`, circuit breaker.

**Confini:** **deterministico**. Nessuna chiamata a un modello, nessuna
dipendenza da `ai/`, nessuna casualità. Dato lo stesso ingresso dà sempre lo
stesso verdetto — altrimenti non sarebbe verificabile.

---

## 7. Esecuzione — farlo, una volta sola

**Package:** `execution/`, `positions/`

**Responsabilità:** inviare ordini approvati garantendo l'esecuzione al massimo
una volta, riconciliare lo stato con il broker, gestire le posizioni aperte fino
alla chiusura.

**Vocabolario:** `ExecutionIntent`, `OrderResult`, trailing stop, chiusura
parziale, pareggio, invalidazione della tesi.

**Confini:** non decide se un'operazione ha senso. Riceve un piano approvato e si
occupa che accada esattamente quello. **Vuoto fino alla fase 11** (ADR 0004).

---

## 8. Apprendimento — com'è andata

**Package:** `journal/`, `performance/`, `backtesting/`

**Responsabilità:** registrare ogni decisione con il contesto che l'ha prodotta,
misurare i risultati per asset, sessione, regime, setup e confidence, rilevare il
degrado di una strategia, eseguire backtest, walk-forward, Monte Carlo e
confronti A/B.

**Vocabolario:** expectancy, profit factor, R multiplo, MAE, MFE, Sharpe,
Sortino, degrado di strategia, calibrazione.

**Confini:** **osserva, non comanda.** Può ridurre la confidence attribuita a un
setup, segnalare un degrado, proporre un miglioramento. Non può modificare i
limiti dell'utente né mettere in produzione una strategia da sola.

---

## Le due regole che tengono in piedi tutto

1. **Il flusso non torna indietro per chiamata.** Se l'apprendimento deve
   influenzare la decisione, lo fa attraverso dati persistiti e letti al ciclo
   successivo, non chiamando la decisione.

2. **Un solo contesto può dire sì all'esecuzione.** Il rischio. E lo dice
   producendo un oggetto che l'esecuzione richiede per firma (ADR 0005).
