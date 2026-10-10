# AI Trading Agent

Piattaforma di trading algoritmico AI-assisted su MetaTrader 5, per broker XM.

Il principio che regge tutto:

> **L'utente definisce il rischio. L'AI decide come operare dentro quel rischio.**

E l'ordine in cui si ragiona, sempre:

```
cosa sta succedendo nel mondo
        ↓
cosa sta succedendo sui mercati
        ↓
quali strumenti sono forti e quali deboli
        ↓
in che regime siamo
        ↓
dove sono le opportunità migliori
        ↓
questa operazione ha senso
        ↓
sta nel portafoglio che ho già
        ↓
il rischio è accettabile
        ↓
solo allora: esegui
```

Questo ordine non vive nel prompt dell'agente: vive nelle dipendenze fra i
moduli, e una violazione diventa un errore di import o un test rosso
([ADR 0001](docs/adr/0001-pipeline-nell-architettura.md)).

**Non è un bot che apre più operazioni possibili.** `WAIT` è un esito di pari
dignità, e il budget giornaliero è la perdita *massima tollerabile*, non una
somma da spendere.

---

## Stato attuale

| Fase | Contenuto | Stato |
|------|-----------|-------|
| 0 | Analisi della specifica, bounded context, flusso, eventi, 13 ADR | ✅ |
| 1 | Configurazione, logging, database, FastAPI, Docker, kill switch, test | ✅ |
| 2 | MT5 in **sola lettura**: conto, posizioni, simboli, candele, mock | ✅ |
| 3 | Sola lettura completa + livello quantitativo e analisi multi-timeframe | ✅ |
| — | Consolidamento: valutazione centralizzata, identità strumenti, qualità quotazioni, ora broker | ✅ |
| 4 | Intelligence globale: regime, forza valute, intermarket, volatilità, forza relativa | ✅ |
| 4.5 | Fonti esterne (calendario, VIX, tassi), grafo valutario, intermarket con base storica | ✅ |
| 5A | AI Market Analyst in **sola lettura**: analisi strutturata, validata, immutabile | ✅ |
| 5B | AI Trade Thesis in **shadow mode**: gruppi A/B/C, esiti senza hindsight, metriche | ✅ |
| MVP | Operativo in **PAPER**: scheduler, scanner, AI, piano deterministico, gestione, P/L, dashboard | ✅ |
| 11 | Fondazione dell'esecuzione reale: gateway, idempotenza, riconciliazione, gate LIVE (**disabilitato**) | ✅ |
| 5-13 | Scanner, rischio, AI operativa, paper, backtest, dashboard, esecuzione | ⬜ |

**Oltre 900 test verdi in circa 3 minuti**, più 14 test contro MT5 reale (opt-in,
verdi sul conto demo in piena sessione). Fonti esterne:
[`docs/EXTERNAL_PROVIDERS.md`](docs/EXTERNAL_PROVIDERS.md). Contesto globale: [`docs/GLOBAL_INTELLIGENCE.md`](docs/GLOBAL_INTELLIGENCE.md). Report della foundation: [`docs/FOUNDATION_VALIDATION.md`](docs/FOUNDATION_VALIDATION.md).
Verificato sul conto XM demo reale: analisi
completa su GOLD, EURUSD, US100Cash, USDJPY, SILVER e US30Cash, esposizione di
portafoglio e ricostruzione dello storico operazioni.

Dalla verifica sul campo sono emerse **otto assunzioni sbagliate**, corrette
nell'architettura e non nel codice: le più costose erano un `tick_value` non in
valuta del conto (errore del 12,5% su metalli e indici) e un calcolatore di
profitto non lineare (+47% su EURUSD). Dettagli in
[`docs/ANALISI_SPECIFICA.md`](docs/ANALISI_SPECIFICA.md) §6.

