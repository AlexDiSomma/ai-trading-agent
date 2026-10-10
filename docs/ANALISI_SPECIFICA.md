# Analisi della specifica (fase 0)

Lettura critica della specifica, prima di scrivere codice. Per ogni punto: il
problema, perché conta, cosa è stato deciso. Dove la decisione è strutturale
rimanda a una ADR.

La specifica è solida nell'impostazione — la separazione fra rischio dell'utente
e autonomia dell'AI, la pipeline "prima il mondo poi l'asset", WAIT come esito
legittimo — e i rilievi che seguono riguardano la sua *realizzabilità*, non la
sua direzione.

---

## 1. Problemi che cambiano l'architettura

### 1.1 La pipeline a 17 stadi non può essere una catena sincrona

**Problema.** Diciassette stadi in cascata, dal contesto globale all'esecuzione.
Se ogni stadio chiama il successivo, lo stadio più lento e meno affidabile (le
news, che dipendono da un provider esterno) detta il ritmo a quello più critico
(la gestione di una posizione aperta).

**Perché conta.** Un provider di news lento non deve poter ritardare la chiusura
di una posizione in perdita.

**Decisione.** Due cicli a frequenza diversa che comunicano con snapshot
versionati e scadenza, non con chiamate dirette → **ADR 0002**.

### 1.2 MetaTrader 5 e Docker non convivono

**Problema.** La specifica chiede Docker Compose e il pacchetto `MetaTrader5`.
Quel pacchetto esiste solo su Windows, parla via IPC con un terminale grafico, è
sincrono e non thread-safe.

**Perché conta.** È il punto in cui il sistema tocca il denaro: non è il posto
per uno strato di emulazione.

**Decisione.** Topologia ibrida: database e cache in Docker, il processo che
possiede MT5 sull'host Windows → **ADR 0003**.

### 1.3 "Il Risk Engine è indipendente dall'AI" non si garantisce con una frase

**Problema.** Niente, nella specifica, impedisce a un modulo di costruire un
ordine e inviarlo senza passare dal Risk Engine. Il motore continuerebbe a
funzionare; semplicemente nessuno lo chiamerebbe.

**Decisione.** L'approvazione del rischio è un *tipo* richiesto dalla firma
dell'esecuzione, verificata di nuovo al momento dell'invio; nessun import
incrociato fra `risk/` e `ai/`, con un test che lo controlla sull'AST →
**ADR 0005**.

### 1.4 Manca l'idempotenza degli ordini

**Problema.** La specifica non menziona cosa fare quando `order_send` va in
timeout. MT5 può eseguire e non rispondere.

**Perché conta.** È il modo più realistico di ritrovarsi con una posizione
doppia, cioè con il rischio dell'utente violato *senza che nessun limite sia
stato toccato*. Un rinvio automatico peggiora la situazione.

**Decisione.** Tabella di intenti di esecuzione scritta **prima** dell'invio, con
un identificativo nostro riportato in `magic`/`comment`; dopo un timeout si
riconcilia, non si rinvia → **ADR 0005**.

### 1.5 Il budget di rischio tenuto in memoria si azzera al riavvio

**Problema.** La specifica descrive un `DailyRiskBudget` come contatore. Un
contatore in RAM ripartirebbe da zero a ogni riavvio: il limite giornaliero
dell'utente sarebbe di fatto raddoppiato riavviando il processo.

**Decisione.** Il PnL realizzato si **ricostruisce** dallo storico dei deal del
broker a ogni avvio (`MT5Connector.get_deals`, `DealSnapshot.is_realizing`).
L'equity del broker è l'unica fonte di verità sul capitale. Lo stato del circuit
breaker è persistente, per lo stesso motivo → **ADR 0006**.

### 1.6 L'ora del broker non è UTC

**Problema.** La specifica parla di perdita giornaliera e di sessioni
autorizzate senza dire in quale fuso. Con XM lo scarto da UTC è di 2-3 ore e
cambia con l'ora legale.

**Perché conta.** Una giornata di trading disallineata crea ogni giorno una
finestra in cui il limite di perdita vale il doppio; e un evento macro datato UTC
confrontato con un timestamp ora-broker fa sbagliare il filtro proprio sulle
pubblicazioni importanti.

**Decisione.** Tempo sempre timezone-aware, ora del broker chiesta al broker,
offset stimato e monitorato → **ADR 0010**.

### 1.7 La freschezza del dato non è un dettaglio implementativo

**Problema.** In un sistema a due cicli ogni dato è vecchio di qualcosa, e
guardando un numero non si sa di quando sia.

**Decisione.** `Fresh[T]` con `as_of`, TTL e sorgente; leggere un dato scaduto
richiede una riga esplicita e visibile → **ADR 0007**.

### 1.8 `market_data` su PostgreSQL non regge i volumi

