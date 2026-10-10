# ADR 0015 — L'età di un dato si calcola solo con l'ora del broker

**Stato:** accettata · **Data:** 2026-10-04 · **Origine:** difetto fail-open
trovato rileggendo il codice · **Corregge:** ADR 0010 (misura dello scarto)

## Contesto: il difetto

I timestamp di MT5 — tick e candele — sono in **ora del server del broker
etichettata UTC**. XM oggi è UTC+3 (dedotto: ultimo tick del venerdì etichettato
23:57:59, chiusura forex alle 21:00 UTC in ora legale americana).

Il connettore (`get_tick(require_fresh=True)`) e il rilevatore di stato calcolavano
l'età di un tick come `ora_di_sistema_UTC − etichetta`. A mercato aperto, un tick
appena arrivato risultava vecchio di **−3 ore**; e un tick fermo da **2 ore e 59
minuti** risultava vecchio di −1 minuto, cioè *fresco*. Era un **fail-open**: il
dato vecchio dichiarato valido.

Perché i test non se ne erano accorti: il terminale finto etichettava i dati in
UTC vero (scarto zero), e il test sul conto reale era stato fatto di sabato,
quando tutti i tick erano comunque vecchi di oltre trenta ore.

C'era un secondo difetto, più sottile, nella misura dello scarto (ADR 0010): lo
si deduceva dal tick che si stava valutando. Un tick vecchio esattamente di tre
ore, su un server a UTC+0, sarebbe stato letto come fresco con scarto +3 —
ragionamento circolare.

## Decisione

1. **L'età di un'etichetta in ora broker si calcola in un solo posto**,
   `BrokerClock.age_bounds` (`backend/market_data/broker_time.py`). Restituisce
   `(minima, massima, esatta)`:
   * con scarto affidabile l'età è esatta;
   * senza, l'età vera sta in ±14 ore (l'ampiezza dei fusi orari) attorno a quella
     grezza.
2. **Senza età esatta nessuna quotazione è `VALID`.** Lo stato è `STALE` con
   mercato `UNKNOWN`, e il motivo lo dice.
3. **Il mercato si può ancora dedurre chiuso** senza scarto, usando l'età
   *minima*: se anche nel caso più favorevole il tick ha più di 15 minuti e le
   barre sono ferme, è `CLOSED_INFERRED`. Asimmetria voluta: per dire "vecchio" si
   usa l'estremo prudente in un verso, per dire "fresco" in quello opposto.
4. **Lo scarto si stabilisce solo con almeno due quotazioni concordi**
   (`observe_many`), lette da tutta la whitelist all'avvio e prima di ogni analisi
   se la misura non è recente. `observe` sul singolo tick può solo *confermare*
   uno scarto già stabilito. Una misura vale dodici ore (i cambi d'ora legale sono
   notturni).
5. **Il connettore non giudica la freschezza.** `require_fresh` è stato rimosso:
   restituisce il dato, e la qualità si valuta in `market_data/quote_quality.py`.
   `MarketDataService.get_tick` converte l'etichetta in UTC vero per `Fresh.as_of`;
   se lo scarto non è noto, `as_of` è l'etichetta meno 14 ore, così
   `Fresh.require_fresh` non può dichiarare fresco un dato di età ignota.
6. **Il terminale finto etichetta in ora broker** (`broker_offset_seconds`, default
   tre ore). Con scarto zero il mock nascondeva il difetto.

## Stati espliciti

* `QuoteQualityStatus`: `VALID`, `STALE`, `MISSING`, `MARKET_CLOSED`,
  `INCONSISTENT` — la qualità del dato;
* `MarketStatus`: `OPEN_CONFIRMED`, `CLOSED_INFERRED`, `UNKNOWN` — cosa sappiamo
  del mercato. **Non esiste uno stato "chiuso certo"**: MT5 non espone le sessioni
  (ADR 0011), quindi "chiuso" è sempre una deduzione.

Invarianti verificate dal costruttore di `QuoteQuality` e da test di proprietà:
`CLOSED_INFERRED` solo con `is_inference=True`; `OPEN_CONFIRMED` solo con `VALID`
e mai come inferenza.

## Alternative scartate

* **Leggere l'ora del server da una API.** Non esiste nell'API Python di MT5.
* **Scarto fisso in configurazione (`BROKER_UTC_OFFSET=3`).** Due volte l'anno
  sarebbe sbagliato per un'ora, e l'errore sarebbe di nuovo un fail-open.
* **Un tick solo, ma "molto recente"**: non c'è modo di sapere che è recente senza
  lo scarto. È esattamente il ragionamento circolare da evitare.

## Conseguenze

A mercato chiuso il sistema non conosce lo scarto, e lo dichiara (fonte
`UNAVAILABLE` nell'istantanea). Alla riapertura lo misura da solo alla prima
analisi. Finché non lo misura, **nessuna quotazione è valida e nessun
`TradeIntent` è eseguibile**: è il comportamento voluto.

Aperto: lo scarto non è ancora persistito, quindi dopo un riavvio a mercato
aperto si rimisura (pochi millisecondi); a mercato chiuso non serve.
