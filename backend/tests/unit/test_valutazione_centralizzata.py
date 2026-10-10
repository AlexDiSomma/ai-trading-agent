"""Garanzia strutturale: ogni numero monetario passa da `BrokerValuationService`.

Una regola scritta in un ADR si perde alla prima urgenza; una regola verificata
sull'albero dei sorgenti no. Questi test leggono il codice (AST) e falliscono se
un modulo, fuori dai pochi autorizzati:

* legge `tick_value` / `trade_tick_value` — il campo che su XM è in USD per
  metalli e indici e ha fatto sbagliare la size del 12,5% (ADR 0012);
* chiama i calcolatori del broker (`order_calc_profit`, `order_calc_margin`) o i
  loro involucri (`calculate_profit`, `calculate_margin`) — cioè calcola P/L o
  margine per conto proprio invece di chiederlo al servizio (ADR 0014);
* legge il timestamp di un tick per calcolarne l'età senza l'ora broker — il
  difetto fail-open corretto dall'ADR 0015.

Ogni elenco di autorizzati ha anche un'asserzione **positiva**: se il servizio
smettesse di contenere ciò che deve contenere, il test passerebbe a vuoto.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.config.paths import BACKEND_ROOT

#: Moduli che possono leggere `tick_value`, ognuno con il motivo.
LETTORI_TICK_VALUE: dict[str, str] = {
    "brokers/mt5/mapping.py": "traduce la struttura MT5 in SymbolInfo",
    "brokers/mt5/mock.py": "riproduce il terminale, incoerenza compresa",
    "services/persistence.py": "archivia la specifica com'è, per confronto storico",
    "valuation/service.py": "tick_value_gap: unico uso, diagnostico",
    "validazione_foundation.py": "riporta il valore dichiarato accanto a quello vero",
}

#: Moduli che possono chiamare i calcolatori del broker.
CHIAMANTI_CALCOLATORI: dict[str, str] = {
    "brokers/mt5/terminal.py": "involucro della libreria MetaTrader5",
    "brokers/mt5/mock.py": "implementazione finta",
    "brokers/mt5/connector.py": "involucro asincrono, senza formule",
    "valuation/service.py": "IL punto di calcolo",
    "validazione_foundation.py": "confronta il servizio con la chiamata grezza",
}

ATTRIBUTI_TICK_VALUE = {"tick_value", "trade_tick_value"}
CALCOLATORI = {"order_calc_profit", "order_calc_margin", "calculate_profit", "calculate_margin"}


def _sorgenti() -> list[tuple[str, ast.AST]]:
    risultato = []
    for percorso in sorted(BACKEND_ROOT.rglob("*.py")):
        relativo = percorso.relative_to(BACKEND_ROOT).as_posix()
        if relativo.startswith(("tests/", "database/migrations/")):
            continue
        risultato.append((relativo, ast.parse(percorso.read_text(encoding="utf-8"))))
    return risultato


def _usi(albero: ast.AST, nomi: set[str]) -> list[int]:
    """Righe in cui compare un attributo fra `nomi`, anche via getattr(x, "nome")."""
    righe = []
    for nodo in ast.walk(albero):
        if (isinstance(nodo, ast.Attribute) and nodo.attr in nomi) or (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id in {"getattr", "hasattr"}
            and len(nodo.args) >= 2
            and isinstance(nodo.args[1], ast.Constant)
            and nodo.args[1].value in nomi
        ):
            righe.append(nodo.lineno)
    return righe


SORGENTI = _sorgenti()


def test_i_sorgenti_sono_stati_letti() -> None:
    """Asserzione positiva: senza file il resto passerebbe a vuoto."""
    nomi = {nome for nome, _ in SORGENTI}
    assert "valuation/service.py" in nomi
    assert "portfolio/exposure.py" in nomi
    assert len(nomi) > 50


@pytest.mark.parametrize(("modulo", "albero"), SORGENTI, ids=[n for n, _ in SORGENTI])
def test_tick_value_letto_solo_dai_moduli_autorizzati(modulo: str, albero: ast.AST) -> None:
    righe = _usi(albero, ATTRIBUTI_TICK_VALUE)
    if modulo in LETTORI_TICK_VALUE:
        return
    assert not righe, (
        f"{modulo} legge tick_value alle righe {righe}. Su XM quel campo è in USD per "
        "metalli e indici: ogni valore monetario va chiesto a BrokerValuationService "
        "(ADR 0014)."
    )


@pytest.mark.parametrize(("modulo", "albero"), SORGENTI, ids=[n for n, _ in SORGENTI])
def test_calcolatori_del_broker_usati_solo_dal_servizio(modulo: str, albero: ast.AST) -> None:
    righe = _usi(albero, CALCOLATORI)
    if modulo in CHIAMANTI_CALCOLATORI:
        return
    assert not righe, (
        f"{modulo} calcola profitto o margine per conto proprio (righe {righe}). "
        "Nessun modulo deve duplicare formule di P/L, pip value o margine: usa "
        "BrokerValuationService (ADR 0014)."
    )


def test_il_servizio_contiene_davvero_i_calcoli() -> None:
    """Asserzione positiva sugli elenchi di autorizzati."""
    servizio = dict(SORGENTI)["valuation/service.py"]
    assert _usi(servizio, {"calculate_profit"})
    assert _usi(servizio, {"calculate_margin"})
    assert _usi(servizio, ATTRIBUTI_TICK_VALUE), "tick_value_gap non legge più tick_value?"


def test_gli_elenchi_non_contengono_file_inesistenti() -> None:
    """Un'eccezione per un file che non c'è più è un buco che nessuno vede."""
    for elenco in (LETTORI_TICK_VALUE, CHIAMANTI_CALCOLATORI):
        for relativo in elenco:
            assert (BACKEND_ROOT / Path(relativo)).exists(), relativo


def test_nessuna_eta_calcolata_dal_timestamp_grezzo_del_tick() -> None:
    """ADR 0015: `age_seconds(tick.time)` era il difetto fail-open.

    L'etichetta del tick è in ora broker. L'età si calcola solo in
    `market_data/broker_time.py`, con lo scarto; altrove `tick.time` non deve
    entrare in un calcolo di età.
    """
    sospetti = []
    for modulo, albero in SORGENTI:
        if modulo == "market_data/broker_time.py":
            continue
        for nodo in ast.walk(albero):
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Name)
                and nodo.func.id == "age_seconds"
                and nodo.args
                and isinstance(nodo.args[0], ast.Attribute)
                and nodo.args[0].attr == "time"
            ):
                sospetti.append(f"{modulo}:{nodo.lineno}")
    assert not sospetti, "Età calcolata da un'etichetta in ora broker: " + ", ".join(sospetti)
