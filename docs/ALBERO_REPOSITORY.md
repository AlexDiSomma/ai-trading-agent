# Albero del repository

I package esistono tutti già adesso, anche quelli vuoti, e ognuno contiene un
`__init__.py` che dichiara il proprio ruolo e la fase in cui viene riempito.
Così l'albero delle cartelle si legge come indice del progetto, e l'ordine della
pipeline (ADR 0001) è visibile prima di aprire un file.

Stato al 2026-10-04 (fine fase 3): **169 file, 52 cartelle** (escluse `.venv/`,
`node_modules/`, `__pycache__/`, `var/`, `logs/`).

```
ai-trading-agent/
│
├── .env                        credenziali locali, MAI su git
├── .env.example                modello commentato di tutte le variabili
├── pyproject.toml              dipendenze, extra [mt5]/[ai]/[dev], pytest, ruff, mypy
├── alembic.ini                 migrazioni (la URL viene dalla configurazione, non da qui)
├── docker-compose.yml          PostgreSQL + Redis (+ API opzionale). MT5 resta fuori: ADR 0003
├── avvia.bat                   infrastruttura + migrazioni + backend, in un colpo
├── verifica.bat                diagnostica: configurazione, MT5, conto, mercato
├── .vscode/                    interprete, test, debug, estensioni consigliate
│
├── DECISIONI.md                ogni scelta non ovvia: problema, alternativa scartata, perché
├── README.md                   architettura, installazione, avvio, test
├── CLAUDE.md                   istruzioni per le sessioni di sviluppo successive
│
├── docs/
│   ├── ANALISI_SPECIFICA.md    fase 0: problemi della specifica e modifiche decise
│   ├── BOUNDED_CONTEXTS.md     gli otto contesti e le regole che li separano
│   ├── DATA_FLOW.md            i tre cicli, con le scadenze di ogni dato
│   ├── EVENTI.md               catalogo degli eventi interni
│   ├── DATABASE.md             schema per fase, convenzioni, comandi Alembic
│   ├── ROADMAP.md              fasi 0-13 con criteri di accettazione
│   ├── FOUNDATION_VALIDATION.md  report del consolidamento, confronto col conto reale
│   ├── GLOBAL_INTELLIGENCE.md  fasi 4 e 4.5: contesto globale, metodo, report reali su XM
│   ├── EXTERNAL_PROVIDERS.md   fonti esterne: cosa serve, alternative verificate, scelte
│   ├── validazione/            misure sul conto XM (`python -m backend.validazione_foundation`)
│   └── adr/                    venti decisioni architetturali
│
├── infra/
│   ├── backend/Dockerfile      immagine dei processi che NON parlano con MT5
│   └── postgres/init/          estensioni che richiedono privilegi di superuser
│
├── backend/
│   │
│   ├── core/                   ── base: non importa nulla dal resto del backend
│   │   ├── enums.py            modalità, timeframe (con ruolo), regimi, verdetti
│   │   ├── errors.py           errori che dichiarano se sono transitori e se bloccano
│   │   ├── clock.py            orologio iniettabile; ora broker ≠ UTC (ADR 0010)
│   │   ├── freshness.py        Fresh[T]: as_of + TTL + sorgente (ADR 0007)
│   │   ├── events.py           catalogo EventType + bus in-process
│   │   ├── kill_switch.py      interruttore su file, persistente, fail-closed (ADR 0006)
│   │   └── logging.py          structlog + censura obbligatoria dei segreti
│   │
│   ├── config/                 ── configurazione: un solo punto di verità
│   │   ├── paths.py            radice calcolata dal file, non dalla cwd
│   │   ├── risk_limits.py      LIMITI DELL'UTENTE: frozen, validati fra loro
│   │   └── settings.py         app, log, db, redis, mt5, ai, api + avvisi di avvio
│   │
│   ├── brokers/                ── il mercato: unico contesto che conosce MT5
│   │   ├── base.py             MarketDataReader / AccountDataReader / OrderWriter
│   │   ├── factory.py          un solo punto decide vero o finto; nessun fallback
│   │   └── mt5/
│   │       ├── terminal.py     protocollo di SOLE letture + adattatore reale
│   │       ├── connector.py    sola lettura, thread dedicato, errori tipizzati
│   │       ├── mapping.py      tutte le costanti e i nomi di campo MT5, in un file
│   │       └── mock.py         terminale finto deterministico, con iniezione di guasti
│   │
│   ├── market_data/            ── il mercato, dentro il perimetro autorizzato
│   │   ├── reader.py           whitelist + freschezza + gerarchia multi-timeframe
│   │   ├── registry.py         catalogo del broker; gli alias suggeriscono, non sostituiscono
│   │   ├── specifications.py   specifiche con rilevazione dei cambiamenti e storico
│   │   ├── sessions.py         stato operativo della quotazione e fase di mercato (ADR 0011)
│   │   ├── quote_quality.py    QuoteQuality: unico punto in cui un prezzo è giudicato valido
│   │   ├── broker_time.py      ora del broker: scarto da 2+ quotazioni, età solo con lo scarto (ADR 0015)
│   │   ├── instruments.py      identità canonica: GOLD -> XAUUSD, US100Cash -> US100 (ADR 0014)
│   │   └── ingestion.py        candele multi-timeframe con giudizio di qualità
│   │
│   ├── quant/                  ── indicatori: funzioni pure, nessuno stato
│   │   ├── series.py           serie con None nei buchi; Python puro e il perché
│   │   └── indicators.py       EMA, RSI, MACD, ATR, ADX, Bollinger, volatilità, momentum
│   ├── structure/              ── price action, classificazioni probabilistiche
│   │   ├── swings.py           swing con conferma: la difesa contro il look-ahead
│   │   ├── market_structure.py HH/HL, break of structure, change of character
│   │   ├── levels.py           supporti e resistenze dagli swing confermati
│   │   └── breakout.py         compressione, rottura, falsa rottura, ritest
│   │
│   ├── global_market/          ── "cosa succede sui mercati?", prima del singolo strumento
│   │   ├── engine.py           motore PURO: taglio temporale, dimensioni, qualità, impronta
│   │   ├── reader.py           lettura dell'universo di osservazione (37 strumenti)
│   │   ├── features.py         momentum normalizzato, percentili di volatilità, taglio
│   │   ├── currency_strength.py  8 valute dai 28 cross
│   │   ├── relative_strength.py  classifica e direzione dei gruppi (azioni, metalli)
│   │   ├── volatility.py       livello, tendenza, shock
│   │   ├── risk.py             evidenze di rischio, regime, conflitti
│   │   ├── qualita.py          GOOD / DEGRADED / INSUFFICIENT per dimensione
│   │   ├── backdrop.py         il contesto visto da un singolo strumento
│   │   └── parametri.py        TUTTE le soglie (euristiche) e la versione del motore
│   ├── intermarket/
│   │   ├── correlation.py      Pearson senza NaN, allineamento per orario
│   │   └── engine.py           relazioni: conferma, divergenza, stabilità
│   ├── global_market/external_dims.py  VIX, tassi USA, DXY ricostruito (formula ICE)
│   ├── macro/event_risk.py     rischio da calendario + incertezza implicita nei prezzi
│   ├── calendar/risk.py        finestre di evento (limiti utente), livello, copertura
│   ├── providers/              ── fonti esterne (fase 4.5, ADR 0018)
│   │   ├── base.py             cinque interfacce, stato, NotConfigured* (mai dati finti)
│   │   ├── http.py             UNICO punto che esce in rete, con cache
│   │   ├── forexfactory.py     calendario (settimana corrente, senza actual)
│   │   ├── cboe.py             VIX ufficiale
│   │   └── us_treasury.py      curva dei rendimenti USA ufficiale
│   ├── news/                   ⬜ NewsProvider, deduplica, sentiment (rinviato)
│   │
│   ├── regime/
│   │   └── trend.py            classificazione tendenza: fattori separati dal punteggio
│   ├── scanner/                ⬜ fase 5 — graduatoria delle opportunità
│   │
│   ├── risk/                   ⬜ fase 6 — deterministico, non importa ai/ (ADR 0005)
│   ├── portfolio/              ── misura, non giudica
│   │   ├── exposure.py         per strumento, valuta e classe di attività (ADR 0012)
│   │   └── deals.py            PnL realizzato e operazioni ricostruite dai deal
│   │
│   ├── ai/                     ⬜ fase 7 — NON importa risk/
│   │   ├── providers/          Anthropic, OpenAI, null, dietro un'interfaccia
│   │   └── agents/             agente globale, di asset, di gestione
│   ├── signals/                ⬜ fase 7 — osservazioni operative
│   ├── valuation/
│   │   └── service.py          BrokerValuationService: OGNI numero monetario (ADR 0014)
│   ├── trade_planner/          TradeIntent oggi; TradePlan in fase 7
│   │   └── intent.py           idea -> volume, perdita, margine; mai eseguibile su dati non validi
│   │
│   ├── execution/              ⬜ fase 11 — VUOTO per scelta (ADR 0004)
│   ├── positions/              ⬜ fase 8 — gestione fino alla chiusura
│   ├── journal/                ⬜ fase 8 — Decision Journal
│   ├── performance/            ⬜ fase 9 — memoria delle performance, degrado
│   ├── backtesting/            ⬜ fase 9 — riusa gli stessi componenti del live
│   ├── workers/                ⬜ fase 7 — processi di fondo, frequenze diverse
│   │
│   ├── models/                 entità SQLAlchemy (system_events, config_versions,
│   │                           accounts, symbols)
│   ├── schemas/                DTO Pydantic immutabili; non incrociano models/
│   ├── database/               engine, sessioni, migrazioni Alembic
│   │
│   ├── services/
│   │   ├── container.py        dipendenze a vita lunga; l'avvio tollera i guasti
│   │   ├── health.py           risponde a "si può operare, e se no perché"
│   │   ├── asset_analysis.py   compone il contesto di uno strumento su 5 timeframe
│   │   ├── market_snapshot.py  globale PRIMA, poi conto + esposizione + contesti
│   │   ├── global_intelligence.py  lettura + motore + cache 60 s + persistenza
│   │   ├── persistence.py      scritture di sola aggiunta su database
│   │   ├── system_journal.py   audit trail su DB, iscritto a tutti gli eventi
│   │   └── cache.py            Redis: contesti con scadenza obbligatoria
│   │
│   ├── api/
│   │   ├── deps.py             contenitore da app.state, token Bearer
│   │   ├── router.py           un solo prefisso /api
│   │   └── routes/
│   │       ├── health.py       /api/health (pubblico), /api/health/live
│   │       ├── system.py       limiti di rischio (sola lettura), kill switch
│   │       ├── account.py      conto, posizioni, portafoglio — sola lettura
│   │       ├── market.py       watchlist, simboli disponibili, specifiche, tick, candele
│   │       ├── analysis.py     registro, specifiche+valore, contesto asset, snapshot
│   │       ├── global_market.py  /api/global/context, /snapshot, /summary
│   │       └── portfolio.py    esposizione e storico realizzato
│   │
│   ├── main.py                 FastAPI: errori di dominio → risposte in italiano
│   ├── verifica.py             `python -m backend.verifica`: funziona? e se no, cosa manca
│   ├── validazione_foundation.py  servizio contro MT5 grezzo sul conto reale -> JSON
│   ├── report_globale.py       contesto globale reale, read-only -> JSON
│   │
│   └── tests/
│       ├── conftest.py         isolamento dal .env reale, orologio fermo
│       ├── unit/
│       │   ├── test_config.py              LIVE non si attiva per distrazione
│       │   ├── test_risk_limits.py         il contratto utente-sistema
│       │   ├── test_core.py                orologio, freschezza, kill switch, bus, censura
│       │   ├── test_mapping_puro.py        conversioni MT5 senza il pacchetto MT5
│       │   ├── test_mt5_mapping.py         costanti contro il pacchetto vero (solo Windows)
│       │   ├── test_mt5_connector.py       i modi in cui MT5 sbaglia
│       │   ├── test_market_data.py         il perimetro vale anche in lettura
│       │   ├── test_read_only_guarantee.py legge il SORGENTE: nessuna scrittura
│       │   ├── test_valutazione_centralizzata.py  AST: tick_value e calcolatori solo dove ammesso
│       │   ├── test_foundation_strumenti.py   6 strumenti x 7 verifiche, contro il conto reale
│       │   ├── test_foundation_invarianti.py  test di proprietà (hypothesis) sugli invarianti
│       │   ├── test_regressioni_xm.py         le scoperte sul conto reale, una classe ciascuna
│       │   ├── test_contesto_globale.py       regimi, conflitti, qualità, determinismo, look-ahead
│       │   ├── test_correlazione.py           intervallo e assenza di NaN (hypothesis)
│       │   └── test_lettore_globale.py        stati delle serie sul terminale finto
│       ├── scenari_globali.py  scenari sintetici (SOLO test) con forze note
│       ├── dati/               valori misurati sul conto XM, congelati
│       ├── live/               contro MT5 reale; solo con RUN_LIVE_MT5=1
│       └── integration/
│           ├── conftest.py     app avviata davvero, con terminale finto
│           └── test_api.py     parte senza database e dice cosa manca
│
└── frontend/
    ├── app/
    │   ├── layout.tsx
    │   ├── page.tsx                  reindirizza a system-health
    │   └── system-health/page.tsx    "il sistema può operare, e se no perché"
    ├── components/
    │   ├── StatoBadge.tsx            colore + parola, non solo colore
    │   └── SchedaComponente.tsx      stato sempre accompagnato dal motivo
    ├── lib/api.ts                    token lato server, nessuna cache
    ├── types/health.ts               allineati a schemas/health.py
    ├── tailwind.config.ts            semantica dei colori di stato
    └── package.json
```

## Come si legge

**Le cartelle vuote non sono segnaposto.** Ogni `__init__.py` contiene il ruolo
del modulo e la fase: `backend/risk/__init__.py` dice già che il Risk Engine è
deterministico e non importa `ai/`, e quella frase è verificata da un test. La
regola esiste prima del codice che dovrà rispettarla, quando è ancora facile.

**L'ordine dei package nell'albero è l'ordine della pipeline.** Dal mondo
(`global_market`) all'esecuzione (`execution`), passando per rischio e
portafoglio. Non è alfabetico per caso: è il principio del progetto reso
visibile.

**`execution/` vuota è una dichiarazione.** Non c'è codice per inviare ordini e
un test lo verifica sul sorgente (ADR 0004).

Per rigenerare l'albero dai file reali:
`python docs/_albero.py` → `docs/_albero_generato.txt`
