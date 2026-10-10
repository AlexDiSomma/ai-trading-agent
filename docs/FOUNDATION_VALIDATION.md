# Validazione della foundation

**Data:** 2026-10-04 (domenica, mercati chiusi) · **Conto:** XM demo
`XMGlobalMU-MT5 2`, valuta EUR, leva 1:1000 · **Stato:** foundation consolidata,
fase 4 non iniziata.

Questo report chiude il consolidamento richiesto prima della fase 4. Ogni numero
viene dal conto reale: `python -m backend.validazione_foundation` confronta il
sistema con le chiamate grezze al terminale e salva tutto in
[`validazione/xm_demo_2026-10-04.json`](validazione/xm_demo_2026-10-04.json). Una
copia congelata è il termine di confronto dei test automatici.

**In una riga.** Il servizio di valutazione coincide con MT5 al centesimo su sei
strumenti; nove invarianti reggono su migliaia di casi generati; e il
consolidamento ha trovato **un difetto fail-open** (tick vecchi dichiarati freschi)
e **un errore nella nostra stessa documentazione** (il verso dell'errore di
`tick_value`), entrambi corretti.

| | |
|---|---|
| Test automatici | **744 verdi**, 13 saltati di default |
| Test contro MT5 reale (`RUN_LIVE_MT5=1`) | **13/13 verdi** sul conto demo |
| Strumenti validati | EURUSD, USDJPY, GOLD, SILVER, US100Cash, US30Cash |
| Nuove ADR | [0014](adr/0014-valutazione-e-identita-centralizzate.md) valutazione e identità, [0015](adr/0015-eta-dei-dati-in-ora-broker.md) età dei dati in ora broker |
| Ordini inviati | nessuno — `order_send` non esiste nel codice (ADR 0004) |

---

## 1. Assunzioni iniziali risultate errate

Le prime otto sono emerse nella fase 3, le altre nel consolidamento. Nessuna è
stata aggirata nel codice: ognuna ha cambiato l'architettura e ha un test di
regressione.

| # | Assunzione | Realtà misurata | Conseguenza |
|---|------------|-----------------|-------------|
| 1 | L'API Python di MT5 espone le sessioni | Solo MQL5. `session_open/close` sono **prezzi** | "Chiuso" è sempre dedotto (ADR 0011) |
| 2 | `trade_tick_value` è in valuta del conto | In EUR per il forex, **in USD** per metalli e indici | Mai usato nei calcoli (ADR 0012, 0014) |
| 3 | `order_calc_profit` è lineare nel delta | Converte al prezzo di chiusura: 1.0 su EURUSD dà 47056 invece di 88801 | Delta di 100 tick |
| 4 | Lo spread in punti è confrontabile | EURUSD 20, GOLD 53, US100Cash 616 punti, tutti normali | Spread/ATR(H1) (ADR 0013) |
| 5 | Le valute del broker descrivono l'esposizione | GOLD è USD/USD/USD | Coppia economica separata (ADR 0014) |
| 6 | `trade_stops_level` protegge da stop troppo vicini | È 0 ovunque | Il minimo in ATR dell'utente è l'unica difesa |
| 7 | Il nome del server dice se il conto è demo | `XMGlobalMU-MT5 2` è demo | Si legge `trade_mode` |
| 8 | I nomi canonici esistono | `GOLD`, `US100Cash`, `US30Cash`; `NAS100` ambiguo | Identità canonica, alias mai sostituiti |
| 9 | **I timestamp di MT5 sono UTC** | **Ora broker (UTC+3) etichettata UTC** | **Fail-open corretto** (ADR 0015) |
| 10 | Un tick basta per misurare lo scarto orario | Ragionamento circolare sul tick da valutare | Due quotazioni concordi |
| 11 | `order_calc_margin` valida il volume | 0.05 lotti su US100Cash (passo 0.1) → 1.37 EUR | Normalizzazione nostra, sempre prima |
| 12 | Il calcolatore è preciso | Arrotonda al centesimo: US100Cash 0.89 invece di 0.8887 per punto | Misura su 1000 lotti |
| 13 | Stessa distanza, stessa perdita long e short | EURUSD 50 pip: 446.38 long, 442.43 short | Perdita sul movimento esatto |
| 14 | Il mock fedele "a meno dei prezzi" | Aveva l'ora broker = UTC: nascondeva il #9 | Mock in UTC+3 |
| 15 | Un centesimo di tolleranza sul rischio è innocuo | Il rischio è un limite, non una stima | Tolleranza zero |
| 16 | **(nostro)** `tick_value` "sovrastima la size" | Su un conto in EUR la **sottostima** dell'11% | Motivazione corretta, §5 |

