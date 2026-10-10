# ADR 0010 — L'ora del broker non è UTC, e la giornata di trading è la sua

**Stato:** accettata · **Data:** 2026-10-04 · **Corretta da:** [ADR 0015](0015-eta-dei-dati-in-ora-broker.md) — lo scarto non si misura più dal singolo tick, e l'età di un dato si calcola solo con lo scarto

## Contesto

MT5 restituisce i timestamp come epoch seconds, ma i valori rappresentano l'ora
del **server del broker**. Con XM lo scarto da UTC è di due o tre ore a seconda
dell'ora legale, e cambia due volte l'anno.

Le conseguenze di sbagliare non sono estetiche:

- la **perdita massima giornaliera** si azzera a mezzanotte *del broker*. Usare
  la mezzanotte locale creerebbe ogni giorno una finestra in cui il limite è di
  fatto doppio;
- le **sessioni autorizzate** definite sull'ora del PC si spostano con l'ora
  legale e con i fusi orari;
- un evento macro pubblicato a un'ora UTC, confrontato con un timestamp
  ora-broker, risulta passato o futuro di ore: il filtro eventi sbaglierebbe
  proprio sulle pubblicazioni importanti.

## Decisione

Tre regole.

1. **Ogni datetime nel sistema è timezone-aware.** I timestamp MT5 vengono
   etichettati UTC per essere confrontabili, ma restano *ora-broker*:
   `from_broker_time()` lo dichiara nel proprio docstring.
2. **L'ora del broker si chiede al broker**, non all'orologio di sistema:
   `MT5Connector.server_time()` la deduce dall'ultimo tick di uno strumento molto
   liquido. Sessioni, reset giornaliero e settimanale si calcolano su questa.
3. **Lo scarto viene stimato e registrato.** Con il modulo calendario (fase 4)
   l'offset broker↔UTC sarà calcolato confrontando l'ora del tick con quella di
   sistema, salvato e monitorato: un cambio improvviso è un sintomo da vedere,
   non un dettaglio.

Finché il punto 3 non è implementato, i timestamp del broker si usano solo per
ordinare le candele e per confronti fra dati dello stesso broker — mai per
decidere se un evento datato UTC è già passato.

## Conseguenze

Positive: il limite giornaliero dell'utente coincide con la giornata del conto;
le sessioni autorizzate sono stabili nel tempo.

Negative: due nozioni di tempo convivono nel sistema e la distinzione va
ricordata. È il motivo per cui `core/clock.py` e `mapping.from_broker_time()`
hanno docstring sproporzionate rispetto alla quantità di codice.
