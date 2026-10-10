"""Codici di ritorno di `order_send`/`order_check` e loro significato operativo.

Valori dalla documentazione MQL5 (`TRADE_RETCODE_*`). La classe dice cosa fare:

* `DONE`      — eseguito per intero;
* `PARTIAL`   — eseguito in parte (`DONE_REMAINDER`): si registra il volume vero;
* `RETRY`     — prezzo cambiato o requote: si può ritentare con un prezzo nuovo,
                entro lo slittamento consentito e la validità dell'approvazione;
* `REJECTED`  — rifiuto definitivo (stop non validi, fondi, mercato chiuso, …);
* `UNKNOWN`   — esito ignoto (timeout, connessione): **mai** ritentare alla cieca,
                si riconcilia con le posizioni e lo storico del broker.
"""

from __future__ import annotations

from typing import Literal

Classe = Literal["DONE", "PARTIAL", "RETRY", "REJECTED", "UNKNOWN"]

DONE = 10009
DONE_REMAINDER = 10010
PLACED = 10008
REQUOTE = 10004
REJECT = 10006
CANCEL = 10007
ERROR = 10011
TIMEOUT = 10012
INVALID = 10013
INVALID_VOLUME = 10014
INVALID_PRICE = 10015
INVALID_STOPS = 10016
TRADE_DISABLED = 10017
MARKET_CLOSED = 10018
NO_MONEY = 10019
PRICE_CHANGED = 10020
PRICE_OFF = 10021
TOO_MANY_REQUESTS = 10024
LOCKED = 10028
FROZEN = 10029
INVALID_FILL = 10030
CONNECTION = 10031
ONLY_REAL = 10032
LIMIT_ORDERS = 10033
LIMIT_VOLUME = 10034
POSITION_CLOSED = 10036
#: `order_check` restituisce 0 quando la richiesta è valida.
CHECK_OK = 0

_CLASSI: dict[int, Classe] = {
    DONE: "DONE",
    PLACED: "DONE",
    DONE_REMAINDER: "PARTIAL",
    REQUOTE: "RETRY",
    PRICE_CHANGED: "RETRY",
    PRICE_OFF: "RETRY",
    TOO_MANY_REQUESTS: "RETRY",
    TIMEOUT: "UNKNOWN",
    CONNECTION: "UNKNOWN",
    ERROR: "UNKNOWN",
}


def classe(retcode: int | None) -> Classe:
    if retcode is None:
        return "UNKNOWN"
    return _CLASSI.get(retcode, "REJECTED")


DESCRIZIONI: dict[int, str] = {
    DONE: "eseguito",
    DONE_REMAINDER: "eseguito in parte",
    REQUOTE: "requote",
    REJECT: "rifiutato dal server",
    CANCEL: "annullato",
    TIMEOUT: "timeout",
    INVALID: "richiesta non valida",
    INVALID_VOLUME: "volume non valido",
    INVALID_PRICE: "prezzo non valido",
    INVALID_STOPS: "stop non validi",
    TRADE_DISABLED: "trading disabilitato",
    MARKET_CLOSED: "mercato chiuso",
    NO_MONEY: "fondi insufficienti",
    PRICE_CHANGED: "prezzo cambiato",
    PRICE_OFF: "nessuna quotazione",
    TOO_MANY_REQUESTS: "troppe richieste",
    INVALID_FILL: "modalità di riempimento non ammessa",
    CONNECTION: "nessuna connessione col server",
    POSITION_CLOSED: "posizione già chiusa",
}


def descrivi(retcode: int | None) -> str:
    if retcode is None:
        return "nessuna risposta"
    return f"{retcode} {DESCRIZIONI.get(retcode, 'codice sconosciuto')}"