**Problema.** Salvare candele di dieci strumenti su sei timeframe più i tick
significa decine di milioni di righe l'anno, sullo stesso database che serve le
query operative. Inoltre MT5 è già l'archivio autorevole dello storico.

**Decisione.** Il database conserva fatti e decisioni; i prezzi si chiedono al
broker e si congelano su file solo per il backtest → **ADR 0009**.

---

## 2. Problemi sul comportamento dell'AI

### 2.1 La confidence di un LLM non è una probabilità

**Problema.** La specifica usa la confidence dichiarata dal modello per
modulare il rischio (`0.75-0.85 → 50%`). Quel numero non è calibrato: un modello
che scrive 0.9 non vince nove volte su dieci.

**Decisione.** La scala resta, ma come *riduttore* del limite dell'utente, a
scaglioni discreti e configurabile; in parallelo si registra (confidence, esito)
e si misura la calibrazione con il Brier score, per correggere la scala sui dati
→ **ADR 0008**.

### 2.2 Un LLM non deterministico rende i backtest inutili

**Problema.** L'A/B test fra "senza AI", "con AI" e "AI autonoma" non misura
nulla se la variante con AI dà risultati diversi a ogni esecuzione.

**Decisione.** `temperature=0`, cache delle risposte per impronta del prompt,
prompt versionato → **ADR 0008**.

### 2.3 L'A/B test su un conto solo non è un A/B test

**Problema.** Tre varianti non possono operare insieme sullo stesso conto: si
influenzerebbero a vicenda attraverso margine, esposizione e limiti di rischio
condivisi.

**Decisione.** Una sola variante opera (paper o live); le altre girano in
**shadow mode**, producendo piani registrati e valutati sui prezzi reali senza
inviare ordini. Dove serve un confronto live, le varianti si separano per `magic`
number e con budget di rischio distinti — e il rischio totale resta quello
dell'utente, non la somma dei tre.

### 2.4 L'OpportunityScore 0-100 con quindici fattori è una formula inventata

**Problema.** La specifica elenca quindici dimensioni da pesare in un punteggio
unico. Pesi scelti a mano producono un numero che sembra oggettivo e non lo è, e
che si adatta ai dati su cui lo si guarda.

**Decisione.** Lo scanner produce prima i fattori **separati e leggibili**
(trend, momentum, volatilità, struttura, rischio/rendimento, spread,
correlazione, rischio da calendario). Il punteggio unico arriva dopo, con pesi
giustificati e validati walk-forward, e il contributo di ogni fattore resta
sempre visibile nel journal. Un punteggio di cui non si possono leggere le
componenti non è utilizzabile per capire perché una strategia smette di
funzionare.

### 2.5 Le relazioni intermarket non sono costanti

**Problema.** "Oro sale quando il dollaro scende", "yen sale in risk-off": sono
regolarità, non leggi, e si invertono per mesi.

**Decisione.** Nessuna relazione è scritta nel codice come verità. Si calcolano
correlazioni mobili e forza relativa **dai dati**, e le relazioni note entrano
nel contesto come informazione con una misura di stabilità.

---

## 3. Lacune della specifica

### 3.1 Il kill switch non dice cosa fare delle posizioni aperte

"Blocca immediatamente nuovi ordini" lascia aperto il caso delle posizioni già
aperte. Decisione: il kill switch blocca le aperture e **non** chiude; la
chiusura forzata è un comando separato e volontario → **ADR 0006**.

### 3.2 Mancano tabelle necessarie

All'elenco della specifica servono almeno: `execution_intents` (idempotenza,
§1.4), `ai_requests` (costi, token, cache delle risposte), `risk_budget_state`
(stato persistente di budget e breaker), `prompt_versions`, `calibration`.

### 3.3 Calendario e news: non esistono fonti gratuite affidabili

La specifica dà per disponibili calendario economico e news con `previous`,
`forecast`, `actual`. Nella realtà le fonti gratuite sono incomplete o
instabili. Decisione: interfacce astratte (`CalendarProvider`, `NewsProvider`) e
un provider "nullo" il cui comportamento è **configurabile**: con il filtro
eventi obbligatorio, assenza di calendario significa non operare; altrimenti
significa operare con un avviso esplicito. In nessun caso il sistema finge di
conoscere il calendario.

### 3.4 La validità temporale di una decisione non è definita

Una decisione presa trenta minuti fa non va eseguita adesso. Ogni `MarketDecision`
e ogni `TradePlan` hanno una scadenza; superata, l'evento è
`plan.expired` e non un'esecuzione tardiva.

### 3.5 Non è definito cosa accade alle posizioni aperte quando cambia il regime globale

La specifica dice di rivalutare, senza dire con quale autorità. Decisione: la
rivalutazione può chiudere, ridurre o proteggere una posizione, mai aumentarla.
Aumentare è una nuova operazione e passa dal percorso completo, Risk Engine
compreso.

