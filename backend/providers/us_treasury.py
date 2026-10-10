"""Curva dei rendimenti dei Titoli di Stato USA: fonte ufficiale del Tesoro.

Perché non da XM: su XM i titoli di Stato esistono solo come CFD su ETF (TLT, IEF)
con 189 barre giornaliere e prezzi, non rendimenti (ADR 0016). Il Tesoro pubblica la
curva "par yield" giornaliera in CSV, un file per anno (verificato il 2026-10-05,
anche per l'anno precedente).

Si leggono l'anno corrente e il precedente, per avere almeno un anno di storia.
Le intestazioni cambiano negli anni (nel 2025 è comparso "1.5 Month"): si leggono
per nome, e le scadenze usate (3M, 2Y, 10Y) devono esserci, altrimenti errore.

**Disponibilità**: come per il VIX, il dato del giorno `d` vale dalla mezzanotte di
New York successiva.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from backend.core.clock import Clock, SystemClock
from backend.core.enums import DataFrequency, IntendedUse, ProviderKind, SourceDomain
from backend.providers.base import ProviderError, ProviderInfo, StatoProvider
from backend.providers.cboe import NEW_YORK, disponibile_da
from backend.providers.http import Cache, scarica
from backend.schemas.external import ProviderStatus, YieldCurveHistory, YieldCurvePoint

URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{anno}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={anno}&page&_format=csv"
)
#: Colonne del Tesoro → nostre scadenze.
SCADENZE = {"3 Mo": "3M", "2 Yr": "2Y", "5 Yr": "5Y", "10 Yr": "10Y", "30 Yr": "30Y"}
NECESSARIE = ("3M", "2Y", "10Y")
GIORNI_MASSIMI_SENZA_DATI = 5

INFO = ProviderInfo(
    name="us_treasury",
    kind=ProviderKind.RATES,
    official=True,
    requires_key=False,
    free=True,
    coverage="curva par yield giornaliera dei Titoli di Stato USA (1M-30Y)",
    license_note="dati pubblici del Dipartimento del Tesoro USA",
    domain=SourceDomain.MACRO,
    frequency=DataFrequency.DAILY,
    publication_delay="curva del giorno d considerata pubblica dalla mezzanotte di New York d+1",
    intended_use=IntendedUse.STRUCTURAL_CONTEXT,
    url=URL.format(anno="AAAA"),
)


def analizza_csv(testi: list[str], *, retrieved_at: datetime) -> YieldCurveHistory:
    punti: dict[object, YieldCurvePoint] = {}
    for testo in testi:
        lettore = csv.DictReader(io.StringIO(testo))
        if lettore.fieldnames is None or "Date" not in lettore.fieldnames:
            raise ProviderError(f"Tesoro USA: intestazione inattesa {lettore.fieldnames}")
        mancanti = [
            c for c, s in SCADENZE.items() if s in NECESSARIE and c not in lettore.fieldnames
        ]
        if mancanti:
            raise ProviderError(f"Tesoro USA: colonne mancanti {mancanti}")
        for riga in lettore:
            try:
                giorno = datetime.strptime(riga["Date"], "%m/%d/%Y").date()
            except ValueError as errore:
                raise ProviderError(f"Tesoro USA: data non leggibile {riga['Date']!r}") from errore
            rendimenti = {
                nostra: float(riga[sua])
                for sua, nostra in SCADENZE.items()
                if riga.get(sua) not in (None, "", "N/A")
            }
            if all(s in rendimenti for s in NECESSARIE):
                punti[giorno] = YieldCurvePoint(
                    day=giorno, available_at=disponibile_da(giorno), yields=rendimenti
                )
    if not punti:
        raise ProviderError("Tesoro USA: nessuna osservazione completa.")
    return YieldCurveHistory(
        provider=INFO.name,
        points=tuple(punti[g] for g in sorted(punti)),  # type: ignore[type-var]
        retrieved_at=retrieved_at,
    )


class UsTreasuryRates:
    info = INFO

    def __init__(self, *, timeout: float = 20.0, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._timeout = timeout
        self._stato = StatoProvider(INFO)
        self._cache: Cache[YieldCurveHistory] = Cache(6 * 3_600, self._clock)

    def status(self) -> ProviderStatus:
        return self._stato.status()

    async def yield_curve(self) -> YieldCurveHistory:
        async def carica() -> YieldCurveHistory:
            anno = self._clock.now().astimezone(NEW_YORK).year
            testi = [
                await scarica(URL.format(anno=a), limite_secondi=self._timeout)
                for a in (anno - 1, anno)
            ]
            return analizza_csv(testi, retrieved_at=self._clock.now())

        try:
            curva = await self._cache.ottieni(carica)
        except ProviderError as errore:
            self._stato.errore(str(errore))
            raise
        ultimo = curva.points[-1]
        eta = (self._clock.now() - ultimo.available_at).total_seconds() / 86_400
        self._stato.successo(
            curva.retrieved_at, ultimo.available_at, vecchio=eta > GIORNI_MASSIMI_SENZA_DATI
        )
        return curva
