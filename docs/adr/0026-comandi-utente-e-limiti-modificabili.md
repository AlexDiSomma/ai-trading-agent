# ADR 0026 — Comandi dell'utente dalla dashboard e limiti di rischio modificabili

**Stato:** accettata · **Data:** 2026-10-06 · **Origine:** richiesta dell'utente
· **Modifica:** invariante 4 di CLAUDE.md ("i limiti non si modificano a runtime")

## Contesto

L'utente deve poter guidare il sistema dalla dashboard, anche dal telefono: fermarlo,
metterlo in pausa, scegliere mercati e strategie, chiudere posizioni, cambiare i limiti.
Fino a oggi i limiti si cambiavano solo nel `.env` con riavvio, per impedire che un
qualunque componente (AI compresa) li alzasse.

## Decisione

* **Comandi** (`services/controls.py`, file `var/paper/controls.json`, registro):
  pausa delle aperture, interruttore per strumento, interruttore per strategia (AI,
  QUANT). Riducono ciò che il sistema può fare; la gestione delle posizioni aperte
  continua sempre.
* **Kill switch globale** dalla dashboard (stesso file di sempre, ADR 0006).
* **Chiusura manuale** di una posizione paper o di tutte, al prezzo corrente con lo
  slittamento simulato; motivo d'uscita `MANUAL`.
* **Limiti modificabili** (`config/risk_overrides.py`): solo un elenco di campi, ognuno
  con un tetto di sicurezza non superabile da interfaccia (es. rischio per trade ≤ 2%,
  perdita giornaliera ≤ 10%); motivo obbligatorio, conferma con le differenze, registro di
  sola aggiunta; il risultato è un **nuovo** `RiskLimits` validato che sostituisce il
  precedente (nessun oggetto modificato sul posto); override salvati e riapplicati al
  riavvio sopra al `.env`; **rifiutati in LIVE**. L'AI non ha alcun accesso a questo modulo.
  Esecutore paper e Risk Engine leggono i limiti a ogni decisione.
* **Nuovi limiti**: take profit giornaliero facoltativo (raggiunto: stop alle aperture fino
  a domani) e perdita settimanale anche nel conto paper.
* **Sessioni**: per scelta dell'utente il sistema opera 24 ore nei giorni feriali
  (`RISK_ALLOWED_SESSIONS=00:00-23:59`); i filtri su spread ed eventi restano attivi.

## Alternative scartate

Modifica del `.env` dall'interfaccia: il file contiene credenziali e non deve essere
scritto da un'API.
