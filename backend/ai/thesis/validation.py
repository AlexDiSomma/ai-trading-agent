"""Validazione di tesi e filtro: tutto ciò che non è dimostrabile si rifiuta.

Politica sulle evidenze (fase 5B):

* **affermazioni decisive** — scenario, setup, entry, stop, target, condizioni, ed
  evidenze a favore di un LONG/SHORT — con un `evidence_id` inesistente o senza
  evidenze: la tesi è **REJECTED**;
* **affermazioni non decisive** — evidenze contrarie, rischi del filtro — con id
  inesistenti: tenute ma **marcate unsupported** nel record;
* un LONG/SHORT con meno di due evidenze a favore sostenute: REJECTED.

Poi la geometria, controllata sui numeri del contesto e non sulle parole: lati di
stop e target, tipo di ingresso coerente col prezzo, distanze in ATR, RR dichiarato
uguale a quello calcolato, condizioni verificabili e non già vere alla decisione.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from backend.ai.thesis.contracts import Claim, FilterVerdict, ThesisInput, TradeThesis
from backend.shadow.conditions import PriceCondition
from backend.shadow.evidence import CatalogoEvidenze

#: Riferimenti a volume, size e leva: il piano ombra non li ha, e il modello non deve
#: ragionarci sopra.
_DIMENSIONE = re.compile(
    r"\b\d+(\.\d+)?\s*(lots?|lotti)\b|\bposition siz|\bdimensione della posizione\b"
    r"|\bleva\b|\bleverage\b|\brischiare (il )?\d+\s*%|\bpiazzare un ordine\b|\border_send\b",
    re.IGNORECASE,
)

STOP_MIN_ATR = 0.3
STOP_MAX_ATR = 8.0
TARGET_MAX_ATR = 15.0
ENTRATA_MAX_ATR = 3.0
MARKET_TOLLERANZA_ATR = 0.3
MINIMO_EVIDENZE_A_FAVORE = 2


def _sconosciuti(ids: tuple[str, ...], ammessi: set[str]) -> list[str]:
    return [i for i in ids if i not in ammessi]


def _testi_tesi(t: TradeThesis) -> list[str]:
    testi = [t.scenario.narrative, t.setup.description, t.wait_reason or ""]
    testi += [c.statement for c in (*t.supporting_evidence, *t.contradictory_evidence)]
    testi += [u.description for u in t.uncertainties]
    testi += [
        c.description
        for c in (*t.entry_conditions, *t.invalidation_conditions, *t.target_conditions)
    ]
    return testi


class _Controllo:
    def __init__(self, ammessi: set[str]) -> None:
        self.ammessi = ammessi
        self.errori: list[str] = []
        self.non_sostenuti: list[str] = []

    def decisivo(self, nome: str, ids: tuple[str, ...]) -> None:
        if not ids:
            self.errori.append(f"{nome}: nessuna evidenza citata.")
        elif bad := _sconosciuti(ids, self.ammessi):
            self.errori.append(f"{nome}: evidenze inesistenti {', '.join(bad[:4])}.")

    def accessorio(self, nome: str, c: Claim) -> bool:
        bad = _sconosciuti(c.evidence_ids, self.ammessi)
        if not c.evidence_ids or bad:
            self.non_sostenuti.append(
                f"{nome}: «{c.statement[:80]}» "
                + (f"(id inesistenti: {', '.join(bad[:3])})" if bad else "(nessuna evidenza)")
            )
            return False
        return True


def _condizioni(
    k: _Controllo,
    ruolo: str,
    condizioni: tuple[PriceCondition, ...],
    *,
    ultime_chiusure: Mapping[tuple[str, str], float],
    vietata_se_gia_vera: bool,
) -> None:
    for c in condizioni:
        k.decisivo(f"{ruolo} '{c.etichetta()}'", c.evidence_ids)
        chiusura = ultime_chiusure.get((c.subject, c.timeframe))
        if chiusura is None:
            k.errori.append(
                f"{ruolo} '{c.etichetta()}': nessuna serie di prezzi per {c.subject} "
                f"{c.timeframe}, condizione non verificabile."
            )
        elif vietata_se_gia_vera and c.gia_vera(chiusura):
            k.errori.append(
                f"{ruolo} '{c.etichetta()}': già vera alla decisione (ultima chiusura "
                f"{chiusura:g}), non descrive un evento futuro."
            )


def problemi_tesi(
    t: TradeThesis,
    ingresso: ThesisInput,
    catalogo: CatalogoEvidenze,
    *,
    ultime_chiusure: Mapping[tuple[str, str], float],
    qualita_globale: str,
) -> tuple[list[str], list[str]]:
    """(errori che rendono la tesi REJECTED, affermazioni marcate unsupported)."""
    k = _Controllo(set(ingresso.allowed_evidence_ids))
    if t.canonical != ingresso.canonical:
        k.errori.append(f"Tesi su {t.canonical}, richiesta su {ingresso.canonical}.")

    k.decisivo("Scenario", t.scenario.evidence_ids)
    k.decisivo("Setup", t.setup.evidence_ids)
    sostenute = 0
    for c in t.supporting_evidence:
        if t.decision == "WAIT":
            sostenute += k.accessorio("Evidenza a favore", c)
        elif not c.evidence_ids or _sconosciuti(c.evidence_ids, k.ammessi):
            k.decisivo(f"Evidenza a favore «{c.statement[:60]}»", c.evidence_ids)
        else:
            sostenute += 1
    for c in t.contradictory_evidence:
        k.accessorio("Evidenza contraria", c)

    if t.decision == "WAIT":
        if not t.wait_reason or len(t.wait_reason.strip()) < 10:
            k.errori.append("WAIT senza motivo.")
        if t.entry or t.stop_loss or t.take_profit:
            k.errori.append("WAIT con ingresso, stop o target: incoerente.")
        _condizioni(
            k,
            "Condizione",
            t.invalidation_conditions + t.target_conditions,
            ultime_chiusure=ultime_chiusure,
            vietata_se_gia_vera=False,
        )
    else:
        _geometria(k, t, catalogo)
        if sostenute < MINIMO_EVIDENZE_A_FAVORE:
            k.errori.append(
                f"{t.decision} con {sostenute} evidenze a favore sostenute "
                f"(minimo {MINIMO_EVIDENZE_A_FAVORE})."
            )
        if not t.invalidation_conditions:
            k.errori.append(f"{t.decision} senza condizioni di invalidazione.")
        _condizioni(
            k,
            "Condizione d'ingresso",
            t.entry_conditions,
            ultime_chiusure=ultime_chiusure,
            vietata_se_gia_vera=False,
        )
        _condizioni(
            k,
            "Invalidazione",
            t.invalidation_conditions,
            ultime_chiusure=ultime_chiusure,
            vietata_se_gia_vera=True,
        )
        _condizioni(
            k,
            "Target",
            t.target_conditions,
            ultime_chiusure=ultime_chiusure,
            vietata_se_gia_vera=True,
        )

    if t.confidence == "HIGH" and (catalogo.qualita != "GOOD" or qualita_globale != "GOOD"):
        k.errori.append("Etichetta HIGH con dati non GOOD.")
    if catalogo.note and t.decision != "WAIT" and not t.uncertainties:
        k.errori.append("Avvisi sui dati dello strumento ignorati: nessuna incertezza dichiarata.")
    vietati = sorted({m.group(0).lower() for x in _testi_tesi(t) for m in _DIMENSIONE.finditer(x)})
    if vietati:
        k.errori.append("Riferimenti a size, leva o ordini: " + ", ".join(vietati) + ".")
    return k.errori, k.non_sostenuti


def _geometria(k: _Controllo, t: TradeThesis, cat: CatalogoEvidenze) -> None:
    if t.entry is None or t.stop_loss is None or t.take_profit is None:
        k.errori.append(f"{t.decision} senza ingresso, stop o target.")
        return
    k.decisivo("Ingresso", t.entry.evidence_ids)
    k.decisivo("Stop", t.stop_loss.evidence_ids)
    k.decisivo("Target", t.take_profit.evidence_ids)
    atr = cat.atr_h1
    if not atr:
        k.errori.append("ATR H1 non disponibile: geometria non verificabile.")
        return
    s = 1 if t.decision == "LONG" else -1
    prezzo = cat.prezzo
    e = t.entry.price
    # Il tipo d'ingresso (MARKET/STOP/LIMIT) non è più del modello: lo decide il
    # codice dal livello e dal prezzo. Qui si controlla solo la coerenza dei campi.
    if t.entry.mode == "NOW":
        if e is not None and abs(e - prezzo) > MARKET_TOLLERANZA_ATR * atr:
            k.errori.append(f"Ingresso NOW a {e:g} lontano dal prezzo {prezzo:g}.")
        e = prezzo
    elif e is None:
        k.errori.append("Ingresso AT_LEVEL senza prezzo.")
        return
    if abs(e - prezzo) > ENTRATA_MAX_ATR * atr:
        k.errori.append(f"Ingresso a {abs(e - prezzo) / atr:.1f} ATR H1 dal prezzo.")
    if t.entry.valid_for_hours > t.expected_horizon_hours:
        k.errori.append("Validità dell'ingresso oltre l'orizzonte della tesi.")

    sl, tp = t.stop_loss.price, t.take_profit.price
    if s * (e - sl) <= 0:
        k.errori.append(f"Stop {sl:g} dalla parte sbagliata dell'ingresso {e:g}.")
        return
    if s * (tp - e) <= 0:
        k.errori.append(f"Target {tp:g} dalla parte sbagliata dell'ingresso {e:g}.")
        return
    rischio = abs(e - sl)
    minimo = max(STOP_MIN_ATR * atr, 3 * cat.spread_prezzo)
    if rischio < minimo:
        k.errori.append(f"Stop a {rischio / atr:.2f} ATR H1: sotto il minimo (rumore e spread).")
    if rischio > STOP_MAX_ATR * atr:
        k.errori.append(f"Stop a {rischio / atr:.1f} ATR H1: oltre {STOP_MAX_ATR}.")
    if abs(tp - e) > TARGET_MAX_ATR * atr:
        k.errori.append(f"Target a {abs(tp - e) / atr:.1f} ATR H1: oltre {TARGET_MAX_ATR}.")
    # Il RR non si controlla qui: lo calcola il codice e lo giudica il gate di rischio.


def problemi_filtro(v: FilterVerdict, ingresso: ThesisInput) -> tuple[list[str], list[str]]:
    k = _Controllo(set(ingresso.allowed_evidence_ids))
    if v.canonical != ingresso.canonical:
        k.errori.append(f"Verdetto su {v.canonical}, richiesto su {ingresso.canonical}.")
    piano = (ingresso.payload.get("quant_plan") or {}).get("decision")
    if v.plan_direction_read != piano:
        k.errori.append(
            f"Il filtro ha letto il piano come {v.plan_direction_read}, il piano è {piano}."
        )
    a_favore = sum(k.accessorio("Punto a favore", c) for c in v.points_for)
    contro = sum(k.accessorio("Punto contro", c) for c in v.points_against)
    # Il verdetto deve poggiare su almeno un punto sostenuto del proprio lato.
    if v.verdict == "APPROVE" and a_favore == 0:
        k.errori.append("APPROVE senza punti a favore sostenuti da evidenze.")
    if v.verdict == "REJECT" and contro == 0:
        k.errori.append("REJECT senza punti contro sostenuti da evidenze.")
    for c in v.key_risks:
        k.accessorio("Rischio", c)
    vietati = sorted(
        {
            m.group(0).lower()
            for c in (*v.points_for, *v.points_against, *v.key_risks)
            for m in _DIMENSIONE.finditer(c.statement)
        }
    )
    if vietati:
        k.errori.append("Riferimenti a size, leva o ordini: " + ", ".join(vietati) + ".")
    return k.errori, k.non_sostenuti
