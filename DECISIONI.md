# Decisioni

Ordine cronologico **inverso**: la voce più recente in alto. Una voce per ogni
scelta non ovvia, con il problema, l'alternativa scartata e il motivo.

Quando una decisione viene superata, la voce vecchia **non si cancella**: si
aggiunge una voce nuova che la supera, così resta la storia. Il motivo è il
progetto precedente, diventato ingestibile anche perché le decisioni erano
sparse in sei file contraddittori senza traccia del ragionamento.

Le decisioni strutturali hanno una ADR dedicata in [`docs/adr/`](docs/adr/);
qui c'è il riassunto e il rimando.

---

## 2026-10-09 — Esperimenti dell'utente: segnali invertiti e target QUANT a 1,3R

**Problema.** L'utente non vuole fermare QUANT né passare settimane a provare strategie:
chiede di "puntare l'opposto" (con un interruttore) e poi di incassare a 1,3R.

**Deciso.** Entrambi come comandi in Controlli, per le nuove aperture, con statistiche
separate (`|inverso`, `|tp1.3`) e varianti nel backtest. Il target vale solo per QUANT:
invece di abbassare il RR minimo generale (che cambierebbe anche l'AI e il suo prompt), il
gate di quel conto accetta un minimo pari al target scelto. Inversione spenta; target
impostato a 1,3R su richiesta esplicita. ADR 0029.

**Detto all'utente prima di attivare.** Nel backtest l'inversione peggiora (−0,086R per
trade) e il target a 1,3R migliora ma resta negativo (−0,028R). Sono esperimenti voluti
dall'utente, non soluzioni.

---

## 2026-10-09 — Backtest di QUANT: nessun vantaggio, lo stop mobile peggiora

**Problema.** −300 € nel paper QUANT in tre giorni; l'utente chiede se ci affidiamo a
segnali a caso. QUANT era un termine di paragone mai verificato sul passato.

**Fatto.** Backtest (fase 9, prima parte) con lo stesso codice del paper su ~16 mesi e
10 strumenti, sette varianti scelte prima dei risultati, due metà del periodo, Monte
Carlo. Nessuna chiamata all'AI. Dettagli e tabella: ADR 0028.

**Esito.** Base −0,05R per trade su 1.729 trade (−37,6% senza blocchi di conto). Le tre
regole di stop mobile **peggiorano**: il replay di 14 trade del 7 ottobre era rumore —
la voce del 7 ottobre resta valida nel metodo (misurare prima di accendere), e
l'interruttore resta spento. Unica variante positiva in entrambe le metà: stop 2,5 ATR
con orizzonte 48 ore (+0,032R, probabilità di expectancy positiva 78%), guidata da oro
e argento: indizio, non prova.

**Non fatto, di proposito.** Nessuna modifica a QUANT né al paper: l'utente ha chiesto
solo il backtest. Le opzioni (pausa, nuova versione con stop largo, universo ridotto)
sono sue da scegliere.

**Errore mio trovato durante il lavoro.** Il primo giro ha trattato un timeout di MT5
come "storico finito" e ha salvato D1 vuote per quattro coppie (zero segnali): ora tre
tentativi, mai cache vuote, simboli con storico corto esclusi e dichiarati.

---

## 2026-10-07 — Analisi approfondita: tre errori di gestione, sprechi AI, documenti superati

**Problema.** Dopo due giorni di paper l'utente ha chiesto una verifica degli errori,
senza consumare il credito OpenAI (l'analisi non ha fatto chiamate al modello).

**Trovati e corretti** (ADR 0027):
- *MFE/MAE non aggiornati sul tick*: un trade chiuso dal tick risultava con un massimo
  inferiore all'uscita (XAUUSD +2,07R, MFE 1,54R). Falsava proprio i dati per giudicare
  lo stop mobile. La validazione dei trade ora rifiuta un'uscita oltre le escursioni.
- *Stop nella barra d'innesco ignorato* per gli ordini STOP/LIMIT: errore ottimistico,
  il paper poteva "salvare" un trade che il broker avrebbe chiuso.
- *Solo le ultime 4 ore di barre M1*: dopo un fermo più lungo stop e target andavano
  persi senza traccia. Ora si legge dall'ultimo controllo e un buco viene annotato.
- *Report ombra* (`report_shadow`) rotto su un campo rimosso (`expected_rr`) e avviato
  anche con argomenti sconosciuti: ora rifiuta l'argomento prima di collegarsi.
- *README e `verifica.bat`* dicevano "il codice per inviare ordini non esiste": falso
  dalla fondazione LIVE (ADR 0025). Riscritti.

**Sprechi AI.** ~100 tesi al giorno da ~19 mila token d'ingresso, 85% WAIT. Tagliate le
chiamate certamente inutili (conto che non può aprire, freno notizie, evento, mercato
chiuso). **Non** ridotte le tesi utili né il prompt: cambierebbe la strategia AI, va
deciso con i dati.

**Lasciati com'erano, dichiarati.** 357 avvisi di mypy, quasi tutti di tipo (annotazioni,
`Any`) e preesistenti; quelli con aria di errore sono stati controllati uno per uno. Il
tetto `max_position_size_lots` (0,5 lotti) fa rischiare a US500 ~7 € invece di ~47 €: in
R non cambia nulla, in euro US500 pesa un settimo degli altri.

---

## 2026-10-07 — Stop mobile misurato in ombra, posizioni correlate, avviso sull'AI

**Problema.** Trade arrivati a +40/+54 € e chiusi allo stop a −47 €: l'utente propone
uno stop mobile, attivabile e disattivabile. Inoltre il credito OpenAI si è esaurito di
notte e il conto AI è rimasto fermo per ore senza alcun avviso visibile.

**Analisi prima di decidere.** Replay minuto per minuto dei 14 trade chiusi: le regole a
+1R migliorano il totale da +0,24R a +2,2/+3,2R, ma tutto il vantaggio viene da 3 trade e
nessuno stop preso è poi tornato al target. Il trailing a 0,5R taglia i vincenti migliori.

**Scartato.** Accendere subito una regola (campione troppo piccolo, rischio di accorciare
i vincenti con target 2R); bloccare subito le posizioni correlate (la correlazione
amplifica anche i guadagni: il 6 ottobre US100 e US500 insieme hanno vinto entrambi).

**Deciso.** Tre regole calcolate **in ombra su ogni trade** (confronto sugli stessi trade,
in Performance), interruttore per conto **spento di default**, regola fissata
all'apertura e statistiche separate; misura delle posizioni correlate dallo storico;
monitor del provider AI con avviso in ogni pagina, eventi di guasto/ripresa e consumo
giornaliero. Si rivaluta con 30-40 trade per conto. Dettagli: ADR 0027.

---

## 2026-10-06 — Login della dashboard e ascolto solo su localhost

**Problema.** La dashboard ascoltava su tutte le interfacce senza autenticazione:
chiunque sulla stessa rete Wi-Fi poteva vedere il conto e usare i comandi (kill switch,
chiusura posizioni, limiti). L'utente vuole in prospettiva accedervi da fuori.

**Scartate.** Pubblicare su GitHub Pages: ospita solo file statici, non può fare da
proxy verso il PC né custodire il token del backend. Aprire una porta sul router:
espone il PC a Internet. Login gestito dal backend Python: la dashboard resterebbe
comunque aperta in lettura (le pagine sono renderizzate da Next).

**Deciso.** Dashboard su `127.0.0.1` (`next dev/start -H 127.0.0.1`) e login con nome
utente e password nel frontend: impronta scrypt in `.env.local` (scritta dallo script
`npm run imposta-password`, mai la password in chiaro), cookie di sessione firmato
HMAC-SHA256, middleware **fail-closed** (senza configurazione tutto chiuso, le API
rispondono 401), controllo di stessa origine su login e azioni, blocco di 15 minuti
dopo 5 errori per IP più un tetto globale, ritardo fisso sugli errori. Cambiare
password rigenera il segreto: tutte le sessioni decadono. L'accesso remoto, se
approvato, passerà da una rete privata (Tailscale), non da Internet.

**Dettaglio trovato dal test.** Il separatore dell'impronta è `:` e non `$` (formato
classico): Next, leggendo `.env.local`, espande `$` come variabile e alterava
l'impronta, rifiutando la password corretta.

---

## 2026-10-06 — Notizie da fonti gratuite: freno di rischio ed evidenze per l'AI

**Problema.** Il sistema non vedeva eventi fuori calendario (dichiarazioni, geopolitica).
L'utente chiede notizie utili all'agente e leggibili, partendo da fonti gratuite.

**Confrontate** (prezzi verificati il 2026-10-06): agenzie istituzionali (Dow Jones
Newswires 5.000-50.000 $/anno per utente, Reuters su preventivo), squawk (Newsquawk
199-399 $/mese, API solo Enterprise), aggregatori (Marketaux 0-199 $/mese, Finnhub
gratuito con chiave), GDELT (gratuito, ma HTTP 429 anche rispettando i limiti).

**Deciso.** 14 feed RSS gratuiti e verificati: banche centrali (Fed, BCE, BoE, BoJ,
fonte primaria), testate (BBC, Guardian, Al Jazeera, CNBC, MarketWatch), siti forex
(FXStreet, investingLive). Lettura sicura (`defusedxml`), filtro di regola per tema e
asset, conferma = fonte ufficiale o almeno due editori indipendenti, classificazione AI a
lotti (impatto, asset, sintesi in italiano, voce non verificata). Le notizie sono un
**freno** (alto impatto + confermata + recente + non voce → nessuna apertura sugli
strumenti toccati per 60 minuti) e un'**evidenza citabile** nella tesi AI (prompt 2.1.0,
nuova versione di strategia: la performance resta separata per versione). Mai un motivo
per entrare. Abbonamento a pagamento solo se i dati paper mostrano un beneficio.

**Limite dichiarato.** Il confronto "AI con notizie / senza" è per versione nel tempo, non
simultaneo: un confronto pulito richiederebbe un terzo conto paper (costo AI doppio).

---

## 2026-10-06 — Grafici a candele in tempo reale e tema scuro

**Deciso.** Grafici con **Lightweight Charts** di TradingView (v5, Apache 2.0, nessun
servizio esterno): l'unica dipendenza nuova del frontend, scelta perché nata per le
candele finanziarie in tempo reale e leggera. Endpoint `/api/chart/{simbolo}` con orari
convertiti da ora broker a UTC, candela in formazione inclusa, posizioni paper come
linee e trade chiusi come marcatori; aggiornamento da 2 s (1m) a 30 s (1D).