**Nessun ordine può partire senza tutti i gate LIVE e la tua conferma scritta.** Fino
alla fase 11 il codice per inviare ordini non esisteva
([ADR 0004](docs/adr/0004-broker-read-only-per-costruzione.md)); dalla fondazione
LIVE esiste in due soli moduli (`execution/mt5_terminal.py`, `execution/gateway.py`),
un test sul sorgente verifica che nessun altro modulo possa scrivere, e ogni invio
rivaluta tutti i gate ([ADR 0025](docs/adr/0025-fondazione-esecuzione-live.md)).

---

## Da dove iniziare a leggere

0. [`docs/CONTESTO.md`](docs/CONTESTO.md) — stato attuale, risultati, questioni aperte
   e come riprendere il lavoro da un altro PC.
1. [`docs/ANALISI_SPECIFICA.md`](docs/ANALISI_SPECIFICA.md) — i problemi trovati
   nella specifica e cosa è stato cambiato. Si legge prima del codice.
2. [`docs/BOUNDED_CONTEXTS.md`](docs/BOUNDED_CONTEXTS.md) — gli otto contesti e
   le due regole che li tengono separati.
3. [`docs/DATA_FLOW.md`](docs/DATA_FLOW.md) — i tre cicli e la scadenza di ogni
   dato.
4. [`DECISIONI.md`](DECISIONI.md) — ogni scelta non ovvia con il suo perché.
5. [`docs/ROADMAP.md`](docs/ROADMAP.md) — le fasi, con criteri di accettazione.

---

## Architettura in breve

**Tre cicli indipendenti**, non una catena di diciassette chiamate
([ADR 0002](docs/adr/0002-pipeline-a-due-cicli.md)):

- **lento** (15-60 min): macro, calendario, news, intermarket → produce un
  `GlobalMarketContext` con scadenza;
- **veloce** (5-15 min): prezzi, indicatori, struttura, regime, scanner,
  decisione, rischio, esecuzione — *legge* il contesto e ne verifica la
  freschezza;
- **gestione** (1-5 min): le posizioni aperte, che vanno guardate anche quando
  tutto il resto è fermo.

**Ogni dato dichiara la propria età.** `Fresh[T]` porta `as_of`, TTL e sorgente;
leggere un dato scaduto richiede una riga esplicita e visibile
([ADR 0007](docs/adr/0007-freschezza-come-tipo.md)). Era il difetto principale
del progetto precedente: non si sapeva se un numero a schermo fosse di adesso.

**Il Risk Engine non si può aggirare.** È l'unico modulo che produce
`RiskApproval`, e l'esecuzione la richiede per firma
([ADR 0005](docs/adr/0005-risk-engine-non-aggirabile.md)). `risk/` non importa
`ai/` e `ai/` non importa `risk/`: lo verifica un test.

**MT5 non gira in Docker.** Il pacchetto esiste solo su Windows e parla con un
terminale grafico: database e cache stanno in container, il processo che possiede
MT5 sta sull'host ([ADR 0003](docs/adr/0003-mt5-gateway-windows.md)).

**Fail closed.** Se non si sa, non si opera. Kill switch su file, persistente,
che blocca le aperture ma non chiude le posizioni
([ADR 0006](docs/adr/0006-kill-switch-e-breaker.md)).

**Fatti e deduzioni restano distinti.** L'API Python di MT5 non espone gli orari di
sessione, quindi "mercato chiuso" è una nostra conclusione: viene marcata come tale
e accompagnata dalle prove ([ADR 0011](docs/adr/0011-sessioni-non-esposte-da-mt5.md)).

**Il valore monetario di un movimento si chiede al broker.** `trade_tick_value` su
questo conto in EUR è in euro per il forex e in dollari per metalli e indici:
i numeri derivati da quel campo sono sbagliati del 12,5%
([ADR 0012](docs/adr/0012-valore-monetario-dal-broker.md)). Ogni numero monetario
— profitto, perdita allo stop, margine, size — passa da un solo servizio,
`BrokerValuationService`, e un test sull'AST impedisce che rinascano formule
altrove ([ADR 0014](docs/adr/0014-valutazione-e-identita-centralizzate.md)).

