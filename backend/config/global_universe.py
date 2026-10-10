"""Universo di riferimento dell'intelligence globale.

**Universo di riferimento contro whitelist operativa (ADR 0016).** La whitelist
(`RISK_ALLOWED_SYMBOLS`) è il perimetro su cui il sistema può *operare*. Per
rispondere a "cosa sta succedendo sui mercati" servono però strumenti che l'utente
non ha chiesto di operare: i 28 cross per la forza delle valute, gli indici
europei e asiatici, il petrolio. Questi entrano in un universo **di sola
osservazione**: si leggono le candele e il tick, mai specifiche per operare, mai
un `TradeIntent`.

Le grafie XM (`GOLD`, `US500Cash`) stanno solo qui: il resto dell'intelligence
globale ragiona sui nomi canonici. All'avvio ogni simbolo viene riletto dal broker
e il suo canonico confrontato con quello dichiarato (`InstrumentRegistry`): se non
coincide, lo strumento risulta mancante invece di essere usato al posto di un
altro.

Esclusi, misurato sul conto il 2026-10-04 (ADR 0016):

* **VIX e indice del dollaro**: su XM esistono solo come futures con scadenza
  (`VIX-OCT26`, `USDX-DEC26`). Il nome cambia a ogni rollover e la serie salta;
  usarli richiede una gestione del roll che oggi non c'è.
* **Obbligazioni**: solo ETF (`TLT`, `IEF`, `HYG`), con storico D1 da gennaio 2026
  (189 barre) e quotati solo in orario americano.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.enums import ReferenceGroup

#: Le otto valute di cui si misura la forza.
VALUTE: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD")


@dataclass(frozen=True, slots=True)
class StrumentoDiRiferimento:
    """Uno strumento dell'universo di osservazione."""

    canonical: str
    broker_symbol: str
    group: ReferenceGroup
    #: Valuta in cui è quotato: per il forex è la valuta di quotazione.
    quote_currency: str
    #: Per il forex, la valuta base; per gli altri strumenti, il sottostante.
    base: str


def _fx(nome: str) -> StrumentoDiRiferimento:
    return StrumentoDiRiferimento(nome, nome, ReferenceGroup.FX, nome[3:], nome[:3])


#: I 28 cross fra le otto valute, nella quotazione di mercato (verificata su XM:
#: tutti presenti con questo nome).
CROSS_FX: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "EURCHF",
    "EURAUD",
    "EURCAD",
    "EURNZD",
    "GBPJPY",
    "GBPCHF",
    "GBPAUD",
    "GBPCAD",
    "GBPNZD",
    "CHFJPY",
    "AUDJPY",
    "CADJPY",
    "NZDJPY",
    "AUDCHF",
    "CADCHF",
    "NZDCHF",
    "AUDCAD",
    "AUDNZD",
    "NZDCAD",
)

UNIVERSO: tuple[StrumentoDiRiferimento, ...] = (
    *(_fx(n) for n in CROSS_FX),
    StrumentoDiRiferimento("XAUUSD", "GOLD", ReferenceGroup.METAL, "USD", "XAU"),
    StrumentoDiRiferimento("XAGUSD", "SILVER", ReferenceGroup.METAL, "USD", "XAG"),
    StrumentoDiRiferimento("US500", "US500Cash", ReferenceGroup.EQUITY_INDEX, "USD", "US500"),
    StrumentoDiRiferimento("US100", "US100Cash", ReferenceGroup.EQUITY_INDEX, "USD", "US100"),
    StrumentoDiRiferimento("US30", "US30Cash", ReferenceGroup.EQUITY_INDEX, "USD", "US30"),
    StrumentoDiRiferimento("GER40", "GER40Cash", ReferenceGroup.EQUITY_INDEX, "EUR", "GER40"),
    StrumentoDiRiferimento("UK100", "UK100Cash", ReferenceGroup.EQUITY_INDEX, "GBP", "UK100"),
    StrumentoDiRiferimento("JP225", "JP225Cash", ReferenceGroup.EQUITY_INDEX, "JPY", "JP225"),
    StrumentoDiRiferimento("OIL", "OILCash", ReferenceGroup.ENERGY, "USD", "OIL"),
    # Fase 4.5: sesta componente del DXY ricostruito con la formula ICE. Non entra
    # nella forza delle valute (la corona non è fra le otto).
    StrumentoDiRiferimento("USDSEK", "USDSEK", ReferenceGroup.FX, "SEK", "USD"),
)

PER_CANONICO: dict[str, StrumentoDiRiferimento] = {s.canonical: s for s in UNIVERSO}


def del_gruppo(gruppo: ReferenceGroup) -> tuple[StrumentoDiRiferimento, ...]:
    return tuple(s for s in UNIVERSO if s.group is gruppo)


#: Relazioni intermarket da **misurare**. Non c'è un segno atteso scritto qui: il
#: segno e la forza della relazione si leggono dai dati (correlazione di lungo
#: periodo), perché le relazioni note non sono costanti (ANALISI §2.5).
RELAZIONI: tuple[tuple[str, str], ...] = (
    ("US500", "GER40"),
    ("US500", "JP225"),
    ("US100", "US30"),
    ("XAUUSD", "XAGUSD"),
    ("XAUUSD", "EURUSD"),
    ("XAUUSD", "US500"),
    ("AUDJPY", "US500"),
    ("USDJPY", "US500"),
    ("USDCAD", "OIL"),
)