L'ultima merita una nota: era scritta in ADR 0012, nell'analisi e in sei commenti, e
nessun test la verificava. Rifacendo i conti con i numeri reali il verso era
opposto. La regola resta giusta, ma per un motivo diverso — ed è pericoloso tenere
una regola giusta con una motivazione falsa: chi la verifica e la trova falsa può
concludere che la regola non serve.

---

## 2. Comportamento osservato su XM

### 2.1 Specifiche e identità

| Broker | Canonico | Fonte | Classe | Economica | Profitto / margine | Contratto | Volume min / passo / max | Percorso |
|---|---|---|---|---|---|---|---|---|
| EURUSD | EURUSD | regola_fx | FX | EUR/USD | USD / EUR | 100 000 | 0.01 / 0.01 / 50 | `Forex\Standard\Majors` |
| USDJPY | USDJPY | regola_fx | FX | USD/JPY | JPY / USD | 100 000 | 0.01 / 0.01 / 50 | `Forex\Standard\Majors` |
| GOLD | XAUUSD | catalogo | METAL | XAU/USD | USD / USD | 100 | 0.01 / 0.01 / 50 | `Derivatives\Spot Metals` |
| SILVER | XAGUSD | catalogo | METAL | XAG/USD | USD / USD | 5 000 | 0.01 / 0.01 / 50 | `Derivatives\Spot Metals` |
| US100Cash | US100 | regola_indice | INDEX | US100/USD | USD / USD | 1 | 0.1 / 0.1 / 240 | `Derivatives\Cash\Cash Indices` |
| US30Cash | US30 | regola_indice | INDEX | US30/USD | USD / USD | 1 | 0.1 / 0.1 / 180 | `Derivatives\Cash\Cash Indices` |

`trade_stops_level` e `trade_freeze_level` sono 0 su tutti; `trade_mode` 4 (pieno).

### 2.2 Valutazione, margine, size (budget 100 EUR)

Prezzo di riferimento = ultimo tick del venerdì. Stop a distanza fissa per
strumento. "Grezzo" è `order_calc_*` chiamato direttamente; "servizio" è
`BrokerValuationService`. **Coincidono al centesimo su tutte le righe.**

| Strumento | Prezzo | Stop a | Perdita long / short, 1 lotto | Valore 1.0 di prezzo | Margine 1 lotto (buy / sell) | Size | Perdita alla size | Un passo in più |
|---|---|---|---|---|---|---|---|---|
| EURUSD | 1.12511 | 0.0050 | 446.38 / 442.43 | 88 801.27 | 100.00 / 100.00 | 0.22 | 98.20 | 102.67 |
| USDJPY | 157.866 | 0.50 | 281.56 / 281.45 | 563.12 | 88.89 / 88.87 | 0.35 | 98.55 | 101.36 |
| GOLD | 4140.34 | 10.0 | 888.88 / 888.88 | 88.8723 | 368.03 / 367.96 | 0.11 | 97.78 | 106.67 |
| SILVER | 60.376 | 0.30 | 1333.32 / 1333.32 | 4 443.615 | 268.34 / 268.29 | 0.07 | 93.33 | 106.67 |
| US100Cash | 30794.64 | 100 | 88.89 / 88.89 | 0.88872 | 27.37 / 27.37 | 1.1 | 97.78 | 106.67 |
| US30Cash | 51191.77 | 200 | 177.78 / 177.78 | 0.88872 | 45.50 / 45.50 | 0.5 | 88.89 | 106.67 |

Importi in EUR. "Un passo in più" è la perdita, chiesta al broker, con un
`volume_step` oltre la size: supera sempre il budget, quindi la size è la massima
consentita.

### 2.3 Altri comportamenti

* **Ora del server.** Etichette in UTC+3: ultimo tick del venerdì 23:57:59
  "UTC", cioè 20:58 UTC reali. Di domenica lo scarto **non è misurabile** (nessuna
  coppia di quotazioni fresche): la validazione riporta fonte `UNAVAILABLE`, ed è
  il comportamento corretto.
* **Qualità delle quotazioni di domenica.** Tutti e sei: `MARKET_CLOSED`, mercato
  `CLOSED_INFERRED`, `is_inference=true`, "ultima quotazione di almeno 30.1 ore fa"
  — età **minima garantita** calcolata senza scarto, come prevede l'ADR 0015.