**L'età di un dato si calcola in ora broker.** MT5 etichetta tick e candele con
l'ora del server (XM: UTC+3) spacciata per UTC. Senza lo scarto, misurato da almeno
due quotazioni concordi, nessuna quotazione è valida
([ADR 0015](docs/adr/0015-eta-dei-dati-in-ora-broker.md)).

---

## Stack

| | |
|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic |
| Dati | PostgreSQL 16, Redis 7 |
| Calcolo | pandas, numpy, scipy, scikit-learn |
| Broker | MetaTrader5 (solo Windows), conto XM |
| AI | provider astratto: Anthropic, OpenAI, null |
| Frontend | Next.js 15, React 19, TypeScript, Tailwind |
| Test | pytest, pytest-asyncio, mock MT5 completo |
| Infra | Docker Compose, logging strutturato |

---

## Installazione

### 1. Requisiti

- **Windows** con il terminale **MetaTrader 5 di XM** installato, aperto e
  collegato al conto. Su questa macchina è in
  `C:\Program Files\XM Global MU MT5 Terminal\terminal64.exe`.
- **Python 3.12 o superiore.** Verificato su 3.14: il pacchetto `MetaTrader5` ha
  wheel per CPython 3.14 (`metatrader5-5.0.6231-cp314`).
- **Docker Desktop** per PostgreSQL e Redis (in alternativa: installazioni
  locali).
- **Node.js 20+** per il frontend.

### 2. Ambiente Python

```bash
cd ai-trading-agent
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1

pip install -e ".[mt5,ai,dev]"
```

Gli extra: `mt5` installa il pacchetto MetaTrader5 (solo Windows, grazie al
marker di piattaforma), `ai` i client dei provider LLM, `dev` pytest, ruff e
mypy.

### 3. Configurazione

Il file `.env` è già compilato, riportando i puntamenti dei progetti precedenti:

- MT5/XM (login, server, password, percorso del terminale);
- chiave e modello OpenAI;
- password del database e token dell'API, generati casuali.

Da `.env.example` si rigenera da zero. Le voci da conoscere:

| Variabile | Significato |
|-----------|-------------|
| `APP_MODE` | `OBSERVE` / `SIGNAL` / `PAPER` / `LIVE` — default `PAPER` |
| `APP_ENABLE_LIVE_TRADING` | secondo consenso per il denaro reale, default `false` |
| `MT5_USE_MOCK` | `true` = terminale finto, per lavorare senza MT5 aperto |
| `RISK_*` | **i limiti dell'utente.** Si cambiano qui e si riavvia |
| `RISK_ALLOWED_SYMBOLS` | whitelist, con la grafia **esatta** del broker |
| `AI_REQUIRED` | `true` = provider AI non disponibile significa NO TRADE |
| `API_AUTH_TOKEN` | token che il frontend usa per parlare col backend |

> **Nomi dei simboli.** Su XM l'oro si chiama `GOLD` (non `XAUUSD`) e gli indici
> `US30Cash`, `US100Cash`, `US500Cash`. Un nome sbagliato rende lo strumento
> invisibile al sistema. Per trovare la grafia giusta:
> `GET /api/market/symbols-available?cerca=gold`.

### 4. PostgreSQL e Redis

```bash
docker compose up -d postgres redis
docker compose ps                     # entrambi "healthy"
alembic upgrade head                  # crea lo schema
```

Senza Docker: PostgreSQL 16 e Redis 7 locali, poi allineare `DB_*` e `REDIS_*`
in `.env`.

### 5. Backend

```bash
uvicorn backend.main:app --reload --port 8000
```

Oppure `trading-api` (installato con il pacchetto), oppure `avvia.bat`.

- API: <http://127.0.0.1:8000>
- Documentazione interattiva: <http://127.0.0.1:8000/docs>
- Stato: <http://127.0.0.1:8000/api/health>

### 6. Frontend

