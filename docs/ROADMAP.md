# Fasi di sviluppo

Una fase si considera chiusa quando i suoi criteri di accettazione sono verdi e
le decisioni non ovvie sono scritte in `DECISIONI.md`. Le fasi non si
sovrappongono: la ragione è quella del progetto precedente, diventato
ingestibile perché tutto era a metà contemporaneamente.

---

## ✅ Fase 0 — Analisi dell'architettura

Lettura critica della specifica, problemi, miglioramenti, bounded context,
flusso dei dati, eventi, dieci ADR.

→ `docs/ANALISI_SPECIFICA.md`, `docs/BOUNDED_CONTEXTS.md`, `docs/DATA_FLOW.md`,
`docs/EVENTI.md`, `docs/adr/`

## ✅ Fase 1 — Fondazioni

Struttura del repository, configurazione validata, limiti di rischio immutabili,
logging strutturato con censura dei segreti, orologio iniettabile, contratto di
freschezza, bus eventi, kill switch, PostgreSQL + Alembic, FastAPI, Docker
Compose, test.

**Accettazione:** il backend si avvia con database e Redis spenti e dichiara
cosa manca; `/api/health` risponde sempre; nessun segreto nei log. ✔

## ✅ Fase 2 — MT5 in sola lettura

Connettore MetaTrader 5 serializzato su thread dedicato, mock del terminale,
conto, posizioni, ordini pendenti, storico operazioni, specifiche di contratto,
candele multi-timeframe. Nessun `order_send` nel repository.

**Accettazione:** conto XM demo letto, candele e specifiche reali, 161 test
verdi, il test sull'AST conferma l'assenza di scritture. ✔

**Verificato sul conto reale:** `GOLD`, `US30Cash`, `EURUSD` con
`tick_value=0.8887` (conto in EUR) — motivo per cui il valore del pip non va
scritto nel codice.

## ✅ Fase 3 — Sistema di sola lettura completo e primo livello quantitativo

**Sola lettura completa.** Registro dei simboli con scoperta e mapping canonico,
archivio delle specifiche con rilevazione dei cambiamenti, stato delle quotazioni
e fase di mercato, ora del broker stimata, acquisizione candele multi-timeframe
con giudizio di qualità, snapshot di conto, posizioni ed esposizione, storico
operazioni, `MarketSnapshot` normalizzato.

**Livello quantitativo.** EMA 20/50/200, RSI, MACD, ATR, ADX, ampiezza di
Bollinger con percentile, volatilità realizzata, momentum su tre orizzonti, swing
high/low con conferma, struttura di mercato, supporti e resistenze, rilevazione di
rottura, classificazione della tendenza con fattori separati.

**Multi-timeframe** D1 → H4 → H1 → M15 → M5 che produce un `AssetMarketContext`
tipizzato e persistibile.

**Accettazione:** RSI verificato contro i valori pubblicati (70.4641 sui prezzi di
riferimento, esatto a mano), ATR e ADX calcolati a mano, proprietà invarianti,
nessun look-ahead (gli swing non confermati non entrano nella struttura).
403 test verdi in 90 secondi. ✔

**Verificato sul conto XM reale:** analisi completa su GOLD, EURUSD, US100Cash,
USDJPY, SILVER, US30Cash. Otto assunzioni sbagliate scoperte e corrette
nell'architettura, non nel codice: vedi `docs/ANALISI_SPECIFICA.md` §6 e le ADR
0011-0013.

## ✅ Consolidamento della foundation (prima della fase 4)

`BrokerValuationService` unico punto di calcolo monetario, verificato sull'AST;
`Instrument` con identità canonica e coppia economica; `QuoteQuality` con stati
`VALID/STALE/MISSING/MARKET_CLOSED/INCONSISTENT`; stati di mercato
`OPEN_CONFIRMED/CLOSED_INFERRED/UNKNOWN`; età dei dati in ora broker (correzione di
un fail-open); `TradeIntent` con precondizioni sui dati.

**Accettazione:** test per strumento su EURUSD, USDJPY, GOLD, SILVER, US100Cash,
US30Cash confrontati con il conto reale; nove invarianti verificati con test di
proprietà; 13 test contro MT5 reale (opt-in) verdi; 744 test verdi. ✔
Report: [`FOUNDATION_VALIDATION.md`](FOUNDATION_VALIDATION.md), ADR 0014-0015.

## ✅ Fase 4 — Intelligence globale

