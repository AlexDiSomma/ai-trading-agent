# ADR 0018 — XM è autorevole per ciò che si negozia, non l'unica fonte dell'intelligence

**Stato:** accettata · **Data:** 2026-10-05 · **Origine:** fase 4.5, richiesta
dell'utente · **Modifica:** ADR 0016 (le esclusioni di VIX, DXY e tassi)

## Contesto

Nella fase 4 l'intelligence globale leggeva solo da XM, e per questo escludeva VIX,
indice del dollaro e rendimenti (su XM esistono solo come futures o ETF). L'utente ha
corretto la premessa: XM/MT5 è la fonte autorevole per strumenti negoziabili,
esecuzione, conto, margine e prezzi del broker, ma **non deve essere l'unica fonte
della Global Intelligence**.

## Decisione

1. **Tre categorie di dato, indipendenti dalla fonte** (`DataCategory`):
   `TRADABLE_INSTRUMENT` (fonte autorevole: il broker), `REFERENCE_INSTRUMENT`
   (prezzi di riferimento, anche non negoziabili su XM: VIX, DXY, i cross usati per
   la forza delle valute), `MACRO_SERIES` (rendimenti, calendario, dati macro).
   Lo snapshot globale dichiara per ogni dato categoria e fonte (`sources`).
2. **Cinque interfacce** in `backend/providers/base.py`: `MarketReferenceProvider`,
   `EconomicCalendarProvider`, `RatesProvider`, `MacroDataProvider`, `NewsProvider`.
   Ogni provider riporta il proprio stato (`AVAILABLE`, `FAILED`, `STALE`,
   `NOT_CONFIGURED`), ultimo successo, ultimo errore, data dell'ultimo dato.
3. **Nessun dato finto fuori dai test.** Una famiglia senza fonte è un
   `NotConfigured*` che dichiara lo stato e solleva se interrogato. I parser si
   testano su file reali registrati; le impostazioni di prova disattivano la rete.
4. **Fonti scelte oggi** dopo il confronto in [`EXTERNAL_PROVIDERS.md`](../EXTERNAL_PROVIDERS.md):
   Forex Factory (calendario), CBOE (VIX), Tesoro USA (curva). Tutte gratuite e
   senza chiave; nessuna a pagamento. Macro e news: nessun provider.
5. **DXY ricostruito** dai sei cambi XM con la formula ICE (categoria
   `REFERENCE_INSTRUMENT`, fonte "derivato"), invece di una fonte esterna: verificato
   contro il future XM (−0,23%, coerente con il differenziale dei tassi).
6. **Nessun look-ahead dalle fonti esterne.** Ogni osservazione porta
   `available_at`: il dato giornaliero del giorno *d* vale dalla mezzanotte di New
   York successiva. Il motore usa solo ciò che era pubblico all'istante di analisi.
7. **Un solo punto esce in rete**: `providers/http.py`. Verificato sull'AST.

## Alternative scartate

* **Futures XM al posto degli indici** (`VIX-OCT26`, `USDX-DEC26`): scadono, cambiano
  nome, il prezzo non è l'indice.
* **Fonti a pagamento** (Trading Economics): non necessarie oggi; da riconsiderare
  solo se le gratuite si rivelassero inaffidabili.
* **Scraping** di siti senza feed: escluso per i termini d'uso e la fragilità.

## Conseguenze

* La qualità del contesto globale può ora essere `GOOD` (calendario disponibile e
  completo per le prossime 24 ore). Senza rete il sistema funziona come in fase 4,
  con le dimensioni esterne dichiarate non disponibili.
* Le fonti esterne hanno limiti dichiarati: Forex Factory copre solo la settimana e
  non ha il valore pubblicato; CBOE e Tesoro sono giornalieri.