```bash
cd frontend
npm install
copy .env.local.example .env.local    # e incollare API_TOKEN da .env
npm run imposta-password              # nome utente e password della dashboard
npm run dev
```

Dashboard su <http://localhost:3000>, raggiungibile **solo da questo PC** (ascolta su
`127.0.0.1`). Senza credenziali impostate la dashboard resta chiusa e la pagina di
accesso spiega come configurarle; `avvia.bat` lancia `imposta-password` da solo se mancano.
Dopo aver cambiato la password va riavviata la dashboard.

---

## Verificare la connessione a MT5

```bash
curl http://127.0.0.1:8000/api/health
```

La risposta dice sempre **se il sistema può operare e, se no, perché**:

```json
{
  "status": "UNHEALTHY",
  "effective_mode": "PAPER",
  "can_trade": false,
  "blocking_reasons": [
    "PostgreSQL: PostgreSQL non raggiungibile su postgresql://trading@localhost:5432/trading_agent (ConnectionRefusedError)."
  ],
  "components": [
    { "name": "MetaTrader5", "status": "HEALTHY",
      "detail": "Connesso al conto 12345678 su XMGlobalMU-MT5 2." }
  ]
}
```

Con il token, le letture (sostituire `$T` con `API_AUTH_TOKEN`):

```bash
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/account
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/account/positions
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/market/watchlist
curl -H "Authorization: Bearer $T" "http://127.0.0.1:8000/api/market/symbols-available?cerca=gold"
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/market/symbols/GOLD
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/market/tick/GOLD
curl -H "Authorization: Bearer $T" "http://127.0.0.1:8000/api/market/candles/GOLD?timeframe=H4&count=200"
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/system/risk-limits
```

### Analisi di mercato (fase 3)

```bash
# registro dei simboli e verifica della whitelist contro il catalogo del broker
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/analysis/symbols

# specifiche + valore monetario di un movimento, con lo scarto rispetto a tick_value
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/analysis/specifications/GOLD

# contesto completo: D1 -> H4 -> H1 -> M15 -> M5 con indicatori, struttura, livelli
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/analysis/asset/GOLD

# istantanea normalizzata: conto, esposizione e tutti gli strumenti autorizzati
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/analysis/snapshot

# esposizione per strumento, valuta e classe di attivita'
curl -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/portfolio/exposure

# PnL realizzato e operazioni ricostruite dallo storico del broker
curl -H "Authorization: Bearer $T" "http://127.0.0.1:8000/api/portfolio/realized?giorni=30"
```

Esempio di lettura reale su GOLD (conto demo, mercato chiuso di sabato):

```
GOLD (METAL)  qualita=GOOD  quotazione=MARKET_CLOSED (deduzione)
  valore di 1.0 di prezzo per lotto = 88.87 EUR   (dal broker, non da tick_value)
  spread 53 punti = 2.7% dell'ATR di H1
  multi-TF: allineamento=-0.72  strutturale=-1  regime dominante=TREND_DOWN
  D1  RSI 40.6  ADX 19.9  ATR 2.20%  TREND_DOWN   DOWNTREND
  H4  RSI 42.5  ADX 21.1  ATR 0.96%  TREND_DOWN   RANGE      COMPRESSION
  H1  RSI 43.3  ADX 11.5  ATR 0.48%  UNCERTAIN    RANGE
```

### Se MT5 non si connette

| Sintomo | Causa | Rimedio |
|---------|-------|---------|
| `IPC initialize failed` (-10003) | terminale chiuso, o `MT5_TERMINAL_PATH` sbagliato | aprire MT5 e collegarlo al conto; verificare il percorso |
| `collegato a un conto diverso` | il terminale è su un altro login | cambiare conto nel terminale, oppure `MT5_LOGIN` in `.env` |
| `simbolo non disponibile` | nome sbagliato | `GET /api/market/symbols-available?cerca=...` |
| `Pacchetto MetaTrader5 non disponibile` | manca l'extra, o non è Windows | `pip install -e ".[mt5]"`, oppure `MT5_USE_MOCK=true` |
| `tick vecchio di N secondi` | mercato chiuso | nessun rimedio: è un motivo valido per non operare |

