"""VIX da CBOE: storico giornaliero ufficiale, gratuito, senza chiave.

Perché non da XM: su XM il VIX esiste solo come future con scadenza (`VIX-OCT26`),
il cui nome cambia a ogni rollover e il cui prezzo non è l'indice (ADR 0016). CBOE
è chi calcola l'indice e pubblica lo storico in CSV (verificato il 2026-10-05:
OHLC dal 1990, ultimo dato del giorno di borsa precedente).

**Disponibilità**: la chiusura del giorno `d` si considera pubblica dalla mezzanotte
di New York successiva. È prudente (CBOE pubblica in serata) e impedisce di usare la
chiusura di oggi in un'analisi fatta oggi.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.core.clock import Clock, SystemClock
from backend.core.enums import (
    DataCategory,
    DataFrequency,
    IntendedUse,
    ProviderKind,
    SourceDomain,
)
from backend.providers.base import ProviderError, ProviderInfo, StatoProvider
from backend.providers.http import Cache, scarica
from backend.schemas.external import DailyObservation, ExternalSeries, ProviderStatus

URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
NEW_YORK = ZoneInfo("America/New_York")
#: Oltre questo numero di giorni senza un nuovo dato la serie è vecchia (weekend
#: lungo con festività compreso).
GIORNI_MASSIMI_SENZA_DATI = 5

INFO = ProviderInfo(
    name="cboe",
    kind=ProviderKind.MARKET_REFERENCE,
    official=True,
    requires_key=False,
    free=True,
    coverage="VIX giornaliero (OHLC) dal 1990, chiusura del giorno di borsa precedente",
    license_note="dati pubblicati da Cboe Global Markets; uso informativo",
    domain=SourceDomain.MARKET_REFERENCE,
    frequency=DataFrequency.DAILY,
    publication_delay="chiusura del giorno d considerata pubblica dalla mezzanotte di New York d+1",
    # Giornaliero: contesto strutturale, mai intraday.
    intended_use=IntendedUse.STRUCTURAL_CONTEXT,
    url=URL,
)


def disponibile_da(giorno: object) -> datetime:
    """Mezzanotte di New York del giorno dopo, in UTC."""
    successivo = giorno + timedelta(days=1)  # type: ignore[operator]
    return datetime.combine(successivo, time(0, 0), tzinfo=NEW_YORK).astimezone(UTC)


def analizza_csv(testo: str, *, retrieved_at: datetime, ultime: int = 800) -> ExternalSeries:
    lettore = csv.DictReader(io.StringIO(testo))
    attese = {"DATE", "OPEN", "HIGH", "LOW", "CLOSE"}
    if lettore.fieldnames is None or not attese <= set(lettore.fieldnames):
        raise ProviderError(f"CBOE: intestazione inattesa {lettore.fieldnames}")
    osservazioni = []
    for riga in lettore:
        try:
            giorno = datetime.strptime(riga["DATE"], "%m/%d/%Y").date()
            chiusura = float(riga["CLOSE"])
        except (ValueError, TypeError) as errore:
            raise ProviderError(f"CBOE: riga non leggibile {riga!r}") from errore
        osservazioni.append(
            DailyObservation(
                day=giorno,
                available_at=disponibile_da(giorno),
                open=float(riga["OPEN"]),
                high=float(riga["HIGH"]),
                low=float(riga["LOW"]),
                close=chiusura,
            )
        )
    if not osservazioni:
        raise ProviderError("CBOE: nessuna osservazione nel file.")
    osservazioni.sort(key=lambda o: o.day)
    return ExternalSeries(
        series_id="VIX",
        name="CBOE Volatility Index",
        category=DataCategory.REFERENCE_INSTRUMENT,
        unit="punti indice",
        provider=INFO.name,
        observations=tuple(osservazioni[-ultime:]),
        retrieved_at=retrieved_at,
    )


class CboeVix:
    info = INFO

    def __init__(self, *, timeout: float = 20.0, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._timeout = timeout
        self._stato = StatoProvider(INFO)
        self._cache: Cache[ExternalSeries] = Cache(6 * 3_600, self._clock)

    def status(self) -> ProviderStatus:
        return self._stato.status()

    async def daily_series(self, series_id: str) -> ExternalSeries:
        if series_id != "VIX":
            raise ProviderError(f"CBOE: serie '{series_id}' non supportata (solo VIX).")

        async def carica() -> ExternalSeries:
            testo = await scarica(URL, limite_secondi=self._timeout)
            return analizza_csv(testo, retrieved_at=self._clock.now())

        try:
            serie = await self._cache.ottieni(carica)
        except ProviderError as errore:
            self._stato.errore(str(errore))
            raise
        ultimo = serie.observations[-1]
        eta_giorni = (self._clock.now() - ultimo.available_at).total_seconds() / 86_400
        self._stato.successo(
            serie.retrieved_at, ultimo.available_at, vecchio=eta_giorni > GIORNI_MASSIMI_SENZA_DATI
        )
        return serie
