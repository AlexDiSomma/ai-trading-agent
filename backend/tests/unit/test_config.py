"""Test della configurazione.

Verificano le due sicurezze che valgono denaro: che LIVE non si attivi per
distrazione e che i limiti di rischio non siano modificabili a runtime.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.config.settings import AppSettings, MT5Settings
from backend.core.enums import TradingMode
from backend.tests.conftest import crea_settings


class TestModalitaOperativa:
    def test_default_e_paper(self) -> None:
        """Un repository appena clonato non deve poter operare con denaro vero."""
        app = AppSettings(_env_file=None)
        assert app.mode is TradingMode.PAPER
        assert app.enable_live_trading is False
        assert app.can_send_orders is False

    def test_live_senza_doppio_consenso_viene_declassato(self) -> None:
        app = AppSettings(_env_file=None, mode=TradingMode.LIVE, enable_live_trading=False)
        assert app.effective_mode is TradingMode.PAPER
        assert app.mode_was_downgraded is True
        assert app.can_send_orders is False

    def test_live_con_doppio_consenso_e_attivo(self) -> None:
        app = AppSettings(_env_file=None, mode=TradingMode.LIVE, enable_live_trading=True)
        assert app.effective_mode is TradingMode.LIVE
        assert app.can_send_orders is True

    def test_il_declassamento_compare_fra_gli_avvisi(self) -> None:
        """L'utente deve leggere che LIVE è stato ignorato, non indovinarlo."""
        settings = crea_settings(mode=TradingMode.LIVE, enable_live=False)
        avvisi = " ".join(settings.startup_warnings())
        assert "APP_MODE=LIVE ignorato" in avvisi

    def test_live_attivo_viene_dichiarato(self) -> None:
        settings = crea_settings(mode=TradingMode.LIVE, enable_live=True, use_mock=False)
        avvisi = " ".join(settings.startup_warnings())
        assert "LIVE ATTIVA" in avvisi


class TestAvvisiDiAvvio:
    def test_mock_viene_dichiarato(self) -> None:
        settings = crea_settings(use_mock=True)
        avvisi = " ".join(settings.startup_warnings())
        assert "mock" in avvisi.lower()
        assert "NON sono quelli del broker" in avvisi

    def test_credenziali_mt5_mancanti_sono_un_avviso_non_un_crash(self) -> None:
        """Il backend deve partire per poter dire cosa manca."""
        mt5 = MT5Settings(_env_file=None, enabled=True, use_mock=False)
        assert mt5.missing_credentials == ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")
        assert mt5.is_ready is False

    def test_credenziali_complete(self) -> None:
        mt5 = MT5Settings(
            _env_file=None,
            enabled=True,
            use_mock=False,
            login=1,
            password="x",
            server="XMGlobalMU-MT5 2",
        )
        assert mt5.missing_credentials == ()
        assert mt5.is_ready is True

    def test_mock_non_richiede_credenziali(self) -> None:
        mt5 = MT5Settings(_env_file=None, enabled=True, use_mock=True)
        assert mt5.missing_credentials == ()

    def test_ai_obbligatoria_senza_provider(self) -> None:
        settings = crea_settings(ai_provider="null", ai_required=True)
        avvisi = " ".join(settings.startup_warnings())
        assert "nessuna decisione" in avvisi.lower()


class TestSegreti:
    def test_le_password_non_compaiono_nella_rappresentazione(self) -> None:
        mt5 = MT5Settings(
            _env_file=None, use_mock=False, login=1, password="segretissima", server="S"
        )
        assert "segretissima" not in repr(mt5)
        assert "segretissima" not in str(mt5)
        # Recuperabile solo chiedendola esplicitamente.
        assert mt5.password is not None
        assert mt5.password.get_secret_value() == "segretissima"

    def test_il_dsn_mostrabile_non_contiene_la_password(self) -> None:
        from backend.config.settings import DatabaseSettings

        db = DatabaseSettings(_env_file=None, password="password-del-database")
        assert "password-del-database" in db.async_dsn  # quello vero la contiene
        assert "password-del-database" not in db.safe_dsn  # quello mostrabile no
        assert "@" in db.safe_dsn


class TestTimeoutMt5:
    def test_timeout_fuori_scala_rifiutato(self) -> None:
        with pytest.raises(ValidationError):
            MT5Settings(_env_file=None, use_mock=True, timeout_ms=10)
