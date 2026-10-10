# Istruzioni per le sessioni di sviluppo

## Cos'è questo progetto

Piattaforma di trading algoritmico AI-assisted su MetaTrader 5, broker XM.
Principio: **l'utente definisce il rischio, l'AI decide come operare dentro quel
rischio.** Non è un bot che massimizza il numero di operazioni: `WAIT` è un
esito di pari dignità.

**All'inizio di ogni sessione leggi [`docs/CONTESTO.md`](docs/CONTESTO.md)**: preferenze di
lavoro dell'utente, stato del sistema, risultati del backtest e questioni aperte, riassunti
dalle conversazioni precedenti (anche da altri PC). Poi le prime voci di
[`DECISIONI.md`](DECISIONI.md).

Stato (10 ottobre 2026): MVP **PAPER** operativo 24 ore su 24 con due conti, QUANT (regola
fissa) e AI (tesi del modello), dashboard con login, fondazione LIVE pronta ma
**disabilitata**, backtest di QUANT (fase 9, prima parte). Fasi in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Lingua

Documentazione, commenti, messaggi di errore e testi della dashboard in
**italiano**. Nomi di variabili, funzioni e classi in inglese, secondo la
convenzione del linguaggio.

## Tracciabilità

Ogni scelta non ovvia va in [`DECISIONI.md`](DECISIONI.md) **contestualmente**
alla modifica, non dopo: il problema, l'alternativa scartata, il motivo. Le
decisioni strutturali hanno una ADR in `docs/adr/`.

Quando una scelta viene rovesciata, **non cancellare** la voce vecchia:
aggiungerne una nuova che la supera, così resta la storia.

Nei commenti spiegare il **perché**, non il cosa. Il cosa si legge dal codice.

## Vincoli da non violare

Sono invarianti del progetto, verificati da test. Se un test li blocca, il test
ha ragione.

1. **Ordini solo da `execution/mt5_terminal.py` e `execution/gateway.py`.** Nessun
   altro modulo chiama `order_send`, `order_check`, `position_close`, `order_modify`
   (`test_read_only_guarantee.py`, su tutto il backend). Il gateway invia solo con
   `RiskApproval` e con **tutti** i gate LIVE veri, compresa la conferma dell'utente
   in `var/LIVE_CONFIRMATION.json` (ADR 0025).
2. **`risk/` non importa `ai/` e `ai/` non importa `risk/`.** Verificato sugli
   import.
3. **Nessun endpoint HTTP che scriva sul conto.** Verificato sulle rotte.
4. **I limiti di rischio li cambia solo l'utente.** `RiskLimits` resta `frozen`: una
   modifica dalla dashboard crea un nuovo oggetto validato, entro tetti di sicurezza,
   con motivo e registro, mai in LIVE; l'AI non vi ha accesso (ADR 0026).
5. **Nessun segreto nei log, nel frontend, nel repository.** La censura è un
   processore di structlog; le credenziali sono `SecretStr`.
6. **Il default resta PAPER.** `MODE=LIVE` richiede anche
   `ENABLE_LIVE_TRADING=true`.
7. **Nessun fallback automatico al terminale finto.** Dati simulati vanno
   dichiarati a ogni livello.
8. **Niente dati vecchi usati come attuali.** Ogni lettura di mercato torna come
   `Fresh[T]`; la qualità di una quotazione si decide solo in
   `market_data/quote_quality.py` (`QuoteQuality`).
9. **Ogni numero monetario passa da `BrokerValuationService`.** Profitto, perdita
   allo stop, margine, size, normalizzazione del volume: nessun altro modulo
   calcola P/L, pip value o margine, e nessuno legge `trade_tick_value` (su XM è
   in USD per metalli e indici, errore del 12,5%). Verificato sull'AST da
   `test_valutazione_centralizzata.py` (ADR 0012, 0014).
10. **I nomi dei simboli non si sostituiscono.** Un alias è un suggerimento; la
    whitelist è il contratto (ADR 0011, `market_data/registry.py`).
11. **Fatti e deduzioni restano distinti.** Quello che il broker dichiara e quello
    che concludiamo noi non si confondono: `is_inference` (ADR 0011).
12. **Ogni asserzione negativa nei test va accompagnata da una positiva.** Un
    controllo che non trova niente deve fallire, non passare: è il difetto che ha
    reso vuoti i test sulle rotte HTTP.
13. **I timestamp di MT5 sono in ora broker.** L'età di un tick o di una candela
    si calcola solo con `BrokerClock.age_bounds`; senza scarto noto nessuna
    quotazione è `VALID`. Lo scarto si stabilisce solo con due quotazioni
    concordi, mai dal tick che si sta valutando (ADR 0015).
14. **"Chiuso" è sempre una deduzione.** Stati di mercato: `OPEN_CONFIRMED`,
    `CLOSED_INFERRED`, `UNKNOWN`. Non esiste un "chiuso certo" e un'inferenza
    non diventa mai certezza (ADR 0011, 0015).
15. **Il sistema ragiona sui nomi canonici.** `Instrument.canonical` (`XAUUSD`,
    `US100`); `broker_symbol` (`GOLD`, `US100Cash`) si usa solo per parlare col
    terminale (ADR 0014).
16. **Il contesto globale è quantitativo, deterministico e senza look-ahead.**
    Nessun LLM; serie tagliate all'istante di analisi; impronta sha256 + versione
    del motore; nessun punteggio globale unico (ADR 0017).
17. **Universo di osservazione ≠ whitelist.** I 37 strumenti di
    `config/global_universe.py` si guardano, non si operano; l'intelligence globale
    non importa valutazione, rischio, trade planner, esecuzione o AI (ADR 0016).