* **Calcolatori.** Accettano volumi oltre `volume_max` (fino a 10 000 lotti
  provati), lineari nel volume, arrotondati al centesimo. `order_calc_margin`
  accetta volumi fuori passo. Un simbolo inesistente restituisce `None` con
  `last_error = (-1, 'Terminal: Call failed')`.
* **Bid/ask a zero** per i simboli non ancora attivati nel Market Watch (fase 3).

---

## 3. Decisioni architetturali definitive

1. **`BrokerValuationService` è l'unico punto di calcolo monetario** — profitto,
   perdita allo stop (sul movimento esatto), rischio residuo di posizioni aperte,
   valore per unità (100 tick × 1000 lotti), nozionale, margine (solo su volumi
   normalizzati), normalizzazione del volume (per difetto, `Decimal`), size per
   rischio verificata col broker e senza tolleranza. Nessun altro modulo legge
   `tick_value` o chiama i calcolatori: **verificato sull'AST** di tutto il
   backend (ADR 0014).
2. **`Instrument` è l'identità** — `canonical` per il sistema, `broker_symbol`
   solo per il terminale; coppia economica (`base/quote`) distinta dalle valute di
   calcolo (`profit/margin`); `mapping_source` dichiara se il canonico è certo.
   L'esposizione per valuta usa la coppia economica (ADR 0014).
3. **`QuoteQuality` è l'unico giudizio su un prezzo** — `VALID`, `STALE`,
   `MISSING`, `MARKET_CLOSED`, `INCONSISTENT`, con bid, ask, spread, spread/ATR,
   ora dell'ultimo tick e dell'ultima candela, età e motivi. `QuoteState` aggiunge
   solo la politica (trade_mode, spread).
4. **Stati di mercato `OPEN_CONFIRMED`, `CLOSED_INFERRED`, `UNKNOWN`.** Non esiste
   "chiuso certo". Invarianti nel costruttore: chiuso solo come inferenza, aperto
   solo con dato valido e mai come inferenza.
5. **L'età si calcola in ora broker** — scarto da almeno due quotazioni concordi;
   senza scarto, intervallo di ±14 ore e nessuna quotazione valida (ADR 0015).
6. **`TradeIntent`** — volume, perdita, margine e `executable` (= dati validi, non
   approvazione). Mai eseguibile senza `VALID` + `OPEN_CONFIRMED`.
7. **Il confronto col broker reale è parte dei test** — valori congelati per la
   suite normale, confronto istantaneo con la chiamata grezza per i test live.

Restano in vigore, invariate: nessun `order_send` (ADR 0004), Risk Engine
indipendente dall'AI (ADR 0005), default PAPER.

---

## 4. Strumenti testati

Per **ognuno** dei sei strumenti, in `tests/unit/test_foundation_strumenti.py`
(111 casi) contro i valori reali congelati, e in `tests/live/test_live_xm.py`
contro MT5 nello stesso istante:

| Verifica | Contro il conto reale (valori congelati) | Contro MT5 grezzo (live) |
|---|---|---|
| Mappatura del simbolo | identità e specifica **identiche** | `by_canonical` → simbolo XM |
| Calcolo del profitto | long e short entro 0,1% | — |
| Stima della perdita allo stop | long e short entro 0,1%; stop dal lato sbagliato rifiutato | identica al centesimo |
| Calcolo della size | volume **identico**, perdita entro 0,1%; budget insufficiente → 0 | ≤ budget, un passo in più > budget |
| Calcolo del margine | 1 lotto e volume minimo entro 0,1%; volume non normalizzato rifiutato | identico al centesimo |
| Normalizzazione del volume | quattro casi **identici** (passo, minimo, massimo, residuo) | multiplo esatto del passo |
| Stato della quotazione | `MARKET_CLOSED`/`CLOSED_INFERRED` come di domenica; `VALID` solo con ora nota; `MISSING`; `INCONSISTENT` | coerente con lo stato dell'ora broker |

Più: asimmetria long/short di EURUSD, scarto da `tick_value` (12,52% metalli e
indici, ~0 forex), `TradeIntent` eseguibile con quotazione valida e bloccato con
quotazione vecchia, verso dell'errore di `tick_value` con i numeri reali.

Il terminale finto sta entro lo **0,04%** del broker su profitto e margine, con
specifiche identiche. La tolleranza dei test è 0,1%.

---

## 5. Mismatch tra formule teoriche e broker

