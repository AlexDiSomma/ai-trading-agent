# Fonti esterne: cosa serve davvero, alternative confrontate

**Data:** 2026-10-05 · **Fase:** 4.5 · **Decisione:** [ADR 0018](adr/0018-fonti-oltre-il-broker.md)

XM/MT5 resta la fonte autorevole per tutto ciò che si **negozia**: strumenti,
prezzi del broker, specifiche, margine, conto, esecuzione. Non è però l'unica fonte
della Global Intelligence: alcuni dati di riferimento su XM non esistono, o esistono
in una forma che non si può usare. Questo documento dice **quali servono davvero**,
quali alternative sono state verificate e perché è stata scelta ciascuna.

Ogni riga "verificato" è una richiesta fatta davvero da questa macchina il
2026-10-05, non un'informazione presa da documentazione.

---

## 1. Cosa serve, e perché XM non basta

| Dato | Categoria | Serve per | Su XM | Necessità |
|---|---|---|---|---|
| Calendario economico | `MACRO_SERIES` | finestre di blocco attorno agli eventi, regime `EVENT_DRIVEN` | **assente** (l'API Python di MT5 non ha funzioni di calendario: 32 funzioni, nessuna) | **alta**: senza, il rischio da eventi è `UNKNOWN` e il contesto non è mai `GOOD` |
| VIX (volatilità implicita) | `REFERENCE_INSTRUMENT` | sentiment di rischio, regime di volatilità | solo future con scadenza `VIX-OCT26`: nome che cambia, prezzo che non è l'indice | **media**: la volatilità realizzata c'è già, il VIX aggiunge l'aspettativa del mercato |
| Rendimenti titoli di Stato | `MACRO_SERIES` | tassi, pendenza della curva | solo CFD su ETF (`TLT`, `IEF`), 189 barre D1, prezzi e non rendimenti | **media** |
| Indice del dollaro (DXY) | `REFERENCE_INSTRUMENT` | verifica della forza USD | solo future `USDX-DEC26` | **bassa**: si ricostruisce con la formula ICE dai sei cambi XM (verificato: 102,155 contro 101,925 del future, −0,23%, coerente con il differenziale dei tassi) |
| Dati macro (inflazione, occupazione, PIL) | `MACRO_SERIES` | sorpresa rispetto alle attese, regime macro | assenti | **bassa in questa fase**: servirà quando si leggerà la sorpresa, non solo l'orario |
| Notizie | — | sentiment, eventi non in calendario | assenti | **rinviata** per scelta (punto 4 della richiesta) |

---

## 2. Calendario economico

| Fonte | Costo | Chiave | Verificato il 2026-10-05 | Copertura | Valore pubblicato (`actual`) | Note |
|---|---|---|---|---|---|---|
| **Forex Factory** (feed JSON `ff_calendar_thisweek.json`) | gratuito | no | ✅ HTTP 200, 79 eventi, 10 valute | **solo settimana corrente** | **no** | non ufficiale, formato non garantito; `Cache-Control: max-age=60` |
| MT5 (calendario del terminale) | incluso | — | ❌ non esposto dall'API Python (solo MQL5) | — | — | servirebbe un Expert Advisor MQL5 che esporti i dati |
| Trading Economics | **a pagamento** | sì | ❌ HTTP 410: "guest account discontinued" | ampia | sì | standard di settore, piani da centinaia di euro/mese |
| Financial Modeling Prep | piano gratuito con chiave | sì | ⚠️ HTTP 401 con chiave demo; richiede registrazione | ampia | sì | limite 250 richieste/giorno nel piano gratuito (da verificare con chiave) |
| Finnhub | chiave; calendario economico nel piano a pagamento secondo la loro documentazione | sì | ⚠️ HTTP 401 senza chiave; non verificabile senza account | ampia | sì | — |
| Investing.com | — | — | non provato | ampia | sì | richiederebbe scraping: escluso per i termini d'uso |

**Scelta oggi: Forex Factory**, l'unica fonte gratuita e senza chiave che risponde.
Limiti gestiti nel codice: oltre la fine della settimana coperta il livello è
`UNKNOWN`; un formato inatteso è un errore, mai un calendario vuoto; cache di un'ora.

**Raccomandazione**: Forex Factory basta per sapere **quando** c'è un evento (finestre
di blocco). Per leggere la **sorpresa** (valore pubblicato contro atteso) serve una
fonte con `actual`: il candidato gratuito da verificare è FMP con chiave. Se l'utente
volesse una fonte ufficiale e affidabile a pagamento, Trading Economics; ma non
serve finché non si legge la sorpresa.

