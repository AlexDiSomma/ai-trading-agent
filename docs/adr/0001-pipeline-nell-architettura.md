# ADR 0001 — La pipeline sta nell'architettura, non nel prompt

**Stato:** accettata · **Data:** 2026-10-04

## Contesto

La specifica descrive un ordine preciso: *cosa succede nel mondo → cosa succede
sui mercati → quali asset sono forti → in che regime siamo → dove sono le
opportunità → questa operazione ha senso → sta nel portafoglio → il rischio è
accettabile → solo allora si esegue.*

Il modo più rapido di ottenerlo è scriverlo nel prompt dell'agente AI. Il
problema è che un prompt è una raccomandazione: quando il modello cambia, o
quando qualcuno aggiunge una funzione che chiama direttamente l'esecuzione,
l'ordine si perde senza che nulla si rompa.

## Decisione

L'ordine è reso **strutturale**: visibile nelle dipendenze fra i package, e una
violazione diventa un errore di import o un test rosso.

- `global_market/` non importa `scanner/`: il contesto globale si forma senza
  sapere su cosa si opererà.
- `scanner/` riceve il contesto globale come dato in ingresso: non può
  classificare un asset prima che quel contesto esista.
- `ai/` non importa `risk/`, `risk/` non importa `ai/` (verificato da
  `backend/tests/unit/test_read_only_guarantee.py`).
- `execution/` accetterà solo un piano con approvazione del Risk Engine (ADR 0005).
- `core/` non importa nulla dal resto del backend.

## Conseguenze

Positive: l'ordine sopravvive al cambio di modello, di sviluppatore e di
strategia. Chi arriva dopo lo legge dall'albero delle cartelle.

Negative: più passaggi di dati espliciti e più DTO da mantenere. È il costo
accettato: in un sistema che muove denaro la rigidità nei punti giusti vale più
della comodità.