### 3.6 Nessun requisito di osservabilità sui costi dell'AI

Un agente che analizza dieci strumenti su sei timeframe può costare molto in
chiamate al modello. `AI_DAILY_COST_LIMIT_USD` esiste nella configurazione dalla
prima fase, e il consumo va registrato per richiesta.

---

## 4. Punti della specifica che restano invariati perché giusti

- **L'utente definisce il rischio, l'AI decide dentro quel rischio.** È il
  cardine del progetto, ed è reso con una classe `frozen` e validata.
- **WAIT è un esito di pari dignità** e il budget giornaliero è un limite, non
  un obiettivo di spesa.
- **Prima il quadro generale, poi il singolo mercato.** Reso strutturale →
  **ADR 0001**.
- **Fail closed.** Davanti all'ignoto non si opera.
- **Niente strategia che si riscrive e si mette in produzione da sola.** L'AI
  propone, l'utente approva, il backtest valida.
- **Ogni decisione registrata e ricostruibile.** È il motivo per cui
  `system_events` e `config_versions` esistono dalla prima migrazione.

---

## 6. Scoperte sul conto reale (fase 3)

Le sezioni precedenti sono critica della specifica *a tavolino*. Questa raccoglie
le assunzioni che si sono rivelate sbagliate **collegandosi al conto XM demo**. Non
sono state aggirate nel codice: hanno cambiato il progetto, e ognuna ha una ADR.

### 6.1 L'API Python di MT5 non espone le sessioni di mercato

`symbol_info_sessions_quote` e `symbol_info_sessions_trade` esistono solo in MQL5.
Non c'è nemmeno una funzione per l'ora corrente del server. E `session_open` /
`session_close` di `symbol_info` sono **prezzi**, non orari.

**Effetto sul progetto.** "Mercato chiuso" non è leggibile dal broker: va dedotto.
Lo stato della quotazione distingue quindi fatti accertati (`trade_mode`, bid/ask a
zero, età del tick) da deduzioni (`MARKET_CLOSED`), e porta `is_inference`. La
fase complessiva si deduce dall'insieme degli strumenti, perché su XM gli indici
chiudono mentre il forex continua → **ADR 0011**.

### 6.2 `trade_tick_value` non è sempre nella valuta del conto

Su questo conto in EUR è in euro per il forex e **in dollari** per metalli e
indici. Il rapporto fra il profitto calcolato dal broker e quello derivato da
`tick_value` vale 1.000 su EURUSD e **0.8887** su GOLD, SILVER e US100Cash — cioè
esattamente 1/EURUSD.

**Effetto sul progetto.** Derivare il valore di un movimento da `tick_value`
sbaglierebbe le posizioni su metalli e indici del **12,5%**, senza che nessun
controllo possa accorgersene. *(Verso corretto il 2026-10-04: su un conto in EUR
la size risulterebbe sottostimata, non sovrastimata; su un conto in una valuta
più debole del dollaro sarebbe il contrario — vedi FOUNDATION_VALIDATION §5.)* Il
valore si chiede al broker con `order_calc_profit`, e la lista delle funzioni
vietate è stata corretta: i calcolatori non sono scritture → **ADR 0012**.

### 6.3 Il calcolatore del broker non è lineare nel delta di prezzo

Converte il profitto nella valuta del conto al prezzo di **chiusura**. Per una
coppia che ha la valuta del conto come base, quel prezzo *è* il tasso di
conversione: chiedendo il profitto per un movimento di 1.0 su EURUSD il risultato
era 47.056 EUR invece di 88.810, un errore del 47%.

**Effetto sul progetto.** Il movimento di prova è cento tick, e il risultato si
normalizza → **ADR 0012**.

### 6.4 Un limite di spread in punti non è confrontabile fra strumenti

Spread reali: EURUSD 20 punti, GOLD 53, US100Cash 616. Con il limite iniziale di
35 punti, **metalli e indici risultavano intrattabili** pur avendo spread normali,
e il sintomo sarebbe stato nessuna proposta su quegli strumenti, senza errori.

**Effetto sul progetto.** Il controllo primario diventa il rapporto spread/ATR, e
l'ATR di riferimento è quello del timeframe **operativo** (H1): con l'ATR di M5,
alla chiusura del venerdì EURUSD risultava a 0.74 e USDJPY a 1.46, perché l'ATR a
cinque minuti collassa nelle ore calme → **ADR 0013**.

### 6.5 XM dichiara l'oro come USD/USD/USD

Non `XAU/USD`. Le valute dichiarate dal broker, da sole, metterebbero oro e indici
americani nello stesso secchiello "USD": due posizioni che non hanno niente in
comune sembrerebbero rischio concentrato.

