"""Percorsi del progetto.

`PROJECT_ROOT` è calcolato dal file, non dalla directory di lavoro: il backend
deve comportarsi allo stesso modo lanciato da VS Code, da uvicorn o da pytest.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
BACKEND_ROOT: Path = PROJECT_ROOT / "backend"
ENV_FILE: Path = PROJECT_ROOT / ".env"
VAR_DIR: Path = PROJECT_ROOT / "var"
LOGS_DIR: Path = PROJECT_ROOT / "logs"


def resolve(path_like: str | Path) -> Path:
    """Rende assoluto un percorso di configurazione, relativo alla radice."""
    path = Path(path_like)
    return path if path.is_absolute() else PROJECT_ROOT / path
