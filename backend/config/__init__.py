"""Configurazione del sistema.

Punto unico di accesso: `from backend.config import get_settings`.
Nessun modulo legge `os.environ` da solo.
"""

from backend.config.paths import BACKEND_ROOT, ENV_FILE, LOGS_DIR, PROJECT_ROOT, VAR_DIR, resolve
from backend.config.risk_limits import RiskLimits
from backend.config.settings import (
    AISettings,
    ApiSettings,
    AppSettings,
    DatabaseSettings,
    LogSettings,
    MT5Settings,
    RedisSettings,
    Settings,
    get_settings,
    reset_settings_cache,
)

__all__ = [
    "BACKEND_ROOT",
    "ENV_FILE",
    "LOGS_DIR",
    "PROJECT_ROOT",
    "VAR_DIR",
    "AISettings",
    "ApiSettings",
    "AppSettings",
    "DatabaseSettings",
    "LogSettings",
    "MT5Settings",
    "RedisSettings",
    "RiskLimits",
    "Settings",
    "get_settings",
    "reset_settings_cache",
    "resolve",
]
