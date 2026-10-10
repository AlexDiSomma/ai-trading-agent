"""La garanzia di sola lettura, verificata sul codice sorgente.

Questo è un test insolito e deliberato: legge i file del package `brokers/` e
verifica che non contengano chiamate di scrittura verso MetaTrader 5.

Perché un controllo sul sorgente invece di uno sul comportamento: un flag
`read_only=True` si può invertire con una riga, e un test che verifica "se il
flag è spento non invia ordini" non dice nulla su cosa accade quando qualcuno lo
accende. Qui l'affermazione è più forte: **il codice per inviare ordini non
esiste**. Il giorno in cui la fase 11 lo introdurrà, questo test fallirà, e
quel fallimento è il punto: obbliga a dichiarare l'intenzione in modo
consapevole, aggiornando la lista dei moduli autorizzati.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.brokers.base import AccountDataReader, MarketDataReader, OrderWriter
from backend.brokers.mt5.connector import MT5Connector
from backend.config.paths import BACKEND_ROOT

#: Funzioni dell'API MetaTrader 5 che **cambiano** qualcosa, o che sottopongono
#: al server una richiesta d'ordine.
#:
#: Correzione del 2026-10-04: `order_calc_margin` e `order_calc_profit` erano
#: inizialmente in questa lista per prudenza, ed era un errore. Sono
#: **calcolatori**: non creano ordini, non inviano nulla, non modificano niente.
#: Vietarli ha una conseguenza concreta e grave — senza `order_calc_profit` il
#: valore monetario di un movimento di prezzo va derivato da `trade_tick_value`,
#: che su questo conto XM non e' nella valuta del conto per metalli e indici, e
#: ogni numero monetario su quegli strumenti risulterebbe sbagliato del 12,5%.
#: Vedi docs/adr/0012-valore-monetario-dal-broker.md.
#:
#: `order_check` resta vietato: sottopone al server una richiesta d'ordine da
#: validare e appartiene al percorso di pre-trade della fase 11.
FUNZIONI_DI_SCRITTURA = (
    "order_send",
    "order_check",
    "position_close",
    "order_modify",
)

#: Calcolatori ammessi: leggono e calcolano, non agiscono.
CALCOLATORI_AMMESSI = ("order_calc_profit", "order_calc_margin")

#: Moduli autorizzati a contenere quelle chiamate. Fase 11 (2026-10-06, ADR 0025):
#: solo il terminale di trading (unico punto che parla con `MetaTrader5`) e il
#: gateway (unico chiamante del terminale, dietro tutti i gate LIVE). Nessun altro.
MODULI_AUTORIZZATI: frozenset[str] = frozenset(
    {"execution/mt5_terminal.py", "execution/gateway.py"}
)


def _sorgenti_backend() -> list[Path]:
    """Tutto il backend tranne i test: la garanzia vale ovunque, non solo in brokers/."""
    return [p for p in _file_python(BACKEND_ROOT) if "tests" not in p.parts]


def _file_python(cartella: Path) -> list[Path]:
    return sorted(p for p in cartella.rglob("*.py") if "__pycache__" not in p.parts)


def _superficie_api() -> list[tuple[str, str]]:
    """Coppie (metodo, percorso) esposte dall'applicazione, dallo schema OpenAPI.

    Lo schema e' la vista autorevole: `app.routes` non espande i router inclusi
    (vedi il docstring del test che lo usa).
    """
    from backend.main import create_app
    from backend.tests.conftest import crea_settings

    schema = create_app(crea_settings()).openapi()
    return [
        (metodo.lower(), percorso)
        for percorso, operazioni in schema.get("paths", {}).items()
        for metodo in operazioni
        if metodo.lower() in {"get", "post", "put", "patch", "delete"}
    ]


class TestSolaLetturaPerCostruzione:
    @pytest.mark.parametrize("percorso", _sorgenti_backend(), ids=lambda p: p.name)
    def test_nessuna_chiamata_di_scrittura_fuori_dai_moduli_autorizzati(
        self, percorso: Path
    ) -> None:
        sorgente = percorso.read_text(encoding="utf-8")
        albero = ast.parse(sorgente)

        chiamate: list[str] = []
        for nodo in ast.walk(albero):
            # Si cercano gli *attributi chiamati* (`mt5.order_send(...)`) e non
            # le occorrenze testuali: così i docstring che spiegano perché
            # `order_send` non c'è non fanno fallire il test.
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr in FUNZIONI_DI_SCRITTURA
            ):
                chiamate.append(nodo.func.attr)

        relativo = percorso.relative_to(BACKEND_ROOT).as_posix()
        if chiamate and relativo not in MODULI_AUTORIZZATI:
            pytest.fail(
                f"{relativo} contiene chiamate di scrittura verso MT5 ({set(chiamate)}). "
                "Solo execution/mt5_terminal.py e execution/gateway.py possono. Se questa è una "
                "modifica voluta, aggiungi il file a MODULI_AUTORIZZATI e documenta "
                "la decisione in DECISIONI.md."
            )

    def test_il_connettore_non_soddisfa_il_protocollo_di_scrittura(self) -> None:
        """La garanzia è nel tipo, non in un commento."""
        connettore_qualunque: object = MT5Connector
        assert not isinstance(connettore_qualunque, OrderWriter)
        for metodo in ("send_order", "modify_position", "close_position"):
            assert not hasattr(MT5Connector, metodo), (
                f"MT5Connector ha acquisito il metodo '{metodo}': "
                "il connettore non deve poter scrivere."
            )

    def test_il_connettore_soddisfa_i_protocolli_di_lettura(self) -> None:
        for metodo in ("get_symbol", "get_tick", "get_candles", "list_symbols"):
            assert hasattr(MT5Connector, metodo)
        for metodo in ("get_account", "get_positions", "get_pending_orders", "get_portfolio"):
            assert hasattr(MT5Connector, metodo)
        # I protocolli sono runtime_checkable: l'asserzione vale sull'istanza,
        # qui si verifica almeno che le firme esistano sulla classe.
        assert MarketDataReader is not None
        assert AccountDataReader is not None

    def test_il_terminale_finto_non_offre_scrittura(self) -> None:
        """Il mock non deve diventare la scorciatoia per rientrare dalla finestra."""
        from backend.brokers.mt5.mock import FakeMT5Terminal

        for funzione in FUNZIONI_DI_SCRITTURA:
            assert not hasattr(FakeMT5Terminal, funzione)

    def test_i_calcolatori_sono_ammessi_e_presenti(self) -> None:
        """Distinzione fra calcolare e agire.

        `order_calc_profit` deve esserci: e' la sola fonte affidabile del valore
        monetario di un movimento di prezzo. Se un giorno venisse rimosso per
        eccesso di prudenza, il dimensionamento tornerebbe a derivarlo da
        `trade_tick_value` e sbaglierebbe del 12,5% su metalli e indici.
        """
        from backend.brokers.mt5.mock import FakeMT5Terminal
        from backend.brokers.mt5.terminal import MT5Terminal, RealMT5Terminal

        assert hasattr(MT5Terminal, "order_calc_profit")
        assert hasattr(RealMT5Terminal, "order_calc_profit")
        assert hasattr(FakeMT5Terminal, "order_calc_profit")
        # ...ma nessun calcolatore deve diventare un modo di inviare ordini.
        assert not hasattr(FakeMT5Terminal, "order_send")

    def test_nessun_endpoint_http_di_scrittura_sul_conto(self) -> None:
        """Nemmeno l'API deve offrire una via per aprire posizioni.

        La superficie pubblica viene letta dallo **schema OpenAPI** e non da
        `app.routes`. Motivo, scoperto il 2026-10-04: FastAPI avvolge i router
        inclusi in un oggetto contenitore, quindi `app.routes` non li espande e
        questo test passava **a vuoto** — non vedeva nessuna rotta e concludeva
        che non ce n'erano di pericolose.

        Da qui la regola: ogni asserzione negativa va accompagnata da una
        positiva. Il controllo su `rotte_di_lettura` fa fallire il test se
        l'elenco e' vuoto, cosi' non puo' piu' passare per il motivo sbagliato.
        """
        operazioni = _superficie_api()

        rotte_di_lettura = [p for metodo, p in operazioni if metodo == "get"]
        assert "/api/account" in rotte_di_lettura, (
            "Lo schema OpenAPI non contiene le rotte attese: il controllo non sta "
            "guardando niente e passerebbe per il motivo sbagliato."
        )

        pericolose = [
            f"{metodo.upper()} {percorso}"
            for metodo, percorso in operazioni
            if metodo in {"post", "put", "patch", "delete"}
            and ("/account" in percorso or "/order" in percorso or "/position" in percorso)
        ]
        assert pericolose == [], "Esistono endpoint di scrittura sul conto: " + ", ".join(
            pericolose
        )


class TestIlRiskEngineNonDipendeDallAi:
    """L'indipendenza fra rischio e AI, verificata sugli import.

    Il Risk Engine deve poter essere letto, capito e testato senza sapere che
    esiste un modello linguistico. Se un giorno `risk/` importasse `ai/`, un
    cambio di prompt potrebbe cambiare un limite: questo test lo impedisce
    adesso, mentre i due package sono ancora vuoti e la regola è facile da
    rispettare.
    """

    @pytest.mark.parametrize("cartella", ["risk", "portfolio"], ids=["risk", "portfolio"])
    def test_nessun_import_di_ai(self, cartella: str) -> None:
        for percorso in _file_python(BACKEND_ROOT / cartella):
            albero = ast.parse(percorso.read_text(encoding="utf-8"))
            for nodo in ast.walk(albero):
                if isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith(
                    "backend.ai"
                ):
                    pytest.fail(
                        f"{percorso.name} importa da backend.ai: il Risk Engine deve "
                        "restare indipendente dall'AI."
                    )
                if isinstance(nodo, ast.Import):
                    for alias in nodo.names:
                        if alias.name.startswith("backend.ai"):
                            pytest.fail(f"{percorso.name} importa {alias.name}.")

    def test_l_ai_non_importa_il_risk_engine(self) -> None:
        """Simmetrico: l'agente non deve poter leggere né toccare i limiti."""
        for percorso in _file_python(BACKEND_ROOT / "ai"):
            albero = ast.parse(percorso.read_text(encoding="utf-8"))
            for nodo in ast.walk(albero):
                if isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith(
                    "backend.risk"
                ):
                    pytest.fail(
                        f"{percorso.name} importa da backend.risk: l'AI riceve il contesto "
                        "di rischio come dato di sola lettura, non il motore."
                    )
