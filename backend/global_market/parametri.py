"""Parametri dell'intelligence globale, in un solo posto.

**Tutte euristiche non validate.** Sono scelte ragionevoli, documentate una per
una, che la fase 9 (walk-forward) dovrà misurare. Stanno qui e non sparse nel
codice perché cambiarne una deve cambiare `VERSIONE_MOTORE`: un contesto salvato
deve poter dire con quali regole è stato prodotto, altrimenti non è riproducibile.
"""

from __future__ import annotations

from backend.core.enums import Timeframe

#: Cambia a ogni modifica di regole o soglie. Entra nell'impronta del contesto.
VERSIONE_MOTORE = "global-1.1.0"

#: Timeframe analizzati e loro peso nel composito: il lento pesa di più perché
#: descrive il contesto, il veloce di meno perché è in gran parte rumore.
TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15)
PESI: dict[Timeframe, float] = {
    Timeframe.D1: 0.4,
    Timeframe.H4: 0.3,
    Timeframe.H1: 0.2,
    Timeframe.M15: 0.1,
}
#: Timeframe "lenti" e "veloci" per riconoscere una transizione.
LENTI: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4)
VELOCI: tuple[Timeframe, ...] = (Timeframe.H1, Timeframe.M15)

#: Barre richieste per serie. 400 coprono la finestra del percentile ATR (250) più
#: il riscaldamento degli indicatori.
BARRE_RICHIESTE = 400
#: Sotto questo numero di barre chiuse una serie non entra nei calcoli.
BARRE_MINIME = 60
#: Sotto questo numero i percentili di volatilità non sono calcolati.
BARRE_PER_PERCENTILI = 260

# --- momentum normalizzato -------------------------------------------------
#: Orizzonte del momentum, in barre del timeframe.
ORIZZONTE = 20
#: Finestra della volatilità di riferimento per normalizzare il momentum.
FINESTRA_VOLATILITA = 50
#: Il momentum normalizzato si tronca a ±10: oltre è quasi certamente un dato
#: anomalo, e un valore enorme dominerebbe ogni media.
LIMITE_Z = 10.0

# --- classificazioni -------------------------------------------------------
#: Soglia (in unità di deviazione standard) per dire STRONG/WEAK.
SOGLIA_FORZA = 0.5
#: Quota minima di componenti concordi per un gruppo non MIXED.
SOGLIA_AMPIEZZA = 0.66

# --- volatilità ------------------------------------------------------------
FINESTRA_PERCENTILE_ATR = 250
FINESTRA_PERCENTILE_BOLLINGER = 120
VOL_BREVE = 10
VOL_LUNGA = 50
SOGLIA_ESPANSIONE = 1.20
SOGLIA_COMPRESSIONE = 0.85
#: Percentili ATR mediani per i livelli.
LIVELLO_BASSO = 0.25
LIVELLO_ELEVATO = 0.75
LIVELLO_ESTREMO = 0.95
#: Shock: quota di strumenti con ATR veloce oltre il 97° percentile.
PERCENTILE_SHOCK = 0.97
QUOTA_SHOCK = 0.40

# --- intermarket (rivisto in fase 4.5) -------------------------------------
#: Rendimenti della finestra corrente (gli ultimi N, allineati per orario).
CAMPIONE_CORRENTE = 40
#: Minimo di rendimenti nella base storica, che precede la finestra corrente.
CAMPIONE_BASE_MINIMO = 100
#: Finestra e passo delle correlazioni mobili usate per la stabilità della base.
FINESTRA_STABILITA = 40
PASSO_STABILITA = 10
#: Soglia di rilevanza pratica della base (oltre alla significatività statistica).
SOGLIA_RELAZIONE = 0.25
#: t di Student minimo perché la base sia significativa (≈ p < 0.05).
T_SIGNIFICATIVITA = 2.0
#: |z| di Fisher minimo perché corrente e base siano diverse.
Z_DIFFERENZA = 2.0
SOGLIA_STABILITA = 0.60
#: |corrente| minima perché un cambio di segno sia una divergenza e non rumore.
SOGLIA_DIVERGENZA = 0.20

# --- sentiment di rischio --------------------------------------------------
SOGLIA_RISCHIO = 0.35
SOGLIA_ACCORDO = 0.70

# --- qualità ---------------------------------------------------------------
#: Copertura minima di una dimensione per non essere INSUFFICIENT, e per GOOD.
COPERTURA_MINIMA = 0.50
COPERTURA_BUONA = 0.90