**Effetto sul progetto.** L'esposizione ha tre viste invece di una, e la classe di
attività si ricava dal **percorso di categoria** dichiarato dal broker
(`Derivatives\Spot Metals\GOLD`), che è un dato e non una congettura.

**Corretto nel consolidamento (2026-10-04).** Usare USD/USD per la vista per valuta
non solo mescolava oro e indici: dava all'oro il **segno sbagliato**, un long oro
risultava lungo dollaro. Ora ogni strumento ha una coppia **economica** (XAU/USD,
US100/USD) distinta dalle valute **di calcolo** dichiarate dal broker, e la vista
per valuta usa la prima → **ADR 0014**.

### 6.6 `trade_stops_level` è 0 su tutti gli strumenti

XM non impone una distanza minima fra prezzo e stop loss.

**Effetto sul progetto.** Il minimo in ATR definito dall'utente
(`RISK_MIN_STOP_DISTANCE_ATR`) è l'**unica** difesa contro uno stop troppo vicino
al prezzo, e resta obbligatorio nel Risk Engine della fase 6.

### 6.7 Il nome del server non dice se il conto è demo

Il conto demo di prova sta su `XMGlobalMU-MT5 2`, senza la parola "demo".
L'euristica sul nome lo annunciava come **CONTO REALE**.

**Effetto sul progetto.** Il tipo di conto si legge da `account_info().trade_mode`,
e se il campo manca si assume denaro vero.

### 6.8 I nomi canonici non esistono

`XAUUSD`, `US30`, `NAS100` non esistono su XM: sono `GOLD`, `US30Cash`,
`US100Cash`. E `NAS100` ha **due** candidati (`Nasdaq` e `US100Cash`).

**Effetto sul progetto.** Il registro dei simboli suggerisce i candidati e **non
sostituisce mai**: operare su `GOLD` perché l'utente ha scritto `XAUUSD`
significherebbe operare su uno strumento non autorizzato. Con più candidati lo
stato è `AMBIGUOUS` e deve scegliere l'utente.

### 6.9 I timestamp di MT5 sono in ora broker etichettata UTC

Tick e candele portano l'ora del server XM (oggi UTC+3) spacciata per UTC. L'età
calcolata contro l'ora di sistema era sbagliata di tre ore: un tick fresco
risultava "dal futuro" e **un tick fermo da 2h59m risultava fresco**. Il test di
sabato non poteva vederlo: a mercato chiuso tutti i tick sono vecchi di trenta ore.

**Effetto sul progetto.** L'età si calcola solo con lo scarto dell'ora broker,
stabilito da almeno due quotazioni concordi; senza scarto nessuna quotazione è
valida, ma il mercato si può ancora dedurre chiuso usando l'età minima garantita.
Gli stati di mercato diventano `OPEN_CONFIRMED`, `CLOSED_INFERRED`, `UNKNOWN` →
**ADR 0015**.

### 6.10 Il calcolatore arrotonda al centesimo e accetta qualunque volume

`order_calc_profit` restituisce importi al centesimo: con 1 lotto e 100 tick,
US100Cash vale 0.89 EUR invece di 0.8887 (+0,14%). Accetta però volumi oltre
`volume_max` ed è lineare nel volume (1, 100, 1000, 10000 lotti danno lo stesso
valore per unità). `order_calc_margin` allo stesso modo non valida il volume: 0.05
lotti su US100Cash (passo 0.1) restituiscono 1.37 EUR.

**Effetto sul progetto.** Il valore per unità si misura su 1000 lotti; la
normalizzazione del volume è responsabilità nostra, prima di ogni margine →
**ADR 0014**.

### 6.11 Long e short sullo stesso movimento non perdono lo stesso importo

Su EURUSD, 50 pip su 1 lotto: 446.38 EUR in long, 442.43 EUR in short. Il broker
converte al prezzo di chiusura, che per uno short in perdita è più alto. Sui
metalli e sugli indici (valuta di profitto USD, conversione indipendente dal
prezzo dello strumento) i due lati coincidono.

**Effetto sul progetto.** La perdita allo stop non si linearizza mai
(`distanza × valore_per_unità`): si chiede al broker **sul movimento esatto**
entry → stop, ed è anche il numero su cui si verifica la size → **ADR 0014**.

### 6.12 Margine su XM

Misurato con leva 1:1000, 1 lotto: EURUSD 100.00 EUR, USDJPY 88.89, GOLD 368.03,
SILVER 268.34, US100Cash 27.37, US30Cash 45.50. Corrisponde a
`volume × contratto × (prezzo, solo per CFD) / leva`, convertito in EUR. Lineare nel
volume. Il sistema non usa questa formula: è scritta qui solo per leggere i numeri.

---

## 7. Difetti trovati nei nostri stessi controlli

Vale la pena registrarli: erano protezioni che non proteggevano.

