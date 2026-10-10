"""Identità di uno strumento, indipendente dal broker.

Due nomi per ogni strumento, con ruoli distinti:

* **canonical** — `XAUUSD`, `US100`, `EURUSD`. È il nome con cui il resto del
  sistema ragiona: esposizione, journal, statistiche, confronti fra broker. Non
  dipende da XM.
* **broker_symbol** — `GOLD`, `US100Cash`, `EURUSD`. È la grafia da inviare al
  terminale, e la si usa **solo** quando si parla con il broker.

E quattro valute, perché misurato su XM non coincidono con quello che ci si
aspetterebbe:

* `base_currency` / `quote_currency` — la coppia **economica**: per l'oro XAU/USD,
  per un indice il codice dell'indice contro la valuta in cui è quotato. Serve a
  capire *a cosa* si è esposti;
* `profit_currency` / `margin_currency` — quelle **dichiarate dal broker** per i
  calcoli: XM dichiara GOLD come USD/USD/USD. Servono a capire *in cosa* si
  calcolano profitto e margine.

Confondere le due coppie è l'errore che faceva sembrare l'oro un'esposizione al
dollaro (ANALISI_SPECIFICA §6.5).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.core.enums import AssetClass

_FROZEN = ConfigDict(frozen=True, extra="ignore")


class ContractSpecification(BaseModel):
    """Specifica di contratto dichiarata dal broker.

    Contiene **solo** ciò che il broker dichiara. Il valore monetario di un
    movimento non c'è, di proposito: non è una specifica ma un calcolo, e i
    calcoli si chiedono al broker tramite `BrokerValuationService` (ADR 0012).
    """

    model_config = _FROZEN

    digits: int = Field(ge=0)
    point: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    contract_size: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    #: Distanza minima di SL/TP dal prezzo, in punti. Su XM vale 0 ovunque.
    stops_level_points: int = Field(ge=0)
    #: Distanza entro cui un ordine non è più modificabile, in punti.
    freeze_level_points: int = Field(ge=0)
    #: 0 vietato, 1 solo long, 2 solo short, 3 solo chiusura, 4 pieno.
    trade_mode: int = Field(ge=0, le=4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def stops_level_price(self) -> float:
        return self.stops_level_points * self.point


class Instrument(BaseModel):
    """Uno strumento, con identità canonica e dati del broker."""

    model_config = _FROZEN

    canonical: str
    broker_symbol: str
    asset_class: AssetClass
    description: str = ""

    # --- coppia economica: a cosa si è esposti ---
    base_currency: str
    quote_currency: str

    # --- valute dichiarate dal broker: in cosa si calcola ---
    profit_currency: str
    margin_currency: str

    contract: ContractSpecification

    #: Come è stato ricavato il nome canonico: `catalogo` (tabella esplicita),
    #: `regola_fx`, `regola_indice` o `dedotto`. Un nome `dedotto` non va usato
    #: per confronti fra broker: è la miglior ipotesi, non un'identità certa.
    mapping_source: str
    as_of: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_is_certain(self) -> bool:
        return self.mapping_source != "dedotto"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tradeable(self) -> bool:
        """Il broker consente di aprire posizioni (in almeno una direzione)."""
        return self.contract.trade_mode in (1, 2, 4)
