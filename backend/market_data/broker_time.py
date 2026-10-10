"""Ora del server del broker.

MT5 non espone l'ora del server (ADR 0011), e i timestamp di tick e candele sono in
**ora del broker etichettata UTC** — XM oggi è UTC+3, dedotto dalla chiusura del
venerdì (ultimo tick 23:57:59 = 20:58 UTC, chiusura forex 21:00 UTC in ora legale
americana). Lo scarto va quindi misurato, ed è l'ingresso di ogni calcolo di età
(ADR 0015): senza scarto, l'età di un tick non è nota.

**Regola di misura: due quotazioni concordi, mai una sola.** Lo scarto si deduce
dalla differenza fra il timestamp di un tick e l'ora di sistema, arrotondata al
quarto d'ora. Un tick vecchio di un multiplo esatto di mezz'ora sarebbe
indistinguibile da un tick fresco con un altro scarto: se a stabilire lo scarto
bastasse il tick che si sta valutando, il ragionamento sarebbe circolare e quel
tick vecchio verrebbe dichiarato fresco. Con almeno due strumenti che indicano
lo stesso scarto, la coincidenza richiederebbe due tick vecchi esattamente dello
stesso multiplo di mezz'ora — e il più recente fra tutti i tick è comunque quello
che conta.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.core.clock import Clock, SystemClock, age_seconds, ensure_utc
from backend.core.logging import get_logger
from backend.schemas.market import Tick

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BrokerTimeEstimate:
    """Ora del server del broker e come ci siamo arrivati."""

    server_time: datetime | None
    #: Scarto fra ora del broker e UTC, in secondi. XM: +2 ore d'inverno, +3
    #: d'estate (ora legale americana).
    offset_seconds: float | None
    #: `MEASURED` (misura recente), `CACHED` (misura più vecchia, con l'età) o
    #: `UNAVAILABLE` (mai misurata).
    source: str
    measured_at: datetime | None = None
    measurement_age_seconds: float | None = None
    #: Quanti strumenti concordavano sullo scarto nell'ultima misura.
    corroborating_ticks: int = 0

    @property
    def reliable(self) -> bool:
        """Misurata da poco, o da meno di dodici ore.

        Dodici ore perché i cambi d'ora legale avvengono di notte: una misura più
        vecchia potrebbe stare dall'altra parte del cambio.
        """
        if self.source == "MEASURED":
            return True
        if self.source == "CACHED" and self.measurement_age_seconds is not None:
            return self.measurement_age_seconds < 12 * 3_600
        return False


class BrokerClock:
    """Stima e conserva lo scarto fra ora del broker e UTC."""

    #: Residuo massimo, dopo aver tolto lo scarto di fuso, perché un tick sia
    #: "di adesso": ritardi di rete e di stampa del broker.
    FRESCHEZZA_MASSIMA_SECONDI = 120.0

    #: Scarto massimo plausibile: i fusi orari stanno entro ±14 ore. Serve anche
    #: come ampiezza dell'incertezza quando lo scarto non è noto.
    SCARTO_MASSIMO_SECONDI = 14 * 3_600

    #: Strumenti che devono concordare per stabilire uno scarto.
    MIN_CORROBORANTI = 2

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._offset: float | None = None
        self._misurato_a: datetime | None = None
        self._corroboranti = 0

    # ------------------------------------------------------------------
    # Misura
    # ------------------------------------------------------------------
    def _candidato(self, tick: Tick, adesso: datetime) -> int | None:
        """Scarto suggerito da un tick, se il tick è compatibile con "adesso"."""
        scarto = (ensure_utc(tick.time) - adesso).total_seconds()
        if abs(scarto) > self.SCARTO_MASSIMO_SECONDI:
            return None
        residuo = abs(scarto) % 1_800
        residuo = min(residuo, 1_800 - residuo)
        if residuo > self.FRESCHEZZA_MASSIMA_SECONDI:
            return None
        return int(round(scarto / 900) * 900)

    def observe_many(self, ticks: Sequence[Tick], *, now: datetime | None = None) -> bool:
        """Stabilisce lo scarto se almeno due quotazioni concordano. True se misurato."""
        adesso = ensure_utc(now or self._clock.now())
        candidati = [c for t in ticks if (c := self._candidato(t, adesso)) is not None]
        if not candidati:
            return False
        scarto, conteggio = Counter(candidati).most_common(1)[0]
        if conteggio < self.MIN_CORROBORANTI:
            return False
        if self._offset is not None and scarto != self._offset:
            # Due strumenti concordi su uno scarto nuovo: quasi sempre un cambio
            # d'ora legale. Si accetta, ma lo si dice.
            logger.warning(
                "ora_broker.scarto_cambiato",
                precedente=self._offset,
                nuovo=scarto,
                strumenti_concordi=conteggio,
            )
        self._offset = scarto
        self._misurato_a = adesso
        self._corroboranti = conteggio
        return True

    def observe(self, tick: Tick, *, now: datetime | None = None) -> bool:
        """**Conferma** uno scarto già stabilito; non lo stabilisce mai da solo.

        Stabilire lo scarto dal tick che si sta per valutare sarebbe circolare: un
        tick vecchio esattamente di tre ore verrebbe letto come fresco con scarto
        zero. Per stabilirlo serve `observe_many`.
        """
        if self._offset is None:
            return False
        adesso = ensure_utc(now or self._clock.now())
        if self._candidato(tick, adesso) != self._offset:
            return False
        self._misurato_a = adesso
        return True

    def seed(self, offset_seconds: float, measured_at: datetime, corroborating: int = 2) -> None:
        """Imposta uno scarto noto, es. ripreso dalla cache dopo un riavvio."""
        self._offset = offset_seconds
        self._misurato_a = ensure_utc(measured_at)
        self._corroboranti = corroborating

    def reset(self) -> None:
        """Dimentica lo scarto: va rimisurato (es. dopo un cambio di server)."""
        self._offset = None
        self._misurato_a = None
        self._corroboranti = 0

    # ------------------------------------------------------------------
    # Lettura
    # ------------------------------------------------------------------
    def estimate(self, *, now: datetime | None = None) -> BrokerTimeEstimate:
        adesso = ensure_utc(now or self._clock.now())
        if self._offset is None or self._misurato_a is None:
            return BrokerTimeEstimate(server_time=None, offset_seconds=None, source="UNAVAILABLE")
        eta = age_seconds(self._misurato_a, now=adesso)
        return BrokerTimeEstimate(
            server_time=adesso + timedelta(seconds=self._offset),
            offset_seconds=self._offset,
            source="MEASURED" if eta < self.FRESCHEZZA_MASSIMA_SECONDI else "CACHED",
            measured_at=self._misurato_a,
            measurement_age_seconds=round(eta, 1),
            corroborating_ticks=self._corroboranti,
        )

    def age_bounds(
        self, broker_time: datetime, *, now: datetime | None = None
    ) -> tuple[float, float, bool]:
        """Età di un istante in ora-broker: (minima, massima, esatta).

        Con uno scarto affidabile l'età è esatta e i due estremi coincidono. Senza,
        l'età vera sta in un intervallo di ±14 ore attorno a quella grezza, e chi la
        usa deve ragionare sull'estremo che lo rende prudente: il minimo per dire
        "è vecchio", il massimo per dire "è fresco".
        """
        adesso = ensure_utc(now or self._clock.now())
        grezza = (adesso - ensure_utc(broker_time)).total_seconds()
        stima = self.estimate(now=adesso)
        if stima.reliable and stima.offset_seconds is not None:
            esatta = grezza + stima.offset_seconds
            return esatta, esatta, True
        return grezza - self.SCARTO_MASSIMO_SECONDI, grezza + self.SCARTO_MASSIMO_SECONDI, False

    def to_utc(self, broker_time: datetime) -> datetime | None:
        """Converte un istante ora-broker in UTC vero, se lo scarto è noto."""
        stima = self.estimate()
        if not stima.reliable or stima.offset_seconds is None:
            return None
        return ensure_utc(broker_time) - timedelta(seconds=stima.offset_seconds)