`GlobalMarketSnapshot` e `GlobalMarketContext` tipizzati e persistibili; regime
quantitativo (RISK_ON, RISK_OFF, MIXED, TRANSITION, EVENT_DRIVEN, UNCERTAIN);
forza delle 8 valute dai 28 cross; forza relativa di valute, metalli e indici;
intermarket con correlazione mobile, divergenza, conferma e stabilità;
multi-timeframe D1/H4/H1/M15; regime di volatilità; conflitti; qualità
GOOD/DEGRADED/INSUFFICIENT per dimensione. Universo di sola osservazione di 37
strumenti (ADR 0016), motore puro e riproducibile (ADR 0017). Calendario economico
dietro interfaccia, oggi assente e dichiarato `UNKNOWN`.

**Accettazione:** test su intervallo delle correlazioni, assenza di NaN, dati
fermi, simboli mancanti, storia insufficiente, determinismo, evidenze in conflitto,
nessun look-ahead; 853 test verdi; 14 test contro MT5 reale verdi; report reale sul
conto XM durante la riapertura. ✔ Documento: [`GLOBAL_INTELLIGENCE.md`](GLOBAL_INTELLIGENCE.md).

**Gate della foundation:** test a mercato realmente aperto superato alla riapertura
domenicale (FOUNDATION_VALIDATION §7.1). Da ripetere in piena sessione.

**Rinviato:** news (fase successiva, nessun provider scelto), calendario reale,
persistenza dello scarto dell'ora broker, futures con rollover (VIX, USDX).

**Accettazione:** il contesto globale si produce e si legge dalla cache; con i
provider assenti il sistema lo dichiara invece di operare alla cieca.

## ✅ Fase 4.5 — External Context Foundation

XM autorevole per ciò che si negozia, non unica fonte: categorie
`TRADABLE_INSTRUMENT` / `REFERENCE_INSTRUMENT` / `MACRO_SERIES`; interfacce
`MarketReferenceProvider`, `EconomicCalendarProvider`, `MacroDataProvider`,
`RatesProvider`, `NewsProvider`; calendario economico (Forex Factory) con finestre di
evento e rischio macro; VIX (CBOE), curva USA (Tesoro), DXY ricostruito; forza
valutaria a grafo; intermarket con base storica e significatività; MIXED dalla
discordanza; persistenza BEST_EFFORT / REQUIRED. ADR 0018-0020,
[`EXTERNAL_PROVIDERS.md`](EXTERNAL_PROVIDERS.md).

**Accettazione:** parser testati su dati reali registrati, nessuna rete nei test,
proprietà del grafo valutario, nuovi stati intermarket, discordanza, politica di
persistenza; 910 test verdi; 14 test live verdi in piena sessione; report reale a
mercati aperti con tutte le fonti disponibili. ✔ **Gate per la fase 5: conferma
dell'utente.**

## ✅ Fase 5A — AI Market Analyst (READ ONLY)

Prima: qualità separata per dominio, metadati delle fonti (frequenza, ritardo, uso
previsto), DXY solo come verifica, grafo valutario robusto, calendario con
previous/forecast/actual e fasi (ADR 0021). Poi `ai/`: l'AI riceve il
`GlobalMarketContext` e produce `MarketAnalysis` (sintesi, temi, scenari, evidenze a
favore e contrarie, incertezze, strumenti da osservare, condizioni di invalidazione
verificabili). Validazione Pydantic + regole; output invalido = `UNAVAILABLE`. Record
versionati e immutabili (diario + tabella `ai_analyses`). Nessun ordine, nessun
TradeIntent, nessuna size (ADR 0022).

**Accettazione:** test senza rete con modello finto; isolamento verificato sull'AST;
output reali su XM per un caso MIXED e due UNCERTAIN, tutti validi. ✔ **Gate: conferma
dell'utente.**

## ✅ Fase 5B — AI Trade Thesis (SHADOW MODE)

Tre gruppi separati: A baseline quant deterministica (nuova, dichiarata, versionata), B
A + filtro AI, C tesi AI indipendente (LONG/SHORT/WAIT con scenario, setup, ingresso,
condizioni tipizzate, stop, target, RR, evidenze, incertezze, orizzonte). Evidenze con id
esatti, fail closed, `ShadowTradePlan` senza volume, snapshot immutabili decisione →
esito, `OutcomeEvaluator` senza hindsight, replay con taglio all'istante, metriche per
gruppo e confronti con bootstrap (ADR 0023).

**Accettazione:** test senza rete (esiti, validazione, fail closed, isolamento AST, replay
senza barre successive); esecuzione reale su XM con GOLD, EURUSD, US100 e uno strumento
scelto dall'agente, comprese tesi WAIT e rifiutate. ✔ **Gate: conferma dell'utente.** Il
verdetto su "l'AI aggiunge valore" richiede campioni che oggi non esistono (≥30
operazioni chiuse per il filtro, ≥50 decisioni appaiate per la tesi).

## ⬜ Fase 5 — Regime e scanner

