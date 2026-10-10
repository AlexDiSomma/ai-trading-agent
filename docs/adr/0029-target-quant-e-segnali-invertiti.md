# ADR 0029 — Target di QUANT regolabile e segnali invertiti (esperimenti dell'utente)

**Stato:** accettata · **Data:** 2026-10-09 · **Origine:** richieste dell'utente dopo il
backtest (ADR 0028)

## Contesto

Visto che QUANT perde sul passato, l'utente ha chiesto due esperimenti nel paper, entrambi
attivabili e disattivabili: (1) "fare l'opposto" dei segnali; (2) incassare prima, con il
target a 1,3R invece di 2R, lasciando QUANT attivo.

## Decisione

* **Segnali invertiti** (`paper/planner.inverti`, interruttore per conto, spento di
  default): verso opposto, stop e target specchiati rispetto all'ingresso (stesso RR),
  LIMIT↔STOP, invalidazioni tolte; versione `|inverso`.
* **Target di QUANT** (`paper/planner.con_target`, comando `quant_target_r`, scelte 2R
  standard / 1,5R / 1,3R, limiti 1-3R): target a N volte la distanza dello stop; versione
  `|tp1.3`. Per il **solo conto QUANT** il gate accetta un RR minimo pari al target scelto
  (`PaperExecutor._limiti_conto`: mai più alto del limite generale). Il limite generale
  (1,8) non cambia, quindi l'AI — che lo riceve anche nel prompt — non ne è toccata.
* Entrambi valgono per le nuove aperture, statistiche separate per versione, e sono anche
  varianti del backtest. Su richiesta esplicita: target QUANT impostato a 1,3R il 9 ottobre.

## Backtest (16 mesi, 10 strumenti, stesso metodo dell'ADR 0028)

| Variante | Trade | Win | Exp. R | P>0 |
|---|---|---|---|---|
| base (2R) | 1729 | 36% | −0,049 | 6% |
| target 1,3R | 1995 | 44% | −0,028 | 10% |
| target 1,3R + stop 2,5 ATR, 48 h | 1062 | 44% | +0,004 | 55% |
| invertito | 1746 | 34% | −0,086 | 0% |
| invertito + stop 2,5 ATR, 48 h | 820 | 37% | −0,104 | 0% |

L'inversione peggiora: la regola perde soprattutto per i costi, che si pagano in entrambi i
versi. Il target a 1,3R riduce la perdita ma non la annulla. Nessuna delle due rende QUANT
positiva; restano esperimenti, non correzioni.

## Aggiunta (2026-10-09): pausa dopo uno stop

Osservato nel paper: US100 rientrato nello stesso minuto dopo uno stop, e fermato di
nuovo dopo 17 minuti. Variante del backtest: niente aperture sullo stesso strumento per
N ore dopo uno stop in perdita (noto solo a chiusura avvenuta). Solo misura: nessuna
modifica al paper.

| Variante | Trade | Win | Exp. R | 1ª metà | 2ª metà | P>0 |
|---|---|---|---|---|---|---|
| base (2R) | 1729 | 36% | −0,049 | +0,030 | −0,125 | 6% |
| 2R + pausa 4 h | 1626 | 37% | −0,025 | +0,051 | −0,100 | 21% |
| 2R + pausa 24 h | 1440 | 37% | −0,010 | +0,050 | −0,069 | 38% |
| 1,3R (attuale) | 1995 | 44% | −0,028 | +0,011 | −0,067 | 10% |
| 1,3R + pausa 4 h | 1891 | 44% | −0,032 | +0,028 | −0,093 | 10% |
| 1,3R + pausa 24 h | 1626 | 45% | −0,029 | +0,013 | −0,074 | 13% |

La pausa aiuta con il target a 2R (dimezza o quasi azzera la perdita), non con 1,3R.
Nessuna combinazione è positiva in entrambe le metà.
