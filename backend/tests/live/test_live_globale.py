"""Intelligence globale sul conto XM reale. Solo con `RUN_LIVE_MT5=1`.

Verifica sul broker vero ciò che i test sintetici non possono: che i 37 simboli
dell'universo esistano con l'identità attesa, che nessun tick risulti "dal
futuro" con una lettura reale (lenta), che il motore sia deterministico sugli
stessi dati reali.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from backend.config.global_universe import UNIVERSO
from backend.config.settings import Settings
from backend.core.enums import QuoteQualityStatus, SeriesStatus
from backend.global_market.engine import costruisci
from backend.services.container import ApplicationContainer

pytestmark = pytest.mark.live_broker


@pytest.fixture
async def reale() -> AsyncIterator[ApplicationContainer]:
    settings = Settings()
    if settings.mt5.use_mock:
        pytest.skip("MT5_USE_MOCK=true: servono il terminale e il conto reali.")
    container = ApplicationContainer(settings)
    await container.startup()
    if container.broker is None:
        await container.shutdown()
        pytest.skip(f"Broker non disponibile: {container.broker_error}")
    try:
        yield container
    finally:
        await container.shutdown()


async def test_universo_reale_e_motore_deterministico(reale: ApplicationContainer) -> None:
    servizio = reale.require_global_intelligence()
    await reale.ensure_broker_time()
    lettura = await servizio._lettore.leggi()

    # Tutti i 37 simboli esistono su XM con l'identità dichiarata.
    mancanti = [
        i.canonical for i in lettura.ingressi if set(i.stati.values()) == {SeriesStatus.MISSING}
    ]
    assert mancanti == [], mancanti
    assert len(lettura.ingressi) == len(UNIVERSO)

    # Nessun tick "dal futuro" nonostante la lettura sequenziale.
    assert QuoteQualityStatus.INCONSISTENT not in {i.quote_status for i in lettura.ingressi}

    argomenti = {
        "as_of": lettura.as_of,
        "limite_broker": lettura.limite_broker,
        "broker_offset_seconds": lettura.broker_offset_seconds,
        "broker_time_source": lettura.broker_time_source,
    }
    s1, c1 = costruisci(lettura.ingressi, **argomenti)
    s2, c2 = costruisci(lettura.ingressi, **argomenti)
    assert s1.input_fingerprint == s2.input_fingerprint
    assert c1.model_dump_json() == c2.model_dump_json()

    assert len(c1.currency_strength.currencies) == 8
    for r in c1.intermarket.relationships:
        for v in (r.current_correlation, r.baseline_correlation):
            assert v is None or -1.0 <= v <= 1.0
    assert c1.simulated is False
