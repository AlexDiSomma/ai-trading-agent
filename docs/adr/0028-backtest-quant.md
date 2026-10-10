# ADR 0028 — Backtest della strategia QUANT (fase 9, prima parte)

**Stato:** accettata · **Data:** 2026-10-09 · **Origine:** richiesta dell'utente dopo
−300 € nel conto paper QUANT in tre giorni

## Contesto

QUANT (`quant-baseline-1.0.0`) era nata come termine di paragone fisso per l'AI ("non
deve essere buona, deve essere fissa") ed è finita a operare nel paper senza mai essere
verificata sul passato. Con 5-10 trade al giorno servirebbero mesi per giudicarla.

## Decisione

`backend/backtesting/` + `python -m backend.backtest`:

* **storico** dal broker in sola lettura, a blocchi da 5.000 barre con tre tentativi
  (un timeout di MT5 sulle D1 aveva svuotato in silenzio quattro coppie forex alla
  prima esecuzione), cache in `var/backtest/cache`, mai salvata vuota;
* **segnali** a ogni chiusura H1 con lo stesso codice del paper: candele tagliate
  all'istante (solo barre chiuse, le ultime `BARRE_RICHIESTE` come nel live) →
  `analyse_replay` → `costruisci_catalogo` → `piano_quant`;
* **simulatore** deterministico: gate `valuta_ingresso`, `risolvi_barra`, stop mobile
  di `paper/trailing.py`, gruppi di correlazione; ingresso all'apertura M5 successiva
  all'ask con lo spread della barra e lo slittamento configurato;
* **rapporto**: due misure — *strategia pura* (regole del singolo trade, senza i blocchi
  di conto) e *come il paper* (anche perdita giornaliera/settimanale e drawdown 10%) —,
  due metà del periodo, Monte Carlo dell'expectancy (2.000 ricampionamenti, seme fisso).
* **nessuna chiamata all'AI.** Il backtest dell'AI richiederebbe migliaia di chiamate e
  un modello che "ricorda" il periodo: non è una misura onesta.

Le sette varianti sono quelle discusse **prima** di vedere i risultati: base, tre stop
mobili, una posizione per scommessa, orizzonte 72 ore, stop 2,5 ATR con 48 ore.

Approssimazioni dichiarate: decisioni ogni ora (il paper ogni 5 minuti), gestione su M5
(il paper su M1), equity e blocchi sul realizzato, niente swap, niente notizie né
calendario storici.

## Risultati (2025-06-14 → 2026-10-09, 10 strumenti, rischio 0,5% per trade)

| Variante | Trade | Win | Exp. R | Tot. R | Rend. | DD | 1ª metà | 2ª metà | MC 90% | P>0 |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 1729 | 36% | −0,049 | −84,5 | −37,6% | 45% | +0,030 | −0,125 | [−0,098, +0,003] | 6% |
| pareggio a +1R | 1916 | 29% | −0,062 | −118,2 | −47,2% | 49% | +0,007 | −0,126 | [−0,105, −0,020] | 1% |
| blocca +0,5R | 2186 | 46% | −0,053 | −116,8 | −46,8% | 50% | +0,005 | −0,110 | [−0,091, −0,015] | 1% |
| trailing 1R | 2111 | 45% | −0,070 | −148,0 | −54,5% | 55% | −0,014 | −0,122 | [−0,110, −0,031] | 0% |
| una per scommessa | 1551 | 36% | −0,044 | −67,5 | −31,3% | 34% | +0,014 | −0,099 | [−0,095, +0,010] | 9% |
| orizzonte 72 h | 1483 | 35% | −0,013 | −19,4 | −13,9% | 35% | +0,083 | −0,105 | [−0,071, +0,043] | 33% |
| stop 2,5 ATR, 48 h | 895 | 41% | +0,032 | +28,8 | +13,3% | 12% | +0,044 | +0,020 | [−0,036, +0,097] | 78% |

Con i blocchi del conto ogni variante tocca il drawdown del 10% e si ferma (da −6% a −11%).

## Conclusioni

1. **QUANT 1.0.0 non ha vantaggio**: −0,05R per trade su 1.729 trade, peggio nella
   seconda metà. Le perdite del paper sono coerenti con la regola, non sfortuna.
2. **Lo stop mobile peggiora** con tutte e tre le regole: alza la quota di trade vinti
   ma taglia i vincenti più di quanto salvi. Il replay di 14 trade del 7 ottobre
   (+2/+3R) era rumore. La misura in ombra resta; nessuna regola va accesa.
3. **Stop più largo e orizzonte più lungo** è l'unica variante positiva in entrambe le
   metà, con un meccanismo plausibile (spread da 0,062R a 0,037R per trade, meno stop
   presi dal rumore). Non è una prova: è la migliore di sette, l'intervallo comprende
   lo zero e il guadagno viene soprattutto da oro e argento (+0,22R e +0,16R per
   trade, in un periodo di forte tendenza dei metalli), mentre i cambi principali
   restano negativi.
4. Nessuna modifica al paper in questa ADR: le scelte spettano all'utente.

## Prossimi passi possibili

* verificare "stop 2,5 ATR" su uno storico più lungo (H1 dal 2010 per i cambi) con
  gestione su H1, per capire se regge fuori da questo periodo;
* walk-forward vero quando ci saranno parametri da scegliere;
* Monte Carlo del drawdown e rischio di rovina.
