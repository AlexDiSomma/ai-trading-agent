"""Forza delle valute: modello a grafo robusto, stimato dalle equazioni dei cross.

**Misura primaria delle valute** (correzione pre-fase 5). Il DXY ricostruito la
verifica, non la sostituisce.

**Il modello.** Ogni cross osserva la differenza fra due valute: il rendimento di
EURUSD in una barra è `r(EUR,USD) = x_EUR − x_USD`. Con 8 valute e fino a 28 cross il
sistema è sovradeterminato: per ogni barra si stima `x` con i **minimi quadrati
pesati**, con il vincolo `Σ x = 0` (le forze sono relative). In forma chiusa, con
`L_w` il laplaciano pesato del grafo e `J` la matrice di uni:

    (L_w + J) · x = b_w,    b_i = Σ w·r(i, j) − Σ w·r(j, i)

**Robustezza** (fase 4.5 → correzioni pre-fase 5):

* **cross mancanti** — un'equazione in meno; le valute senza nessun cross restano
  `None`, le altre devono formare un grafo connesso;
* **volatilità diversa** — ogni cross pesa `1 / varianza` dei suoi rendimenti nella
  finestra: GBPJPY, che si muove il triplo di EURCHF, non domina la stima;
* **dati fermi** — un cross con varianza nulla o con la maggioranza dei rendimenti
  esattamente zero è escluso per tutto il timeframe (quotazione bloccata);
* **outlier** — per ogni barra si calcolano i residui; un'equazione con residuo oltre
  6 deviazioni robuste (MAD) **e** oltre 1 pip (1e-4) viene tolta e la barra
  ristimata, al massimo tre volte. Un prezzo sbagliato non sposta le altre valute.

Il **residuo** mediano resta una misura di coerenza dei prezzi.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from backend.config.global_universe import CROSS_FX, VALUTE
from backend.core.enums import StrengthLabel, Timeframe
from backend.global_market import parametri as par
from backend.global_market.features import Misure, SerieDiRiferimento, composito, concordi
from backend.global_market.qualita import Stati, qualita_dimensione
from backend.intermarket.correlation import rendimenti_per_orario
from backend.schemas.global_market import CurrencyStrength, CurrencyStrengthView

Misurazioni = Mapping[str, Mapping[Timeframe, Misure]]
Serie = Mapping[tuple[str, Timeframe], SerieDiRiferimento]

#: Cross minimi in una barra perché la si usi (su 28).
CROSS_MINIMI_PER_BARRA = 14
#: Soglia degli outlier: deviazioni robuste (1.4826·MAD) e minimo assoluto (1 pip).
SOGLIA_OUTLIER_SIGMA = 6.0
SOGLIA_OUTLIER_MINIMA = 1e-4
OUTLIER_MASSIMI_PER_BARRA = 3
#: Quota di rendimenti esattamente nulli oltre la quale un cross è "fermo".
QUOTA_FERMO = 0.5

_INDICE = {v: i for i, v in enumerate(VALUTE)}


def etichetta(score: float | None, per_tf: Mapping[Timeframe, float | None]) -> StrengthLabel:
    """STRONG / WEAK / NEUTRAL, oppure MIXED se lento e veloce si contraddicono.

    MIXED richiede che il blocco lento (D1, H4) e quello veloce (H1, M15) superino
    **entrambi** la soglia, in direzioni opposte. Un singolo timeframe di segno
    opposto non basta: il momentum normalizzato di un timeframe ha rumore
    dell'ordine di 1, e con quella regola quasi ogni strumento risultava MIXED.
    """
    if score is None:
        return StrengthLabel.UNKNOWN
    lento = composito({tf: per_tf.get(tf) for tf in par.LENTI}, minimo=1)
    veloce = composito({tf: per_tf.get(tf) for tf in par.VELOCI}, minimo=1)
    if (
        lento is not None
        and veloce is not None
        and abs(lento) >= par.SOGLIA_FORZA
        and abs(veloce) >= par.SOGLIA_FORZA
        and lento * veloce < 0
    ):
        return StrengthLabel.MIXED
    if score >= par.SOGLIA_FORZA:
        return StrengthLabel.STRONG
    if score <= -par.SOGLIA_FORZA:
        return StrengthLabel.WEAK
    return StrengthLabel.NEUTRAL


# ======================================================================
# Algebra
# ======================================================================
def _risolvi(matrice: list[list[float]], termini: list[float]) -> list[float] | None:
    n = len(termini)
    a = [[*riga, termini[i]] for i, riga in enumerate(matrice)]
    for colonna in range(n):
        pivot = max(range(colonna, n), key=lambda r: abs(a[r][colonna]))
        if abs(a[pivot][colonna]) < 1e-12:
            return None
        a[colonna], a[pivot] = a[pivot], a[colonna]
        for r in range(n):
            if r != colonna:
                fattore = a[r][colonna] / a[colonna][colonna]
                if fattore:
                    for c in range(colonna, n + 1):
                        a[r][c] -= fattore * a[colonna][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def _componente(archi: Sequence[tuple[int, int]], partenza: int) -> set[int]:
    vicini: dict[int, set[int]] = {}
    for i, j in archi:
        vicini.setdefault(i, set()).add(j)
        vicini.setdefault(j, set()).add(i)
    visti, coda = {partenza}, deque([partenza])
    while coda:
        for v in vicini.get(coda.popleft(), ()):
            if v not in visti:
                visti.add(v)
                coda.append(v)
    return visti


def _minimi_quadrati(
    archi: Sequence[tuple[int, int, float, float]],
) -> tuple[list[float | None], list[float]] | None:
    """Soluzione pesata su un insieme di archi (i, j, rendimento, peso), con residui."""
    presenti = sorted({i for i, _, _, _ in archi} | {j for _, j, _, _ in archi})
    if _componente([(i, j) for i, j, _, _ in archi], presenti[0]) != set(presenti):
        return None
    m = len(presenti)
    posizione = {v: k for k, v in enumerate(presenti)}
    sistema = [[1.0] * m for _ in range(m)]  # parte J: vincolo Σ x = 0
    termini = [0.0] * m
    for i, j, r, w in archi:
        a, b = posizione[i], posizione[j]
        sistema[a][a] += w
        sistema[b][b] += w
        sistema[a][b] -= w
        sistema[b][a] -= w
        termini[a] += w * r
        termini[b] -= w * r
    ridotta = _risolvi(sistema, termini)
    if ridotta is None:
        return None
    x: list[float | None] = [None] * len(VALUTE)
    for v, valore in zip(presenti, ridotta, strict=True):
        x[v] = valore
    residui = [r - (x[i] - x[j]) for i, j, r, _ in archi]  # type: ignore[operator]
    return x, residui


@dataclass
class EsitoBarra:
    x: list[float | None]
    residuo: float
    outlier: list[str] = field(default_factory=list)


def stima_barra(
    rendimenti: Mapping[str, float],
    *,
    pesi: Mapping[str, float] | None = None,
    minimo: int = CROSS_MINIMI_PER_BARRA,
) -> tuple[list[float | None], float] | None:
    """Rendimenti delle valute in una barra, e residuo RMS. Vedi `stima_barra_robusta`."""
    esito = stima_barra_robusta(rendimenti, pesi=pesi, minimo=minimo)
    return None if esito is None else (esito.x, esito.residuo)


def stima_barra_robusta(
    rendimenti: Mapping[str, float],
    *,
    pesi: Mapping[str, float] | None = None,
    minimo: int = CROSS_MINIMI_PER_BARRA,
) -> EsitoBarra | None:
    """Stima pesata con rimozione iterativa degli outlier."""
    attivi = dict(sorted(rendimenti.items()))
    tolti: list[str] = []
    for _ in range(OUTLIER_MASSIMI_PER_BARRA + 1):
        if len(attivi) < minimo:
            return None
        archi = [
            (_INDICE[c[:3]], _INDICE[c[3:]], r, (pesi or {}).get(c, 1.0)) for c, r in attivi.items()
        ]
        soluzione = _minimi_quadrati(archi)
        if soluzione is None:
            return None
        x, residui = soluzione
        assoluti = sorted(abs(e) for e in residui)
        mad = assoluti[len(assoluti) // 2]
        soglia = max(SOGLIA_OUTLIER_SIGMA * 1.4826 * mad, SOGLIA_OUTLIER_MINIMA)
        peggiore = max(range(len(residui)), key=lambda k: abs(residui[k]))
        if abs(residui[peggiore]) <= soglia or len(tolti) >= OUTLIER_MASSIMI_PER_BARRA:
            rms = math.sqrt(math.fsum(e * e for e in residui) / len(residui))
            return EsitoBarra(x=x, residuo=rms, outlier=tolti)
        cross = list(attivi)[peggiore]
        tolti.append(cross)
        del attivi[cross]
    return None


@dataclass
class StimaTimeframe:
    """Serie stimate delle valute su un timeframe, con la diagnostica."""

    valute: dict[str, list[tuple[datetime, float]]]
    residui: list[float]
    usati: set[str]
    outlier: int = 0
    esclusi: dict[str, str] = field(default_factory=dict)


def serie_valute(serie: Serie, tf: Timeframe) -> StimaTimeframe:
    """Serie dei rendimenti stimati di ogni valuta su un timeframe."""
    per_cross = {
        c: rendimenti_per_orario(s.tempi, s.chiusure)
        for c in CROSS_FX
        if (s := serie.get((c, tf))) is not None
    }
    # Cross fermi ed esclusi, e peso a varianza inversa per gli altri.
    esclusi: dict[str, str] = {}
    varianze: dict[str, float] = {}
    for c, r in per_cross.items():
        valori = list(r.values())
        if not valori:
            esclusi[c] = "nessun rendimento"
            continue
        nulli = sum(1 for v in valori if v == 0.0) / len(valori)
        media = math.fsum(valori) / len(valori)
        varianza = math.fsum((v - media) ** 2 for v in valori) / len(valori)
        if varianza <= 0 or nulli > QUOTA_FERMO:
            esclusi[c] = f"quotazione ferma ({nulli:.0%} di rendimenti nulli)"
            continue
        varianze[c] = varianza
    attivi = {c: r for c, r in per_cross.items() if c in varianze}
    media_inv = math.fsum(1 / v for v in varianze.values()) / len(varianze) if varianze else 1.0
    pesi = {c: (1 / v) / media_inv for c, v in varianze.items()}

    tempi = sorted({t for r in attivi.values() for t in r})
    risultato = StimaTimeframe(
        valute={v: [] for v in VALUTE}, residui=[], usati=set(), esclusi=esclusi
    )
    for t in tempi:
        barra = {c: r[t] for c, r in attivi.items() if t in r}
        esito = stima_barra_robusta(barra, pesi=pesi)
        if esito is None:
            continue
        risultato.residui.append(esito.residuo)
        risultato.outlier += len(esito.outlier)
        risultato.usati.update(c for c in barra if c not in esito.outlier)
        for v, valore in zip(VALUTE, esito.x, strict=True):
            if valore is not None:
                risultato.valute[v].append((t, valore))
    return risultato


def _momentum(rendimenti: Sequence[float]) -> float | None:
    if len(rendimenti) < par.FINESTRA_VOLATILITA:
        return None
    finestra = rendimenti[-par.FINESTRA_VOLATILITA :]
    media = math.fsum(finestra) / len(finestra)
    sigma = math.sqrt(math.fsum((r - media) ** 2 for r in finestra) / len(finestra))
    if sigma <= 0:
        return None
    z = math.fsum(rendimenti[-par.ORIZZONTE :]) / (sigma * math.sqrt(par.ORIZZONTE))
    return round(max(-par.LIMITE_Z, min(par.LIMITE_Z, z)), 6) if math.isfinite(z) else None


def forza_valute(
    serie: Serie,
    stati: Stati,
    stimate_per_tf: Mapping[Timeframe, StimaTimeframe] | None = None,
) -> CurrencyStrengthView:
    """Forza delle valute. `stimate_per_tf` permette di riusare `serie_valute`."""
    per_valuta: dict[str, dict[Timeframe, float | None]] = {v: {} for v in VALUTE}
    residui: dict[Timeframe, float | None] = {}
    barre: dict[Timeframe, int] = {}
    outlier: dict[Timeframe, int] = {}
    esclusi: dict[Timeframe, tuple[str, ...]] = {}
    cross_usati: dict[str, set[str]] = {v: set() for v in VALUTE}
    for tf in par.TIMEFRAMES:
        stima = (
            stimate_per_tf[tf]
            if stimate_per_tf and tf in stimate_per_tf
            else serie_valute(serie, tf)
        )
        barre[tf] = len(stima.residui)
        residui[tf] = (
            round(sorted(stima.residui)[len(stima.residui) // 2], 8) if stima.residui else None
        )
        outlier[tf] = stima.outlier
        esclusi[tf] = tuple(f"{c}: {m}" for c, m in sorted(stima.esclusi.items()))
        for v in VALUTE:
            per_valuta[v][tf] = _momentum([x for _, x in stima.valute[v]])
            cross_usati[v].update(c for c in stima.usati if v in (c[:3], c[3:]))

    risultati = []
    for v in VALUTE:
        score = composito(per_valuta[v])
        risultati.append(
            CurrencyStrength(
                currency=v,
                score=score,
                label=etichetta(score, per_valuta[v]),
                by_timeframe=per_valuta[v],
                crosses_used=len(cross_usati[v]),
                timeframes_agree=concordi(per_valuta[v]),
            )
        )
    ordinati = sorted(risultati, key=lambda c: (c.score is None, -(c.score or 0.0), c.currency))
    classificati = tuple(
        c.model_copy(update={"rank": i + 1 if c.score is not None else None})
        for i, c in enumerate(ordinati)
    )
    return CurrencyStrengthView(
        currencies=classificati,
        quality=qualita_dimensione("currency_strength", CROSS_FX, stati),
        graph_residual=residui,
        bars_estimated=barre,
        outliers_removed=outlier,
        crosses_excluded=esclusi,
    )
