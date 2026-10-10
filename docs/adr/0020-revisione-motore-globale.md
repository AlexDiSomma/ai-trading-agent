# ADR 0020 — Forza valutaria a grafo, intermarket con base storica, MIXED da discordanza

**Stato:** accettata · **Data:** 2026-10-05 · **Origine:** fase 4.5, revisione
richiesta dall'utente · **Modifica:** ADR 0017

## 1. Forza delle valute: modello a grafo

**Prima (fase 4).** Per ogni valuta, media dei momentum normalizzati dei cross in
cui compare. Due difetti: ogni valuta veniva valutata su un insieme diverso di
coppie, e la normalizzazione *per cross* faceva pesare di più le coppie poco volatili.

**Ora.** Ogni cross è un'equazione `r(A,B) = x(A) − x(B)`. Per ogni barra si stimano
i rendimenti delle valute con i minimi quadrati su **tutte** le equazioni, con il
vincolo `Σx = 0`: `(L + J)·x = b`, con `L` laplaciano del grafo dei cross. Con il
grafo completo la soluzione è chiusa (`x = b/8`). Valute senza cross restano `None`;
le altre devono formare un grafo connesso. La normalizzazione per la volatilità si fa
poi sulla serie stimata di ogni valuta. Il **residuo** delle equazioni misura la
coerenza dei prezzi (arbitraggio triangolare).

**Verificato.** Test di proprietà: con cross coerenti i rendimenti si ritrovano
esattamente; togliere coppie del dollaro non sposta le stime. Sul conto reale
(2026-10-05 08:59 UTC): stessa classifica del metodo precedente (correlazione 0,994);
togliendo quattro coppie del dollaro il metodo precedente **perde l'USD** (3 cross,
sotto il minimo) e sposta le altre di 0,47 in totale, il grafo di 0,02.

## 2. Intermarket: correlazione corrente contro base storica

**Prima.** Correlazione lunga (120) e breve (20) sovrapposte, stato `CONFIRMING` se
il segno coincideva. "Confermata" diceva più di quanto sapessimo.

**Ora.** Per ogni relazione, su rendimenti logaritmici allineati per orario:
correlazione **corrente** (ultimi 40), **base storica** su tutti i rendimenti
precedenti **senza sovrapposizione**, **dimensioni dei campioni**, **significatività**
della base (t ≥ 2 e |r| ≥ 0,25), **dispersione** e **stabilità** delle correlazioni
mobili nella base, **differenza** corrente − base in errori standard (Fisher).
Stati: `CONSISTENT`, `WEAKENING`, `DIVERGING`, `UNSTABLE`, `NO_BASELINE`,
`INSUFFICIENT_DATA`. Lo stato complessivo `CONFIRMED` diventa `CONSISTENT`.

Esempio reale: AUDJPY ~ S&P 500 su D1, base +0,60 (n=354), corrente −0,12 (n=40),
z −4,7 → `WEAKENING` (non `DIVERGING`: la corrente è troppo vicina a zero).

## 3. MIXED dalla discordanza fra dimensioni

**Prima.** MIXED nasceva dal composito sotto soglia con accordo basso, o da conflitti.

**Ora** si aggiunge una regola che non passa dal composito: se almeno una dimensione
indica **con forza** risk-on (|valore| ≥ 0,5) e almeno una **con forza** risk-off, il
sentiment è MIXED, qualunque sia la media. Le dimensioni in disaccordo sono elencate
nel contesto (`strong_risk_on`, `strong_risk_off`) e nel motivo del regime. I
conflitti fra dimensioni (ora anche VIX contro sentiment, DXY contro forza USD)
continuano a trasformare RISK_ON/RISK_OFF in MIXED.

Esempio reale (2026-10-05): VIX al 15° percentile annuo (risk-on forte) contro valute
cicliche deboli (risk-off forte) → MIXED, con composito −0,28.

## Conseguenze

* `VERSIONE_MOTORE` passa a `global-1.1.0`: i contesti della 1.0.0 non sono
  confrontabili uno a uno.
* Negli scenari sintetici dei test, volatilità e direzione devono essere coerenti
  perché un regime sia RISK_ON o RISK_OFF: prima bastava il composito.