Perdita per 1 lotto allo stop, in EUR. "Teorica" = formula da manuale
(`distanza × contratto`, convertita a 1.12511); "da tick_value" = `distanza /
tick_size × tick_value`, il calcolo che il sistema **non** fa.

| Strumento | Teorica | Lineare (`distanza × valore per unità`) | Broker long | Broker short | Da `tick_value` |
|---|---|---|---|---|---|
| EURUSD | 444.40 | 444.01 | **446.38** (+0,45%) | **442.43** (−0,44%) | 444.36 |
| USDJPY | 281.50 | 281.56 | 281.56 | 281.45 | 281.45 |
| GOLD | 888.80 | 888.72 | 888.88 | 888.88 | **1000.00** (+12,5%) |
| SILVER | 1333.20 | 1333.08 | 1333.32 | 1333.32 | **1500.00** (+12,5%) |
| US100Cash | 88.88 | 88.87 | 88.89 | 88.89 | **100.00** (+12,5%) |
| US30Cash | 177.76 | 177.74 | 177.78 | 177.78 | **200.00** (+12,5%) |

Cosa dicono i numeri:

* **`tick_value` sbaglia del 12,5% su metalli e indici, e il verso dipende dal
  conto.** Su questo conto in EUR la perdita per lotto risulta *sovrastimata*,
  quindi la size **sottostimata** dell'11% (GOLD: 0.10 lotti invece di 0.11) e il
  nozionale sovrastimato. È un errore prudente *per caso*: su un conto in una valuta
  che vale meno del dollaro (JPY) sarebbe stato un fail-open di un fattore 157.
  Le versioni precedenti della documentazione dicevano "sovrastima la size":
  corretto, con nota, in ADR 0012 e nell'analisi §6.2.
* **La formula lineare sbaglia il lato** su EURUSD (±0,45%): il broker converte al
  prezzo di chiusura. Per questo la perdita si chiede sul movimento esatto.
* **Il delta di 1.0** su EURUSD dava 47 056 invece di 88 801 (−47%): il calcolatore
  non è lineare nel delta (fase 3).
* **Un lotto di prova** arrotonda: US100Cash 0.89 invece di 0.88872 per punto
  (+0,14%). Con 1000 lotti l'errore scompare.
* **Il margine** segue `volume × contratto × (prezzo, solo per i CFD) / leva`,
  convertito: EURUSD 100.00 = 100 000/1000 EUR; GOLD 368.03 ≈ 100 × 4140.34 / 1000
  USD. Il sistema non usa la formula: lo scarto con la teoria è sotto lo 0,05%.

---

## 6. Invarianti verificati

Con test di proprietà (`tests/unit/test_foundation_invarianti.py`, hypothesis, 60
casi generati ciascuno sui sei strumenti, entrambe le direzioni, stop da 0,05 a 20
volte la distanza di riferimento) e, dove indicato, sul broker vero.

| Invariante | Test di proprietà | Conto reale |
|---|---|---|
| Aumentando il volume la perdita allo stop non diminuisce (e cresce davvero se il volume raddoppia) | ✔ | ✔ perdite a 1×, 2×, 10×, 100× il minimo crescenti su tutti |
| Spostando lo stop più lontano la perdita non diminuisce | ✔ | ✔ a 0,5×, 1×, 2× la distanza |
| Riducendo il budget la size non aumenta | ✔ | ✔ budget 400 → 10: EURUSD 0.89, 0.44, 0.22, 0.11, 0.02 |
| La size non supera `volume_max` né il massimo dell'utente | ✔ (rischi fino a 10 milioni) | — |
| La size è multiplo esatto di `volume_step`, e zero o ≥ `volume_min` | ✔ (`Decimal`) | ✔ live |
| La perdita alla size scelta non supera il budget, nemmeno di un centesimo | ✔ (richiesta di nuovo al broker) | ✔ live e congelato |
| Quotazione `STALE`, `MISSING`, `INCONSISTENT`, `MARKET_CLOSED` o mercato `UNKNOWN`/`CLOSED_INFERRED` → `TradeIntent` non eseguibile | ✔ (5 combinazioni × strumenti × budget) | — |
| Quotazione di un altro strumento → non eseguibile | ✔ | — |
| Senza ora broker nota nessuna quotazione è valida, per qualunque etichetta da −14 a +60 ore | ✔ | ✔ domenica: nessuna valida |
| Con ora nota, `VALID` **se e solo se** l'età vera è entro il limite (scarti 0, +2, +3, −5, +9 ore) | ✔ | — |
| Un tick solo non stabilisce mai lo scarto | ✔ | — |
| `CLOSED_INFERRED` ⇒ inferenza; `OPEN_CONFIRMED` ⇒ `VALID` e non inferenza; nessuno stato "chiuso certo" | ✔ (costruttore) | ✔ |

