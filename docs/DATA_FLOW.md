# Flusso dei dati

Tre cicli indipendenti con frequenze diverse, più un percorso di apprendimento
che li osserva. Nessun ciclo chiama l'altro: comunicano con snapshot che portano
la propria scadenza (ADR 0002, ADR 0007).

---

## Ciclo lento — "cosa succede nel mondo" (ogni 15-60 minuti)

```
  provider macro ─┐
  calendario   ───┤
  news         ───┼──→ normalizzazione ──→ IntermarketAnalyzer
  prezzi degli ───┘      e deduplica          (correlazioni mobili,
  indici/valute                                forza relativa,
  di riferimento                               divergenze)
                                                     │
                                                     ▼
                                          GlobalMarketAnalyst (AI)
                                                     │
                                                     ▼
                                    ┌─── GlobalMarketContext ───┐
                                    │  as_of, ttl               │
                                    │  regime globale           │
                                    │  risk-on/off, confidence  │
                                    │  forza del dollaro        │
                                    │  asset forti/deboli       │
                                    │  rischi principali        │
                                    └───────────────────────────┘
                                         │              │
                                    Redis (cache)   PostgreSQL
                                         │          (global_contexts)
                                         ▼
                                  evento world.context.updated
```

Un cambio di regime globale emette `world.regime.changed`, che **interrompe
l'attesa** del ciclo veloce: è uno dei pochi casi in cui un ciclo influenza
l'altro, e lo fa con un evento, non con una chiamata.

---

## Ciclo veloce — "questa operazione ha senso" (ogni 5-15 minuti)

```
                     ┌─ GlobalMarketContext (da Redis)
                     │        │
                     │        ▼
                     │  require_fresh()  ──── scaduto? ──→ WAIT
                     │
  per ogni strumento della whitelist:
                     │
     MT5Connector ───┴──→ Fresh[CandleSeries]  D1 · H4 · H1 · M30 · M15 · M5
          │                      │
          │                      ▼
          │              quant/  (EMA, RSI, MACD, ADX, ATR, Bollinger,
          │                       momentum, volatilità realizzata)
          │              structure/  (HH/HL/LH/LL, BoS, CHoCH,
          │                           supporti, compressione, breakout)
          │                      │
          │                      ▼
          │              regime/ → MarketRegime + confidence
          │                      │
          │                      ▼
          │              scanner/ → OpportunityFactors (fattori separati,
          │                          poi punteggio: vedi ANALISI §2.4)
          │                      │
          │                      ▼
          │              graduatoria ──→ i primi N strumenti
          │                                      │
          └──→ PortfolioSnapshot ────────────────┤
               (conto + posizioni, stesso istante)│
                                                  ▼
                                        AssetAnalyst (AI)
                                        riceve contesto strutturato,
                                        non migliaia di candele
                                                  │
                                                  ▼
                                      MarketDecision (Pydantic)
                                      non valida ──→ WAIT
                                                  │
                                        LONG/SHORT │ WAIT ──→ fine ciclo
                                                  ▼
                                            TradePlanner
                                      entry · stop · target · RR
                                      invalidazioni · durata attesa
                                                  │
                                       salvato su DB PRIMA di tutto
                                                  ▼
                                  ╔═══════════════════════════════╗
                                  ║         RISK ENGINE           ║
                                  ║  deterministico, senza AI     ║
                                  ║  17 controlli pre-trade       ║
                                  ╚═══════════════════════════════╝
                                     │        │        │       │
                                 APPROVED  REDUCED  REJECTED  WAIT
                                     │        │        └───┬───┘
                                     └────┬───┘            │
                                          ▼           registrato
                                   RiskApproval       con il motivo
                                   (impronta del piano,
                                    scadenza breve)
                                          │
                                          ▼
                                 ExecutionEngine (fase 11)
                                 1. kill switch libero?
                                 2. impronta del piano combacia?
                                 3. approvazione non scaduta?
                                 4. scrive ExecutionIntent
                                 5. invia (magic = id intento)
                                 6. timeout → riconcilia, NON rinvia
```

---

## Ciclo di gestione — "questa posizione ha ancora senso" (ogni 1-5 minuti)

Gira anche quando il resto è fermo, ed è il motivo per cui il kill switch non
chiude le posizioni (ADR 0006): qualcuno deve continuare a guardarle.

```
  posizioni aperte dal broker (fonte di verità)
        │
        ├─→ prezzo, R multiplo, MAE/MFE correnti
        ├─→ struttura e momentum aggiornati
        ├─→ regime globale cambiato dall'apertura?
        ├─→ evento macro imminente sullo strumento?
        └─→ condizioni di invalidazione del piano verificate?
                        │
                        ▼
              PositionManager (+ AI per i casi non meccanici)
                        │
      ┌─────────┬───────┴────┬──────────┬───────────┐
      ▼         ▼            ▼          ▼           ▼
    hold    sposta SL    pareggio   parziale    chiudi
                        │
                        ▼
          ogni modifica passa dal Risk Engine
          e viene registrata con il motivo
```

Una rivalutazione può chiudere, ridurre o proteggere. **Non può aumentare**:
aumentare è una nuova operazione e rifà il percorso completo (ANALISI §3.5).

---

## Percorso di apprendimento

```
  bus eventi ──→ SystemJournal ──→ system_events   (ogni evento, sempre)
                      │
  posizione chiusa ───┴──→ PostTradeAnalysis
                               │
                               ├─→ R realizzato, MAE, MFE, durata
                               ├─→ confronto con lo scenario atteso
                               ├─→ confidence dichiarata vs esito
                               │    (calibrazione, ADR 0008)
                               └─→ performance per asset, sessione,
                                   regime, setup, timeframe
                                        │
                                        ▼
                             rilevazione del degrado
                             (PF storico vs ultimi 30 trade)
                                        │
                                        ▼
                      StrategyImprovementProposal → approvazione
                      dell'utente → backtest → paper → live
```

Il percorso di apprendimento **non chiude il cerchio automaticamente**. Scrive
dati; il ciclo successivo li legge. Nessuna strategia si modifica e si mette in
produzione da sola.

---

## Cosa attraversa i confini

| Dato | Da | A | Scadenza |
|------|----|----|----------|
| `GlobalMarketContext` | Mondo | Analisi, Decisione | 30-60 min |
| `Fresh[CandleSeries]` | Mercato | Analisi | ~1/3 della barra |
| `Fresh[Tick]` | Mercato | Rischio, Esecuzione | 15 s |
| `SymbolInfo` | Mercato | Rischio (sizing) | 1 h |
| `PortfolioSnapshot` | Mercato | Rischio, Decisione | 30 s |
| `OpportunityFactors` | Analisi | Decisione | un ciclo |
| `MarketDecision` | Decisione | Trade planner | 5-15 min |
| `TradePlan` | Decisione | Rischio | 5-15 min |
| `RiskApproval` | Rischio | Esecuzione | 30-60 s |

Ogni riga di questa tabella è un punto in cui il sistema può decidere di non
operare perché l'informazione non è più attuale. Sono i punti in cui, nel
progetto precedente, si decideva su dati vecchi senza accorgersene.