`regime/`: tredici regimi con confidence, per il mercato globale e per asset.
La classificazione della **tendenza** per timeframe è già fatta (fase 3): qui si
aggiungono volatilità, eventi e caos, e il regime globale.
`scanner/`: fattori separati e leggibili prima del punteggio unico
(ANALISI §2.4), graduatoria sugli strumenti autorizzati.

**Accettazione:** una lista vuota di opportunità è un esito valido e si vede in
dashboard; in regime CHAOTIC lo scanner non propone nulla.

## ⬜ Fase 6 — Risk Engine e portafoglio

`risk/`: diciassette controlli pre-trade, `DailyRiskBudget` ricostruito dallo
storico del broker, position sizing con le specifiche reali del simbolo,
circuit breaker persistente, `RiskApproval` come tipo (ADR 0005).
`portfolio/`: esposizione per valuta, asset, direzione e correlazione.

**Accettazione:** nessun percorso di codice può produrre un ordine senza
`RiskApproval`; superato il limite giornaliero il sistema si ferma e non riparte
con un riavvio; i test coprono ogni singolo limite violato.

## ⬜ Fase 7 — Agenti AI e trade planner

`ai/providers/` (Anthropic, OpenAI, null), `ai/agents/` (globale, asset,
gestione), `trade_planner/`. Contesti strutturati, non candele grezze. Output
validato con Pydantic: non valido significa WAIT. Cache per impronta del prompt,
registro di token e costi, limite di spesa giornaliero.

**Accettazione:** `AI_REQUIRED=true` con provider spento produce NO TRADE e
nessun output inventato; due analisi dello stesso contesto danno la stessa
decisione.

## ⬜ Fase 8 — Paper trading, gestione posizioni, journal

Esecuzione simulata con spread e slippage realistici, `positions/` con trailing,
pareggio, parziali e uscita anticipata motivata, `journal/` con ricostruzione
completa di un trade, riconciliazione all'avvio.

**Accettazione:** un trade chiuso si ricostruisce integralmente dal database,
dalla candela che l'ha fatto notare fino all'uscita.

## 🟨 Fase 9 — Backtest, walk-forward, Monte Carlo, A/B

**Fatto (2026-10-09, ADR 0028):** backtest della strategia QUANT su ~16 mesi di M5 XM
con gli stessi componenti del paper (`analyse_replay`, `piano_quant`, gate,
`risolvi_barra`, stop mobile), varianti di gestione, due metà del periodo,
Monte Carlo dell'expectancy, test di assenza di look-ahead e di ripetibilità.
**Manca:** walk-forward con addestramento di parametri (oggi nessun parametro viene
ottimizzato), distribuzione Monte Carlo del drawdown e rischio di rovina, backtest
dell'AI (costo e memoria del modello sul passato).

`backtesting/` che riusa gli **stessi** componenti del live. Walk-forward con
periodi di addestramento, validazione e out-of-sample. Monte Carlo per
distribuzione del drawdown e rischio di rovina. A/B fra le tre varianti in
shadow mode (ANALISI §2.3).

**Accettazione:** nessun look-ahead (test che ricalcola su serie troncate), due
esecuzioni dello stesso backtest danno numeri identici.

## ⬜ Fase 10 — Dashboard

Overview, Global Market, Macro, Calendario, Scanner, Analisi asset, AI Analyst,
Segnali, Piani, Posizioni, Rischio di portafoglio, Trade, Performance, AI
Performance, Backtest, Decision Journal, Impostazioni, System Health.

**Vincolo:** ogni numero mostrato porta la propria età. Nessun dato simulato
presentato come reale.

## ⬜ Fase 11 — Execution Engine (LIVE ancora disabilitato)

`execution/`: `OrderWriter` su MT5, intenti di esecuzione, riconciliazione,
esecuzione al massimo una volta. `MODULI_AUTORIZZATI` in
`test_read_only_guarantee.py` si aggiorna qui, consapevolmente (ADR 0004).

**Accettazione:** `ENABLE_LIVE_TRADING=false` resta il default; tutto gira in
paper; i test di idempotenza coprono il timeout dopo l'esecuzione.

## ⬜ Fase 12 — Test di guasto

Perdita di connessione a metà ordine, ordine duplicato, database non disponibile,
dati scaduti, spread anomalo, AI che non risponde, ogni limite di rischio al
confine, riavvio con posizioni aperte, kill switch durante un invio.

**Accettazione:** ogni scenario ha un test e un comportamento documentato. In
nessuno il sistema procede "sperando".

## ⬜ Fase 13 — LIVE

Attivazione con capitale minimo, sorveglianza quotidiana, confronto fra
risultati reali e paper sugli stessi segnali.

**Vincolo permanente:** `MODE=LIVE` **e** `ENABLE_LIVE_TRADING=true`, entrambi
espliciti. Il default del repository resta PAPER.
