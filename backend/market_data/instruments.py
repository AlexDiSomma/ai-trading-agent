"""Registro degli strumenti: unico punto in cui un nome XM diventa un'identità.

Il resto dell'applicazione ragiona su `Instrument` (nome canonico, classe,
valute, specifica) e non sui nomi del broker. `GOLD`, `US100Cash`, `US30Cash`
compaiono solo qui e nel connettore.

Come si ricava il nome canonico — in ordine, e con la fonte dichiarata in
`mapping_source`:

1. **catalogo** — una tabella esplicita per i casi che nessuna regola deduce:
   `GOLD` → `XAUUSD`. È una mappatura dichiarata e rivista, non un'euristica;
2. **regola_fx** — sei lettere maiuscole che coincidono con le valute dichiarate
   dal broker: `EURUSD` resta `EURUSD`, con base e quotazione dalle lettere;
3. **regola_indice** — la classe è `INDEX` (dal percorso del broker): si toglie
   il suffisso commerciale, `US100Cash` → `US100`;
4. **dedotto** — tutto il resto: nome normalizzato, valute del broker, e il flag
   `canonical_is_certain` a falso.

Differenza dai suggerimenti di `registry.py`: là si cerca di indovinare cosa
intendeva l'utente con un nome che **non esiste**, e il risultato non viene mai
usato da solo. Qui si dà un nome stabile a un simbolo che **esiste** sul conto.
La direzione è opposta, e solo questa è deterministica.
"""

from __future__ import annotations

import re

from backend.core.clock import Clock, SystemClock
from backend.core.enums import AssetClass
from backend.core.errors import TradingAgentError
from backend.market_data.registry import classifica_asset, normalizza
from backend.market_data.specifications import SpecificationRepository
from backend.schemas.instrument import ContractSpecification, Instrument
from backend.schemas.market import SymbolInfo

#: Simboli del broker il cui nome canonico non è deducibile da una regola.
#: Chiave: nome normalizzato del simbolo XM. Valore: (canonico, base, quotazione).
CATALOGO: dict[str, tuple[str, str, str]] = {
    "GOLD": ("XAUUSD", "XAU", "USD"),
    "SILVER": ("XAGUSD", "XAG", "USD"),
    "PLATINUM": ("XPTUSD", "XPT", "USD"),
    "PALLADIUM": ("XPDUSD", "XPD", "USD"),
}

_FX = re.compile(r"^[A-Z]{6}$")


class InstrumentMappingError(TradingAgentError):
    """Un nome canonico corrisponde a più simboli del broker, o a nessuno."""

    blocks_trading = False


def costruisci_strumento(info: SymbolInfo) -> Instrument:
    """Identità di uno strumento a partire dalla specifica del broker."""
    classe = classifica_asset(info.path, info.name)
    chiave = normalizza(info.name)

    if chiave in CATALOGO:
        canonico, base, quotazione = CATALOGO[chiave]
        fonte = "catalogo"
    elif (
        _FX.match(chiave)
        and classe in (AssetClass.FX, AssetClass.OTHER)
        and chiave[:3] == (info.currency_base or chiave[:3])
        and chiave[3:] == (info.currency_profit or chiave[3:])
    ):
        canonico, base, quotazione, fonte = chiave, chiave[:3], chiave[3:], "regola_fx"
    elif classe is AssetClass.INDEX:
        canonico = chiave
        base, quotazione, fonte = chiave, info.currency_profit or "?", "regola_indice"
    else:
        canonico = chiave
        base = info.currency_base or "?"
        quotazione = info.currency_profit or "?"
        fonte = "dedotto"

    return Instrument(
        canonical=canonico,
        broker_symbol=info.name,
        asset_class=classe,
        description=info.description,
        base_currency=base,
        quote_currency=quotazione,
        profit_currency=info.currency_profit or quotazione,
        margin_currency=info.currency_margin or info.currency_base or base,
        contract=ContractSpecification(
            digits=info.digits,
            point=info.point,
            tick_size=info.tick_size,
            contract_size=info.contract_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            stops_level_points=info.stops_level_points,
            freeze_level_points=info.freeze_level_points,
            trade_mode=info.trade_mode,
        ),
        mapping_source=fonte,
        as_of=info.as_of,
    )


class InstrumentRegistry:
    """Strumenti noti, accessibili per nome del broker o per nome canonico."""

    def __init__(
        self, specifications: SpecificationRepository, *, clock: Clock | None = None
    ) -> None:
        self._specifiche = specifications
        self._clock = clock or SystemClock()
        self._per_broker: dict[str, Instrument] = {}

    async def by_broker_symbol(self, symbol: str) -> Instrument:
        """Strumento a partire dalla grafia del broker. Specifica sempre aggiornata."""
        info = (await self._specifiche.get(symbol)).value
        strumento = costruisci_strumento(info)
        self._per_broker[strumento.broker_symbol] = strumento
        return strumento

    def by_canonical(self, canonical: str) -> Instrument:
        """Strumento a partire dal nome canonico, fra quelli già incontrati.

        Solleva se il nome corrisponde a più simboli del broker: scegliere al
        posto dell'utente significherebbe operare su uno strumento che non ha
        indicato.
        """
        trovati = [s for s in self._per_broker.values() if s.canonical == canonical.upper()]
        if not trovati:
            raise InstrumentMappingError(
                f"Nessuno strumento noto con nome canonico '{canonical}'.", canonical=canonical
            )
        if len(trovati) > 1:
            raise InstrumentMappingError(
                f"Il nome canonico '{canonical}' corrisponde a più simboli del broker: "
                + ", ".join(s.broker_symbol for s in trovati),
                canonical=canonical,
            )
        return trovati[0]

    @property
    def known(self) -> tuple[Instrument, ...]:
        return tuple(self._per_broker.values())
