"""Rigenera l'albero del repository in `docs/_albero_generato.txt`.

Serve a tenere `ALBERO_REPOSITORY.md` allineato ai file reali: un albero scritto
a mano diventa falso alla prima cartella aggiunta, e un indice falso è peggio di
nessun indice.

Uso:  python docs/_albero.py
"""

from __future__ import annotations

import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
USCITA = RADICE / "docs" / "_albero_generato.txt"

ESCLUSI = {
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".next",
    ".git",
    "var",
    "logs",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".idea",
    ".vs",
}


def cammina(cartella: Path, righe: list[str], prefisso: str = "") -> None:
    voci = sorted(
        (p for p in cartella.iterdir() if p.name not in ESCLUSI and not p.name.startswith(".env")),
        # Cartelle prima dei file: l'albero si legge meglio dall'alto.
        key=lambda p: (p.is_file(), p.name.lower()),
    )
    for indice, percorso in enumerate(voci):
        ultimo = indice == len(voci) - 1
        ramo = "└── " if ultimo else "├── "
        righe.append(f"{prefisso}{ramo}{percorso.name}{'/' if percorso.is_dir() else ''}")
        if percorso.is_dir():
            cammina(percorso, righe, prefisso + ("    " if ultimo else "│   "))


def main() -> int:
    righe: list[str] = []
    cammina(RADICE, righe)
    USCITA.write_text("\n".join(righe) + "\n", encoding="utf-8")
    cartelle = sum(1 for r in righe if r.rstrip().endswith("/"))
    print(f"{USCITA.relative_to(RADICE)}: {len(righe) - cartelle} file, {cartelle} cartelle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