**Per lavorare senza MT5** (altro sistema operativo, terminale chiuso, CI):
`MT5_USE_MOCK=true`. Il terminale finto produce prezzi deterministici, e sia
System Health sia ogni risposta dichiarano che i dati sono simulati. Non esiste
un passaggio automatico al mock: sarebbe il modo più rapido di presentare numeri
inventati come reali.

---

## Test

```bash
pytest                                  # tutto
pytest backend/tests/unit -q            # solo unitari, veloci
pytest backend/tests/integration -q     # API in-process, terminale finto
set RUN_LIVE_MT5=1 && pytest backend/tests/live   # contro MT5 reale, opt-in
python -m backend.validazione_foundation           # misura il conto -> docs/validazione/
python -m backend.report_globale                   # contesto globale reale, read-only
pytest --cov=backend --cov-report=html  # copertura

ruff check backend && ruff format --check backend
mypy backend
```

I test di integrazione girano **con PostgreSQL e Redis spenti**, deliberatamente:
verificano che il backend parta comunque e dichiari cosa manca. Se un giorno
smettesse di partire con il database giù, non sarebbe più in grado di spiegare
perché non opera.

Nessun test parla con il broker reale se non lo si chiede: i test in
`backend/tests/live/` (marker `live_broker`) vengono saltati senza `RUN_LIVE_MT5=1`.
I test per strumento confrontano il terminale finto con i valori misurati sul conto
reale, congelati in `backend/tests/dati/`.

---

## Visual Studio Code

Estensioni: **Python**, **Pylance**, **Ruff**, **Docker**,
**ES7+ React/Redux** (frontend).

Impostazioni di workspace suggerite, in `.vscode/settings.json`:

```json
{
  "python.defaultInterpreterPath": ".venv\\Scripts\\python.exe",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["backend/tests"],
  "python.analysis.typeCheckingMode": "strict",
  "editor.formatOnSave": true,
  "[python]": { "editor.defaultFormatter": "charliermarsh.ruff" },
  "files.exclude": { "**/__pycache__": true, "**/.pytest_cache": true }
}
```

Avvio in debug, in `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Backend (uvicorn)",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["backend.main:app", "--reload", "--port", "8000"],
      "jinja": true
    }
  ]
}
```

---

## Sicurezza

- Le credenziali MT5 **non raggiungono mai il frontend**. Il browser parla solo
  col backend, con un token; il broker lo vede soltanto il backend.
- Nessun segreto nei log: la censura è un processore obbligatorio di structlog,
  non una raccomandazione a chi scrive `logger.info`.
- `.env` è escluso da git. Nessuna credenziale nel codice.
- Le password sono `SecretStr`: compaiono come `**********` anche se qualcuno le
  stampa per sbaglio.
- Ogni evento finisce in `system_events`, con la modalità operativa del momento.
- Dashboard protetta da login: password salvata solo come impronta scrypt in
  `frontend/.env.local`, sessione in un cookie firmato (httpOnly, SameSite strict,
  12 ore o 30 giorni con "Ricordami"). Pagine e API senza sessione sono chiuse (il
  middleware nega se manca la configurazione), le azioni accettano solo richieste
  dalla dashboard stessa, dopo 5 tentativi errati l'IP è bloccato per 15 minuti.

---

## AI Market Analyst (fase 5A)

```bash
python -m backend.report_ai          # tre casi reali: dal vivo, senza fonti esterne, replay
```

`POST /api/ai/market-analysis` analizza il contesto globale corrente; `GET` elenca il
diario immutabile (`var/ai_journal`). Nessun ordine, nessuna size: vedi
[ADR 0022](docs/adr/0022-ai-market-analyst-read-only.md).

---

## MVP operativo PAPER

```bat
avvia.bat
```

