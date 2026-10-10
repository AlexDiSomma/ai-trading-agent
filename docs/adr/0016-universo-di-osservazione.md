# ADR 0016 — Un universo di sola osservazione, distinto dalla whitelist operativa

**Stato:** accettata · **Data:** 2026-10-04 · **Origine:** fase 4, misure sul conto XM · **Modificata da:** [ADR 0018](0018-fonti-oltre-il-broker.md) (VIX, tassi e DXY ora da fonti esterne o ricostruiti) ·
**Precisa:** la regola di `market_data/reader.py` ("se un simbolo non è autorizzato
non deve nemmeno comparire in un'analisi")

## Contesto

La whitelist (`RISK_ALLOWED_SYMBOLS`) è il perimetro su cui il sistema può
**operare**, e finora valeva anche per la lettura: uno strumento non autorizzato non
compariva in nessuna analisi. Per rispondere a "cosa sta succedendo sui mercati?"
servono però strumenti che l'utente non ha chiesto di operare: i 28 cross per
separare la forza di ogni valuta, gli indici europei e asiatici, il petrolio.
Aggiungerli alla whitelist sarebbe sbagliato: renderebbe operabili strumenti che
l'utente non ha scelto.

Misurato sul conto il 2026-10-04 (1639 simboli):

* **i 28 cross fra USD, EUR, GBP, JPY, CHF, AUD, CAD, NZD esistono tutti** con il
  nome standard;
* **VIX e indice del dollaro esistono solo come futures con scadenza**
  (`VIX-OCT26`, `USDX-DEC26`): il nome cambia a ogni rollover e la serie salta;
* **le obbligazioni esistono solo come CFD su ETF** (`TLT`, `IEF`, `HYG`), con
  storico D1 da gennaio 2026 (189 barre) e quotazione solo in orario americano;
* esistono indici tematici proprietari XM (`USD_FX_Index`, `EUR_FX_Index`), con
  metodologia non documentata.

## Decisione

1. **Due perimetri.** La whitelist resta il perimetro operativo e non cambia.
   L'**universo di riferimento** (`config/global_universe.py`, 37 strumenti) è di
   sola osservazione: se ne leggono candele, tick e specifiche; non entra mai in
   un `TradeIntent`, in un calcolo di valutazione o nella whitelist. Un test
   sull'AST verifica che `global_market/`, `intermarket/`, `macro/` e
   `calendar/` non importino `valuation`, `trade_planner`, `risk`, `execution`
   né `ai`.
2. **Le grafie XM stanno solo nella configurazione dell'universo.** All'avvio ogni
   simbolo viene riletto e il suo canonico (`InstrumentRegistry`) confrontato con
   quello dichiarato: se diverge, lo strumento è `MISSING` con il motivo, mai usato
   al posto di un altro. Verificato dal vivo: tutti i 37 coincidono.
3. **Esclusi per ora:** VIX e indice del dollaro (serve una gestione del rollover
   dei futures), obbligazioni (storico troppo corto), indici tematici XM
   (metodologia ignota). La volatilità si misura dagli strumenti stessi; la forza
   del dollaro dai cross.

## Alternative scartate

* **Allargare la whitelist.** Confonde "posso guardarlo" con "posso operarlo".
* **Usare `VIX-OCT26` così com'è.** Scade a ottobre: il giorno dopo il simbolo non
  esiste più e lo storico del contratto successivo non è continuo.
* **Usare `USD_FX_Index` di XM al posto della forza calcolata.** Non sappiamo come
  è costruito; può servire, in futuro, solo come confronto.

## Conseguenze

* MT5 richiede che un simbolo sia nel Market Watch per leggerne i dati: i 37
  strumenti vengono aggiunti alla lista del terminale. Effetto visibile
  nell'interfaccia di MT5, nessun effetto sul conto.
* Il terminale finto contiene ora anche l'universo di riferimento (37 simboli),
  con gli stessi nomi di XM.
* Aperto: gestione dei futures con rollover (VIX, USDX), da valutare quando servirà
  una misura di volatilità implicita.
