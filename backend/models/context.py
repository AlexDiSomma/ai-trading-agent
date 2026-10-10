"""Persistenza del contesto di mercato.

Perché salvare i contesti e non solo le decisioni: una decisione senza il contesto
che l'ha prodotta non è ricostruibile. Fra sei mesi, davanti a una perdita, la
domanda non sarà "cosa ha deciso" ma "cosa vedeva quando ha deciso" — e se quella
risposta non è su disco, non esiste.

La forma è deliberata: **colonne per ciò su cui si interroga, JSON per il resto.**
Il payload completo serve a ricostruire; le colonne estratte servono a rispondere a
domande come "quante volte ADX era sopra 25 su H1 mentre la struttura era
ribassista" senza leggere e deserializzare migliaia di JSON.

Le candele non ci sono: lo storico prezzi è già del broker (ADR 0009). Qui c'è
cosa ne abbiamo concluso.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class AssetContextRow(Base, TimestampMixin):
    """Un `AssetMarketContext` persistito."""

    __tablename__ = "asset_market_contexts"
    __table_args__ = (
        # L'interrogazione tipica è "la storia di questo strumento in questo
        # periodo": l'indice composto la copre.
        Index("ix_asset_contexts_symbol_as_of", "symbol", "as_of"),
        Index("ix_asset_contexts_regime", "dominant_regime", "as_of"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False, default="OTHER")
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Ora del broker: può essere assente, ed è un'informazione in sé (significa
    # che non c'era una quotazione fresca da cui misurarla).
    broker_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    broker_time_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UNAVAILABLE"
    )

    # --- stato della quotazione ---
    quote_state: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Vero quando lo stato è una deduzione nostra e non un dato del broker
    #: (ADR 0011). Senza questa colonna, rileggendo lo storico non si saprebbe
    #: distinguere "il broker ha detto no" da "abbiamo concluso che fosse chiuso".
    quote_is_inference: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    spread_points: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- lettura d'insieme ---
    structural_bias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operational_bias: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alignment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dominant_regime: Mapped[str] = mapped_column(String(32), nullable=False, default="UNCERTAIN")
    dominant_structure: Mapped[str] = mapped_column(String(16), nullable=False, default="UNDEFINED")
    structure_execution_conflict: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # --- affidabilità ---
    quality: Mapped[str] = mapped_column(String(20), nullable=False, default="GOOD")
    #: Separa i contesti simulati da quelli reali: una statistica che li mescola
    #: non significa nulla.
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    usable_for_decisions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Contesto completo, per la ricostruzione.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - diagnostica
        return (
            f"<AssetContextRow {self.symbol} @ {self.as_of:%Y-%m-%d %H:%M} {self.dominant_regime}>"
        )
