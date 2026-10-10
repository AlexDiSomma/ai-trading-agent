# ADR 0027 — Stop mobile misurato in ombra, posizioni correlate, salute dell'AI

**Stato:** accettata · **Data:** 2026-10-07 · **Origine:** richiesta dell'utente dopo
due giorni di trading paper

## Contesto

L'utente ha visto trade arrivare a +40/+54 € e chiudersi allo stop a −47 €, e ha chiesto
uno stop mobile, "attivabile e disattivabile senza che continui a influire". Prima di
decidere sono stati rigiocati minuto per minuto i 14 trade chiusi (barre M1 da MT5):

| Regola | Totale R (14 trade) |
|---|---|
| attuale | +0,24 |
| pareggio a +1R | +2,24 |
| blocca +0,5R a +1R | +2,96 |
| trailing 1R da +1R | +3,16 |
| trailing 0,5R da +1R | +2,95 (taglia i due vincenti migliori da ~1,9R a ~1,2R) |

Tutto il vantaggio veniva da 3 trade; nessuno degli stop presi è poi tornato al target.
Campione troppo piccolo per accendere una regola, abbastanza per volerla misurare. Dagli
stessi dati: tre perdite di fila erano la stessa scommessa (long indici USA). E il
7 ottobre il credito OpenAI si è esaurito di notte senza che la dashboard lo dicesse.

## Decisione

**Stop mobile** (`paper/trailing.py`), tre regole: pareggio a +1R, blocca +0,5R a +1R,
trailing 1R da +1R.

* **In ombra, sempre:** ogni posizione aperta da ora porta uno stop "ombra" per ogni
  regola; alla chiusura il trade registra l'uscita di ciascuna (`shadow_r`) accanto alla
  propria (`price_r`), in R di prezzo. Confronto sugli stessi trade, senza ipotesi a
  posteriori; le posizioni aperte prima della misura ne restano fuori.
* **Reale, solo se acceso:** un interruttore per conto (Controlli), **spento di default**.
  La regola si fissa all'apertura (`trailing_rule`): spegnerla vale per le nuove posizioni
  e non tocca quelle aperte; i trade con stop mobile hanno motivo d'uscita `TRAIL` e
  statistiche separate (`by_trailing_rule`).
* **Picco solo da barre M1 chiuse.** Aggiornarlo sul tick confronterebbe lo stop nuovo con
  minimi della stessa barra avvenuti *prima* del picco (uscita falsa). Ombre e trade vero
  usano la stessa risoluzione intrabar (`risolvi_barra`) e lo stesso slittamento.
* Lo stop non arretra mai. Il rischio aperto del gate continua a contare 1R pieno anche
  dopo lo spostamento (prudente). Solo PAPER: il gestore LIVE non applica regole mobili.

**Posizioni correlate** (`paper/performance.py`, `GRUPPI`): un trade è correlato se il
conto aveva già in corso la stessa scommessa (gruppo + verso di rischio: indici USA, yen,
dollaro, metalli). Ricostruito dallo storico, vale anche per il passato. Solo misura:
nessun blocco finché i dati non lo giustificano.

**Salute dell'AI** (`ai/health.py`): `ClientMonitorato` avvolge il client di analisi, tesi
e notizie; motivo classificato (credito, limite, chiave, rete) senza testo grezzo; avviso
in testa a ogni pagina, un evento `AI_UNAVAILABLE` / `AI_RESTORED` per passaggio, consumo
giornaliero (chiamate, token) salvato in `var/paper/ai_usage.json`.

**Meno chiamate inutili:** la tesi AI non si chiede se il conto non può aprire comunque
(`PaperExecutor.blocchi_conto`: stesso gate con una richiesta neutra; più freno notizie e
finestra evento dello strumento); l'analisi oraria non gira a mercato chiuso.

## Correzioni emerse dall'analisi

* MFE/MAE non aggiornati sul tick: un trade chiuso dal tick registrava un massimo
  inferiore all'uscita (XAUUSD: +2,07R chiuso, MFE 1,54R). Ora anche tick e chiusure
  manuali; la validazione rifiuta un'uscita oltre le escursioni registrate.
* Barra d'innesco degli ordini STOP/LIMIT non controllata per lo stop (errore
  ottimistico): ora uno stop toccato nella barra d'ingresso chiude, marcato ambiguo.
* Lettura fissa delle ultime 240 barre M1: dopo un fermo di oltre 4 ore stop e target
  andavano persi in silenzio. Ora si legge dal punto dell'ultimo controllo (fino a ~2
  settimane) e un buco nei dati del broker è annotato sulla posizione.

## Alternative scartate

* **Accendere subito una regola:** 3 casi favorevoli su 14 non distinguono un vantaggio
  dalla fortuna, e con target 2R ogni regola che accorcia i vincenti sposta il pareggio.
* **Trailing 0,5R:** nel replay tagliava i due trade migliori.
* **Bloccare subito le posizioni correlate:** il 6 ottobre US100 e US500 aperti insieme
  hanno vinto entrambi; la correlazione amplifica nei due sensi.
