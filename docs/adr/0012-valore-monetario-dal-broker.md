# ADR 0012 — Il valore monetario di un movimento si chiede al broker

**Stato:** accettata · **Data:** 2026-10-04 · **Origine:** misura sul conto XM · **Estesa da:** [ADR 0014](0014-valutazione-e-identita-centralizzate.md) — tutti i calcoli monetari in un solo servizio; valore per unità su 1000 lotti

## Contesto

Il dimensionamento di una posizione richiede una sola grandezza: **quanti euro
costa un movimento di prezzo contrario**. Da essa, con la distanza dello stop e il
rischio consentito, si ricava il volume.

La via naturale è `trade_tick_value / trade_tick_size`, con i valori dichiarati
dal broker. Misurando sul conto XM in EUR contro `order_calc_profit` — il
calcolatore del broker — i due numeri **non coincidono**:

| Strumento | profitto broker | derivato da `tick_value` | rapporto |
|-----------|-----------------|--------------------------|----------|
| EURUSD | 88.81 | 88.87 | 0.9993 |
| USDJPY | 56.31 | 56.29 | 1.0004 |
| **GOLD** | **88.87** | **100.00** | **0.8887** |
| **SILVER** | **444.36** | **500.00** | **0.8887** |
| **US100Cash** | **8.89** | **10.00** | **0.8890** |

0.8887 è esattamente 1/EURUSD. Su questo conto `trade_tick_value` è **in euro per
il forex e in dollari per metalli e indici**: non è convertito nella valuta del
conto. Derivarlo da quel campo sovrastimerebbe la dimensione delle posizioni su
metalli e indici del **12,5%** — il limite di rischio dell'utente violato in
silenzio, senza che nessun controllo possa accorgersene, perché tutti i numeri a
valle sarebbero coerenti fra loro e sbagliati insieme.

> **Correzione del 2026-10-04 (consolidamento della foundation).** Il *verso*
> dell'errore descritto qui sopra era sbagliato. Su questo conto in EUR, leggere
> come euro un `tick_value` in dollari **sovrastima la perdita per lotto** (un
> dollaro vale 0.8887 euro), quindi la size risulta **sottostimata** dell'11% —
> GOLD con stop a 10 dollari e budget 100 EUR: 0.10 lotti invece di 0.11 — e il
> nozionale sovrastimato del 12,5%. Il limite dell'utente non sarebbe stato
> violato *su questo conto*; lo sarebbe su un conto in una valuta che vale meno
> del dollaro (JPY: errore di un fattore 157 nella direzione pericolosa). La
> decisione resta identica; cambia la motivazione. Dettagli in
> `docs/FOUNDATION_VALIDATION.md` §5.

## Decisione

Il valore monetario di un movimento si **chiede al broker** con
`order_calc_profit`, e non si deriva.

Questo ha richiesto di correggere una regola che avevamo posto noi. La lista delle
funzioni vietate fino alla fase 11 includeva `order_calc_margin` "per prudenza".
Era un errore di categoria: la famiglia `order_calc_*` sono **calcolatori** — non
creano ordini, non inviano nulla, non modificano niente. Vietarli non aumentava la
sicurezza, costringeva a derivare un numero sbagliato.

La lista corretta distingue tre cose:

* **cambiano lo stato del conto** — `order_send`, `order_modify`,
  `position_close`: vietate fino alla fase 11;
* **sottopongono al server una richiesta d'ordine** — `order_check`: vietata, fa
  parte del percorso di pre-trade della fase 11;
* **calcolano** — `order_calc_profit`, `order_calc_margin`: ammesse, perché
  leggono e non agiscono.

Il test `test_read_only_guarantee.py` verifica entrambe le direzioni: che le
funzioni di scrittura non ci siano, e che `order_calc_profit` **ci sia** — perché
rimuoverlo per eccesso di prudenza farebbe tornare il difetto.

### Sotto-scoperta: il calcolatore non è lineare

Il primo tentativo chiedeva il profitto per un movimento di 1.0 di prezzo. Su
EURUSD ha restituito 47.056 EUR invece di 88.810: il broker converte il profitto
nella valuta del conto usando il prezzo di **chiusura**, e per una coppia che ha
la valuta del conto come base quel prezzo *è* il tasso di conversione. Chiedendo
il profitto da 1.125 a 2.125, la conversione avviene a 2.125.

Il movimento di prova è quindi **cento tick**, e il risultato si divide per il
delta: abbastanza grande da non perdersi negli arrotondamenti del broker (che
restituisce due decimali), abbastanza piccolo da non spostare il cambio.

## Conseguenze

Positive: il numero su cui poggia tutto il dimensionamento viene dalla stessa
fonte che poi calcolerà il profitto reale. Nessuna conversione valutaria scritta
da noi, nessuna tabella di cambi da mantenere.

Negative: una chiamata MT5 in più per strumento, e il valore va riletto quando il
cambio si muove (è in cache con la specifica, un'ora). Se il broker non lo
fornisce, `value_per_price_unit` resta `None` e lo strumento **non è
dimensionabile**: il Risk Engine rifiuterà, invece di approssimare. È il
comportamento voluto.