**I test sulle rotte HTTP passavano a vuoto.** FastAPI avvolge i router inclusi in
un oggetto contenitore, quindi `app.routes` non li espande: il test "nessun
endpoint di scrittura sul conto" non vedeva nessuna rotta e concludeva che non ce
n'erano di pericolose. Ora la superficie si legge dallo **schema OpenAPI**, e ogni
asserzione negativa è accompagnata da una positiva che fallisce se l'elenco è
vuoto.

**Il perimetro non era applicato all'analisi.** `SpecificationRepository` parla
direttamente col broker e non conosce la whitelist: `AssetAnalyst.analyse()` poteva
leggere e analizzare uno strumento non autorizzato. Ora il controllo è all'ingresso
e solleva, invece di restituire un contesto vuoto che il chiamante leggerebbe come
"nessun dato".

**Il terminale finto condivideva le specifiche fra istanze.** `DEFAULT_SYMBOLS` è
una tupla di dataclass mutabili: un test che modificava un volume minimo inquinava
tutti i successivi, e il risultato dipendeva dall'ordine di esecuzione. Ora ogni
terminale ne riceve una copia.

**`RETEST` veniva classificato senza una rottura precedente.** Bastava la vicinanza
al bordo del range, condizione sempre vera dentro un range stretto: ogni mercato
compresso risultava "in ritest". Un ritest richiede per definizione un livello già
rotto.

**Il terminale finto aveva l'ora broker uguale a UTC.** Era l'unica differenza
rilevante dal broker vero, ed era esattamente quella che nascondeva il difetto
fail-open di §6.9: tutti i test di freschezza passavano su un'ipotesi falsa. Ora il
finto etichetta in UTC+3 come XM.

**Un test di regressione certificava il numero sbagliato.** `TestValoreDelTick`
moltiplicava `tick_value` e otteneva 100 per 100 tick di GOLD, asserendolo come
"valore in valuta del conto": era il numero giusto in dollari. In euro è 88.87.

**La size poteva superare il rischio di un centesimo.** C'era una tolleranza di
0.01 sulla perdita attesa, per gli arrotondamenti. Il rischio è un limite
dell'utente: la tolleranza è stata tolta.

**Il contesto non era ricostruibile dal proprio payload.** `extra="forbid"` rifiuta
i campi calcolati che il dump include, quindi un contesto salvato su database non
poteva essere riletto — cioè non era persistibile nel senso che serve.

---

## 7-bis. Scoperte della fase 4 (intelligence globale)

Misurate sul conto XM il 2026-10-04, in parte durante la riapertura domenicale.

### 7b.1 VIX e indice del dollaro esistono solo come futures con scadenza

`VIX-OCT26`, `USDX-DEC26`: il nome cambia a ogni rollover e la serie salta.
**Effetto:** esclusi dall'universo; la volatilità si misura dagli strumenti stessi,
la forza del dollaro dai 28 cross → **ADR 0016**.

### 7b.2 Le obbligazioni esistono solo come CFD su ETF, con poco storico

`TLT`, `IEF`, `HYG`: 189 barre D1 (da gennaio 2026), quotazione solo in orario
americano. **Effetto:** esclusi; nessuna misura dei tassi in questa fase.

### 7b.3 La riapertura non è un istante, ed è sottile

Domenica 21:00 UTC (00:00 ora broker) ricominciano a quotare prima EURUSD e GBPUSD;
alle 21:32 UTC 18 strumenti su 37 quotavano e 14 erano ancora chiusi (metalli,
indici, cross dello yen). Alle 22:28 UTC 28 su 37 quotavano, ma 9 non stampavano un
tick ogni 15 secondi, e GBPUSD stesso risultava a tratti `STALE`. Spread di EURUSD
alla riapertura: 76 punti contro i 20 abituali. **Effetto:** la fase di mercato si
calcola per strumento e resta `PARTIALLY_OPEN` finché meno dell'80% conferma; il
contesto non è utilizzabile nel frattempo, ed è il comportamento voluto.

### 7b.4 La freschezza di una serie non si misura sull'ultima barra chiusa

Il primo criterio progettato — l'ultima barra *chiusa* recente — al lunedì avrebbe
dichiarato ferme per ore le serie H1/H4, perché l'ultima barra chiusa è del
venerdì. **Effetto:** la freschezza si misura sull'apertura della barra **in
formazione** → **ADR 0017**.

### 7b.5 Le relazioni intermarket "da manuale" non valgono sempre

Sui dati reali, nello stesso giorno: USDCAD e petrolio senza relazione (H1 +0.12,
D1 −0.10), AUDJPY e S&P 500 senza relazione (+0.12 / +0.25), oro **positivamente**
correlato all'S&P 500 (+0.49 / +0.50). **Effetto:** nessun segno atteso nel
codice; il segno e la stabilità vengono dai dati → **ADR 0017**.

### 7b.6 Con il database spento ogni scrittura costava quattro secondi