Tema scuro con **variabili CSS**: in scuro ogni scala di colore si inverte, così tutte le
classi esistenti hanno già il loro equivalente e non serve una variante per ogni
componente. Preferenza salvata nel browser (chiaro, scuro, come il sistema).

---

## 2026-10-06 — Comandi dalla dashboard, limiti modificabili, operatività 24 ore

**Problema.** L'utente vuole guidare il sistema dall'interfaccia (fermarlo, pausa,
mercati, strategie, chiusure, limiti) e che operi anche di notte.

**Deciso.** Comandi persistenti con registro; kill switch e chiusure manuali dalla
dashboard; limiti modificabili solo dall'utente, entro tetti di sicurezza, con motivo,
conferma e registro, mai in LIVE (supera l'invariante 4 "limiti non modificabili a
runtime"); take profit giornaliero facoltativo; perdita settimanale anche nel paper;
sessioni 00:00-23:59 per scelta dell'utente. Dashboard riorganizzata in sei pagine,
usabile da telefono → **ADR 0026**.

**Da fare, segnalato.** L'esposizione correlata (`RISK_MAX_CORRELATED_EXPOSURE_PCT`) non
è applicata: US100 e US500 aperti insieme sono di fatto un rischio doppio.

---

## 2026-10-06 — Fondazione dell'esecuzione reale (fase 11), LIVE disabilitato

**Problema.** Preparare l'invio di ordini reali senza che possa partire prima di 30 trade
paper validati, infrastruttura sana, kill switch provato e conferma esplicita.

**Deciso.** Unici scrittori `execution/mt5_terminal.py` (verso MT5) e
`execution/gateway.py` (verso il terminale); test di sola lettura esteso a tutto il
backend. Idempotenza con chiave `UNIQUE` registrata prima dell'invio, riconciliazione su
ogni esito ignoto (mai reinvio), SL/TP lato server obbligatori con chiusura protettiva,
Risk Engine sul conto reale (budget dallo storico deal), PostgreSQL obbligatorio, breaker
persistente, dieci gate di attivazione, conferma utente solo da file scritto a mano.
Sostituisce, per i due moduli autorizzati, la regola "nessun codice di scrittura"
dell'ADR 0004 → **ADR 0025**.

**Trovato.** Sullo stesso conto demo opera un altro sistema automatico (magic 770001,
GOLD): le sue perdite rientrano nel budget giornaliero del conto. Va separato prima del
LIVE.

---

## 2026-10-05 — MVP operativo PAPER end-to-end

**Problema.** L'utente deve poter aprire una webapp e vedere il sistema lavorare da
solo: XM → analisi → AI → piano → ingresso paper → gestione → uscita → P/L.

**Deciso.** Scheduler in background nel backend, scanner con fattori leggibili, due
conti paper (QUANT e AI) per il confronto, `TradePlan` deterministico, gate di rischio
puro, esecutore paper con spread/slittamento/commissioni/swap, gestione su barre M1 e
tick, conti su file JSON atomico (PostgreSQL facoltativo), dashboard Next.js. Corretti
definitivamente: RR e tipo d'ingresso dal codice (tesi 2.0.0), regola unica
d'ambiguità intrabar, versioni di strategia come etichette che non bloccano.

**Alternative scartate.** PostgreSQL obbligatorio per i conti paper (spesso spento in
sviluppo: la piattaforma non partirebbe); worker separato per lo scheduler (due
processi da avviare per un MVP a utente singolo, senza beneficio oggi) → **ADR 0024**.

---

## 2026-10-05 — Fase 5B: il percorso nell'ingresso deve coincidere con l'id (pipeline 1.2)

**Problema.** Con le sezioni globali sotto `global_context`, il modello citava
`global_context.regime` (id inesistente): 4 tesi su 7 rifiutate nella pipeline 1.1 per
un errore di impaginazione nostro. Inoltre 2 filtri su 12 non disponibili per 7 punti
contro un massimo arbitrario di 6.

**Alternativa scartata.** Accettare `global_context.X` come alias di `X` nella
validazione: correggerebbe a valle un ingresso costruito male.

**Deciso.** Sezioni globali al primo livello dell'ingresso (input 1.1.0), testo del
prompt sugli id aggiornato (1.2.0), massimo 8 punti nel filtro (output 1.2.0) → ADR
0023 §9.

---

## 2026-10-05 — Fase 5B: prima le evidenze, poi il verdetto (filtro e tesi 1.1.0)

**Problema.** Primo giro reale: il filtro AI ha respinto 10 piani SHORT su 11. In 5 casi
i suoi motivi *sostenevano* lo short ("il piano short è coerente con il trend
ribassista") e il verdetto era comunque REJECT; in altri 5 ha letto il piano come
"long". Nello schema il verdetto era il **primo** campo: il modello si impegnava e poi
giustificava. Il piano stava in fondo a ~19k token di ingresso.

**Alternativa scartata.** Tenere i dati del primo giro e "correggere" i verdetti
incoerenti a posteriori: sarebbe un filtro che il modello non ha espresso.

**Deciso.** Filtro: `plan_direction_read` (ricopiato e verificato), `points_for`,
`points_against`, **poi** `verdict`; REJECT richiede un punto contro sostenuto, APPROVE
uno a favore; piano in testa all'ingresso. Tesi: decisione e livelli **dopo** scenario,
evidenze e incertezze. Prompt: i nomi delle sezioni non sono evidence_id. Versione
della pipeline su ogni decisione: le metriche usano una sola versione; i record 1.0.0
restano nel diario come prova del difetto → **ADR 0023 §9**.

---

## 2026-10-05 — Fase 5B: AI Trade Thesis in shadow mode, tre gruppi

**Problema.** Misurare se l'AI aggiunge valore a una regola quantitativa, con
decisioni fissate prima dell'esito e senza alcuna strada verso il conto.

**Alternative scartate.** Mostrare all'AI il piano quant anche per la tesi (C
diventerebbe una variante di B); riparare tesi quasi valide; usare la confidence come
probabilità; simulare con volume e P/L in euro (porterebbe la valutazione monetaria in
un package che deve restare lontano dal conto).

**Deciso.** Gruppi A (baseline quant **nuova e dichiarata**: lo scanner non esiste
ancora), B (A + filtro AI), C (tesi AI indipendente). Claim decisivi senza evidenze
esistenti → REJECTED; claim accessori → marcati unsupported. Condizioni tipizzate su
prezzi reali; invalidazioni già vere rifiutate. RR dichiarato = RR calcolato.
`ShadowTradePlan` in prezzi e R, senza volume. Snapshot immutabili decisione/esito;
`OutcomeEvaluator` solo su barre successive, regole conservative. Replay con taglio
all'istante. Metriche con intervalli bootstrap e soglie di campione → **ADR 0023**.

---

## 2026-10-05 — Fase 5A: condizioni di invalidazione verificabili da un programma

**Problema.** Nel primo output reale le invalidazioni erano in parte prosa travestita
("CORRELATION BELOW 0.41" su un conflitto, "REGIME CHANGES_TO" senza destinazione);
dopo la prima correzione lo scenario risk-on era invalidato dalla propria conferma,
perché il contratto non sapeva dire "se non succede".

**Alternativa scartata.** Lasciare le condizioni in testo e giudicarle in fase 9: si
giudicherebbero col senno di poi.

**Deciso.** `target_state`, direzione `NOT_REACHED`, tabella delle combinazioni
ammesse e semantica dell'orizzonte; una condizione non verificabile rende l'analisi
`UNAVAILABLE`. `currency_strength.X` accettato come `X` (la validazione era più
severa del progetto). Prompt 1.3.0, output 1.2.0 → **ADR 0022 §3**.

---

## 2026-10-05 — Fase 5A: AI Market Analyst in sola lettura

**Problema.** Prima di dare all'AI un ruolo operativo bisogna misurare se le sue
analisi valgono qualcosa, senza hindsight.

**Alternative scartate.** Testo libero letto da una persona (non misurabile);
riparare l'output invalido (analisi che il modello non ha scritto); far calcolare il
regime all'AI (resta del motore deterministico).

**Deciso.** `ai/` non importa broker, rischio, esecuzione (AST). Contratto
`MarketAnalysis` senza campi operativi; validazione Pydantic + regole (regime del
motore, refs esistenti, universo, evidenze contrarie con MIXED/UNCERTAIN, incertezze
con l'informazione mancante, limiti alla confidence, nessun contenuto operativo).
Output invalido = `UNAVAILABLE`. Record immutabili con sha256 nel diario (scrittura
esclusiva) e in `ai_analyses`. OpenAI Responses API strict, temperatura 0, snapshot
registrato; Anthropic pronto ma non verificato (chiave assente) → **ADR 0022**.

---

## 2026-10-05 — Correzioni alla fase 4.5: qualità per fonte, DXY come verifica, calendario a fasi

**Problema.** Una sola qualità mescolava domini diversi; VIX e tassi giornalieri
potevano essere letti come intraday; DXY e forza a grafo contavano due volte il
dollaro; il grafo non reggeva outlier, cross fermi e volatilità diverse; il calendario
non distingueva prima, durante e dopo l'evento.

**Deciso.** Qualità per dominio + complessiva con regole esplicite; ogni fonte dichiara
frequenza, ultima osservazione, ritardo di pubblicazione e uso previsto; grafo come
misura primaria, DXY come `CROSS_CHECK` (rimosso il conflitto `DXY_CONTRO_FORZA_USD`);
grafo pesato 1/varianza, cross fermi esclusi, outlier rimossi; calendario con
previous/forecast/actual, `actual_seen_at` contro l'hindsight e fasi
`PRE_EVENT`/`EVENT_WINDOW`/`POST_EVENT`/`ACTUAL_AVAILABLE` → **ADR 0021**.

---

## 2026-10-05 — Fase 4.5: una rilettura per le serie ferme a mercato aperto

**Problema.** Misurato sul conto: alla prima lettura del lunedì MT5 restituì USDCAD
H4 fermo a venerdì sera, mentre H1 e M15 erano vivi; pochi minuti dopo lo storico era
completo. Il sistema l'aveva marcato `STALE` ed escluso (corretto), ma così il DXY
ricostruito mancava su H4.

**Deciso.** Se una serie risulta ferma con il mercato dello strumento aperto
confermato, la si rilegge una volta dopo mezzo secondo. Se è ancora ferma resta
`STALE` ed esclusa. Test con il terminale finto che restituisce la serie ferma solo
alla prima lettura.

---

## 2026-10-05 — Fase 4.5: gate "mercato aperto" ripetuto in piena sessione

Lunedì 08:55 UTC, tutti i mercati aperti: ora broker confermata da 10 quotazioni
concordi; EURUSD, USDJPY, GOLD, SILVER, US100Cash, US30Cash tutti `VALID`/
`OPEN_CONFIRMED` con età fra 0,6 e 4,6 secondi; size e perdite coincidenti con MT5
al centesimo; 14/14 test live verdi. Misura in `docs/validazione/xm_demo_2026-10-05.json`.
Chiude il punto 1 aperto in FOUNDATION_VALIDATION §7.

---

## 2026-10-05 — Fase 4.5: persistenza best effort per osservare, obbligatoria per operare

**Problema.** La pausa sulle scritture dopo un guasto (fase 4) è giusta per
l'intelligence read-only, sarebbe un fail-open per lo stato del rischio e per gli
intenti di esecuzione.

**Deciso.** `PersistenceRequirement.BEST_EFFORT` / `REQUIRED`.
`MarketPersistence.require(REQUIRED, ...)` solleva `PersistenceUnavailableError`
(blocca l'operatività) se la persistenza non è affidabile; un database mai provato
non lo è. System Health mostra la persistenza dei contesti come componente non
critico; PostgreSQL resta critico per `can_trade`. Le fasi 6 e 11 dovranno chiamare
la guardia → **ADR 0019**.

---

## 2026-10-05 — Fase 4.5: MIXED anche dalla discordanza fra dimensioni

**Problema.** MIXED nasceva da una soglia sul composito (o da conflitti): due
dimensioni forti in versi opposti potevano mediarsi in un RISK_ON.

**Deciso.** Se almeno un'evidenza è oltre +0,5 e una oltre −0,5, il sentiment è
MIXED qualunque sia il composito; le dimensioni discordi sono nel contesto. Nuovi
conflitti: VIX contro sentiment, DXY ricostruito contro forza USD del grafo.
Conseguenza sui test: gli scenari sintetici di RISK_ON e RISK_OFF ora hanno una
volatilità coerente con la direzione → **ADR 0020**.

---

## 2026-10-05 — Fase 4.5: intermarket con base storica (supera "CONFIRMING")

**Problema.** "Una correlazione non significa relazione confermata." Lo stato
`CONFIRMING` diceva più di quanto sapessimo, e le due finestre (120 e 20) si
sovrapponevano.

**Deciso.** Corrente (ultimi 40 rendimenti) contro base storica (tutti i precedenti,
senza sovrapposizione), con dimensioni dei campioni, significatività della base,
dispersione, stabilità e differenza di Fisher. Stati `CONSISTENT`, `WEAKENING`,
`DIVERGING`, `UNSTABLE`, `NO_BASELINE`, `INSUFFICIENT_DATA`. Trovato e corretto dai
test: con correlazione perfetta il t non era definito e la base risultava assente
→ **ADR 0020**.

---

## 2026-10-05 — Fase 4.5: forza delle valute con un modello a grafo (supera la media dei cross)

**Problema.** La media dei momentum dei cross valutava ogni valuta su un insieme
diverso di coppie e pesava di più le coppie poco volatili.

**Deciso.** Minimi quadrati sulle equazioni `r(A,B) = x(A) − x(B)` di tutti i cross,
con `Σx = 0`; normalizzazione per volatilità sulla serie stimata della valuta;
residuo come misura di coerenza; valute senza cross `UNKNOWN`. Verificato sul conto
reale: stessa classifica (correlazione 0,994); togliendo quattro coppie del dollaro
la media perde l'USD e sposta le altre di 0,47, il grafo di 0,02 → **ADR 0020**.

---

## 2026-10-05 — Fase 4.5: XM non è l'unica fonte dell'intelligence (supera le esclusioni dell'ADR 0016)

**Problema.** L'utente: XM/MT5 è autorevole per ciò che si negozia, non deve essere
l'unica fonte della Global Intelligence. In fase 4 VIX, DXY e tassi erano esclusi
perché su XM esistono solo come futures o ETF.

**Scartato.** Futures XM al posto degli indici (scadono, cambiano nome); fonti a
pagamento (non necessarie oggi); scraping.

**Deciso.** Categorie `TRADABLE_INSTRUMENT`, `REFERENCE_INSTRUMENT`, `MACRO_SERIES`;
cinque interfacce di provider; fonti gratuite e senza chiave verificate dal vivo:
Forex Factory (calendario), CBOE (VIX), Tesoro USA (curva). DXY ricostruito con la
formula ICE dai cambi XM. Macro e news senza provider. Nessun dato finto fuori dai
test; un solo punto in rete; osservazioni esterne filtrate per disponibilità (nessun
look-ahead). Confronto completo in `docs/EXTERNAL_PROVIDERS.md` → **ADR 0018**.

---

## 2026-10-05 — Fase 4.5: `EventImportance` della fase 0 unificato

Il segnaposto (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`) non era usato; il calendario ne
richiedeva uno con `HOLIDAY` e `UNKNOWN`. Unificati in uno solo, con la proprietà
`blocks_trading` (HIGH e CRITICAL).

---

## 2026-10-04 — Fase 4: la persistenza salta le scritture per 30 secondi dopo un guasto

**Problema.** Misurato dal vivo: con PostgreSQL spento ogni scrittura attendeva ~4
secondi il rifiuto della connessione. La prima analisi globale, che archivia 37
specifiche nuove, durava 146 secondi — per il database, non per MT5 (ogni chiamata
al terminale dura millisecondi).

**Scartato.** Abbassare il timeout di connessione: un database lento ma vivo
perderebbe scritture valide.

**Deciso.** Dopo una scrittura fallita, le successive per 30 secondi non vengono
tentate e sono contate in `writes_skipped`. La stessa analisi dura 19 secondi.
Coerente con la regola esistente: perdere una riga di storico è grave, smettere di
osservare il mercato perché il database non risponde è peggio.

---

## 2026-10-04 — Fase 4: ogni tick si valuta all'ora della sua lettura

**Problema.** Trovato sul conto reale: il lettore globale valutava i tick contro
l'istante d'inizio lettura. Con 37 strumenti letti in sequenza, i tick degli ultimi
risultavano "dal futuro" fino a 165 secondi, e quattro cross venivano dichiarati
`INCONSISTENT`.

**Deciso.** Il tick si valuta contro l'ora in cui è stato letto; l'istante del
contesto (`as_of`) e il taglio delle barre restano quelli d'inizio. Test di
regressione verificato nei due versi (fallisce col difetto reintrodotto).

---

## 2026-10-04 — Fase 4: freschezza di una serie sulla barra in formazione

**Problema.** Il criterio progettato all'inizio — "l'ultima barra chiusa è recente"
— al lunedì avrebbe dichiarato ferme per ore le serie H1 e H4: l'ultima barra
*chiusa* è quella del venerdì anche se il mercato è riaperto da minuti.

**Deciso.** Una serie è `FRESH` se la barra in formazione si è aperta da meno di
una durata di barra più due minuti, con età verificata dall'ora broker. Altrimenti
`STALE` (esclusa) a mercato non chiuso, `CLOSED` a mercato dedotto chiuso,
`UNVERIFIED` con ora broker ignota → **ADR 0017**.

---

## 2026-10-04 — Fase 4: MIXED solo se lento e veloce si contraddicono oltre soglia

**Problema.** La prima regola dichiarava MIXED una valuta o un gruppo se *un
qualunque* timeframe era oltre soglia nel verso opposto a un altro. Il momentum
normalizzato di un singolo timeframe ha rumore dell'ordine di 1: sui dati sintetici
quasi ogni strumento risultava MIXED, compreso l'oro in uno scenario con l'oro
costruito in rialzo.

**Deciso.** MIXED se il blocco lento (D1, H4) e il blocco veloce (H1, M15) superano
**entrambi** la soglia in versi opposti. È la stessa regola della `TRANSITION` del
regime, applicata allo strumento.

---

## 2026-10-04 — Fase 4: rischio da calendario UNKNOWN, mai LOW

**Problema.** Senza un calendario economico (ANALISI §3.3) il rischio da eventi non
è misurabile. Riportarlo come "basso" sarebbe un fail-open.

**Deciso.** `EconomicCalendar` è un'interfaccia; oggi c'è solo
`NullEconomicCalendar` (`available = False`). Il livello da calendario è `UNKNOWN`,
la dimensione macro è `DEGRADED`, e quindi il contesto non è mai `GOOD`. A parte,
un'incertezza **implicita nei prezzi** (volatilità, shock, intermarket, conflitti),
etichettata come tale. La scelta di un provider resta dell'utente.

---

## 2026-10-04 — Fase 4: contesto globale quantitativo a dimensioni separate

**Problema.** Rispondere a "cosa succede sui mercati?" senza LLM, in modo
deterministico, senza look-ahead, e senza appiattire evidenze contraddittorie in un
punteggio.

**Scartato.** Un `global_score` 0-100 (la media di "azioni forti" e "yen forte" è
"neutro", cioè l'informazione opposta); un LLM che legge i grafici (non
riproducibile, e vietato in questa fase); relazioni intermarket con segno atteso
scritto nel codice (sui dati reali USDCAD-petrolio oggi non ha relazione, l'oro è
correlato positivamente all'S&P 500).

**Deciso.** Motore puro (`global_market/engine.py`) con dimensioni separate, ognuna
con la sua qualità e il suo dettaglio D1/H4/H1/M15; regime per regole in ordine di
precedenza; conflitti espliciti che trasformano RISK_ON/RISK_OFF in MIXED; serie
tagliate all'istante di analisi; impronta sha256 delle barre più versione del
motore; uscita identica byte per byte a parità di ingresso. Tutte le soglie in
`parametri.py`, dichiarate euristiche non validate → **ADR 0017**.

---

## 2026-10-04 — Fase 4: universo di sola osservazione distinto dalla whitelist (precisa la regola del lettore)

**Problema.** `market_data/reader.py` dice che uno strumento non autorizzato non
deve nemmeno comparire in un'analisi. Il contesto globale ha bisogno dei 28 cross e
di indici che l'utente non ha chiesto di operare.

**Scartato.** Allargare la whitelist: confonde "posso guardarlo" con "posso
operarlo".

**Deciso.** La whitelist resta il perimetro operativo e la regola del lettore resta
valida per l'analisi degli strumenti. Si aggiunge un **universo di riferimento** di
37 strumenti, di sola osservazione, con le grafie XM confinate in
`config/global_universe.py` e l'identità verificata dal broker all'avvio. Un test
sull'AST impedisce all'intelligence globale di importare valutazione, trade planner,
rischio, esecuzione o AI. Esclusi VIX e USDX (solo futures con scadenza su XM) e le
obbligazioni (solo ETF, 189 barre D1) → **ADR 0016**.

---

## 2026-10-04 — Fase 4: `GlobalRegime` della fase 0 sostituito

**Problema.** `core/enums.GlobalRegime` esisteva dalla fase 0 come segnaposto
(`RISK_ON`, `RISK_OFF`, `NEUTRAL`, `TRANSITION`, `STRESS`), non usato da nessun
modulo. Gli stati richiesti sono `RISK_ON`, `RISK_OFF`, `MIXED`, `TRANSITION`,
`EVENT_DRIVEN`, `UNCERTAIN`.

**Deciso.** Sostituito. `NEUTRAL` non distingueva "evidenze in disaccordo" (MIXED)
da "evidenze insufficienti" (UNCERTAIN); `STRESS` è diventato `EVENT_DRIVEN`.

---

## 2026-10-04 — Gate "mercato realmente aperto" superato alla riapertura domenicale

**Problema.** Il consolidamento della foundation aveva lasciato aperto il ramo
"mercato aperto" sul broker vero (validazione fatta di domenica a mercati chiusi).

**Fatto.** Alle 21:15 UTC di domenica il forex stava riaprendo: ora broker
`MEASURED` (+10800 s, EURUSD e GBPUSD concordi), EURUSD `VALID`/`OPEN_CONFIRMED`
con età esatta di 9,5 s, gli altri `CLOSED_INFERRED` con età esatta; 13/13 test live
verdi. Misura conservata in `docs/validazione/xm_demo_2026-10-04_riapertura.json`.
Da ripetere in piena sessione come controllo.

---

## 2026-10-04 — Il verso dell'errore di `tick_value` era sbagliato (correzione della voce "Calcolatori ammessi")

**Problema.** La voce più sotto, l'ADR 0012, l'analisi §6.2 e diversi commenti
dicevano che derivare il valore da `tick_value` avrebbe **sovrastimato** la size
di metalli e indici del 12,5%, violando il limite dell'utente. Rifacendo i conti
con i valori misurati nel consolidamento: su un conto in EUR, un `tick_value` in
dollari letto come euro **sovrastima la perdita per lotto** (un dollaro vale 0.8887
euro), quindi la size risulta **sottostimata** dell'11%. GOLD, stop a 10 dollari,
budget 100 EUR: 0.10 lotti invece di 0.11. Il nozionale, invece, è sovrastimato del
12,5%.

**Perché conta.** La decisione (chiedere il valore al broker) resta giusta, ma per
un motivo diverso da quello scritto: su *questo* conto l'errore era prudente. Su un
conto in una valuta che vale meno del dollaro (es. JPY) sarebbe stato un fail-open
di un fattore 157. Una motivazione sbagliata è un rischio: il giorno in cui qualcuno
la verifica e la trova falsa, può concludere che la regola non serve.

**Deciso.** Corretti ADR 0012 (nota di correzione, il testo originale resta),
analisi §6.2, commenti nel connettore e nei test. La voce originale qui sotto **non
si modifica**. Nuovo test `test_verso_dell_errore_sul_conto_reale_in_eur` con i
numeri reali.

---

## 2026-10-04 — Valori reali congelati nei test, confronto live su richiesta

**Problema.** "Confronta con MT5 reale quando possibile" in due sensi diversi: i
test devono girare ovunque (anche senza Windows), ma un confronto con valori
registrati mesi prima non dice niente del broker di oggi.

**Scartato.** Solo test live: la suite smetterebbe di girare in CI e a mercato
chiuso. Solo valori registrati: il giorno in cui XM cambia le condizioni nessuno
se ne accorge.

**Deciso.** Due livelli. `python -m backend.validazione_foundation` misura sul
conto reale e scrive `docs/validazione/xm_demo_<data>.json`; una copia congelata
in `backend/tests/dati/` è il termine di confronto dei test per strumento
(uguaglianza esatta su specifiche, size, normalizzazione; 0,1% su profitto e
margine, perché il finto converte a cambio fisso). I test in `backend/tests/live/`
confrontano il servizio con la chiamata grezza **nello stesso istante** e girano
solo con `RUN_LIVE_MT5=1`.

---

## 2026-10-04 — La size non supera il rischio, nemmeno di un centesimo

**Problema.** `size_for_risk` accettava una perdita attesa fino a rischio + 0.01,
per non scendere di un passo intero a causa dell'arrotondamento del broker.

**Deciso.** Tolleranza tolta. Il rischio per operazione è un limite dell'utente,
non una stima: se l'arrotondamento lo supera, si scende di un passo. Verificato
con test di proprietà (perdita richiesta di nuovo al broker ≤ budget) e sul conto
reale (la perdita al volume scelto è ≤ 100 EUR, un passo in più è > 100 EUR su
tutti e sei gli strumenti).

---

## 2026-10-04 — Il valore per unità si misura su mille lotti (supera "cento tick su un lotto")

**Problema.** Il calcolatore del broker arrotonda al centesimo. Con 1 lotto e 100
tick, US100Cash valeva 0.89 EUR per punto invece di 0.8887: +0,14%, e lo scarto da
`tick_value` risultava 12,36% invece del 12,52% degli altri strumenti.

**Verificato prima di cambiare.** Sul conto reale `order_calc_profit` accetta
volumi oltre `volume_max` ed è lineare nel volume: 1, 100, 1000 e 10000 lotti danno
lo stesso valore per unità a meno dell'arrotondamento.

**Deciso.** Cento tick su 1000 lotti (`VOLUME_DI_PROVA`). Il movimento resta piccolo
per la non linearità nel delta (ADR 0012); il volume grande toglie l'arrotondamento.
Il valore per unità serve al nozionale: la perdita allo stop non lo usa (voce
successiva).

---

## 2026-10-04 — Perdita allo stop sul movimento esatto (supera la linearizzazione nell'esposizione)

**Problema.** L'esposizione calcolava il rischio come `volume × distanza ×
valore_per_unità`. Misurato su EURUSD, 50 pip su 1 lotto: 446.38 EUR long, 442.43
EUR short. La formula lineare dà lo stesso numero ai due lati.

**Deciso.** `BrokerValuationService.loss_at_stop` (nuovi ordini) e
`open_position_risk` (posizioni aperte, dove lo stop può essere già in profitto e
il rischio è zero) chiedono al broker il profitto **da entry a stop**. È il numero
che il broker addebiterà davvero.

---

## 2026-10-04 — Un solo servizio di valutazione, verificato sull'AST

**Problema.** Dopo la fase 3 i numeri monetari venivano da tre posti: connettore,
calcolatore di esposizione, `SymbolInfo.normalize_volume`. La regola "il valore lo
dà il broker" (ADR 0012) era rispettata per disciplina.

**Scartato.** Una formula "verificata" nel nostro codice (`volume × contratto ×
Δprezzo × cambio`): richiede di conoscere cambio e convenzione per strumento, e la
misura sull'asimmetria di EURUSD mostra che anche una formula giusta in teoria
sbaglia.

**Deciso.** `backend/valuation/service.py` è l'unico modulo che calcola profitto,
perdita, margine, size e normalizzazione. Rimossi `MT5Connector.value_per_price_unit`
e `SymbolInfo.normalize_volume`. `test_valutazione_centralizzata.py` legge l'AST di
tutto il backend: fuori da un elenco motivato, nessun modulo legge `tick_value` o
chiama i calcolatori del broker. Il margine rifiuta volumi non normalizzati,
perché il broker non li valida (misurato) → **ADR 0014**.

---

## 2026-10-04 — Identità canonica degli strumenti, coppia economica distinta (supera "l'oro contribuisce una volta sola")

**Problema.** I nomi XM (`GOLD`, `US100Cash`) circolavano ovunque. E la vista per
valuta usava le valute dichiarate da XM: GOLD è USD/USD/USD, quindi un long oro
contribuiva una sola volta, come **lungo dollaro** — segno opposto al vero.

**Deciso.** `Instrument` con `canonical` (`XAUUSD`, `US100`), `broker_symbol` (solo
per il terminale), coppia economica `base/quote` (XAU/USD, US100/USD) e valute di
calcolo `profit/margin` dichiarate dal broker. Il nome canonico viene da un
catalogo esplicito (metalli), da regole verificabili (forex: sei lettere uguali alle
valute del broker; indici: classe dal percorso) o è marcato `dedotto`. Dal canonico
si risale solo fra gli strumenti già letti; un canonico ambiguo è un errore.

La vista per valuta usa la coppia economica: un long oro è lungo XAU e corto USD.
Il test `test_oro_contribuisce_una_volta_sola` è stato sostituito da
`test_oro_espone_xau_contro_usd`, con il motivo nella docstring → **ADR 0014**.

---

## 2026-10-04 — Stati di mercato espliciti, nessun "chiuso certo"

**Problema.** `MarketPhase.OPEN` / `CLOSED` suggerivano due certezze, ma MT5 non
espone le sessioni (ADR 0011): "chiuso" è sempre una deduzione, e "aperto" lo è
solo se una quotazione fresca lo conferma.

**Deciso.** `MarketStatus`: `OPEN_CONFIRMED`, `CLOSED_INFERRED`, `UNKNOWN`.
`MarketPhase` rinominata di conseguenza (`OPEN_CONFIRMED`, `PARTIALLY_OPEN`,
`CLOSED_INFERRED`, `UNKNOWN`) e calcolata sullo stato di mercato, non
sull'operatività (uno spread caro non dice che il mercato è chiuso).
`QuoteQuality` ne fa un'invariante del costruttore: `CLOSED_INFERRED` richiede
`is_inference=True`, `OPEN_CONFIRMED` richiede `VALID` e non può essere
un'inferenza. Il dato e la politica restano separati: `QuoteQuality` (valido,
vecchio, mancante, chiuso, incoerente) e `QuoteState` (che aggiunge `trade_mode`
e spread).

---

## 2026-10-04 — L'età dei dati si calcola con l'ora broker (correzione di un fail-open)

**Problema.** Trovato rileggendo il codice, non da un test: i timestamp di MT5
sono in ora broker (XM UTC+3) etichettata UTC, e l'età veniva calcolata contro
l'ora di sistema. **Un tick fermo da 2h59m risultava fresco.** Il terminale finto
etichettava in UTC vero e il test reale era di sabato: nessuno dei due poteva
accorgersene.

**Secondo difetto.** Lo scarto si deduceva dal tick che si stava valutando (ADR
0010): un tick vecchio esattamente di tre ore sarebbe stato letto come fresco con
scarto diverso. Ragionamento circolare.

**Scartato.** Scarto fisso in configurazione: due volte l'anno sbagliato di
un'ora, e di nuovo in direzione fail-open.

**Deciso.** `BrokerClock.age_bounds` è l'unico calcolo di età; senza scarto
affidabile l'età è un intervallo di ±14 ore e nessuna quotazione è `VALID` (si può
però dedurre il mercato chiuso con l'età minima). Lo scarto si stabilisce solo con
due quotazioni concordi (`observe_many`, sulla whitelist, all'avvio e prima di ogni
analisi); `observe` sul singolo tick conferma soltanto. Rimosso
`get_tick(require_fresh=...)` dal connettore, che faceva il confronto sbagliato.
`Fresh.as_of` di un tick è l'ora UTC vera, o l'etichetta meno 14 ore se lo scarto
non è noto. Il terminale finto ora etichetta in UTC+3 → **ADR 0015**.

---

## 2026-10-04 — `TradeIntent`: il volume si calcola anche quando non è eseguibile

**Problema.** Con una quotazione non valida si poteva non calcolare niente, o
calcolare e segnare l'intent come bloccato.

**Deciso.** Si calcola comunque (volume, perdita, margine) e si dichiarano i
blocchi. Serve al journal per spiegare cosa sarebbe successo. `executable` vuol dire
solo "i dati sono validi": quotazione `VALID`, mercato `OPEN_CONFIRMED`, simbolo
coerente, strumento operabile, size positiva, perdita entro il budget. Non è
un'approvazione: quella resta del Risk Engine (ADR 0005), che non esiste ancora.

---

## 2026-10-04 — Lo spread si misura in rapporto all'ATR di H1

**Problema.** `RISK_MAX_SPREAD_POINTS=35` rendeva intrattabili GOLD (53 punti),
SILVER (93) e tutti gli indici (600-616), benché i loro spread siano normali. Un
punto è l'ultima cifra del prezzo, e il prezzo ha un numero di cifre diverso per
ogni strumento: 35 punti sono 3,5 pip su EURUSD e un ventesimo di punto indice su
US100Cash.

**Perché conta più di quanto sembri.** Il sintomo sarebbe stato il peggiore
possibile: nessuna proposta su metalli e indici, senza errori e senza messaggi.
Un'assenza, non un guasto.

**Scartato.** Un limite per strumento in `.env`: dieci numeri da mantenere a mano,
che invecchiano quando il broker cambia le condizioni.

**Deciso.** Rapporto spread/ATR come controllo primario (`RISK_MAX_SPREAD_ATR_RATIO`,
default 0.30), adimensionale e quindi confrontabile. Il tetto in punti resta largo,
come rete di sicurezza per quando l'ATR non è calcolabile.

**Seconda correzione, dalla misura.** L'ATR di riferimento è quello del timeframe
**operativo** (H1), non di esecuzione (M5). Alla chiusura del venerdì, con l'ATR di
M5, EURUSD risultava a 0.74 e USDJPY a 1.46 — perché l'ATR a cinque minuti collassa
quando il mercato rallenta, e tutti gli strumenti sarebbero risultati troppo cari
proprio nei momenti di calma. Lo spread va confrontato con il movimento che
l'operazione si aspetta di catturare.

Con H1, misurato sul conto: GOLD 0.027, US30Cash 0.043, US100Cash 0.060, EURUSD
0.124, SILVER 0.188, USDJPY 0.192.

→ [ADR 0013](docs/adr/0013-spread-relativo-all-atr.md)

## 2026-10-04 — Il calcolatore del broker non è lineare: movimento di prova piccolo

**Problema.** `value_per_price_unit` chiedeva al broker il profitto per un
movimento di 1.0 di prezzo. Su EURUSD ha restituito **47.056 EUR invece di
88.810**: un errore del 47% sull'ingresso principale del dimensionamento.

**Causa.** `order_calc_profit` converte il profitto nella valuta del conto usando
il prezzo di **chiusura**. Per una coppia che ha la valuta del conto come base —
EURUSD o EURJPY su un conto in EUR — quel prezzo *è* il tasso di conversione:
chiedendo il profitto da 1.125 a 2.125, la conversione avviene a 2.125.

**Deciso.** Cento tick di movimento, e il risultato si divide per il delta.
Abbastanza grande da non perdersi negli arrotondamenti del broker (che restituisce
due decimali), abbastanza piccolo da non spostare il cambio.

**Conseguenza sul terminale finto.** Il mock riproduce la non linearità per le
coppie con la valuta del conto come base: senza quella fedeltà, il difetto non
sarebbe riproducibile in un test, e sarebbe tornato alla prima rifattorizzazione.

→ [ADR 0012](docs/adr/0012-valore-monetario-dal-broker.md)

## 2026-10-04 — I calcolatori del broker non sono scritture: regola corretta

**Problema.** La lista delle funzioni MT5 vietate fino alla fase 11 includeva
`order_calc_margin`, con la nota "innocua, ma appartiene al percorso di
esecuzione". Era prudenza mal collocata, e aveva un costo concreto: senza
`order_calc_profit` il valore monetario di un movimento andava derivato da
`trade_tick_value`, che su questo conto non è nella valuta del conto per metalli e
indici. Il risultato era una sovrastima del **12,5%** delle posizioni su quegli
strumenti — il limite di rischio dell'utente violato in silenzio.

**Deciso.** Tre categorie invece di due:

* *cambiano lo stato del conto* — `order_send`, `order_modify`, `position_close`:
  vietate fino alla fase 11;
* *sottopongono al server una richiesta d'ordine* — `order_check`: vietata, fa
  parte del pre-trade della fase 11;
* *calcolano* — `order_calc_profit`, `order_calc_margin`: **ammesse**.

Il test di garanzia verifica entrambe le direzioni: che le scritture non ci siano,
e che `order_calc_profit` **ci sia**. Rimuoverlo per eccesso di prudenza farebbe
tornare il difetto, e il test lo impedisce.

**Nota di metodo.** Questa voce non cancella la precedente sulla sola lettura: la
supera. La garanzia resta la stessa — nessun ordine può partire — ma è espressa su
ciò che cambia lo stato, non su ciò che somiglia all'esecuzione.

→ [ADR 0012](docs/adr/0012-valore-monetario-dal-broker.md)

## 2026-10-04 — Lo stato del mercato è una deduzione dichiarata, non un dato

**Problema.** Il piano prevedeva di leggere gli orari di sessione dal broker.
Verificato sul conto: l'API Python di MT5 **non li espone**
(`symbol_info_sessions_quote` esiste solo in MQL5), non c'è una funzione per l'ora
corrente del server, e `session_open`/`session_close` di `symbol_info` sono
**prezzi**, non orari — un nome che invita all'errore.

**Scartato.** Dedurre lo stato e presentarlo come se venisse dal broker: è
esattamente il tipo di confusione fra fatto e congettura che questo progetto
esiste per evitare.

**Deciso.** `QuoteAssessment` separa fatti accertati (`trade_mode`, bid/ask a zero,
età del tick) da deduzioni (`MARKET_CLOSED`), e porta `is_inference`. La deduzione
richiede **due prove concordi**: quotazione vecchia di oltre quindici minuti *e*
nessuna barra nuova. Con la sola età del tick si confonderebbe un flusso prezzi
interrotto con un mercato chiuso, e sono guasti diversi.

La fase complessiva si deduce dall'insieme degli strumenti: su XM gli indici
chiudono mentre il forex continua, quindi "US100Cash fermo" non significa "mercato
chiuso". Quando si fermano tutti, il messaggio dice entrambe le possibilità
(mercato chiuso **oppure** connessione caduta) invece di scegliere.

**La colonna resta su database** (`quote_is_inference`): senza di essa, rileggendo
lo storico fra mesi, non si saprebbe distinguere "il broker ha detto no" da
"abbiamo concluso che fosse chiuso".

→ [ADR 0011](docs/adr/0011-sessioni-non-esposte-da-mt5.md)

## 2026-10-04 — L'ora del broker si misura, e se non si può si dice

**Problema.** Senza una funzione per l'ora del server, lo scarto da UTC si può
misurare solo da una quotazione fresca — cioè a mercato aperto.

**Deciso.** `BrokerClock` misura lo scarto dal timestamp di un tick recente e lo
conserva con la sua età. Tre stati espliciti: `MEASURED`, `CACHED` (con l'età) e
`UNAVAILABLE`. Una misura di più di dodici ore non è affidabile, perché potrebbe
trovarsi dall'altra parte di un cambio di ora legale.

**Due limiti scoperti dai test.** Lo scarto deve essere plausibile come fuso orario
(±14 ore): un tick vecchio di 31 ore produce uno "scarto" di 31 ore, e trentuno ore
non sono un fuso. E un tick vecchio di un multiplo **esatto** di mezz'ora resta
indistinguibile da uno scarto di fuso: non è risolvibile con i dati disponibili, non
si verifica in pratica (a mercato aperto i tick arrivano a distanza di secondi), e
viene dichiarato nel codice invece di essere nascosto.

## 2026-10-04 — Tre viste sull'esposizione, perché la valuta da sola inganna

**Problema.** XM dichiara `GOLD` come **USD/USD/USD**, non XAU/USD. Raggruppando
per sola valuta, oro e indici americani finiscono nello stesso secchiello "USD": due
posizioni che non hanno nulla in comune sembrano rischio concentrato, e un futuro
limite di esposizione per valuta le bloccherebbe a vicenda senza motivo.

**Deciso.** Tre viste: per valuta, per **classe di attività** e per strumento. La
classe si ricava dal percorso di categoria dichiarato dal broker
(`Derivatives\Spot Metals\GOLD`), che è un dato e non una congettura; il nome resta
solo come ripiego per i broker che non lo compilano.

**Dettaglio scoperto dai test.** Nell'ordine dei controlli, le criptovalute vanno
prima del forex: la categoria XM si chiama `Cryptocurrencies`, che contiene
"CURRENC", e Bitcoin veniva classificato come cambio.

## 2026-10-04 — Il rischio non misurabile non diventa zero

**Problema.** Una posizione senza stop loss ha rischio indeterminato. Sommarla come
se fosse nulla darebbe un portafoglio che sembra prudente mentre non lo è.

**Deciso.** `total_risk_at_stop` resta `None` se almeno una posizione è priva di
stop, `risk_is_measurable` è falso e lo snapshot elenca gli strumenti scoperti. Lo
stesso vale quando il broker non fornisce il valore monetario di un movimento: la
posizione resta fuori dai totali e il fatto viene dichiarato, invece di essere
approssimato con un numero che nessuno saprebbe più distinguere da uno vero.

## 2026-10-04 — Gli swing non confermati non entrano nella struttura

**Problema.** Un massimo è un massimo solo se le barre successive non lo superano.
Riconoscerlo nel momento in cui si forma significa sapere il futuro: funziona
magnificamente sullo storico e perde denaro in tempo reale.

**Deciso.** Ogni `SwingPoint` porta `confirmed`, vero solo se esistono `forza` barre
successive. La struttura di mercato, i livelli e il cambio di carattere usano
**esclusivamente** gli swing confermati; quelli recenti esistono nel contesto solo
per essere mostrati.

**Correzione collegata.** `RETEST` veniva assegnato in base alla sola vicinanza al
bordo del range: condizione sempre vera dentro un range stretto, quindi ogni mercato
compresso risultava "in ritest". Un ritest richiede per definizione un livello già
rotto, e ora lo richiede.

## 2026-10-04 — Le serie degli indicatori conservano la lunghezza, con None nei buchi

**Problema.** Le librerie che restituiscono serie accorciate costringono a
riallineare gli indici a mano. Un MACD costruito su una EMA già tagliata si
disallinea di qualche barra, il backtest continua a funzionare e i risultati sono
sbagliati in modo credibile.

**Deciso.** Ogni funzione in `quant/` restituisce una serie **lunga quanto
l'ingresso**, con `None` dove il valore non è definito. Più verboso, e per questo
immune: l'indice `i` del risultato corrisponde sempre alla barra `i`.

**Perché Python puro e non numpy.** Non per diffidenza — numpy è installato e servirà
a vettorizzare il backtest — ma perché ogni ricorrenza (Wilder, EMA, DX) deve essere
leggibile riga per riga: un indicatore sbagliato avvelena in silenzio tutto ciò che
sta a valle, e nessun test lo scopre se la formula è plausibile. Su 500 barre per
cinque timeframe il costo è irrilevante.

**Verifica.** RSI confrontato con la serie di riferimento classica: 70.4641 contro i
70.53 della tabella pubblicata. La differenza è l'arrotondamento dei prezzi nella
tabella — 70.4641 è il risultato esatto della formula di Wilder su quei prezzi a due
decimali, verificato a mano e con una seconda implementazione indipendente.

## 2026-10-04 — Il contesto di mercato non contiene proposte operative

**Problema.** `AssetMarketContext` è l'oggetto che la fase 7 passerà all'agente. La
tentazione di aggiungere "direzione suggerita" o "entry proposta" arriverà.

**Deciso.** Il contesto **descrive** e non propone: nessun campo `entry`,
`stop_loss`, `decision` o `signal`, e un test lo verifica sui campi del modello.
Entry, stop e direzioni nascono nel Trade Planner, che passa dal Risk Engine. Un
campo "direzione suggerita" qui aprirebbe una strada che aggira entrambi.

**Conseguenza sulla persistenza.** I DTO del contesto usano `extra="ignore"` e non
`forbid`: il dump include i campi calcolati, che in ingresso non sono dati, e con
`forbid` un contesto salvato su database non poteva essere riletto — cioè non era
persistibile nel senso che serve.

## 2026-10-04 — Il perimetro si applica anche all'analisi, non solo alla lettura

**Problema.** `SpecificationRepository` parla direttamente col broker e non conosce
la whitelist. `AssetAnalyst.analyse()` poteva quindi leggere specifiche e analizzare
uno strumento **non autorizzato**: la whitelist era applicata in
`MarketDataService`, ma non all'ingresso dell'analisi.

**Deciso.** Il controllo è all'inizio di `analyse()` e **solleva**. Restituire un
contesto vuoto sarebbe peggio: il chiamante lo leggerebbe come "nessun dato" invece
di "non autorizzato", e sono cose diverse.

## 2026-10-04 — I test sulle rotte HTTP passavano a vuoto

**Problema.** FastAPI 0.142 avvolge i router inclusi in un oggetto contenitore:
`app.routes` non li espande. Il test "nessun endpoint di scrittura sul conto" non
vedeva **nessuna** rotta e concludeva che non ce n'erano di pericolose. Una
protezione che non proteggeva.

**Deciso.** La superficie pubblica si legge dallo **schema OpenAPI**, che è la vista
autorevole e piatta. E ogni asserzione negativa è accompagnata da una positiva: il
test fallisce se l'elenco delle rotte attese è vuoto, così non può più passare per il
motivo sbagliato.

**Regola generale, aggiunta a CLAUDE.md.** Un controllo che non trova niente deve
fallire, non passare.

## 2026-10-04 — Il terminale finto riproduce le specifiche reali di XM

**Problema.** Il mock usava specifiche inventate e nomi canonici (`XAUUSD`, `US30`).
Un mock più gentile del broker vero fa passare i test e rompere la produzione.

**Deciso.** Nomi e specifiche sono quelli **misurati** sul conto: `GOLD`, `SILVER`,
`US30Cash`, `US100Cash`, `US500Cash`, con digits, contract size, volume step e
valute reali. Tre fedeltà deliberate, ognuna riproduce un difetto:

* `trade_tick_value` di EURUSD è **0.8887** e non 1.0 — un codice che assume la
  valuta del conto si rompe nei test;
* metalli e indici portano un fattore di conversione 0.8887 — riproduce
  l'incoerezza del broker;
* EURUSD ed EURJPY convertono al prezzo di chiusura — riproduce la non linearità del
  calcolatore.

`XAUUSD` e `US30` **non esistono** nel mock, di proposito: servono a verificare che
il sistema lo dica invece di sostituirli.

**Difetto collegato, trovato dalla suite completa.** `DEFAULT_SYMBOLS` è una tupla di
dataclass **mutabili** condivise: un test che modificava un volume minimo inquinava
tutti i successivi, e il risultato dipendeva dall'ordine di esecuzione. Ogni
terminale ne riceve ora una copia.

## 2026-10-04 — Timeout brevi sulle connessioni, perché lo stato si legge subito

**Problema.** La suite di test passava da 27 secondi a 4 minuti e 36: ogni avvio
del contenitore spendeva 4 secondi nel tentativo di connessione a PostgreSQL
spento. Causa: `localhost` su Windows risolve sia IPv6 sia IPv4, e asyncpg prova
SSL e non-SSL su ciascuno, in sequenza, prima di arrendersi.

**Deciso.** `DB_CONNECT_TIMEOUT_SECONDS` (default 5) e
`REDIS_CONNECT_TIMEOUT_SECONDS` (default 1.5), passati al driver. I test usano
0.3 e 0.2 e puntano a `127.0.0.1`.

**Perché conta oltre la comodità.** Il controllo di stato deve rispondere
*subito* anche quando tutto è spento: è il momento in cui serve. Una pagina
System Health che si fa attendere quattro secondi per dire "il database non
risponde" non è una pagina diagnostica. E una suite da quattro minuti e mezzo è
una suite che si smette di eseguire.

**Conseguenza.** 165 test in 27 secondi.

## 2026-10-04 — Bid/ask a zero significa "nessuna quotazione", non prezzo zero

**Problema.** Trovato dalla diagnostica sul conto reale: `US100Cash` e
`US500Cash` restituiscono bid e ask a 0.0 a mercato chiuso. `Tick` richiede
prezzi positivi, quindi la validazione falliva con un `ValidationError` di
Pydantic — un errore tecnico al posto di un'informazione.

**Scartato.** Togliere il vincolo `gt=0` da `Tick`: un prezzo di zero sarebbe
entrato nei calcoli di rischio e di sizing come se fosse un prezzo.

**Deciso.** Il connettore riconosce il caso e solleva `StaleDataError` con un
messaggio esplicito: mercato chiuso, simbolo appena attivato o non quotato su
questo conto. Il vincolo sul tipo resta. La diagnostica e la dashboard mostrano
`[NO]` accanto a quello strumento e continuano con gli altri.

## 2026-10-04 — Il tipo di conto si legge dal broker, non dal nome del server

**Problema.** `is_demo` controllava la presenza della parola "demo" nel nome del
server. Il conto demo XM di prova sta su `XMGlobalMU-MT5 2`, senza quella
parola: la diagnostica lo annunciava come **CONTO REALE**.

**Perché conta.** Un'etichetta sbagliata in questa direzione fa prendere alla
leggera un conto vero oppure ignorare l'avviso su uno finto. È il tipo di
dettaglio che costa denaro.

**Deciso.** `account_info().trade_mode` lo dichiara: 0 demo, 1 contest, 2 reale.
`AccountSnapshot.account_type` riporta il valore, e se il campo manca resta
`UNKNOWN` — che `is_real_money` tratta come **reale**. In dubbio, la prudenza sta
da quel lato.

## 2026-10-04 — I nomi dei simboli conservano la grafia del broker

**Problema.** La whitelist portava i nomi in maiuscolo per normalizzarli. Provando
sul conto XM reale, `XAUUSD` non esiste: l'oro si chiama `GOLD`, gli indici
`US30Cash`, `US100Cash`, `US500Cash`. E `US30CASH` in maiuscolo **non esiste**:
la normalizzazione avrebbe reso la whitelist inutilizzabile proprio sugli indici.

**Scartato.** Tenere la normalizzazione e documentare "scrivete i nomi in
maiuscolo": avrebbe funzionato per il forex e rotto gli indici in silenzio.

**Deciso.** `RiskLimits.allowed_symbols` restituisce i nomi **come sono scritti**.
Il confronto avviene senza distinzione di maiuscole tramite
`canonical_symbol()`, che restituisce la grafia ufficiale: `gold` scritto a mano
nella dashboard diventa `GOLD` verso il terminale. Aggiunto
`GET /api/market/symbols-available?cerca=...` per scoprire i nomi (il conto ne
espone 1639).

**Conseguenza.** `RISK_ALLOWED_SYMBOLS` in `.env` ora elenca i nomi XM reali.

## 2026-10-04 — `SQLAlchemyError` non basta a intercettare un database spento

**Problema.** Con PostgreSQL fermo, `/api/health` andava in errore 500 invece di
dichiarare il guasto. Causa: asyncpg solleva `ConnectionRefusedError` (una
`OSError`) **prima** che SQLAlchemy possa incapsularla nel proprio albero di
eccezioni, quindi il `except SQLAlchemyError` non la vedeva.

**Deciso.** `Database.check()` cattura `(SQLAlchemyError, OSError)`. Il caso più
comune — database spento — è proprio quello che l'health check deve saper
raccontare: un controllo di stato che va in errore è inutile nel momento in cui
serve.

**Conseguenza.** Il messaggio riporta solo la **classe** dell'errore e il DSN
senza password: il testo di un errore di connessione può contenere la stringa
completa, credenziali incluse.

## 2026-10-04 — Il valore del pip si legge dal broker, sempre

**Problema.** Verificando `EURUSD` sul conto reale, `trade_tick_value` risulta
**0.8887**, non 1.0: il conto è in EUR e il valore del tick dipende dalla valuta
del conto, dal tipo di conto e dal cambio corrente.

**Deciso.** Nessun valore del pip, dimensione di contratto o passo di volume è
scritto nel codice: tutto viene da `symbol_info` a ogni richiesta, e
`SymbolRow` ne conserva lo storico perché XM può cambiarli.
`SymbolInfo.normalize_volume()` arrotonda **per difetto** al passo del broker:
arrotondare per eccesso farebbe rischiare più del calcolato, e il rischio è
dell'utente.

## 2026-10-04 — Il terminale finto usa rumore indicizzato, non una passeggiata casuale

**Problema.** La prima versione del mock generava i prezzi come passeggiata
casuale accumulata dall'origine della griglia temporale. Per leggere le ultime
200 barre M1 di oggi avrebbe dovuto generare tutte quelle dal 2024: circa un
milione di barre a ogni avvio.

**Scartato.** Una cache delle serie generate: avrebbe risolto la lentezza ma non
la riproducibilità fra processi, e due letture a distanza di giorni avrebbero
dato storici diversi.

**Deciso.** Il prezzo di una barra è una **funzione pura del suo indice**:
rumore interpolato su sei ottave (passo 1, 2, 4, 8…) calcolato con `blake2b`.
Costo costante per barra, nessuna cache, identico fra processi e sistemi
operativi. La somma su più ottave produce movimento su più scale temporali —
tendenze lunghe, ritracciamenti, rumore breve — e questo serve a non testare
regime detector e indicatori su una linea piatta.

**Conseguenza.** Lo stesso indice assoluto in minuti alimenta tutti i timeframe,
così M5 e H1 descrivono lo **stesso** mercato a risoluzioni diverse. Senza
questo accorgimento il multi-timeframe mostrerebbe grafici che si contraddicono.

## 2026-10-04 — Le credenziali MT5 mancanti non bloccano l'avvio

**Problema.** La validazione iniziale rifiutava la configurazione se mancavano
`MT5_LOGIN`/`PASSWORD`/`SERVER`. Conseguenza: il backend non partiva, e un
processo morto non può dire a nessuno cosa manca. Sarebbe stato il difetto del
progetto precedente ripetuto.

**Deciso.** Due categorie distinte. Le incoerenze del **contratto** (limiti di
rischio assurdi o contraddittori) fermano l'avvio: sono errori di chi configura,
e partire sarebbe peggio. Le **risorse esterne** assenti o spente (credenziali,
database, Redis, terminale) non fermano l'avvio: compaiono in
`startup_warnings()`, in System Health e nel motivo del blocco operativo.

**Il trading resta bloccato in entrambi i casi.** Il fail closed riguarda
l'*operatività*, non la *diagnosticabilità*.

## 2026-10-04 — `MODE=LIVE` senza secondo consenso viene declassato, non rifiutato

**Problema.** Con `APP_MODE=LIVE` e `APP_ENABLE_LIVE_TRADING=false`: errore
fatale o declassamento?

**Scartato.** L'errore fatale. Chi ha fretta di far partire il processo
metterebbe `true` per togliere l'errore, ottenendo l'esatto contrario della
sicurezza voluta.

**Deciso.** `effective_mode` scende a `PAPER`, con un avviso critico nei log,
un avviso in System Health e `mode_was_downgraded` visibile nella dashboard. Il
sistema funziona, in modalità sicura, e lo dice.

## 2026-10-04 — Il kill switch non chiude le posizioni aperte

**Problema.** La specifica dice "blocca immediatamente nuovi ordini" e tace sulle
posizioni già aperte.

**Scartato.** Chiudere tutto quando il kill switch scatta. Realizzerebbe perdite
che il mercato stava magari rientrando, e trasformerebbe un problema tecnico in
una perdita certa.

**Deciso.** Il kill switch blocca le **aperture**. Lo svuotamento del portafoglio
(`flatten`) è un comando separato, esplicito e volontario. Per lo stesso motivo
il ciclo di gestione delle posizioni continua a girare a kill switch attivo:
qualcuno deve continuare a guardarle.

→ [ADR 0006](docs/adr/0006-kill-switch-e-breaker.md)

## 2026-10-04 — Il kill switch è un file, e il suo stato illeggibile significa "attivo"

**Problema.** Serve un modo di fermare tutto che funzioni **anche quando il
sistema è rotto**. Un interruttore in memoria non serve se il processo è
bloccato; un endpoint HTTP non serve se l'API non risponde.

**Deciso.** Un file. Si crea da terminale, da Esplora risorse o dall'API,
sopravvive al riavvio, non si disarma da solo, e il rilascio richiede un motivo
che finisce nel diario. Se il file **non è leggibile**, il blocco si considera
attivo: non sapere equivale a non operare.

→ [ADR 0006](docs/adr/0006-kill-switch-e-breaker.md)

## 2026-10-04 — La sola lettura è garantita dall'assenza di codice, non da un flag

**Problema.** Fino alla fase 11 il sistema non deve poter inviare ordini. Un
flag `ENABLE_TRADING=false` protegge finché nessuno lo inverte, e non dice nulla
su cosa accada dopo: il codice di esecuzione non è mai stato eseguito.

**Deciso.** Quattro livelli. Protocolli separati (`OrderWriter` dichiarato, mai
implementato); il protocollo `MT5Terminal` elenca solo letture, quindi
`order_send` non esiste per chi lo riceve; un test analizza l'**AST** dei file in
`brokers/` e fallisce se compare una chiamata di scrittura; un test ispeziona le
rotte HTTP e fallisce se esiste un endpoint di scrittura sul conto. Anche il mock
è privo di `order_send`, per non diventare la scorciatoia da cui la scrittura
rientra.

**Conseguenza voluta.** Il giorno della fase 11 quel test fallirà, e il
fallimento obbligherà a dichiarare l'intenzione aggiornando
`MODULI_AUTORIZZATI`, invece di scivolarci dentro.

→ [ADR 0004](docs/adr/0004-broker-read-only-per-costruzione.md)

## 2026-10-04 — Le chiamate a MT5 passano da un thread dedicato, una alla volta

**Problema.** Il pacchetto `MetaTrader5` è sincrono, bloccante, non thread-safe,
e tiene una sola connessione per processo. Una `copy_rates_from_pos` può costare
centinaia di millisecondi: dentro l'event loop bloccherebbe tutte le altre
richieste, inclusa la lettura delle posizioni aperte.

**Deciso.** Executor a **thread singolo** più `asyncio.Lock`: ogni chiamata è
fuori dall'event loop e serializzata. Il costo è che le letture MT5 sono
sequenziali, accettabile perché i timeframe operativi sono minuti e non
microsecondi. Timeout al doppio di quello configurato: non interrompe il thread
— il pacchetto non è interrompibile — ma restituisce il controllo al chiamante.

## 2026-10-04 — Ogni `None` di MT5 diventa un'eccezione tipizzata

**Problema.** MetaTrader 5 segnala gli errori restituendo `None`, senza
eccezioni. Un simbolo inesistente e un terminale disconnesso sono
indistinguibili se non si interroga `last_error()`. E `positions_get()`
restituisce `None` sia per "nessuna posizione" sia per errore.

**Deciso.** Il connettore traduce ogni `None` in un errore del dominio
(`BrokerNotConnectedError`, `SymbolNotFoundError`, `BrokerError`) con il codice
MT5 nel contesto. Per `positions_get` e `orders_get` si legge `last_error()`:
codice 0 significa lista vuota, codice diverso significa guasto. Confondere i
due casi vorrebbe dire calcolare il rischio di portafoglio su zero posizioni
mentre ce ne sono tre aperte.

## 2026-10-04 — Il connettore verifica di essere sul conto giusto

**Problema.** Un terminale MT5 lasciato aperto su un altro conto è un modo
realistico di operare per sbaglio dove non si deve, e nulla lo segnalerebbe.

**Deciso.** Dopo `initialize`, il connettore confronta il login del conto con
`MT5_LOGIN`. Se differiscono, si disconnette e rifiuta la connessione. Fail
closed su un rischio che non ha rimedio a posteriori.

## 2026-10-04 — Il perimetro vale anche per la semplice lettura

**Problema.** Il connettore è broker-agnostico e legge qualsiasi simbolo. La
whitelist dell'utente poteva essere applicata solo al momento di operare.

**Deciso.** `MarketDataService` applica il perimetro a **ogni** lettura: se uno
strumento non è autorizzato non compare né in un tick né in un'analisi. Se il
sistema può *guardarlo*, prima o poi lo includerà in una valutazione.

**Conseguenza.** `GET /api/market/symbols/BTCUSD` risponde 403 anche se il
simbolo esiste sul conto. L'elenco dei simboli del broker resta consultabile in
un endpoint separato, che non autorizza nulla.

## 2026-10-04 — Il budget giornaliero si ricostruisce dal broker, non si accumula in memoria

**Problema.** La specifica descrive `DailyRiskBudget` come contatore. In memoria
ripartirebbe da zero a ogni riavvio: il limite giornaliero dell'utente sarebbe
raddoppiabile riavviando il processo.

**Deciso.** Il PnL realizzato si ricostruisce dallo storico dei deal
(`get_deals`, `DealSnapshot.is_realizing`: solo le uscite realizzano).
L'equity del broker è l'unica fonte di verità sul capitale — commissioni, swap,
conversioni e chiusure manuali fatte dall'utente dal terminale renderebbero
sbagliato qualunque saldo tenuto da noi. Per lo stesso motivo lo stato del
circuit breaker è persistente.

## 2026-10-04 — La freschezza del dato è parte del tipo

**Problema.** Con un ciclo lento e uno veloce, ogni dato che circola è vecchio di
qualcosa, e guardando un numero non si sa di quando sia. Era il difetto che
faceva sembrare rotto il progetto precedente.

**Scartato.** Un campo `timestamp` accanto al valore, o un controllo nei
chiamanti: entrambi opzionali, quindi prima o poi dimenticati.

**Deciso.** `Fresh[T]` con `as_of`, TTL e sorgente. `require_fresh(now)` alza
`StaleDataError` su un dato scaduto; `.value` lo restituisce comunque, ma è una
riga visibile che in revisione si nota. Ogni risposta HTTP riporta `as_of` ed
`eta_secondi`.

→ [ADR 0007](docs/adr/0007-freschezza-come-tipo.md)

## 2026-10-04 — Un tick vecchio viene etichettato, non nascosto

**Problema.** Fuori orario di mercato l'ultimo tick è legittimamente vecchio.
Nasconderlo o sollevare sempre un errore sarebbe sbagliato in modi diversi.

**Deciso.** `get_tick()` lo restituisce con l'età; `get_tick(require_fresh=True)`
alza `StaleDataError`. Chi deve mostrare un prezzo usa il primo, chi deve
decidere usa il secondo. Un tick vecchio è un'informazione utile, ed è un motivo
valido per non operare.

## 2026-10-04 — Le candele dichiarano se l'ultima è chiusa

**Problema.** `copy_rates_from_pos(symbol, tf, 0, n)` restituisce come prima
barra quella **in formazione**. Usarla come chiusa è il modo più comune di
ottenere un backtest brillante e un sistema reale perdente.

**Deciso.** Il connettore chiede `count + 1` barre e marca la serie con
`last_candle_closed=False`. `CandleSeries.closed` esclude la barra in formazione
e `closes` lavora solo su quelle concluse. La barra viva resta disponibile per il
prezzo corrente, ma va chiesta esplicitamente.

## 2026-10-04 — I limiti di rischio sono `frozen` e validati fra loro

**Problema.** "L'AI non può modificare i limiti" è un'affermazione che va resa
vera nel codice.

**Deciso.** `RiskLimits` è un modello **frozen**: assegnare un attributo solleva
`ValidationError`, non viene ignorato. Le validazioni sono **incrociate**: il
rischio per operazione non può superare quello complessivo aperto, il limite
giornaliero non può superare il settimanale, il settimanale non può superare il
drawdown massimo. Una configurazione incoerente blocca l'avvio, non si scopre al
primo ordine.

**Conseguenza.** La scala confidence→rischio può solo **ridurre**: anche con una
scala mal configurata (`0.65:3.0`) la quota resta limitata a 1.0 del massimo
dell'utente.

## 2026-10-04 — L'API non espone un modo di cambiare i limiti

**Problema.** Un endpoint `PUT /api/system/risk-limits` sarebbe comodo.

**Scartato.** Proprio perché comodo: un'API che cambia i limiti a caldo è un'API
che prima o poi li cambia su richiesta di un componente automatico.

**Deciso.** Sola lettura. I limiti si modificano in `.env` e richiedono un
riavvio — un gesto consapevole, tracciato in `config_versions`. Un test verifica
che non esistano rotte di scrittura su quel percorso.

## 2026-10-04 — La censura dei segreti è un processore, non una buona abitudine

**Problema.** Affidarsi alla disciplina di chi scrive `logger.info(...)` perché
non registri una password significa che prima o poi una password finisce su disco.

**Deciso.** `redact_secrets` è un processore obbligatorio di structlog, applicato
a ogni riga, con ricerca per sottostringa sulle chiavi (`password`, `secret`,
`token`, `api_key`, `authorization`, `credential`, `dsn`) e ricorsiva su
dizionari e liste. Le credenziali sono inoltre `SecretStr`, quindi compaiono come
`**********` anche se stampate per sbaglio.

## 2026-10-04 — I moduli pubblicano eventi, non si chiamano a catena

**Problema.** Una pipeline di diciassette stadi in cascata fa dettare il ritmo
dallo stadio più lento e meno affidabile (le news) a quello più critico (le
posizioni aperte).

**Deciso.** Bus eventi con un catalogo chiuso (`EventType`). Un handler che
solleva non interrompe gli altri — il diario non deve poter far cadere
l'esecuzione, né viceversa — e gli handler girano in parallelo. Il
`correlation_id` lega tutti gli eventi nati dalla stessa osservazione di mercato:
è ciò che permette di ricostruire un trade dalla candela che l'ha fatto notare
fino alla chiusura.

→ [ADR 0002](docs/adr/0002-pipeline-a-due-cicli.md),
[`docs/EVENTI.md`](docs/EVENTI.md)

## 2026-10-04 — Il diario di sistema è una tabella, non un file di log

**Problema.** I log si ruotano, si comprimono e si perdono. Servono risposte a
distanza di settimane: *"il 12 marzo il kill switch era attivo?"*, *"con quali
limiti girava il sistema quando ha aperto quella posizione?"*.

**Deciso.** `system_events` su PostgreSQL, scritta da `SystemJournal` iscritto a
**tutti** gli eventi. I moduli non sanno che esiste: nessuno deve ricordarsi di
"loggare anche nel diario", altrimenti la riga che manca è sempre quella che
serviva. Ogni riga porta la modalità operativa del momento, perché un evento
letto senza sapere se era PAPER o LIVE porta a conclusioni sbagliate.

**Se il diario non scrive**, il fatto è `ERROR` e System Health segnala la
tracciabilità compromessa — ma **senza traceback**: con il database spento ogni
evento ne produrrebbe uno, e centinaia di tracciati identici nascondono gli
errori veri.

## 2026-10-04 — PostgreSQL conserva i fatti, non ogni tick

**Problema.** La tabella `market_data` della specifica, presa alla lettera, sono
decine di milioni di righe l'anno sullo stesso database che serve le query
operative. E MT5 è già l'archivio autorevole dello storico prezzi.

**Deciso.** Sul database: eventi, configurazioni, contesti, decisioni, piani,
verdetti di rischio, ordini, posizioni, performance. Dal broker, su richiesta:
candele e tick. Su file Parquet: solo le serie congelate per il backtest.

→ [ADR 0009](docs/adr/0009-postgres-per-i-fatti.md)

## 2026-10-04 — Lo schema del database cresce con le fasi

**Problema.** La specifica elenca ventun tabelle. Crearle tutte subito significa
dare forma a un'ipotesi invece che all'uso reale.

**Deciso.** La prima migrazione crea solo le quattro tabelle che un modulo di
fase 1-2 scrive davvero: `system_events`, `config_versions`, `accounts`,
`symbols`. Le prime due esistono già adesso perché servono dal primo avvio:
senza di esse non si può ricostruire in che stato era il sistema, e quella
ricostruzione è il motivo per cui questo progetto esiste. Lo schema completo,
fase per fase, in [`docs/DATABASE.md`](docs/DATABASE.md).

## 2026-10-04 — MetaTrader 5 fuori da Docker

**Problema.** La specifica chiede Docker Compose e il pacchetto `MetaTrader5`.
Quel pacchetto esiste solo su Windows e parla via IPC con un terminale grafico:
in un container Linux non c'è nulla a cui attaccarsi.

**Scartato.** Wine in container: aggiungerebbe un livello di emulazione fra il
sistema operativo e gli ordini reali. Non è il punto in cui accettare
complessità.

**Deciso.** Topologia ibrida: PostgreSQL e Redis in Docker, il processo che
possiede MT5 sull'host Windows. Nel container `MT5_USE_MOCK=true`. Dalla fase 7
il processo diventa un `mt5-gateway` unico con un'API interna.

→ [ADR 0003](docs/adr/0003-mt5-gateway-windows.md)

## 2026-10-04 — Le costanti MT5 sono letterali, con un test che le confronta

**Problema.** Importare `MetaTrader5` per leggere `TIMEFRAME_H1` rende
impossibile eseguire i test su Linux, in CI e in Docker.

**Deciso.** Le costanti sono scritte come letterali in `brokers/mt5/mapping.py`,
con la fonte documentata. Il rischio — che un giorno divergano — è coperto da
`test_mt5_mapping.py`, che le confronta con il pacchetto vero quando è installato
e viene saltato altrove. La scelta resta comoda senza diventare un errore
silenzioso.

## 2026-10-04 — Nessun fallback automatico al terminale finto

**Problema.** Se MT5 non parte, passare al mock renderebbe il sistema sempre
avviabile.

**Scartato.** Proprio per questo: mostrerebbe prezzi inventati con l'aspetto di
prezzi veri. È il peggiore dei comportamenti possibili in un sistema di trading.

**Deciso.** Il mock si ottiene solo con `MT5_USE_MOCK=true`, scelta esplicita in
`.env`, riportata negli avvisi di avvio, in System Health, nel nome del broker
("MetaTrader5 (simulato)"), nel campo `source` di ogni serie (`mt5-mock`) e in
`dati_simulati` nelle risposte HTTP. Con il mock attivo lo stato del broker non è
mai "sano" ma "degradato".

## 2026-10-04 — Il progetto nasce da zero, riusando solo i puntamenti

**Problema.** Esistono due progetti precedenti: `trading-lab` (eToro, mai andato
in produzione) e `brotrade` (XM via MT5). Riusarne le logiche porterebbe dentro
anche i motivi per cui si sono fermati.

**Deciso.** Nuovo repository, nessuna riga di logica riusata. Dai progetti
precedenti si riportano solo i **puntamenti**: credenziali e server MT5/XM da
`brotrade/.env`, chiave e modello OpenAI da `trading-lab/.env`.

**Cosa si eredita invece volentieri** sono le due lezioni di `brotrade`: leggere
i limiti dal broker invece di scriverli nel codice (qui: specifiche di contratto
rilette a ogni richiesta), e registrare anche le azioni **bloccate** con il
motivo (qui: System Health con `blocking_reasons`, e i verdetti di rifiuto
registrati nel diario).
