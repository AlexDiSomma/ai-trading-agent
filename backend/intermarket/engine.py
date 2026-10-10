"""Motore intermarket: le relazioni fra mercati, misurate e non presunte.

**Una correlazione non significa "relazione confermata"** (revisione fase 4.5).
Per ogni coppia di `config/global_universe.RELAZIONI`, su H1 e D1, si conservano:

* la **correlazione corrente** sugli ultimi 40 rendimenti allineati per orario;
* la **base storica**: la correlazione su tutti i rendimenti *precedenti* (senza
  sovrapposizione con la corrente), con la sua significatività (t di Student) e la
  dispersione delle correlazioni mobili al suo interno;
* la **dimensione dei campioni** di entrambe;
* la **stabilità**: quota delle finestre mobili della base con il segno della base;
* la **differenza** corrente − base in unità di errore standard (trasformata di
  Fisher), che dice se la correlazione di adesso è *davvero* diversa.

Sempre su rendimenti logaritmici, mai su prezzi: due serie con trend correlano sui
livelli anche se non hanno nulla in comune.

Stato, nell'ordine:

1. `INSUFFICIENT_DATA` — meno di 40 rendimenti correnti o 100 di base;
2. `NO_BASELINE` — base non significativa (t < 2) o trascurabile (|r| < 0.25);
3. `UNSTABLE` — base con lo stesso segno in meno del 60% delle finestre;
4. `DIVERGING` — corrente di segno opposto, |corrente| ≥ 0.20, |z| ≥ 2;
5. `WEAKENING` — stesso segno ma più debole, |z| ≥ 2;
6. `CONSISTENT` — nessuna differenza misurabile dalla base.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from backend.config.global_universe import RELAZIONI
from backend.core.enums import GlobalDataQuality, IntermarketState, RelationshipState, Timeframe
from backend.global_market import parametri as par
from backend.global_market.features import SerieDiRiferimento
from backend.global_market.qualita import Stati
from backend.intermarket.correlation import allinea, pearson, rendimenti_per_orario
from backend.schemas.global_market import DimensionQuality, IntermarketView, Relationship

TIMEFRAMES_INTERMARKET: tuple[Timeframe, ...] = (Timeframe.H1, Timeframe.D1)

Serie = Mapping[tuple[str, Timeframe], SerieDiRiferimento]


def _r(v: float | None, cifre: int = 6) -> float | None:
    return None if v is None or not math.isfinite(v) else round(v, cifre)


def t_correlazione(r: float, n: int) -> float | None:
    """t di Student della correlazione `r` su `n` coppie (infinito se |r| = 1)."""
    if n < 4:
        return None
    if abs(r) >= 1:
        # Correlazione perfetta: significativa per definizione. Prima restituiva None
        # e una relazione perfetta risultava "senza base" (trovato dai test).
        return math.copysign(math.inf, r)
    return r * math.sqrt(n - 2) / math.sqrt(1 - r * r)


def z_differenza(r1: float, n1: int, r2: float, n2: int) -> float | None:
    """Differenza fra due correlazioni indipendenti, in errori standard (Fisher)."""
    if n1 < 4 or n2 < 4:
        return None
    limite = 0.999999
    z1 = math.atanh(max(-limite, min(limite, r1)))
    z2 = math.atanh(max(-limite, min(limite, r2)))
    return (z1 - z2) / math.sqrt(1 / (n1 - 3) + 1 / (n2 - 3))


def _scarto_interquartile(valori: list[float]) -> float | None:
    if len(valori) < 4:
        return None
    v = sorted(valori)
    return v[(3 * len(v)) // 4] - v[len(v) // 4]


def _insufficiente(a: str, b: str, tf: Timeframe, n: int, nota: str) -> Relationship:
    return Relationship(
        a=a,
        b=b,
        timeframe=tf,
        current_sample=n,
        state=RelationshipState.INSUFFICIENT_DATA,
        note=nota,
    )


def relazione(a: str, b: str, tf: Timeframe, serie: Serie) -> Relationship:
    sa, sb = serie.get((a, tf)), serie.get((b, tf))
    if sa is None or sb is None:
        mancante = a if sa is None else b
        return _insufficiente(a, b, tf, 0, f"Serie {mancante} {tf} non disponibile.")
    xs, ys = allinea(
        rendimenti_per_orario(sa.tempi, sa.chiusure), rendimenti_per_orario(sb.tempi, sb.chiusure)
    )
    n = len(xs)
    corrente_n = min(par.CAMPIONE_CORRENTE, n)
    base_n = n - corrente_n
    if corrente_n < par.CAMPIONE_CORRENTE or base_n < par.CAMPIONE_BASE_MINIMO:
        return _insufficiente(
            a,
            b,
            tf,
            n,
            f"{n} rendimenti agli stessi orari: servono {par.CAMPIONE_CORRENTE} correnti e "
            f"{par.CAMPIONE_BASE_MINIMO} di base.",
        )

    corrente = pearson(xs[-corrente_n:], ys[-corrente_n:])
    bx, by = xs[:base_n], ys[:base_n]
    base = pearson(bx, by)
    finestra = par.FINESTRA_STABILITA
    mobili = [
        r
        for i in range(finestra, base_n + 1, par.PASSO_STABILITA)
        if (r := pearson(bx[i - finestra : i], by[i - finestra : i])) is not None
    ]
    comune: dict[str, object] = {
        "a": a,
        "b": b,
        "timeframe": tf,
        "current_correlation": _r(corrente),
        "current_sample": corrente_n,
        "baseline_correlation": _r(base),
        "baseline_sample": base_n,
        "baseline_dispersion": _r(_scarto_interquartile(mobili)),
    }
    if corrente is None or base is None:
        return Relationship(
            **comune,  # type: ignore[arg-type]
            state=RelationshipState.INSUFFICIENT_DATA,
            note="Varianza nulla in una delle finestre: correlazione non definita.",
        )

    t = t_correlazione(base, base_n)
    significativa = (
        t is not None and abs(t) >= par.T_SIGNIFICATIVITA and abs(base) >= par.SOGLIA_RELAZIONE
    )
    segno = 1 if base > 0 else -1
    stab = sum(1 for m in mobili if m * segno > 0) / len(mobili) if mobili else None
    z = z_differenza(corrente, corrente_n, base, base_n)
    comune.update(
        baseline_significant=significativa,
        stability=_r(stab, 4),
        difference_z=_r(z, 4),
    )

    if not significativa:
        stato = RelationshipState.NO_BASELINE
        nota = (
            f"Base {base:+.2f} su {base_n} rendimenti non significativa o trascurabile: "
            "non c'è una relazione abituale da verificare."
        )
    elif stab is not None and stab < par.SOGLIA_STABILITA:
        stato = RelationshipState.UNSTABLE
        nota = f"Base {base:+.2f}, ma con lo stesso segno solo nel {stab:.0%} delle finestre."
    elif (
        z is not None
        and corrente * base < 0
        and abs(corrente) >= par.SOGLIA_DIVERGENZA
        and abs(z) >= par.Z_DIFFERENZA
    ):
        stato = RelationshipState.DIVERGING
        nota = (
            f"Corrente {corrente:+.2f} contro base {base:+.2f} (z {z:+.1f}): segno opposto, "
            "differenza significativa."
        )
    elif z is not None and abs(corrente) < abs(base) and abs(z) >= par.Z_DIFFERENZA:
        stato = RelationshipState.WEAKENING
        nota = f"Corrente {corrente:+.2f} più debole della base {base:+.2f} (z {z:+.1f})."
    else:
        stato = RelationshipState.CONSISTENT
        nota = f"Corrente {corrente:+.2f} compatibile con la base {base:+.2f}" + (
            f" (z {z:+.1f})." if z is not None else "."
        )
    return Relationship(**comune, state=stato, note=nota)  # type: ignore[arg-type]


def analizza(serie: Serie, stati: Stati) -> IntermarketView:
    relazioni = tuple(
        relazione(a, b, tf, serie) for tf in TIMEFRAMES_INTERMARKET for a, b in RELAZIONI
    )
    conta = {s: sum(1 for r in relazioni if r.state is s) for s in RelationshipState}
    insufficienti = conta[RelationshipState.INSUFFICIENT_DATA]
    coerenti = conta[RelationshipState.CONSISTENT]
    divergenze = conta[RelationshipState.DIVERGING]
    indebolite = conta[RelationshipState.WEAKENING]
    instabili = conta[RelationshipState.UNSTABLE]

    if insufficienti * 2 > len(relazioni):
        stato = IntermarketState.INSUFFICIENT
    elif divergenze == 0 and indebolite == 0 and coerenti > 0 and coerenti >= instabili:
        stato = IntermarketState.CONSISTENT
    elif divergenze > coerenti:
        stato = IntermarketState.DIVERGENT
    else:
        stato = IntermarketState.MIXED

    copertura = round((len(relazioni) - insufficienti) / len(relazioni), 4)
    motivi: list[str] = []
    if insufficienti:
        motivi.append(f"{insufficienti} relazioni su {len(relazioni)} senza dati sufficienti.")
    non_fresche = {
        s
        for c in {x for r in RELAZIONI for x in r}
        for s in stati.get(c, {}).values()
        if s.usable and str(s) != "FRESH"
    }
    if copertura < par.COPERTURA_MINIMA:
        qualita = GlobalDataQuality.INSUFFICIENT
    elif copertura >= par.COPERTURA_BUONA and not non_fresche:
        qualita = GlobalDataQuality.GOOD
    else:
        qualita = GlobalDataQuality.DEGRADED
        if non_fresche:
            motivi.append("Serie non fresche (mercato chiuso o ora broker ignota).")

    return IntermarketView(
        state=stato,
        relationships=relazioni,
        consistent=coerenti,
        weakening=indebolite,
        diverging=divergenze,
        unstable=instabili,
        quality=DimensionQuality(
            dimension="intermarket", quality=qualita, coverage=copertura, reasons=tuple(motivi)
        ),
    )
