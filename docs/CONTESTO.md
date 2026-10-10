# Contesto di lavoro

Riassunto delle conversazioni fra Alex e Claude su questo progetto, per riprendere da
qualunque PC. **Le scelte e i loro motivi** sono in [`DECISIONI.md`](../DECISIONI.md) e
nelle ADR ([`docs/adr/`](adr/)); qui ci sono le preferenze di lavoro, lo stato del sistema
e le questioni aperte. Aggiornato al **10 ottobre 2026**.

---

## Come lavoriamo

- **Tutto in italiano**: risposte, documenti, commenti, testi della UI, messaggi d'errore.
  Gli identificatori (variabili, funzioni) restano in inglese.
- **Ogni scelta non ovvia va in `DECISIONI.md`**, in ordine inverso, contestualmente alla
  modifica: problema, alternativa scartata, motivo. Le voci superate non si cancellano: se
  ne aggiunge una nuova. Nei commenti del codice si spiega il *perché*.
  Motivo: il progetto precedente (`trading-lab`) è diventato ingestibile anche perché le
  decisioni erano sparse in file contraddittori senza traccia del ragionamento.
- **Mai adattare il codice in silenzio**: una scoperta (es. un comportamento di MT5 diverso
  dalla documentazione) si misura sul conto, si documenta e si corregge l'architettura.
- **Analisi oneste, non compiacenti.** Alex lo ha chiesto esplicitamente: se un'idea non
  regge, va detto con i numeri.
- **Misurare prima di accendere.** Ogni idea di gestione o strategia passa prima dal
  backtest (`python -m backend.backtest`) e/o dalla misura in ombra.
- **Non sprecare credito OpenAI.** I test usano client finti. Non lanciare comandi che
  chiamano il modello (`report_shadow esegui`, `report_ai`) senza chiedere.
- **Sicurezza:** nessuna credenziale nel codice o nel repository; mai password MT5 o chiavi
  API nel frontend o nei log; mai stampare i valori del `.env`. Mai usare o salvare token
  incollati in chat (va consigliato di revocarli).
- **LIVE:** disabilitato (`APP_MODE=PAPER`, `APP_ENABLE_LIVE_TRADING=false`). Si attiva solo
  con tutti i gate veri **e** la conferma scritta a mano da Alex
  (`var/LIVE_CONFIRMATION.json`): quel file non va mai creato dal codice né da Claude.

---

## Com'è nato e cosa c'è

- Nella cartella `BroCloud/projects` ci sono tre tentativi: `trading-lab` (eToro,
  abbandonato), `brotrade` (XM via MT5), **`ai-trading-agent`** (questo, dal 4 ottobre 2026).
  Dai precedenti nessuna logica riusata.
- Specifica in 14 fasi (0-13), una alla volta: vedi [`docs/ROADMAP.md`](ROADMAP.md).
- Conto **demo XM** in EUR, MetaTrader 5 sul PC Windows. Sullo stesso conto demo opera
  anche un altro EA (magic 770001), da separare prima di qualsiasi LIVE.

**Costruito finora** (dettagli nelle ADR):
- fondazione, dati di mercato, contesto globale, fonti esterne gratuite (fasi 0-4.5);
- AI analista e tesi in shadow mode (fasi 5A/5B, OpenAI `gpt-5.4-mini`);
- **MVP paper** con due conti, **QUANT** e **AI**, scheduler 24 ore su 24 (ADR 0024);
- fondazione dell'esecuzione LIVE, spenta (ADR 0025);
- comandi in dashboard: kill switch, pausa, mercati e strategie, limiti modificabili,
  chiusura manuale, obiettivo giornaliero (ADR 0026);
- notizie da 14 feed RSS gratuiti come freno e come evidenza per l'AI (ADR 0023 e voci in
  DECISIONI);
- dashboard Next.js con tema chiaro/scuro, grafici a candele live, **login** e ascolto solo
  su `127.0.0.1`;
- stop mobile misurato in ombra, avviso quando l'AI non risponde, posizioni correlate
  (ADR 0027);
- **backtest** della strategia QUANT (ADR 0028);
- target di QUANT regolabile e segnali invertiti, come esperimenti (ADR 0029).

### QUANT e AI