La prima analisi globale (37 specifiche nuove da archiviare) durava 146 secondi, e
non per MT5 (ogni chiamata è di millisecondi) ma per i tentativi di scrittura su
PostgreSQL spento. **Effetto:** dopo un guasto le scritture si saltano per 30
secondi, contandole: la stessa analisi dura 19 secondi.

### 7b.7 Difetti nostri trovati dal vivo o dai test

* **Tick "dal futuro".** Il lettore valutava ogni tick contro l'istante d'inizio
  lettura; con 37 strumenti letti in sequenza (allora in 3 minuti) i tick degli
  ultimi risultavano "dal futuro" fino a 165 secondi e alcuni cross venivano
  dichiarati `INCONSISTENT`. Ora ogni tick si valuta all'ora della sua lettura;
  un test di regressione fallisce con il difetto reintrodotto.
* **Overflow nella correlazione.** Il test di proprietà ha trovato che valori finiti
  ma enormi (≈1e308) facevano traboccare `math.fsum`: eccezione invece di `None`.
* **MIXED troppo facile.** Un solo timeframe di segno opposto bastava per MIXED;
  il rumore del momentum normalizzato è dell'ordine di 1, quindi quasi tutto era
  MIXED. Ora serve che lento e veloce superino entrambi la soglia.
* **`GlobalRegime` della fase 0** (`NEUTRAL`, `STRESS`) non era usato da nessuno ed
  è stato sostituito dagli stati richiesti.

## 7-ter. Scoperte della fase 4.5 (External Context Foundation)

Verificate dal vivo il 2026-10-05.

### 7c.1 L'API Python di MT5 non ha il calendario economico

32 funzioni, nessuna per calendario o notizie: il calendario del terminale esiste solo
in MQL5. **Effetto:** serve una fonte esterna (ADR 0018).

### 7c.2 Le fonti gratuite di calendario sono poche e limitate

Trading Economics ha chiuso l'accesso ospite (HTTP 410); FMP e Finnhub richiedono una
chiave; FRED dal CSV pubblico va in timeout da questa rete. L'unica fonte gratuita e
senza chiave che risponde è il feed di Forex Factory, che **copre solo la settimana
corrente** e **non pubblica il valore effettivo**. **Effetto:** livello `UNKNOWN`
oltre la copertura; la sorpresa macro non è misurabile (EXTERNAL_PROVIDERS.md).

### 7c.3 Il DXY si ricostruisce dai cambi XM

Su XM esistono tutti e sei i cambi della formula ICE, compreso USDSEK. Il DXY
ricostruito (102,155) differisce dal future dicembre (101,925) dello −0,23%: segno e
ordine di grandezza del differenziale dei tassi. **Effetto:** nessuna fonte esterna
per il DXY.

### 7c.4 MT5 può restituire storico non ancora sincronizzato

Alla prima lettura del lunedì USDCAD H4 risultava fermo a venerdì mentre H1 e M15
erano vivi; poco dopo era completo. Il sistema l'ha escluso come `STALE` (corretto).
**Effetto:** una rilettura dopo mezzo secondo per le serie ferme a mercato aperto.

### 7c.5 La media dei cross dipende dalle coppie disponibili

Sul conto reale, togliendo quattro coppie del dollaro la media dei cross non calcola
più l'USD e sposta le altre valute di 0,47; il modello a grafo di 0,02. Con tutte le
coppie i due metodi danno la stessa classifica (0,994). **Effetto:** grafo (ADR 0020).

### 7c.6 Le conclusioni intermarket dipendono dal metodo

AUDJPY ~ S&P 500 su D1: "nessuna relazione" nella fase 4 (finestra lunga di 120,
+0,25); con la base storica sull'intera storia disponibile (+0,60 su 354 rendimenti)
è una relazione vera che si sta **indebolendo** (corrente −0,12, z −4,7). **Effetto:**
base storica senza sovrapposizione e test di significatività (ADR 0020).

### 7c.7 Difetti nostri trovati dai test

* Correlazione perfetta → t non definito → base "assente".
* Una valuta senza cross annullava la stima di tutte le altre.
* Scenari di test RISK_ON/RISK_OFF incoerenti sulla volatilità: la nuova regola di
  discordanza li ha dichiarati MIXED, correttamente.

---

## 7-quater. Scoperte della fase 5A (AI Market Analyst)

Verificate dal vivo il 2026-10-05 con `gpt-5.4-mini-2026-03-17`.

### 7d.1 Il feed di Forex Factory non ha gli actual, né in JSON né in XML

Verificati entrambi i formati: `previous` e `forecast` sì, `actual` mai. **Effetto:**
la fase `ACTUAL_AVAILABLE` e la sorpresa esistono nel modello ma compaiono solo con un
provider che pubblica gli actual; il limite è fra le `known_limitations` (ADR 0021).

