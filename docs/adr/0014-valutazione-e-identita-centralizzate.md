# ADR 0014 — Un solo servizio di valutazione, una sola identità degli strumenti

**Stato:** accettata · **Data:** 2026-10-04 · **Origine:** consolidamento della
foundation prima della fase 4 · **Estende:** ADR 0012

## Contesto

Dopo la fase 3 i numeri monetari venivano da tre posti diversi: il connettore
(`value_per_price_unit`), il calcolatore di esposizione (`volume × distanza ×
valore_per_unità`) e `SymbolInfo.normalize_volume`. Nessuno era sbagliato da solo,
ma la regola dell'ADR 0012 — "il valore monetario lo dà il broker" — era rispettata
per disciplina, non per costruzione. E la disciplina si perde alla prima urgenza.

Lo stesso per i nomi: `GOLD`, `US100Cash`, `US30Cash` circolavano per tutto il
codice. Un secondo broker, o un cambio di nome di XM, avrebbe toccato l'esposizione,
il journal e le statistiche.

Due misure sul conto reale hanno reso il problema concreto:

* **la linearizzazione sbaglia.** Su EURUSD, uno stop di 50 pip su 1 lotto costa
  446.38 EUR in long e 442.43 EUR in short: il broker converte al prezzo di
  chiusura, che per lo short in perdita è più alto. `distanza × valore_per_unità`
  dà lo stesso numero ai due lati;
* **l'esposizione per valuta aveva il segno sbagliato sull'oro.** Con le valute
  dichiarate da XM (USD/USD/USD) un long oro risultava *lungo dollaro*; in realtà
  è lungo XAU e corto USD.

## Decisione

### 1. `BrokerValuationService` è l'unico punto di calcolo monetario

`backend/valuation/service.py`. Fornisce profitto, perdita allo stop, perdita
residua di una posizione aperta, valore per unità di prezzo, nozionale, margine,
normalizzazione del volume, size per un rischio, e lo scarto diagnostico da
`tick_value`. Ogni numero viene da `order_calc_profit` / `order_calc_margin`.

* **La perdita allo stop si chiede sul movimento esatto** entry → stop, mai come
  distanza × valore. È il calcolo che il broker farà davvero.
* **La size viene verificata col broker**: dopo il calcolo, la perdita al volume
  scelto viene richiesta di nuovo e, se supera il budget anche di un centesimo, si
  scende di un passo. Nessuna tolleranza: il rischio è un limite dell'utente.
* **Il volume si normalizza per difetto** al passo, si taglia a `volume_max` (e al
  massimo dell'utente), sotto il minimo diventa zero. Aritmetica `Decimal`, perché
  `0.1 + 0.2` in virgola mobile non è un multiplo di 0.1.
* **Il margine rifiuta volumi non normalizzati.** Misurato: il broker non li valida
  (0.05 lotti su US100Cash, passo 0.1, restituisce 1.37 EUR).
* **Il valore per unità si misura con 100 tick su 1000 lotti.** Il broker arrotonda
  al centesimo: con 1 lotto US100Cash vale 0.89 invece di 0.8887 (+0,14%). Il
  calcolatore accetta volumi oltre `volume_max` ed è lineare nel volume (misurato
  con 1, 100, 1000, 10000 lotti).

**Garanzia strutturale**, non disciplina: `tests/unit/test_valutazione_centralizzata.py`
legge l'AST di tutto il backend e fallisce se un modulo fuori da un elenco motivato
legge `tick_value` o chiama i calcolatori del broker.

### 2. `Instrument` è l'identità; il nome del broker si usa solo col broker

`backend/schemas/instrument.py`, `backend/market_data/instruments.py`.

* `canonical` (`XAUUSD`, `US100`, `EURUSD`) — il nome con cui ragiona il sistema;
* `broker_symbol` (`GOLD`, `US100Cash`) — solo per parlare col terminale;
* `base_currency` / `quote_currency` — la coppia **economica** (XAU/USD);
* `profit_currency` / `margin_currency` — quelle **dichiarate dal broker**;
* `contract` — la specifica (digits, tick, contratto, volumi, stops/freeze level);
* `mapping_source` — `catalogo`, `regola_fx`, `regola_indice` o `dedotto`. Solo
  `dedotto` non è un'identità certa.

Il catalogo esplicito copre i casi che nessuna regola deduce (`GOLD` → `XAUUSD`).
La direzione è solo dal broker al canonico: dal canonico si risale soltanto fra gli
strumenti già letti, e un canonico ambiguo è un errore, non una scelta.

### 3. `TradeIntent` è il formato fra idea e Risk Engine

Volume, perdita attesa, margine e il flag `executable` — che significa *i dati su
cui si basa sono validi*, non *approvato*. Una quotazione che non sia `VALID` con
mercato `OPEN_CONFIRMED` produce sempre un intent non eseguibile (test di
proprietà). Non è un ordine e nessuno lo esegue.

## Alternative scartate

* **Formule locali "verificate"**: `volume × contract_size × Δprezzo × cambio`.
  Richiede di conoscere il cambio e la convenzione del broker per ogni strumento;
  il broker li conosce già. E la misura su EURUSD mostra che anche una formula
  giusta in teoria (linearizzata) sbaglia il lato short.
* **Tenere i nomi XM e una tabella di alias ai bordi.** Sposta il problema senza
  risolverlo: chi scrive il codice nuovo deve ricordarsi in quale "lingua" è.

## Conseguenze

Positive: un numero monetario sbagliato ha un solo posto dove essere corretto; i
test lo confrontano con il conto reale (`tests/dati/xm_demo_2026-10-04.json`) e,
con `RUN_LIVE_MT5=1`, con la chiamata grezza al terminale nello stesso istante.

Negative: ogni calcolo è una chiamata al terminale (millisecondi, ma sequenziali).
Per la fase 5, che classificherà decine di strumenti, la perdita per lotto andrà
messa in cache per (strumento, distanza) entro il TTL del prezzo.
