"""Condizioni tipizzate su prezzi reali, verificabili da un programma.

Ogni entry, invalidazione e target di un piano ombra è una `PriceCondition`:
strumento, timeframe, misura (chiusura o tocco), operatore, livello. Niente testo
libero da interpretare dopo: la fase di valutazione legge le barre successive alla
decisione e stabilisce **quando** la condizione è diventata vera, o che non lo è
mai diventata.

Le barre usate qui sono in **UTC** (`Barra`): la conversione dall'ora broker si fa
una sola volta, nel servizio che le legge (ADR 0015).

Limite dichiarato della versione 1: le condizioni sono sui **prezzi** (chiusura o
tocco di un livello) di qualsiasi strumento dell'universo osservato. Condizioni su
indicatori (RSI, forza valutaria, regime) richiederebbero di ricalcolare il motore
su ogni barra futura e sono rimandate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_RIGIDO = ConfigDict(frozen=True, extra="forbid")

TimeframeCondizione = Literal["M5", "M15", "H1", "H4", "D1"]
Misura = Literal["CLOSE", "TOUCH"]
Operatore = Literal["ABOVE", "BELOW"]


@dataclass(frozen=True, slots=True)
class Barra:
    """Barra chiusa in UTC: `inizio` è l'apertura, `fine` la chiusura."""

    inizio: datetime
    fine: datetime
    open: float
    high: float
    low: float
    close: float


#: Barre per (strumento canonico, timeframe), ordinate per tempo.
SorgenteBarre = Mapping[tuple[str, str], Sequence[Barra]]


class PriceCondition(BaseModel):
    """Una condizione su un prezzo reale, verificabile sulle barre future."""

    model_config = _RIGIDO

    subject: str = Field(
        max_length=20, description="Nome canonico dello strumento (es. XAUUSD, US500, EURUSD)."
    )
    timeframe: TimeframeCondizione = Field(description="Timeframe delle barre su cui si verifica.")
    measure: Misura = Field(
        description=(
            "CLOSE: una barra del timeframe chiude oltre il livello. "
            "TOUCH: il prezzo raggiunge il livello (massimo o minimo della barra)."
        )
    )
    operator: Operatore = Field(description="ABOVE: sopra il livello. BELOW: sotto il livello.")
    level: float = Field(gt=0, description="Livello di prezzo.")
    evidence_ids: tuple[str, ...] = Field(
        description="Evidenze del contesto che giustificano questo livello (id esatti)."
    )
    description: str = Field(min_length=5, max_length=300)

    def soddisfatta(self, b: Barra) -> bool:
        if self.measure == "CLOSE":
            return b.close > self.level if self.operator == "ABOVE" else b.close < self.level
        return b.high >= self.level if self.operator == "ABOVE" else b.low <= self.level

    def gia_vera(self, ultima_chiusura: float) -> bool:
        """Vera già all'istante della decisione, misurata sull'ultima chiusura."""
        if self.operator == "ABOVE":
            return ultima_chiusura > self.level
        return ultima_chiusura < self.level

    def etichetta(self) -> str:
        return f"{self.subject} {self.timeframe} {self.measure} {self.operator} {self.level:g}"


def prima_volta(
    condizione: PriceCondition, barre: Sequence[Barra], *, dopo: datetime, entro: datetime
) -> datetime | None:
    """Istante (chiusura della barra) in cui la condizione diventa vera per la prima volta.

    Si considerano solo barre **iniziate dopo** la decisione: la barra in corso
    all'istante della decisione conteneva prezzi già noti in parte, e mescolarli
    renderebbe ambiguo l'ordine degli eventi. Per `TOUCH` l'istante è la chiusura
    della barra: con barre a 5 minuti l'imprecisione è di 5 minuti al massimo.
    """
    for b in barre:
        if b.inizio < dopo:
            continue
        if b.inizio >= entro:
            break
        if condizione.soddisfatta(b):
            return b.fine
    return None