18. **XM è autorevole per ciò che si negozia, non l'unica fonte.** Dati esterni solo
    tramite le interfacce di `providers/`; un solo punto esce in rete
    (`providers/http.py`, verificato sull'AST); nessun dato finto fuori dai test;
    ogni osservazione esterna ha `available_at` e il motore non usa il futuro
    (ADR 0018). I test non escono mai in rete (`EXT_*_PROVIDER=none`).
19. **Persistenza: BEST_EFFORT per osservare, REQUIRED per operare.** Rischio ed
    esecuzione chiamano `MarketPersistence.require(REQUIRED, ...)` e si fermano se
    il database non è affidabile (ADR 0019).
20. **MIXED dalla discordanza.** Dimensioni forti in versi opposti non si mediano
    (ADR 0020).
21. **Valute: il grafo è la misura, il DXY una verifica.** VIX e tassi giornalieri
    sono contesto strutturale, mai intraday; qualità separata per dominio (ADR 0021).
22. **L'AI analizza e basta.** `ai/` non importa broker, rischio, esecuzione;
    `MarketAnalysis` non ha campi operativi (`extra="forbid"`). Output invalido =
    `UNAVAILABLE`, mai riparato. Ogni record (ingresso, versioni, modello, risposta
    grezza) è immutabile con impronta sha256; le condizioni di invalidazione sono
    verificabili da un programma (ADR 0022).
23. **Shadow mode: nessuna strada verso il conto.** `shadow/` e `ai/thesis/` non importano
    broker, rischio, valutazione, servizi né esecuzione (AST); `ShadowTradePlan` è in
    prezzi e R, senza volume. Tre gruppi separati (A quant, B quant+filtro AI, C tesi
    AI); claim decisivi senza evidenze → REJECTED; output AI invalido → nessun piano.
    La confidence dell'AI non è una probabilità. Le metriche confrontano solo decisioni
    della stessa `pipeline_version` (ADR 0023).
24. **MVP PAPER: il sistema opera solo nel registro paper.** RR e tipo d'ingresso li
    calcola il codice (`paper/geometry.py`), mai il modello; una sola regola
    d'ambiguità intrabar; ogni ingresso passa da `risk/gate.py`; i conti paper stanno in
    `var/paper/` (scrittura atomica). Lo scheduler gira nel backend, mai dentro una
    richiesta HTTP (ADR 0024).
25. **Stop mobile: misurato sempre, applicato solo se acceso.** Ogni posizione nasce con
    le ombre di tutte le regole (`paper/trailing.py`); la regola reale è spenta di
    default, scelta per conto e fissata all'apertura (spegnerla non tocca le posizioni
    aperte). Il picco si aggiorna solo su barre M1 chiuse; ombre e trade vero usano
    `risolvi_barra`. Solo PAPER. Nessuna tesi AI quando il conto non può aprire
    (`blocchi_conto`, stesso gate) (ADR 0027).
26. **Il backtest non ha logica propria.** `backtesting/` decide con `analyse_replay` e
    `piano_quant`, filtra con `risk/gate.py`, gestisce con `risolvi_barra` e
    `paper/trailing.py`; legge il broker solo in lettura; a ogni istante vede solo barre
    chiuse (test sul taglio). Nessuna chiamata all'AI. Le varianti si scelgono prima di
    vedere i risultati e valgono solo se reggono in entrambe le metà (ADR 0028).

## Regole di stile

- Type hints sempre; `mypy` in modalità strict.
- `async` dove c'è I/O; le funzioni di calcolo restano sincrone e pure.
- Dependency injection dal costruttore, nessun singleton nascosto, nessun
  accesso diretto a `os.environ` fuori da `config/`.
- Protocolli (`typing.Protocol`) per i confini, non classi base astratte.
- Moduli piccoli. Nessuna God Class. Nessuna logica di dominio nelle rotte HTTP.
- `core/` non importa nulla dal resto del backend.
- `schemas/` (DTO Pydantic) e `models/` (entità SQLAlchemy) non si importano a
  vicenda: la conversione è esplicita nei servizi.
- Gli indicatori sono **funzioni pure** su `CandleSeries`: nessuno stato,
  nessuna chiamata al broker. È ciò che rende riusabile lo stesso codice nel
  backtest.
- Solo candele **chiuse** nei calcoli. Serie troppo corta → errore, non un
  numero plausibile e privo di senso.

## Comandi

```bash
.venv\Scripts\activate
pip install -e ".[mt5,ai,dev]"

uvicorn backend.main:app --reload       # backend
pytest                                   # tutti i test
pytest backend/tests/unit -q             # solo unitari
ruff check backend && mypy backend       # lint e tipi
alembic upgrade head                     # migrazioni
docker compose up -d postgres redis      # infrastruttura
python docs/_albero.py                   # rigenera l'albero del repository
python -m backend.validazione_foundation # confronto col conto reale -> docs/validazione/
python -m backend.report_globale         # contesto globale reale (read-only)
set RUN_LIVE_MT5=1 && pytest backend/tests/live   # test contro MT5 reale (opt-in)

cd frontend && npm run dev               # dashboard
```

## Terminale MT5

Il pacchetto `MetaTrader5` gira **solo su Windows** e richiede il terminale
aperto e collegato al conto. Per lavorare senza: `MT5_USE_MOCK=true`.

I nomi dei simboli su XM non sono quelli canonici: l'oro è `GOLD`, gli indici
`US30Cash`, `US100Cash`, `US500Cash`. Per trovare la grafia esatta:
`GET /api/market/symbols-available?cerca=...`.

## Come procedere

Una fase per volta, con conferma. Non aprire la fase successiva finché i criteri
di accettazione di quella corrente non sono verdi: il progetto precedente è
diventato ingestibile perché tutto era a metà contemporaneamente.
