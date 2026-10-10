"""Classificazione della tendenza su un singolo timeframe.

Primo pezzo del riconoscimento di regime: qui si classifica la **tendenza** di un
timeframe, non il regime completo dell'asset (volatilità, eventi, caos), che
arriva in fase 5 quando ci saranno anche contesto globale e calendario.

Due regole che vengono da `docs/ANALISI_SPECIFICA.md` §2.4:

1. **i fattori restano separati e leggibili.** Il punteggio composito c'è, ma
   accanto ai contributi che lo compongono. Un punteggio di cui non si leggono le
   componenti non permette di capire, mesi dopo, perché una strategia ha smesso
   di funzionare;
2. **i pesi sono dichiarati provvisori.** Non sono stati validati su dati
   storici: servono a ordinare, non a stimare probabilità. La validazione
   walk-forward è fase 9, e fino a quel momento questo modulo descrive, non
   prevede.

La `confidence` qui è **accordo fra fattori**, non una probabilità di successo.
Chiamarla probabilità sarebbe la stessa confusione che la specifica fa con la
confidence dell'LLM (ADR 0008).
"""

from __future__ import annotations

from backend.core.enums import MarketRegime, StructureTrend
from backend.schemas.quant import IndicatorSnapshot, StructureSnapshot, TrendClassification

#: Pesi dei fattori nel punteggio composito. **Provvisori**: scelti per
#: ragionevolezza, non misurati. Sommano a 1 per rendere il punteggio leggibile.
PESI: dict[str, float] = {
    "allineamento_medie": 0.30,
    "prezzo_vs_ema200": 0.20,
    "pendenza_ema50": 0.15,
    "direzione_di": 0.15,
    "struttura": 0.20,
}

#: Soglie ADX convenzionali: sotto 18 nessuna direzione, sopra 25 tendenza
#: consolidata. Sono le soglie di Wilder, non una nostra calibrazione.
ADX_ASSENTE = 18.0
ADX_FORTE = 25.0

#: Percentile dell'ampiezza di Bollinger sotto il quale si parla di compressione.
RANGO_COMPRESSIONE = 0.15
#: ...e sopra il quale di espansione.
RANGO_ESPANSIONE = 0.85


def classifica_trend(
    indicatori: IndicatorSnapshot, struttura: StructureSnapshot
) -> TrendClassification:
    """Tendenza, punteggio, accordo fra fattori e motivazioni in italiano."""
    fattori: dict[str, float] = {}
    motivi: list[str] = []

    # --- 1. allineamento delle medie ---
    allineamento = float(indicatori.ema_alignment)
    fattori["allineamento_medie"] = allineamento
    if allineamento > 0:
        motivi.append("Medie allineate al rialzo (EMA20 > EMA50 > EMA200)")
    elif allineamento < 0:
        motivi.append("Medie allineate al ribasso (EMA20 < EMA50 < EMA200)")
    else:
        motivi.append("Medie non allineate: nessuna direzione dominante")

    # --- 2. posizione rispetto alla EMA200 ---
    # Normalizzata su 3 ATR%: oltre quella distanza il contributo è saturato,
    # altrimenti uno strumento molto volatile dominerebbe il punteggio.
    distanza = indicatori.price_vs_ema200_pct
    riferimento = (indicatori.atr_pct or 0.0) * 3
    if distanza is not None and riferimento > 0:
        valore = max(-1.0, min(1.0, distanza / riferimento))
        fattori["prezzo_vs_ema200"] = valore
        posizione = "sopra" if distanza > 0 else "sotto"
        motivi.append(f"Prezzo {posizione} la EMA200 di {abs(distanza):.2f}%")
    else:
        fattori["prezzo_vs_ema200"] = 0.0

    # --- 3. pendenza della EMA50 ---
    # Normalizzata su un decimo di ATR% per barra: una media che si muove di
    # 0.1 ATR per barra è già una tendenza netta.
    pendenza = indicatori.ema50_slope_pct
    scala_pendenza = max((indicatori.atr_pct or 0.0) * 0.1, 1e-9)
    if pendenza is not None:
        fattori["pendenza_ema50"] = max(-1.0, min(1.0, pendenza / scala_pendenza))
    else:
        fattori["pendenza_ema50"] = 0.0

    # --- 4. direzione dei Directional Indicator ---
    if indicatori.di_plus is not None and indicatori.di_minus is not None:
        somma = indicatori.di_plus + indicatori.di_minus
        valore = (indicatori.di_plus - indicatori.di_minus) / somma if somma > 0 else 0.0
        fattori["direzione_di"] = max(-1.0, min(1.0, valore))
    else:
        fattori["direzione_di"] = 0.0

    # --- 5. struttura di mercato ---
    mappa_struttura = {
        StructureTrend.UPTREND: 1.0,
        StructureTrend.DOWNTREND: -1.0,
        StructureTrend.RANGE: 0.0,
        StructureTrend.UNDEFINED: 0.0,
    }
    fattori["struttura"] = mappa_struttura[struttura.trend]
    if struttura.trend is StructureTrend.UPTREND:
        motivi.append("Struttura con massimi e minimi crescenti")
    elif struttura.trend is StructureTrend.DOWNTREND:
        motivi.append("Struttura con massimi e minimi decrescenti")
    elif struttura.trend is StructureTrend.RANGE:
        motivi.append("Struttura mista: il mercato non fa progressi direzionali")
    if struttura.change_of_character:
        motivi.append("Cambio di carattere: rottura contro la struttura prevalente")

    punteggio = sum(PESI[nome] * valore for nome, valore in fattori.items())
    punteggio = max(-1.0, min(1.0, punteggio))

    adx = indicatori.adx14
    regime = _regime(punteggio, adx, indicatori)
    direzione = 0 if regime in _REGIMI_SENZA_DIREZIONE else (1 if punteggio > 0 else -1)

    if adx is not None:
        if adx >= ADX_FORTE:
            motivi.append(f"ADX {adx:.1f}: tendenza consolidata")
        elif adx < ADX_ASSENTE:
            motivi.append(f"ADX {adx:.1f}: nessuna direzione misurabile")

    return TrendClassification(
        regime=regime,
        direction=direzione,
        score=round(punteggio, 4),
        confidence=round(_accordo(fattori, punteggio, adx), 4),
        factors={nome: round(valore, 4) for nome, valore in fattori.items()},
        reasons=tuple(motivi),
    )


