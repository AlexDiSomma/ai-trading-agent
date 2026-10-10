"""Archivio delle specifiche di contratto.

Perché un archivio e non una semplice lettura: le specifiche **cambiano**. XM può
modificare spread, volume minimo, dimensione del contratto o il valore del tick, e
quando accade una size calcolata ieri non è più valida. Senza storico, un ordine
rifiutato o una posizione di dimensione sbagliata resterebbero inspiegabili.

Due distinzioni che questo modulo tiene separate con cura.

**Specifica contro condizione.** `contract_size`, `tick_value`, `volume_step`
sono specifiche: cambiano raramente e un cambiamento è un evento da registrare.
`spread_points` è una condizione di mercato: cambia a ogni tick, e includerlo nel
confronto farebbe registrare un "cambio di specifica" ogni secondo. Per questo
l'impronta lo esclude.

**Cache contro verità.** La cache serve a non interrogare MT5 dieci volte al
secondo, non a evitare di rileggere il broker. Il TTL è un'ora e il calcolo della
size rilegge sempre: il valore del tick è l'ingresso più importante del
dimensionamento, e un valore vecchio di un'ora su un cambio di cambio è un
rischio mal misurato.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from backend.brokers.base import MarketDataReader
from backend.core.clock import Clock, SystemClock
from backend.core.enums import AssetClass
from backend.core.freshness import Fresh
from backend.core.logging import get_logger
from backend.market_data.registry import classifica_asset
from backend.schemas.market import SymbolInfo

logger = get_logger(__name__)

#: Validità della copia in cache.
TTL_SECONDI = 3_600.0

#: Campi che definiscono la **specifica** di un contratto. Lo spread non c'è di
#: proposito: è una condizione di mercato, non una specifica.
CAMPI_SPECIFICA: tuple[str, ...] = (
    "digits",
    "point",
    "contract_size",
    "tick_size",
    "tick_value",
    "volume_min",
    "volume_max",
    "volume_step",
    "stops_level_points",
    "freeze_level_points",
    "trade_mode",
    "currency_base",
    "currency_profit",
    "currency_margin",
)


@dataclass(frozen=True, slots=True)
class SpecificationChange:
    """Un campo di specifica che è cambiato fra due letture."""

    symbol: str
    field: str
    previous: object
    current: object
    detected_at: datetime

    def __str__(self) -> str:  # pragma: no cover - presentazione
        return f"{self.symbol}.{self.field}: {self.previous} -> {self.current}"


def impronta(info: SymbolInfo) -> str:
    """Impronta dei soli campi di specifica.

    Serve a riconoscere un cambiamento senza confrontare campo per campo a ogni
    lettura, e a non registrare una versione nuova a ogni variazione di spread.
    """
    pezzi = [f"{campo}={getattr(info, campo)!r}" for campo in CAMPI_SPECIFICA]
    return hashlib.sha256("|".join(pezzi).encode()).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class SpecificationRecord:
    """Specifica con i suoi metadati di archivio."""

    info: SymbolInfo
    fingerprint: str
    asset_class: AssetClass
    first_seen: datetime
    last_checked: datetime


class SpecificationRepository:
    """Specifiche di contratto, con cache, rilevazione dei cambiamenti e storico."""

    def __init__(
        self,
        broker: MarketDataReader,
        *,
        clock: Clock | None = None,
        ttl_seconds: float = TTL_SECONDI,
        persist: Callable[[SymbolInfo, AssetClass], Awaitable[None]] | None = None,
    ) -> None:
        self._broker = broker
        self._clock = clock or SystemClock()
        self._ttl = ttl_seconds
        # La persistenza è una funzione iniettata, non una dipendenza dal
        # database: così il repository si testa senza PostgreSQL e il modulo non
        # conosce lo schema.
        self._persist = persist
        self._record: dict[str, SpecificationRecord] = {}
        self._cambiamenti: list[SpecificationChange] = []

    # ------------------------------------------------------------------
    # Lettura
    # ------------------------------------------------------------------
    async def get(self, symbol: str, *, force: bool = False) -> Fresh[SymbolInfo]:
        """Specifica del simbolo, dalla cache se ancora valida.

        Con `force=True` rilegge comunque: è ciò che fa il calcolo della size.
        """
        record = self._record.get(symbol)
        adesso = self._clock.now()
        if (
            not force
            and record is not None
            and (adesso - record.last_checked).total_seconds() <= self._ttl
        ):
            return Fresh(
                value=record.info,
                as_of=record.last_checked,
                ttl_seconds=self._ttl,
                source="cache:specifiche",
            )
        info = await self.refresh(symbol)
        return Fresh(
            value=info,
            as_of=info.as_of,
            ttl_seconds=self._ttl,
            source=f"{getattr(self._broker, 'name', 'broker')}:symbol_info",
        )

    async def refresh(self, symbol: str) -> SymbolInfo:
        """Rilegge la specifica dal broker e registra eventuali cambiamenti."""
        info = await self._broker.get_symbol(symbol)
        nuova_impronta = impronta(info)
        adesso = self._clock.now()
        precedente = self._record.get(symbol)

        if precedente is not None and precedente.fingerprint != nuova_impronta:
            cambiamenti = self._confronta(precedente.info, info, adesso)
            self._cambiamenti.extend(cambiamenti)
            for cambiamento in cambiamenti:
                # WARNING e non INFO: un cambio di specifica invalida i calcoli
                # di dimensionamento fatti prima, ed è una cosa da vedere.
                logger.warning(
                    "specifiche.cambiate",
                    symbol=cambiamento.symbol,
                    campo=cambiamento.field,
                    precedente=str(cambiamento.previous),
                    corrente=str(cambiamento.current),
                )

        classe = classifica_asset(info.path, info.name)
        self._record[symbol] = SpecificationRecord(
            info=info,
            fingerprint=nuova_impronta,
            asset_class=classe,
            first_seen=precedente.first_seen if precedente else adesso,
            last_checked=adesso,
        )

        if self._persist is not None and (
            precedente is None or precedente.fingerprint != nuova_impronta
        ):
            await self._persist(info, classe)

        return info

    async def get_many(self, symbols: tuple[str, ...]) -> dict[str, SymbolInfo]:
        """Specifiche di più simboli. Le letture MT5 sono comunque serializzate."""
        risultato: dict[str, SymbolInfo] = {}
        for symbol in symbols:
            risultato[symbol] = (await self.get(symbol)).value
        return risultato

    # ------------------------------------------------------------------
    # Interrogazioni
    # ------------------------------------------------------------------
    def cached(self, symbol: str) -> SymbolInfo | None:
        record = self._record.get(symbol)
        return record.info if record else None

    def asset_class(self, symbol: str) -> AssetClass:
        record = self._record.get(symbol)
        return record.asset_class if record else AssetClass.OTHER

    def record(self, symbol: str) -> SpecificationRecord | None:
        return self._record.get(symbol)

    @property
    def changes(self) -> tuple[SpecificationChange, ...]:
        """Cambiamenti rilevati dall'avvio del processo."""
        return tuple(self._cambiamenti)

    @property
    def known_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._record))

    @staticmethod
    def _confronta(
        precedente: SymbolInfo, corrente: SymbolInfo, adesso: datetime
    ) -> list[SpecificationChange]:
        cambiamenti: list[SpecificationChange] = []
        for campo in CAMPI_SPECIFICA:
            prima, dopo = getattr(precedente, campo), getattr(corrente, campo)
            if prima != dopo:
                cambiamenti.append(
                    SpecificationChange(
                        symbol=corrente.name,
                        field=campo,
                        previous=prima,
                        current=dopo,
                        detected_at=adesso,
                    )
                )
        return cambiamenti