Avvia backend + scheduler e dashboard. Dashboard: **http://localhost:3000**. Il
sistema scansiona, chiede le tesi all'AI, costruisce i piani, apre e gestisce le
posizioni **solo nel conto paper** (`var/paper/`), con spread, slittamento,
commissioni e swap. LIVE resta disabilitato: [ADR 0024](docs/adr/0024-mvp-paper-operativo.md),
[ADR 0025](docs/adr/0025-fondazione-esecuzione-live.md).

Per fermare tutto (backend e dashboard): `ferma.bat`.

**Stop mobile e misure in ombra** ([ADR 0027](docs/adr/0027-stop-mobile-misure-ombra-salute-ai.md)).
Per ogni posizione il sistema calcola anche l'uscita che avrebbero avuto tre regole di
stop mobile, senza toccare il trade; il confronto è in *Performance → Misure in ombra*,
insieme ai trade aperti mentre il conto aveva già la stessa scommessa (posizioni
correlate). Una regola si può accendere per conto in *Controlli → Stop mobile*: è spenta
di default e vale solo per le posizioni aperte dopo la scelta.

**Salute dell'AI.** Se il provider non risponde (credito esaurito, limiti, chiave, rete)
un avviso compare in testa a ogni pagina; il consumo del giorno (chiamate e token) è nel
badge "AI". La tesi non viene chiesta quando il conto non potrebbe comunque aprire.

---

## Backtest (fase 9)

```bat
.venvScriptspython.exe -m backend.backtest            :: tutti gli strumenti autorizzati
.venvScriptspython.exe -m backend.backtest --simboli GOLD,EURUSD
.venvScriptspython.exe -m backend.backtest --aggiorna :: riscarica lo storico
```

Verifica la strategia QUANT su ~16 mesi di storico XM con lo **stesso** codice del
paper (contesto, regola, gate, gestione), sette varianti di gestione, due metà del
periodo e Monte Carlo. Non chiama l'AI. Il primo giro scarica lo storico e calcola i
segnali (~1 ora); i successivi riusano la cache (`var/backtest/`) e durano circa un
minuto. Rapporto in `var/backtest/rapporto_<data>.json`; risultati e limiti in
[ADR 0028](docs/adr/0028-backtest-quant.md).

---

## Shadow mode (fase 5B)

```bash
python -m backend.report_shadow esegui   # decisioni dal vivo + replay della settimana scorsa
python -m backend.report_shadow valuta   # rivaluta gli esiti con i dati di adesso
```

Diario immutabile in `var/shadow_journal/` (decisioni ed esiti separati). Nessun piano
ombra ha un volume o raggiunge un servizio di esecuzione:
[ADR 0023](docs/adr/0023-trade-thesis-shadow-mode.md).

---

## Storico: fase 4

**Intelligence globale.** `global_market/`, `macro/`, `calendar/`, `news/`,
`intermarket/`: il `GlobalMarketContext` che risponde a *"cosa sta succedendo
complessivamente sui mercati?"* prima che qualunque singolo strumento venga
valutato.

Due cose concrete già individuate per quella fase:

- **ricavare il calendario settimanale dalle barre.** Poiché MT5 non espone gli
  orari di sessione (ADR 0011), l'unico modo di *anticipare* una chiusura invece di
  dedurla dopo quindici minuti è osservare quali ore hanno barre negli ultimi mesi;
- **misurare e conservare lo scarto ora-broker.** Oggi si misura solo quando il
  mercato è aperto e si perde a ogni riavvio: va reso persistente, perché la
  giornata di trading dell'utente si chiude sulla mezzanotte del broker.

Criteri di accettazione in [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Convenzioni

Documentazione, commenti, messaggi di errore e testi della dashboard in
**italiano**; nomi di variabili e funzioni in inglese.

I commenti nel codice spiegano il **perché**, non il cosa. Ogni scelta non ovvia
va in `DECISIONI.md` contestualmente alla modifica — e quando una scelta viene
superata non si cancella la voce vecchia: se ne aggiunge una nuova, così resta
la storia.