Garanzie strutturali sull'AST (`test_valutazione_centralizzata.py`): nessun modulo
fuori da un elenco motivato legge `tick_value` o chiama i calcolatori; nessuna età
calcolata da `age_seconds(<x>.time)`; con asserzioni positive perché non passino a
vuoto. Restano verdi le garanzie precedenti: nessun `order_send`, `order_check`,
`position_close`, `order_modify`; nessun endpoint di scrittura sul conto; `risk/` e
`ai/` indipendenti.

---

## 7. Problemi ancora aperti

1. ~~**Il ramo "mercato aperto" non è stato osservato sul broker vero.**~~
   **Chiuso il 2026-10-04 alle 21:15 UTC**, durante la riapertura domenicale: ora
   broker `MEASURED`, scarto +10800 s da due quotazioni concordi (EURUSD, GBPUSD);
   EURUSD `VALID`/`OPEN_CONFIRMED` con età esatta di 9,5 s; gli strumenti non ancora
   riaperti `CLOSED_INFERRED` con età esatta (48,3 ore); 13/13 test live verdi a
   mercato aperto. Misura in
   [`validazione/xm_demo_2026-10-04_riapertura.json`](validazione/xm_demo_2026-10-04_riapertura.json).
   **Ripetuto in piena sessione il 2026-10-05 alle 08:55 UTC**: ora broker
   confermata da 10 quotazioni concordi, tutti e sei gli strumenti `VALID`/
   `OPEN_CONFIRMED` (età 0,6-4,6 s), size e perdite identiche a MT5 al centesimo,
   14/14 test live verdi ([`validazione/xm_demo_2026-10-05.json`](validazione/xm_demo_2026-10-05.json)).
2. **Cambio d'ora legale.** Lo scarto passerà da +3 a +2 (XM segue l'ora legale
   americana, primo weekend di novembre). Viene rilevato solo da due quotazioni
   concordi su uno scarto nuovo, e una misura vale dodici ore. Mai provato dal vero.
3. **Lo scarto non è persistito.** Dopo un riavvio a mercato aperto si rimisura
   all'avvio (millisecondi); a mercato chiuso non serve. Da valutare in fase 4.
4. **Soglie euristiche non validate:** 15 minuti per dedurre il mercato chiuso, 25
   ATR per un prezzo incoerente, ±14 ore di incertezza, spread/ATR 0.30. Da
   misurare in walk-forward (fase 9).
5. **Costo delle chiamate.** Ogni size è da 2 a qualche chiamata al terminale,
   sequenziali. Per lo scanner della fase 5 servirà una cache della perdita per
   lotto per (strumento, distanza) entro il TTL del prezzo.
6. **Altri timestamp in ora broker non ancora rivisti con l'ADR 0015:** gli orari
   dei deal (`DealHistory` usa già la giornata del broker, ma non converte i
   timestamp), il parametro `end` di `get_candles` (confronta un'etichetta broker
   con un istante del chiamante) e il filtro sessioni in `RiskLimits`, che riceve
   l'ora broker da chi lo chiamerà in fase 6.
7. **Freschezza delle serie di candele.** `Fresh.as_of` di una serie è l'istante
   di lettura, non l'età dei dati; l'età vera dell'ultima candela si giudica solo
   in `QuoteQuality`. Corretto ma facile da fraintendere: da rendere esplicito nel
   tipo quando le candele entreranno nelle decisioni.
8. **Strumenti fuori dal catalogo.** I metalli hanno un catalogo esplicito; per
   altre famiglie (crypto, azioni, energia) il canonico è `dedotto` e
   `canonical_is_certain` è falso. Va esteso quando la whitelist le includerà.
9. **Conto in valuta diversa da EUR.** Tutte le misure sono su un conto in EUR. Il
   servizio non assume la valuta (la chiede al broker), ma nessun test copre un
   conto in USD o JPY — che è proprio il caso in cui l'errore di `tick_value`
   diventerebbe pericoloso.

Niente di questo blocca la fase 4, che non tocca valutazione né esecuzione. I punti
1 e 2 vanno chiusi prima della fase 6 (Risk Engine).