### 7d.2 Il modello leggeva un VIX basso come avversione al rischio

Con il solo numero il modello ha scritto il contrario di ciò che significa.
**Effetto:** l'ingresso porta per ogni evidenza `points_to` e `interpretation`
calcolati dal motore; il modello commenta, non reinterpreta (prompt 1.1.0).

### 7d.3 Lo schema "strict" cancellava un campo chiamato `title`

Il pulitore dello schema toglieva le parole chiave `title` anche quando erano *nomi di
campo* (`Theme.title`): OpenAI rispondeva 400. **Effetto:** i nomi dentro
`properties` e `$defs` non si toccano; test dedicato.

### 7d.4 Le condizioni di invalidazione non erano verificabili

Output reale: "CORRELATION BELOW 0.41" su un conflitto, "REGIME CHANGES_TO" senza
destinazione, e lo scenario risk-on invalidato da `REGIME CHANGES_TO RISK_ON` (la sua
conferma) con la descrizione "non si sposta verso risk-on". La fase 9 le avrebbe
giudicate al contrario o solo con hindsight. **Effetto:** `target_state`, direzione
`NOT_REACHED`, tabella metrica/soggetto/direzione, semantica dell'orizzonte;
condizione non verificabile = analisi `UNAVAILABLE` (ADR 0022).

### 7d.5 La validazione era più severa del progetto

Il modello ha scritto `currency_strength.USD` invece di `USD`: un riferimento non
ambiguo, ma rifiutato. **Effetto:** accettate entrambe le forme. Errore nostro, non
del modello.

---

## 7-quinquies. Scoperte della fase 5B (AI Trade Thesis in shadow mode)

Verificate su XM il 2026-10-05: 6 istanti di replay (29/9-2/10) e uno dal vivo, 4
strumenti per istante, `gpt-5.4-mini-2026-03-17`.

### 7e.1 Non esisteva un Quant Engine che proponesse operazioni

Il gruppo A ("Quant only") richiedeva un motore che non c'era: lo scanner è la fase 5.
**Effetto:** baseline deterministica nuova, dichiarata e versionata
(`quant-baseline-1.0.0`), non ottimizzata. Ripetuta su tre giri, dà sempre lo stesso
risultato: il replay è riproducibile.

### 7e.2 Il filtro decideva prima di ragionare

Filtro 1.0.0: 10 piani SHORT respinti su 11; in 5 casi i motivi sostenevano lo short,
in 5 il piano era letto come "long". Il verdetto era il primo campo dello schema.
**Effetto:** direzione ridichiarata e verificata, punti a favore e contro, verdetto per
ultimo e coerente con i punti; stesso ordine nella tesi. Con 1.1.0: direzione letta
bene 10 volte su 10, verdetti coerenti con i motivi.

### 7e.3 Il nostro ingresso suggeriva id sbagliati

Annidare il contesto globale sotto `global_context` faceva citare al modello
`global_context.regime` invece di `regime`: 4 tesi su 7 rifiutate per questo nella
pipeline 1.1. Errore nostro di impaginazione, non del modello. **Effetto:** sezioni
globali al primo livello, il percorso coincide con l'id (input 1.1.0).

### 7e.4 Il modello sbaglia l'aritmetica del rapporto rendimento/rischio

In 5 tesi su 28 (giro 1) il RR dichiarato non corrispondeva ai prezzi scritti dal
modello stesso, in 4 casi **sovrastimato** (1,18 dichiarato, 0,81 reale). **Effetto:**
nessuno sul codice: la regola "RR dichiarato = calcolato" resta e le tesi incoerenti
sono rifiutate. È una misura dell'affidabilità del modello, da seguire nel tempo.

### 7e.5 Il modello confonde STOP e LIMIT

Ingressi STOP SHORT sopra il prezzo (sono LIMIT). Rifiutati dalla geometria.

### 7e.6 Lo schema strict non applica i limiti di lunghezza

Il provider ha restituito 7 punti dove il contratto ne ammetteva 6: lo schema strict
non trasporta `maxItems`, Pydantic sì. Fail closed corretto, limite portato a 8.

### 7e.7 Il diario deve rileggere solo la propria versione

Cambiando lo schema del filtro, i record 1.0.0 non si rileggevano più con i contratti
nuovi e la valutazione si fermava. **Effetto:** `pipeline_version` su ogni decisione; il
diario rilegge e verifica solo la versione corrente e conta le altre; le metriche non
mescolano mai versioni.

### 7e.8 Un errore nostro nell'evaluator, trovato dai test

L'MFE della barra del target includeva il massimo oltre il target, cioè movimento
successivo all'uscita. Corretto prima di ogni esecuzione reale. I 14 esiti chiusi del
primo giro sono stati poi ricontrollati con una verifica indipendente sulle barre
registrate: 14 su 14 coincidono.

