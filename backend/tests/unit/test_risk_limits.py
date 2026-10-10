"""Test dei limiti di rischio.

Sono i test più importanti del progetto in questa fase: descrivono il contratto
fra l'utente e il sistema. Se cadono, l'AI ha ottenuto spazio che l'utente non
le ha dato.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.config.risk_limits import RiskLimits


def limiti(**override: object) -> RiskLimits:
    """Limiti di prova.

    Nota sui nomi: i campi con alias (le liste) si passano con il nome completo
    della variabile d'ambiente, gli altri con il nome del campo. Il prefisso
    RISK_ vale per l'ambiente, non per i parametri del costruttore: passare
    `RISK_MAX_LEVERAGE` qui farebbe scartare il valore in silenzio.
    """
    base: dict[str, object] = {
        "_env_file": None,
        "RISK_ALLOWED_SYMBOLS": "EURUSD,XAUUSD",
        "RISK_ALLOWED_SESSIONS": "08:00-17:30",
        "RISK_ALLOWED_WEEKDAYS": "0,1,2,3,4",
    }
    base.update(override)
    return RiskLimits(**base)  # type: ignore[arg-type]


class TestImmutabilita:
    def test_un_limite_non_si_puo_cambiare_a_runtime(self) -> None:
        """Il cuore del principio: l'AI non puo' alzarsi il tetto da sola."""
        config = limiti()
        with pytest.raises(ValidationError):
            config.max_risk_per_trade_pct = 99.0  # type: ignore[misc]

    def test_la_whitelist_non_si_puo_estendere(self) -> None:
        config = limiti()
        with pytest.raises(ValidationError):
            config.allowed_symbols_raw = "EURUSD,XAUUSD,BTCUSD"  # type: ignore[misc]


class TestCoerenza:
    def test_rischio_per_trade_oltre_il_totale_e_rifiutato(self) -> None:
        with pytest.raises(ValidationError, match="max_open_risk_pct"):
            limiti(max_risk_per_trade_pct=2.0, max_open_risk_pct=1.0)

    def test_limite_giornaliero_oltre_il_settimanale_e_rifiutato(self) -> None:
        with pytest.raises(ValidationError, match="weekly_max_loss_pct"):
            limiti(daily_max_loss_pct=6.0, weekly_max_loss_pct=5.0)

    def test_settimanale_oltre_il_drawdown_e_rifiutato(self) -> None:
        with pytest.raises(ValidationError, match="max_drawdown_pct"):
            limiti(weekly_max_loss_pct=12.0, max_drawdown_pct=10.0)

    def test_whitelist_vuota_e_rifiutata(self) -> None:
        with pytest.raises(ValidationError, match="whitelist"):
            limiti(RISK_ALLOWED_SYMBOLS="")

    def test_stop_minimo_oltre_il_massimo_e_rifiutato(self) -> None:
        with pytest.raises(ValidationError, match="min_stop_distance_atr"):
            limiti(min_stop_distance_atr=3.0, max_stop_distance_atr=2.0)


class TestPerimetro:
    def test_la_grafia_del_broker_viene_conservata(self) -> None:
        """Su XM gli indici si chiamano US30Cash: US30CASH non esiste."""
        config = limiti(RISK_ALLOWED_SYMBOLS="EURUSD, GOLD, US30Cash")
        assert config.allowed_symbols == ("EURUSD", "GOLD", "US30Cash")

    def test_confronto_senza_distinzione_di_maiuscole(self) -> None:
        config = limiti(RISK_ALLOWED_SYMBOLS="EURUSD, US30Cash")
        assert config.is_symbol_allowed("us30cash") is True
        assert config.is_symbol_allowed("BTCUSD") is False
        # ...ma al broker si invia sempre la grafia ufficiale.
        assert config.canonical_symbol("us30cash") == "US30Cash"
        assert config.canonical_symbol("BTCUSD") is None

    def test_sessione_aperta_dentro_la_finestra(self) -> None:
        config = limiti()
        martedi_mattina = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)
        assert config.is_session_open(martedi_mattina) is True

    def test_sessione_chiusa_fuori_orario(self) -> None:
        config = limiti()
        martedi_notte = datetime(2026, 3, 10, 3, 0, tzinfo=UTC)
        assert config.is_session_open(martedi_notte) is False

    def test_sessione_chiusa_nel_weekend(self) -> None:
        config = limiti()
        sabato = datetime(2026, 3, 14, 10, 0, tzinfo=UTC)
        assert config.is_session_open(sabato) is False

    def test_finestra_che_scavalca_la_mezzanotte(self) -> None:
        """La sessione asiatica va da sera a mattina: non deve risultare vuota."""
        config = limiti(RISK_ALLOWED_SESSIONS="23:00-06:00", RISK_ALLOWED_WEEKDAYS="0,1,2,3,4")
        assert config.is_session_open(datetime(2026, 3, 10, 23, 30, tzinfo=UTC)) is True
        assert config.is_session_open(datetime(2026, 3, 10, 2, 0, tzinfo=UTC)) is True
        assert config.is_session_open(datetime(2026, 3, 10, 12, 0, tzinfo=UTC)) is False


class TestScalaConfidence:
    def test_sotto_la_soglia_minima_non_si_opera(self) -> None:
        config = limiti(min_confidence=0.65)
        assert config.risk_share_for_confidence(0.50) == 0.0
        assert config.risk_share_for_confidence(0.6499) == 0.0

    def test_la_quota_cresce_a_scaglioni(self) -> None:
        config = limiti()
        assert config.risk_share_for_confidence(0.70) == 0.25
        assert config.risk_share_for_confidence(0.80) == 0.50
        assert config.risk_share_for_confidence(0.90) == 0.75
        assert config.risk_share_for_confidence(0.95) == 1.00

    def test_la_quota_non_supera_mai_il_limite_dell_utente(self) -> None:
        """Anche con una scala mal configurata, il tetto resta quello dell'utente."""
        config = limiti(RISK_CONFIDENCE_LADDER="0.65:3.0")
        assert config.risk_share_for_confidence(0.99) == 1.0

    def test_confidence_perfetta_non_sblocca_piu_del_massimo(self) -> None:
        config = limiti()
        assert config.risk_share_for_confidence(1.0) == 1.0
