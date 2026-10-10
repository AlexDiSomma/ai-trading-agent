# Global Market Intelligence (fasi 4 e 4.5)

**Data:** 2026-10-04/05 · **Motore:** `global-1.1.0` · **Stato:** fase 4 e fase 4.5
(External Context Foundation) complete; fase AI operativa **non iniziata**.

Il sistema risponde a **"cosa sta succedendo complessivamente sui mercati?"** prima
di guardare il singolo strumento. In modo quantitativo, deterministico,
riproducibile, senza LLM, senza ordini e senza `TradeIntent`.

```
Market Data → Global Regime → Currency Strength → Intermarket → Volatility
            → Relative Strength → GlobalMarketContext → Asset Analysis
```

Decisioni strutturali: [ADR 0016](adr/0016-universo-di-osservazione.md) (universo di
sola osservazione), [ADR 0017](adr/0017-contesto-globale-quantitativo.md) (motore
quantitativo a dimensioni separate), [ADR 0018](adr/0018-fonti-oltre-il-broker.md)
(XM non è l'unica fonte), [ADR 0019](adr/0019-politica-di-persistenza.md) (persistenza),
[ADR 0020](adr/0020-revisione-motore-globale.md) (grafo valutario, intermarket con base
storica, MIXED da discordanza). Fonti esterne confrontate in
[`EXTERNAL_PROVIDERS.md`](EXTERNAL_PROVIDERS.md).

## 0. Architettura aggiornata (fase 4.5)

```
                    ┌──────────────── fonti ────────────────┐
  XM / MT5          │  CBOE            Tesoro USA     Forex Factory │
  (autorevole per   │  (VIX)           (curva tassi)  (calendario)  │
   ciò che si       │  MarketReference RatesProvider  EconomicCal.  │
   negozia)         │  Provider                       Provider      │
     │              │  MacroDataProvider: non configurato (FRED)    │
     │              │  NewsProvider:      non configurato (rinviato)│
     │              └──────────────┬─────────────────────────────────┘
     ▼                             ▼  stato di ogni fonte, available_at
 GlobalDataReader ──► 38 strumenti   DatiEsterni
 (REFERENCE_INSTRUMENT)              (REFERENCE_INSTRUMENT / MACRO_SERIES)
     │                             │
     └──────────────┬──────────────┘
                    ▼   taglio all'istante di analisi (barre e osservazioni)
          global_market/engine.py  (puro, deterministico, impronta sha256)
   ┌────────┬────────────┬────────────┬──────────┬────────┬────────┬─────────┐
   ▼        ▼            ▼            ▼          ▼        ▼        ▼         ▼
 forza   intermarket  volatilità   VIX        tassi    DXY     calendario  azioni /
 valute  (corrente vs (realizzata) (implicita) (curva)  (ICE    e rischio   metalli
 (grafo)  base, z)                                      da XM)  eventi
   └────────┴────────────┴─────┬──────┴──────────┴────────┴────────┴─────────┘
                               ▼
        evidenze di rischio ► voti forti ► conflitti ► regime
        (MIXED se dimensioni forti discordi, qualunque sia il composito)
                               ▼
               GlobalMarketContext ──► AssetGlobalBackdrop per strumento
                                       (forza delle sue valute, finestre di
                                        evento sulle sue valute)
```

Categorie di dato (`DataCategory`): `TRADABLE_INSTRUMENT` (fonte: broker),
`REFERENCE_INSTRUMENT` (prezzi di riferimento, anche non negoziabili su XM),
`MACRO_SERIES` (tassi, calendario, macro). Lo snapshot elenca ogni dato con categoria,
fonte e stato (`sources`), e lo stato di ogni provider (`providers`).

---

## 0-bis. Correzioni prima della fase 5A (ADR 0021)

- **Qualità per dominio** (`source_quality`): `BROKER_DATA`, `MARKET_REFERENCE`,
  `CALENDAR`, `MACRO`, `NEWS`, più la complessiva con regole esplicite; ciò che non è
  configurato finisce in `known_limitations`.
- **Metadati delle fonti**: frequenza, ultima osservazione, ritardo di pubblicazione,
  uso previsto. VIX e curva USA sono `STRUCTURAL_CONTEXT` giornaliero: solo D1.
- **Valute**: il grafo (pesato 1/varianza, cross fermi esclusi, outlier rimossi) è la
  misura; il DXY ricostruito è la verifica `DXY_RICOSTRUITO_VS_USD_GRAFO`.
- **Eventi**: `event_risk.events` con fase, finestra, minuti all'evento, actual
  (solo se già visto all'`as_of`) e sorpresa.

L'AI Market Analyst (fase 5A) legge questo contesto: [ADR 0022](adr/0022-ai-market-analyst-read-only.md).

## 1. Cosa produce

| Oggetto | Cosa dice | Dove |
|---|---|---|
| `GlobalMarketSnapshot` | quali dati sono entrati: 37 strumenti × 4 timeframe, stato di ogni serie, impronta sha256 | `schemas/global_market.py` |
| `GlobalMarketContext` | la lettura: regime e dimensioni separate, conflitti, qualità, sintesi | `schemas/global_market.py` |
| `AssetGlobalBackdrop` | il contesto visto da un singolo strumento (forza delle sue valute, direzione del gruppo) | dentro ogni `AssetMarketContext` dell'istantanea |

Entrambi persistibili (tabella `global_market_contexts`, migrazione 0003) e
ricostruibili dal proprio JSON.

### Le dimensioni, tenute separate

| Dimensione | Stato | Come si calcola |
|---|---|---|
| **Regime globale** | `RISK_ON`, `RISK_OFF`, `MIXED`, `TRANSITION`, `EVENT_DRIVEN`, `UNCERTAIN` | regole in ordine di precedenza (§3) |
| **Sentiment di rischio** | stesso insieme meno `EVENT_DRIVEN` | cinque evidenze pesate, ognuna visibile |
| **Volatilità** | livello `LOW…EXTREME`, tendenza `EXPANDING/STABLE/COMPRESSING`, shock | percentile ATR, ampiezza Bollinger, volatilità realizzata breve/lunga |
| **Forza delle valute** | `STRONG/NEUTRAL/WEAK/MIXED/UNKNOWN` per USD, EUR, GBP, JPY, CHF, AUD, CAD, NZD | da tutti i 28 cross |
| **Forza relativa** | classifica di valute, metalli, indici, petrolio | momentum normalizzato |
| **Azioni** | direzione con ampiezza | sei indici: US500, US100, US30, GER40, UK100, JP225 |
| **Metalli** | direzione con ampiezza | XAUUSD (GOLD), XAGUSD (SILVER) |
| **Intermarket** | `CONFIRMED/DIVERGENT/MIXED/INSUFFICIENT`, e per relazione `CONFIRMING/DIVERGING/UNSTABLE/NO_RELATIONSHIP/INSUFFICIENT` | correlazione mobile lunga e breve, stabilità |
| **Rischio macro/eventi** | da calendario (`UNKNOWN` oggi) e implicito nei prezzi `LOW/MODERATE/HIGH` | §5 |
| **Conflitti** | elenco con gravità `LOW/MEDIUM/HIGH` | §4 |
| **Qualità** | `GOOD/DEGRADED/INSUFFICIENT` per dimensione e complessiva | §6 |
| **Lettura per strumento** | momentum D1/H4/H1/M15, composito, etichetta, coerenza fra timeframe | tutti i 37 strumenti |

Un caso come *"Risk MIXED, USD STRONG, Gold STRONG, Equities WEAK, Volatility
EXPANDING, Macro uncertainty HIGH"* è riprodotto da un test
(`test_l_esempio_dell_utente_e_rappresentabile`).

---

## 2. Universo e metodo

**Universo di riferimento** (`config/global_universe.py`, ADR 0016): 28 cross, GOLD,
SILVER, US500Cash, US100Cash, US30Cash, GER40Cash, UK100Cash, JP225Cash, OILCash.
Sola osservazione: non è la whitelist operativa. Esclusi VIX e indice del dollaro
(su XM solo futures con scadenza) e obbligazioni (solo ETF con 189 barre D1).

**Unità comune — momentum normalizzato.** Per ogni serie:
`z = ln(C_t / C_{t−20}) / (σ_50 · √20)`, con σ_50 la deviazione standard dei
rendimenti logaritmici sulle ultime 50 barre. Troncato a ±10. Composito sui
timeframe con pesi D1 .4, H4 .3, H1 .2, M15 .1 (almeno due timeframe).

**Etichetta STRONG/WEAK/NEUTRAL** con soglia ±0.5; **MIXED** se il blocco lento
(D1, H4) e quello veloce (H1, M15) superano entrambi la soglia in versi opposti.
(Prima versione: bastava un timeframe di segno opposto. Sui dati sintetici quasi
ogni strumento risultava MIXED, perché il rumore di un singolo timeframe è
dell'ordine di 1: corretto prima dei test.)

**Forza delle valute — modello a grafo (fase 4.5, ADR 0020).** Ogni cross è
un'equazione `r(A,B) = x(A) − x(B)`; per ogni barra si stimano i rendimenti delle 8
valute con i minimi quadrati su tutte le equazioni disponibili, con `Σx = 0`
(`(L + J)·x = b`; con i 28 cross la soluzione è `x = b/8`). Poi, per valuta, rendimento
su 20 barre diviso per la sua volatilità. Valute senza cross: `UNKNOWN`; il residuo
delle equazioni misura la coerenza dei prezzi. *Prima (fase 4)*: media dei momentum
dei cross, sostituita perché sensibile al numero di coppie disponibili (verificato
sul conto reale: §7-bis).

**Gruppi (azioni, metalli).** Media dei membri per timeframe; ampiezza = quota dei
membri concordi. Composito forte ma ampiezza sotto il 66% (con ≥3 membri) → `MIXED`.

**Volatilità.** Per strumento e timeframe: percentile dell'ATR(14) su 250 barre,
percentile dell'ampiezza di Bollinger su 120, rapporto σ_10/σ_50 (>1.20 espansione,
<0.85 compressione). Aggregato con la **mediana** (un solo strumento impazzito non
dichiara la tempesta). Livello dal percentile mediano H4/H1: ≤0.25 LOW, ≥0.75
ELEVATED, ≥0.95 EXTREME. **Shock**: ≥40% degli strumenti con ATR M15 o H1 oltre il
97° percentile.

**Intermarket (rivisto in fase 4.5, ADR 0020).** Nove relazioni su H1 e D1, con
rendimenti logaritmici **allineati per orario**. Per ognuna: correlazione corrente
(ultimi 40), base storica (tutti i precedenti, senza sovrapposizione), dimensione dei
due campioni, significatività della base (t ≥ 2 e |r| ≥ 0,25), dispersione e
stabilità delle correlazioni mobili, differenza in errori standard (Fisher). Stati:
`CONSISTENT` (compatibile con la storia — **non** "confermata"), `WEAKENING`,
`DIVERGING`, `UNSTABLE`, `NO_BASELINE`, `INSUFFICIENT_DATA`.

**VIX, tassi, DXY (fase 4.5).** VIX da CBOE: percentile sull'ultimo anno, tendenza
rispetto alla mediana di 20 sedute, evidenza di rischio (peso 0,75). Curva USA dal
Tesoro: livelli, variazione a 20 sedute in punti base, pendenze 2s10s e 3m10y.
DXY ricostruito con la formula ICE dai sei cambi XM, confrontato con la forza USD
del grafo (correlazione e accordo).

---

## 3. Regime globale

**Evidenze di rischio** (segno + = risk-on):

| Evidenza | Peso | Nota |
|---|---|---|
| direzione degli indici azionari | 1.00 | |
| forza dello yen (invertita) | 0.75 | rifugio |
| forza di AUD e NZD | 0.75 | valute cicliche |
| forza del franco (invertita) | 0.50 | rifugio |
| percentile ATR degli indici (invertito) | 0.50 | |

| VIX, percentile annuo (invertito) — fase 4.5 | 0.75 | solo D1, se disponibile |

L'oro non è un'evidenza di rischio: sale con la paura, ma anche con il dollaro
debole. Ha la sua dimensione.

**Discordanza (fase 4.5).** Se almeno un'evidenza è oltre +0,5 e almeno una oltre
−0,5, il sentiment è **MIXED** qualunque sia il composito, e il motivo elenca le
dimensioni in disaccordo.

**Precedenza:** `UNCERTAIN` (qualità insufficiente o meno di tre evidenze) →
`EVENT_DRIVEN` (shock, oppure finestra di evento ad alto impatto attiva su USD, su
due o più valute G8, o su tutte) → `UNCERTAIN` (segnali deboli e concordi) →
`TRANSITION` (lento e veloce oltre ±0.35 in versi opposti) → `RISK_ON`/`RISK_OFF`
(oltre ±0.35 e accordo pesato ≥70%) → `MIXED`. Un conflitto HIGH o due MEDIUM
trasformano RISK_ON/RISK_OFF in `MIXED`.

## 4. Conflitti

| Codice | Gravità | Quando |
|---|---|---|
| `INDIZI_RISCHIO_DISCORDI` | MEDIUM | accordo pesato < 60% con ≥3 evidenze |
| `AZIONI_E_RIFUGI_FORTI` | MEDIUM | azioni STRONG con JPY o CHF STRONG |
| `AZIONI_DEBOLI_YEN_DEBOLE` | MEDIUM | azioni WEAK senza domanda di yen |
| `ORO_E_DOLLARO_FORTI` | MEDIUM | oro e USD STRONG insieme |
| `AZIONI_TIMEFRAME_OPPOSTI` | MEDIUM | indici lento/veloce in versi opposti |
| `RIALZO_CON_VOLATILITA_CRESCENTE` | MEDIUM | azioni STRONG con volatilità azionaria in espansione |
| `INTERMARKET_DIVERGENTE` | HIGH | più relazioni divergenti che confermate |
| `RISCHIO_CONTRO_AZIONI` | HIGH | sentiment RISK_ON con azioni WEAK, o il contrario |
| `INTERMARKET_PARZIALE` | LOW | qualche divergenza |
| `DOLLARO_TIMEFRAME_OPPOSTI` | LOW | USD MIXED |

## 5. Rischio macro ed eventi

**Calendario (fase 4.5)**: Forex Factory, settimana corrente, senza valore
pubblicato. Finestre di blocco con i limiti dell'utente (60 minuti prima, 30 dopo gli
eventi HIGH/CRITICAL). Livello: `HIGH` con una finestra attiva, `MODERATE` con un
evento ad alto impatto nelle prossime 24 ore, `LOW` con le 24 ore interamente coperte
e nessun evento, `UNKNOWN` se il calendario manca o la sua copertura finisce prima.
L'occupazione canadese apre una finestra su CAD (visibile nello sfondo di USDCAD),
non rende `EVENT_DRIVEN` il mondo.

**Incertezza implicita nei prezzi**: volatilità ELEVATED +1 / EXTREME +2, in
espansione +1, shock +2, intermarket divergente o instabile +1, un conflitto HIGH o
due MEDIUM +1. 0 → LOW, 1-2 → MODERATE, ≥3 → HIGH, volatilità ignota → UNKNOWN.

## 6. Qualità dei dati e fail closed

| Stato della serie | Usata nei calcoli | Effetto |
|---|---|---|
| `FRESH` | sì | — |
| `UNVERIFIED` (ora broker ignota) | sì | impedisce GOOD |
| `CLOSED` (mercato dedotto chiuso) | sì, come ultima sessione | impedisce GOOD |
| `STALE` (barre ferme a mercato non chiuso) | **no** | riduce la copertura |
| `INSUFFICIENT_HISTORY` (< 60 barre) | **no** | riduce la copertura |
| `MISSING` | **no** | riduce la copertura, avviso |

Dimensione `INSUFFICIENT` sotto il 50% di copertura; `GOOD` solo con ≥90% e tutte
fresche. Fase diversa da `OPEN_CONFIRMED` o dati simulati → al massimo `DEGRADED`.
Le dimensioni centrali (rischio, valute, volatilità) decidono la qualità
complessiva; le altre al massimo la degradano. `usable_for_decisions` = dati reali
∧ `OPEN_CONFIRMED` ∧ qualità ≠ INSUFFICIENT ∧ regime ≠ UNCERTAIN.

**Freschezza**: una serie è fresca se la barra **in formazione** si è aperta da
meno di una durata di barra (+2 minuti), con età verificata dall'ora broker. Non
l'ultima barra *chiusa*: al lunedì l'ultima H4 chiusa è del venerdì.

**Nessun look-ahead**: ogni serie è tagliata all'istante di analisi in ora broker
(restano le barre la cui *fine* non lo supera). **Riproducibilità**: impronta
sha256 delle barre + versione del motore; stesso ingresso → stesso JSON.

---

## 7-bis. Report reale dopo la fase 4.5 (2026-10-05, 08:59 UTC, mercati aperti)

`python -m backend.report_globale`, motore `global-1.1.0`. JSON completo in
[`validazione/globale_2026-10-05_1059.json`](validazione/globale_2026-10-05_1059.json).

> **Mercato: OPEN_CONFIRMED**, 38 strumenti su 38. Ora broker misurata (+10800 s).
> Tutte le fonti esterne `AVAILABLE`. **Qualità GOOD** (prima volta: c'è il
> calendario). **Utilizzabile per decidere: sì.**

| | |
|---|---|
| **Regime** | **MIXED** — dimensioni in disaccordo: risk-on da VIX (15° percentile annuo), risk-off da valute cicliche (AUD/NZD −0,83); composito −0,28, accordo 59% |
| Volatilità | ELEVATED (ATR), COMPRESSING; VIX 15,31 LOW/STABLE |
| Valute (grafo) | USD +0,99 STRONG · GBP +0,78 STRONG · CHF +0,49 · JPY +0,31 · AUD −0,26 MIXED · CAD −0,35 · EUR −1,14 WEAK · NZD −1,41 WEAK; residuo H1 1,3e-5 |
| DXY ricostruito | 102,16 STRONG (+1,27), correlazione con USD del grafo 0,95; future XM 101,93 (−0,23%) |
| Tassi USA (2/10) | 3M 4,19 · 2Y 4,83 · 10Y 5,28 · 2s10s +45 pb · decennale RISING (+51 pb in 20 sedute) |
| Azioni / metalli / oro | NEUTRAL / NEUTRAL / WEAK |
| Intermarket | MIXED: 11 coerenti, 2 indebolite (US100~US30 D1 z −2,5; AUDJPY~S&P D1 z −4,7), 0 divergenti |
| Calendario | MODERATE: BoJ Ueda martedì 06:35 UTC; prossimi HIGH: USD verbali FOMC mer 18:00, CAD occupazione ven 12:30 |
| Incertezza implicita | MODERATE (volatilità elevata) |
| Conflitti | INDIZI_RISCHIO_DISCORDI (MEDIUM) |

**Confronto forza valutaria (stessi dati):** classifica identica fra media dei cross
e grafo (correlazione 0,994). Senza USDCHF, USDCAD, NZDUSD, AUDUSD: la media non
calcola più l'USD e sposta le altre valute di 0,47 in totale; il grafo di 0,02.
Dati in `validazione/confronto_forza_valute_2026-10-05.json`.

## 7. Report reale sul conto XM (fase 4, motore 1.0.0)

`python -m backend.report_globale` — eseguito il **2026-10-04 alle 22:28 UTC**
(lunedì 01:28 ora broker), **durante la riapertura domenicale**. JSON completo in
[`validazione/globale_2026-10-05_0028.json`](validazione/globale_2026-10-05_0028.json).

> **Mercato: PARTIALLY_OPEN.** 28 strumenti su 37 con quotazione fresca
> confermata, 0 dedotti chiusi, 9 senza un tick negli ultimi 15 secondi
> (liquidità sottile della riapertura). **Il contesto non è utilizzabile per
> decidere**, e lo dichiara. Ora broker misurata: +10800 s, due quotazioni concordi.

| | |
|---|---|
| **Regime globale** | `UNCERTAIN` — composito −0.23, accordo 71%: segnali troppo deboli per una direzione |
| Sentiment per timeframe | D1 −0.47 · H4 −0.35 · H1 +0.21 · M15 +0.21 |
| **Volatilità** | ELEVATED, STABLE, nessuno shock; percentile ATR mediano D1 0.45 · H4 0.97 · H1 0.85 · M15 0.50 |
| **Valute** (forte → debole) | USD +0.71 STRONG · CHF +0.59 STRONG · GBP +0.55 STRONG · JPY +0.23 · AUD −0.18 MIXED · CAD −0.56 WEAK · NZD −0.62 WEAK · EUR −0.72 WEAK (7/7 cross ciascuna) |
| Azioni | NEUTRAL (+0.14), ampiezza 50%: JP225 +1.27, US100 +0.72, US500 +0.31 contro UK100 −0.74, US30 −0.41, GER40 −0.27 |
| Metalli | NEUTRAL (−0.38), ampiezza 100% |
| Intermarket | CONFIRMED: 13 confermate, 0 divergenti, 0 instabili |
| Conflitti | nessuno |
| Incertezza macro | MODERATE (implicita: volatilità elevata) · calendario non disponibile |
| **Qualità** | DEGRADED (fase PARTIALLY_OPEN; calendario assente) |

**Strumenti** (momentum normalizzato D1 / H4 / H1 / M15 → composito):

| Strumento | Mercato | D1 | H4 | H1 | M15 | Composito | Etichetta | Percentile ATR H1 | Vol. |
|---|---|---|---|---|---|---|---|---|---|
| GOLD (XAUUSD) | OPEN_CONFIRMED | −0.96 | −0.10 | −0.24 | +0.19 | −0.45 | NEUTRAL | 0.96 | EXPANDING |
| EURUSD | OPEN_CONFIRMED | −2.36 | −1.34 | +0.18 | +0.05 | −1.30 | WEAK | 0.90 | STABLE |
| USDJPY | UNKNOWN (tick > 15 s) | +0.36 | +0.38 | −0.40 | −0.22 | +0.16 | NEUTRAL | 0.56 | EXPANDING |
| US100 | OPEN_CONFIRMED | +0.83 | +0.82 | +0.68 | +0.05 | +0.72 | STRONG | 0.78 | STABLE |
| US30 | OPEN_CONFIRMED | −1.13 | −0.44 | +0.54 | +0.63 | −0.41 | MIXED | 0.87 | EXPANDING |
| SILVER (XAGUSD) | OPEN_CONFIRMED | −0.86 | −0.15 | −0.15 | +0.94 | −0.32 | NEUTRAL | 0.95 | EXPANDING |

Sfondo per strumento: EURUSD — EUR WEAK contro USD STRONG, differenziale −1.42;
USDJPY — USD STRONG contro JPY NEUTRAL, differenziale +0.48.

**Correlazioni intermarket** (lunga 120 / breve 20 / stabilità):

| Relazione | H1 | D1 |
|---|---|---|
| US500 ~ GER40 | +0.69 / +0.67 / 1.00 CONFIRMING | +0.69 / +0.68 / 1.00 CONFIRMING |
| US500 ~ JP225 | +0.64 / +0.60 / 1.00 CONFIRMING | +0.75 / +0.68 / 1.00 CONFIRMING |
| US100 ~ US30 | +0.62 / +0.63 / 1.00 CONFIRMING | +0.59 / +0.68 / 1.00 CONFIRMING |
| XAUUSD ~ XAGUSD | +0.88 / +0.91 / 1.00 CONFIRMING | +0.87 / +0.93 / 1.00 CONFIRMING |
| XAUUSD ~ EURUSD | +0.34 / +0.43 / 0.87 CONFIRMING | +0.58 / +0.21 / 1.00 CONFIRMING |
| XAUUSD ~ US500 | +0.49 / +0.70 / 0.91 CONFIRMING | +0.50 / +0.50 / 1.00 CONFIRMING |
| AUDJPY ~ US500 | +0.12 / +0.07 / 0.70 NO_RELATIONSHIP | +0.25 / +0.14 / 0.60 NO_RELATIONSHIP |
| USDJPY ~ US500 | −0.28 / −0.80 / 0.76 NO_RELATIONSHIP | −0.37 / −0.35 / 0.90 CONFIRMING |
| USDCAD ~ OIL | +0.12 / +0.26 / 0.72 NO_RELATIONSHIP | −0.10 / −0.23 / 0.65 NO_RELATIONSHIP |

Lettura: oro e argento quasi la stessa cosa (+0.88); oro **positivamente** legato
all'S&P 500 (non un rifugio contro le azioni, in questo periodo); due relazioni "da
manuale" — AUDJPY come proxy del rischio, USDCAD inverso al petrolio — **oggi non
esistono nei dati**. È esattamente il motivo per cui il segno non è scritto nel
codice.

Un report precedente alle 21:32 UTC, con metalli e indici ancora chiusi, è in
`validazione/globale_2026-10-04_2356.json` (14 strumenti `CLOSED_INFERRED`, stesso
regime).

---

## 8. Come si usa

```bash
python -m backend.report_globale          # report read-only sul conto configurato
curl /api/global/context                  # contesto completo (cache 60 s; ?force=true)
curl /api/global/snapshot                 # stato dei dati in ingresso
curl /api/global/summary                  # sintesi leggibile
curl /api/analysis/snapshot               # istantanea: globale prima, poi gli strumenti
set RUN_LIVE_MT5=1 && pytest backend/tests/live   # test contro MT5 reale
```

## 9. Test

| Richiesta | Test |
|---|---|
| correlation range | `test_correlazione.py` (hypothesis, 300 casi), intermarket nel contesto |
| no NaN leakage | `test_nessun_nan_anche_con_ingressi_sporchi`, `test_nessun_nan_e_correlazioni_nell_intervallo` (hypothesis sull'intero motore) |
| stale data | `test_dati_fermi_esclusi…`, `test_tutto_fermo_fail_closed`, `test_barre_ferme_a_mercato_non_chiuso_sono_stale` |
| missing symbols | `test_simboli_mancanti_dichiarati`, `test_quasi_tutto_mancante_insufficiente`, `test_simbolo_inesistente_dichiarato`, `test_identita_diversa_esclusa_non_sostituita` |
| insufficient history | `test_storia_insufficiente`, `test_storia_corta_per_i_percentili`, `test_troppi_pochi_cross_nessun_punteggio` |
| deterministic output | `TestDeterminismo` (stesso JSON, ordine irrilevante, ricostruibile), API a orologio fermo, live sui dati reali |
| conflicting evidence | `test_evidenze_discordi_danno_mixed`, `test_l_esempio_dell_utente_e_rappresentabile`, `test_conflitto_grave_trasforma_risk_off_in_mixed` |
| no future leakage | `test_barre_future_non_cambiano_il_contesto`, `test_il_taglio_esclude_la_barra_non_ancora_chiusa`, `test_spostare_l_istante_indietro…` |

Più: regimi (RISK_ON, RISK_OFF, UNCERTAIN, TRANSITION, EVENT_DRIVEN), mercato
chiuso, ora broker ignota, dati simulati, persistenza col database spento, garanzia
sull'AST che l'intelligence globale non importi AI, rischio, valutazione, trade
planner o esecuzione.

## 10. Limiti e problemi aperti

0. *(Fase 4.5)* Forex Factory non pubblica il valore effettivo degli eventi: la
   sorpresa non è misurabile. Macro e news senza provider. Rendimenti solo USA.
1. **Soglie euristiche non validate** (`global_market/parametri.py`): pesi,
   ±0.5, ±0.35, 70%, 97° percentile, 40%. Vanno misurate in walk-forward (fase 9).
2. ~~Nessun calendario economico~~ — risolto in fase 4.5 (Forex Factory).
3. ~~Nessuna volatilità implicita~~ — risolto in fase 4.5 (CBOE).
4. **La classifica fra gruppi è indicativa**: valute contro il paniere, metalli e
   indici contro la propria valuta.
5. **Alla riapertura domenicale** il mercato resta `PARTIALLY_OPEN` per un tempo
   non ancora misurato: molti strumenti non stampano un tick ogni 15 secondi.
6. **Migrazione 0003** verificata in SQL offline, non applicata a un database vero
   (PostgreSQL non attivo in questa sessione).
7. **Il terminale finto** genera serie indipendenti: le relazioni intermarket si
   testano con serie costruite apposta, non con il finto.