_REGIMI_SENZA_DIREZIONE = frozenset(
    {
        MarketRegime.RANGING,
        MarketRegime.VOLATILITY_COMPRESSION,
        MarketRegime.VOLATILITY_EXPANSION,
        MarketRegime.UNCERTAIN,
    }
)


def _regime(punteggio: float, adx: float | None, indicatori: IndicatorSnapshot) -> MarketRegime:
    """Dal punteggio e dall'ADX al regime.

    L'ordine dei controlli è deliberato: la compressione di volatilità ha
    precedenza su "laterale" perché è un'informazione operativa diversa — un
    mercato fermo e uno fermo *e compresso* richiedono comportamenti opposti.
    """
    rango = indicatori.bollinger_width_rank

    if adx is None:
        return MarketRegime.UNCERTAIN

    if adx >= ADX_FORTE and abs(punteggio) >= 0.55:
        return MarketRegime.STRONG_TREND_UP if punteggio > 0 else MarketRegime.STRONG_TREND_DOWN

    if adx >= ADX_ASSENTE and abs(punteggio) >= 0.30:
        return MarketRegime.TREND_UP if punteggio > 0 else MarketRegime.TREND_DOWN

    if rango is not None and rango <= RANGO_COMPRESSIONE:
        return MarketRegime.VOLATILITY_COMPRESSION
    if rango is not None and rango >= RANGO_ESPANSIONE:
        return MarketRegime.VOLATILITY_EXPANSION

    if adx < ADX_ASSENTE and abs(punteggio) < 0.30:
        return MarketRegime.RANGING

    return MarketRegime.UNCERTAIN


def _accordo(fattori: dict[str, float], punteggio: float, adx: float | None) -> float:
    """Quanto i fattori puntano nella stessa direzione, in [0, 1].

    **Non è una probabilità di successo.** Cinque fattori concordi su un mercato
    che poi si gira danno accordo alto e previsione sbagliata: misura la
    coerenza dell'evidenza, non l'esito.
    """
    rilevanti = [v for v in fattori.values() if abs(v) > 0.05]
    if not rilevanti:
        return 0.0

    segno = 1 if punteggio >= 0 else -1
    concordi = sum(1 for v in rilevanti if (v > 0) == (segno > 0))
    quota_concordi = concordi / len(rilevanti)

    # L'ADX entra come moltiplicatore: fattori concordi senza forza direzionale
    # descrivono un mercato che va da nessuna parte con convinzione.
    forza = 0.5 if adx is None else min(1.0, max(0.0, (adx - 10) / 25))

    return min(1.0, 0.5 * quota_concordi + 0.3 * abs(punteggio) + 0.2 * forza)