---

## 8. Riepilogo delle modifiche alla specifica


| # | Modifica | Ragione |
|---|----------|---------|
| 1 | Pipeline in due cicli invece di una catena | isolare i moduli lenti da quelli critici |
| 2 | MT5 fuori da Docker, su host Windows | vincolo del pacchetto, nessuna emulazione sugli ordini |
| 3 | Approvazione del rischio come tipo | rendere impossibile aggirare il Risk Engine |
| 4 | Intenti di esecuzione + riconciliazione | evitare la posizione doppia dopo un timeout |
| 5 | Budget giornaliero ricostruito dal broker | sopravvivere al riavvio |
| 6 | `Fresh[T]` su ogni lettura | non decidere su dati vecchi |
| 7 | Tempo del broker distinto da UTC | giornata di trading e filtro eventi corretti |
| 8 | Prezzi non persistiti su PostgreSQL | volumi e velocità delle query operative |
| 9 | Confidence calibrata e solo riduttiva | la confidence di un LLM non è una probabilità |
| 10 | AI deterministica e in cache | backtest e A/B test riproducibili |
| 11 | A/B test in shadow mode | un conto solo non regge tre varianti concorrenti |
| 12 | Scanner a fattori separati prima del punteggio | leggibilità e validazione dei pesi |
| 13 | Scadenza di decisioni e piani | non eseguire valutazioni vecchie |
| 14 | Kill switch che non chiude le posizioni | non trasformare un guasto in perdita certa |
| 15 | Cinque tabelle aggiuntive | idempotenza, costi AI, stato del rischio, calibrazione |
| 16 | Stato del mercato dedotto e dichiarato come tale | MT5 non espone le sessioni (§6.1) |
| 17 | Valore monetario chiesto al broker | `tick_value` non è in valuta conto (§6.2) |
| 18 | Movimento di prova di cento tick | il calcolatore non è lineare (§6.3) |
| 19 | Spread giudicato in rapporto all'ATR H1 | i punti non sono confrontabili (§6.4) |
| 20 | Esposizione per classe di attività | XM dichiara l'oro come USD/USD (§6.5) |
| 21 | Nomi dei simboli suggeriti, mai sostituiti | i nomi canonici non esistono (§6.8) |
| 22 | Un solo servizio di valutazione, verificato sull'AST | nessuna formula di P/L duplicata (ADR 0014) |
| 23 | Identità canonica degli strumenti, coppia economica distinta | indipendenza da XM, segno corretto sull'oro (§6.5) |
| 24 | Età dei dati solo con lo scarto dell'ora broker | fail-open sui tick vecchi (§6.9) |
| 25 | Stati `OPEN_CONFIRMED` / `CLOSED_INFERRED` / `UNKNOWN` | un'inferenza non diventa certezza (§6.1, §6.9) |
| 26 | `QuoteQuality` come oggetto con misure e motivi | valido, vecchio, mancante, chiuso, incoerente |
| 27 | Perdita allo stop sul movimento esatto, size verificata col broker | asimmetria long/short (§6.11) |
| 28 | `TradeIntent` mai eseguibile senza quotazione valida | precondizione verificata da test di proprietà |
| 29 | Universo di sola osservazione distinto dalla whitelist | il contesto globale richiede strumenti non operabili (ADR 0016) |
| 30 | Contesto globale a dimensioni separate, nessun punteggio unico | rappresentare evidenze in conflitto (ADR 0017) |
| 31 | Regime per regole quantitative, nessun LLM | determinismo e riproducibilità nel backtest |
| 32 | Taglio temporale delle serie e impronta sha256 | nessun look-ahead, contesto verificabile |
| 33 | Freschezza sulla barra in formazione | riapertura del lunedì (§7b.4) |
| 34 | Rischio da calendario `UNKNOWN` finché non c'è un calendario | l'assenza di informazione non è rassicurante |
| 35 | Persistenza con pausa dopo un guasto | il database spento non deve rallentare l'analisi (§7b.6) |
| 36 | XM autorevole per ciò che si negozia, non unica fonte; tre categorie di dato | VIX, tassi, calendario non disponibili su XM (ADR 0018) |
| 37 | Cinque interfacce di provider, nessun dato finto | fonti sostituibili e assenze dichiarate |
| 38 | Calendario Forex Factory con copertura dichiarata | l'unica fonte gratuita senza chiave (§7c.2) |
| 39 | Forza valutaria a grafo | indipendenza dal numero di coppie (§7c.5) |
| 40 | Intermarket: corrente contro base storica, con significatività | una correlazione non è una conferma (§7c.6) |
| 41 | MIXED dalla discordanza fra dimensioni | la media non deve cancellare un disaccordo |
| 42 | Persistenza BEST_EFFORT / REQUIRED | rischio ed esecuzione fail-closed senza database (ADR 0019) |