## 3. VIX

| Fonte | Costo | Chiave | Verificato | Note |
|---|---|---|---|---|
| **CBOE** (`VIX_History.csv`) | gratuito | no | ✅ HTTP 200 (dopo redirect), OHLC giornaliero dal 1990 | **ufficiale**: Cboe calcola l'indice |
| FRED (`VIXCLS`) | gratuito | per l'API sì (gratuita) | ⚠️ CSV pubblico in timeout da questa rete; API raggiungibile ma chiede la chiave | ripubblica il dato CBOE |
| XM `VIX-OCT26` | incluso | — | ✅ esiste | future: scade, cambia nome, non è l'indice |
| Yahoo Finance | gratuito | no | non provato | non ufficiale, termini d'uso restrittivi |

**Scelta: CBOE.** Fonte primaria, gratuita, senza chiave.

## 4. Rendimenti dei titoli di Stato

| Fonte | Costo | Chiave | Verificato | Note |
|---|---|---|---|---|
| **Tesoro USA** (curva par yield, CSV per anno) | gratuito | no | ✅ HTTP 200, anno corrente e precedente | **ufficiale**; solo USA |
| FRED (`DGS10`, `DGS2`, `DGS3MO`) | gratuito | sì (gratuita) | ⚠️ come sopra | ripubblica i dati del Tesoro, più comodo per molte serie |
| BCE (Data Portal) | gratuito | no | non provato | curva dell'area euro: utile quando servirà il differenziale |

**Scelta: Tesoro USA.** Per i rendimenti europei e giapponesi (differenziali) servirà
una seconda fonte: BCE e Ministero delle Finanze giapponese pubblicano gratuitamente.

## 5. Dati macro

| Fonte | Costo | Chiave | Verificato | Note |
|---|---|---|---|---|
| **FRED** (St. Louis Fed) | gratuito | sì, gratuita | ⚠️ API raggiungibile, serve la chiave | centinaia di migliaia di serie USA e internazionali; ufficiale |
| Fonti nazionali (BLS, Eurostat, ECB) | gratuito | varia | non provato | ufficiali ma frammentate |
| Trading Economics | a pagamento | sì | ❌ guest chiuso | tutto in un posto |

**Nessuna scelta oggi** (`MacroDataProvider` = non configurato). **Raccomandazione:
FRED**, quando servirà: basta che l'utente crei una chiave gratuita.

## 6. Notizie

Attivate il 2026-10-06 con fonti **gratuite** (`backend/news/`): 14 feed RSS verificati.

| Livello | Fonti | Uso |
|---|---|---|
| Ufficiale | Fed, BCE, BoE, BoJ | fonte primaria: vale da sola |
| Testata | BBC Business/World, Guardian Business/World, Al Jazeera, CNBC Economy/World, MarketWatch | vale se confermata da un secondo editore |
| Specializzata | FXStreet, investingLive (ex ForexLive) | veloce ma secondaria |

Escluse: GDELT (HTTP 429 persistente), Casa Bianca e Tesoro USA (feed assenti o vietati).
Alternative a pagamento valutate: Marketaux 29-199 $/mese, Newsquawk 199-399 $/mese (API
solo Enterprise), Dow Jones Newswires 5.000-50.000 $/anno per utente, Reuters su
preventivo. Si passa a pagamento solo se i dati paper mostrano che le notizie migliorano
i risultati.

---

## 7. Riepilogo: cosa serve davvero

1. **Già attivo, gratuito, senza chiave:** calendario (Forex Factory), VIX (CBOE),
   curva USA (Tesoro). Coprono finestre di evento, volatilità implicita, tassi.
2. **Da decidere dall'utente, a costo zero:** una chiave FRED (dati macro) e una
   chiave FMP (calendario con valore pubblicato, per leggere la sorpresa).
3. **Nessun provider a pagamento è necessario oggi.** Trading Economics diventa
   rilevante solo se le fonti gratuite si rivelassero inaffidabili nell'uso.

Tutte le fonti si disattivano con `EXT_*_PROVIDER=none`: la dimensione diventa allora
`NOT_CONFIGURED` e lo dichiara. I test non escono mai in rete.