Due conti paper con lo stesso capitale, le stesse regole di rischio e lo stesso motore di
esecuzione; cambia chi decide.
- **QUANT**: regola fissa (`quant-baseline-1.0.0`): tendenza concorde su D1, H4 e H1,
  allineamento ≥ 0,35, ingresso a mercato, stop 1,5 ATR H1, target 2R, orizzonte 24 ore.
  Nata come **termine di paragone**, non come strategia vincente.
- **AI**: il modello legge contesto, indicatori, struttura e notizie e propone
  compra / vendi / aspetta con livelli e condizioni d'invalidazione; il codice calcola RR e
  tipo d'ingresso e applica il gate. Ogni tesi costa credito.

---

## Stato al 10 ottobre 2026

| Conto | Iniziale | Equity | Trade chiusi | Totale |
|---|---|---|---|---|
| QUANT | 9.815,21 € | 9.313,14 € | 22 | −11,0R |
| AI | 9.815,21 € | 9.636,85 € | 11 | −3,2R |

Impostazioni in Controlli: **target QUANT 1,3R** (chiesto da Alex il 9 ottobre), segnali
invertiti spenti, stop mobile spento, nessuna pausa.

### Cosa ha detto il backtest (16 mesi, 10 strumenti, ADR 0028-0029)

| QUANT | Media per trade |
|---|---|
| com'era (2R) | −0,049R |
| target 1,3R (attuale) | −0,028R |
| 2R + pausa 24 h dopo uno stop | −0,010R |
| stop 2,5 ATR + 48 h | +0,032R (78% di probabilità di essere positiva; trainata dai metalli) |
| stop mobile (tre regole) | peggio: da −0,053 a −0,070R |
| segnali invertiti | peggio: −0,086R |

In sintesi: QUANT 1.0.0 non ha vantaggio; lo spread pesa ~6% del rischio a ogni trade.
Nessuna variante è positiva in entrambe le metà del periodo.

---

## Questioni aperte

1. **QUANT**: tornare a 2R con pausa di 24 ore dopo uno stop (la pausa esiste solo nel
   backtest: va aggiunta al paper come comando), oppure lasciare 1,3R. Da decidere.
2. **Strategie con basi documentate**: trend following su orizzonti lunghi, verificato su
   10-15 anni di storico, con criteri di promozione fissati prima dei risultati. Se nessuna
   passa, fermarsi davvero. Proposto, non avviato.
3. **Costo dell'AI**: ~100 tesi al giorno da ~19 mila token, 85% "aspetta". Ridurre la
   frequenza (es. una tesi ogni 4 ore) è stato proposto, non fatto.
4. **Accesso remoto** alla dashboard con Tailscale (non GitHub Pages, che è solo statico).
   Proposto, non approvato.
5. PostgreSQL spento: serve solo come gate del LIVE.

---

## Lavorare da un altro PC

Il repository contiene codice, test e documenti. **Non** contiene (e non deve contenere):
`.env`, `frontend/.env.local`, `var/` (conti paper, registri, cache del backtest, consumo
AI).

1. `git clone https://github.com/AlexDiSomma/ai-trading-agent.git`
2. Ambiente Python e dashboard come nel [README](../README.md) (`python -m venv .venv`,
   `pip install -e ".[mt5,ai,dev]"`, `npm install` in `frontend`).
3. Crea `.env` da `.env.example` e `frontend/.env.local` da `.env.local.example` con le tue
   credenziali, **copiandole a mano** (mai via repository o chat). Poi
   `npm run imposta-password` in `frontend`.
4. **Un solo PC alla volta deve far girare lo scheduler** (`avvia.bat`): due istanze
   creerebbero due conti paper diversi e raddoppierebbero il consumo dell'AI. Per
   sviluppare senza trading lascia lo scheduler spento (`PAPER_AUTOPILOT_ENABLED=false`).
5. I test non richiedono MT5 né OpenAI: `.venv\Scripts\python.exe -m pytest`.
6. Il backtest richiede MT5 aperto la prima volta (scarica lo storico, ~1 ora).

Per riprendere con Claude su un altro PC: apri la cartella del progetto e chiedi di
leggere `CLAUDE.md`, questo file e le prime voci di `DECISIONI.md`.
