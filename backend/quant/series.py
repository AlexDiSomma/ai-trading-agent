"""Primitive di calcolo su serie di prezzi.

Due scelte che valgono per tutto il livello quantitativo.

**Ogni funzione restituisce una serie lunga quanto l'ingresso, con `None` dove
il valore non è definito.** È più verboso di restituire solo i valori validi, ed
è il motivo per cui gli errori di allineamento qui non possono accadere: l'indice
`i` del risultato corrisponde sempre alla barra `i`. Nelle librerie che
restituiscono serie accorciate, un MACD costruito su una EMA già tagliata si
disallinea di qualche barra e il backtest continua a funzionare, mostrando
risultati sbagliati in modo credibile.

**Python puro, non numpy.** Non per diffidenza verso numpy — è installato e verrà
usato per vettorizzare il backtest — ma perché ogni ricorrenza qui (Wilder, EMA,
DX) deve essere leggibile riga per riga: un indicatore sbagliato avvelena in
silenzio tutto ciò che sta a valle, e non esiste un test che lo scopra se la
formula è plausibile. Su 500 barre per cinque timeframe il costo è irrilevante.

Convenzione sul seeding: le medie esponenziali partono dalla SMA dei primi
`period` valori, le medie di Wilder dalla media semplice. Sono le convenzioni di
MetaTrader e della letteratura; cambiarle sposterebbe i valori sulle serie corte.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from backend.core.errors import TradingAgentError

#: Alias per una serie con buchi: `None` dove il valore non è ancora definito.
Serie = tuple[float | None, ...]


class InsufficientDataError(TradingAgentError):
    """Serie troppo corta per il calcolo richiesto.

    Volutamente un errore e non un valore di ripiego: un indicatore calcolato su
    pochi dati restituisce un numero verosimile che non significa nulla, ed è
    peggio dell'assenza del numero.
    """

    blocks_trading = False


def _richiedi(valori: Sequence[float], minimo: int, nome: str) -> None:
    if len(valori) < minimo:
        raise InsufficientDataError(
            f"{nome}: servono almeno {minimo} valori, ricevuti {len(valori)}",
            richiesti=minimo,
            ricevuti=len(valori),
        )


def ultimo(serie: Serie) -> float | None:
    """Ultimo valore definito della serie, oppure None."""
    for valore in reversed(serie):
        if valore is not None:
            return valore
    return None


def sma(valori: Sequence[float], periodo: int) -> Serie:
    """Media mobile semplice."""
    if periodo < 1:
        raise ValueError("Il periodo deve essere almeno 1")
    risultato: list[float | None] = [None] * len(valori)
    if len(valori) < periodo:
        return tuple(risultato)
    somma = sum(valori[:periodo])
    risultato[periodo - 1] = somma / periodo
    for i in range(periodo, len(valori)):
        somma += valori[i] - valori[i - periodo]
        risultato[i] = somma / periodo
    return tuple(risultato)


def ema(valori: Sequence[float], periodo: int) -> Serie:
    """Media mobile esponenziale, alpha = 2/(periodo+1).

    Seminata con la SMA dei primi `periodo` valori: è la convenzione di
    MetaTrader. Seminare con il primo valore darebbe numeri diversi sulle prime
    decine di barre, e quelle differenze finirebbero nel backtest.
    """
    if periodo < 1:
        raise ValueError("Il periodo deve essere almeno 1")
    risultato: list[float | None] = [None] * len(valori)
    if len(valori) < periodo:
        return tuple(risultato)
    alpha = 2.0 / (periodo + 1)
    precedente = sum(valori[:periodo]) / periodo
    risultato[periodo - 1] = precedente
    for i in range(periodo, len(valori)):
        precedente = valori[i] * alpha + precedente * (1 - alpha)
        risultato[i] = precedente
    return tuple(risultato)


def wilder(valori: Serie, periodo: int) -> Serie:
    """Media di Wilder (usata da RSI, ATR, ADX).

    Accetta una serie con `None` iniziali — il true range non è definito sulla
    prima barra — e si semina sulla media dei primi `periodo` valori definiti.
    """
    if periodo < 1:
        raise ValueError("Il periodo deve essere almeno 1")
    risultato: list[float | None] = [None] * len(valori)

    definiti = [(i, v) for i, v in enumerate(valori) if v is not None]
    if len(definiti) < periodo:
        return tuple(risultato)

    indice_seed = definiti[periodo - 1][0]
    precedente = sum(v for _, v in definiti[:periodo]) / periodo
    risultato[indice_seed] = precedente

    for i in range(indice_seed + 1, len(valori)):
        valore = valori[i]
        if valore is None:
            # Un buco in mezzo alla serie non è previsto: i prezzi arrivano
            # contigui dal broker. Se accadesse, propagare l'ultimo valore
            # nasconderebbe il problema.
            raise InsufficientDataError(
                f"Valore mancante alla posizione {i}: la serie non è contigua",
                posizione=i,
            )
        precedente = (precedente * (periodo - 1) + valore) / periodo
        risultato[i] = precedente
    return tuple(risultato)


def deviazione_standard(valori: Sequence[float], periodo: int) -> Serie:
    """Deviazione standard mobile di popolazione.

    Popolazione e non campione: è la convenzione delle Bande di Bollinger. Con il
    denominatore `n-1` le bande risulterebbero leggermente più larghe e le
    soglie di compressione non corrisponderebbero a quelle di nessuna piattaforma.
    """
    risultato: list[float | None] = [None] * len(valori)
    if len(valori) < periodo:
        return tuple(risultato)
    for i in range(periodo - 1, len(valori)):
        finestra = valori[i - periodo + 1 : i + 1]
        media = sum(finestra) / periodo
        varianza = sum((v - media) ** 2 for v in finestra) / periodo
        risultato[i] = math.sqrt(varianza)
    return tuple(risultato)


def true_range(
    massimi: Sequence[float], minimi: Sequence[float], chiusure: Sequence[float]
) -> Serie:
    """True range. Non definito sulla prima barra: serve la chiusura precedente."""
    if not (len(massimi) == len(minimi) == len(chiusure)):
        raise ValueError("Le serie di massimi, minimi e chiusure devono avere pari lunghezza")
    risultato: list[float | None] = [None] * len(massimi)
    for i in range(1, len(massimi)):
        chiusura_precedente = chiusure[i - 1]
        risultato[i] = max(
            massimi[i] - minimi[i],
            abs(massimi[i] - chiusura_precedente),
            abs(minimi[i] - chiusura_precedente),
        )
    return tuple(risultato)


def rendimenti_logaritmici(chiusure: Sequence[float]) -> Serie:
    """Rendimenti logaritmici.

    Logaritmici e non percentuali perché sono additivi nel tempo: la volatilità
    su più barre si somma correttamente, e un movimento del +10% seguito da uno
    del -10% non lascia un residuo spurio.
    """
    risultato: list[float | None] = [None] * len(chiusure)
    for i in range(1, len(chiusure)):
        precedente, corrente = chiusure[i - 1], chiusure[i]
        if precedente <= 0 or corrente <= 0:
            continue
        risultato[i] = math.log(corrente / precedente)
    return tuple(risultato)


def pendenza(valori: Serie, periodo: int) -> float | None:
    """Pendenza della retta di regressione sugli ultimi `periodo` valori.

    Normalizzata sul livello medio e restituita in percentuale per barra, così da
    essere confrontabile fra EURUSD a 1.12 e US100Cash a 30.000.
    """
    recenti = [v for v in valori[-periodo:] if v is not None]
    if len(recenti) < max(2, periodo // 2):
        return None
    n = len(recenti)
    media_x = (n - 1) / 2
    media_y = sum(recenti) / n
    numeratore = sum((i - media_x) * (v - media_y) for i, v in enumerate(recenti))
    denominatore = sum((i - media_x) ** 2 for i in range(n))
    if denominatore == 0 or media_y == 0:
        return None
    return (numeratore / denominatore) / abs(media_y) * 100


def rango_percentile(valori: Serie, finestra: int) -> float | None:
    """Posizione dell'ultimo valore nella sua finestra storica, da 0 a 1.

    Serve per le compressioni di volatilità: l'ampiezza assoluta delle bande non
    dice niente, la sua posizione rispetto agli ultimi mesi sì.
    """
    recenti = [v for v in valori[-finestra:] if v is not None]
    if len(recenti) < 10:
        return None
    corrente = recenti[-1]
    sotto = sum(1 for v in recenti if v < corrente)
    return sotto / (len(recenti) - 1) if len(recenti) > 1 else None


@dataclass(frozen=True, slots=True)
class SerieChiuse:
    """Le serie OHLC delle sole candele **chiuse**, pronte per il calcolo.

    Esiste per rendere impossibile un errore specifico: passare agli indicatori
    la candela in formazione. Costruirla è l'unico modo previsto per ottenere i
    numeri da `CandleSeries`, e il costruttore scarta la barra viva.
    """

    aperture: tuple[float, ...]
    massimi: tuple[float, ...]
    minimi: tuple[float, ...]
    chiusure: tuple[float, ...]
    volumi: tuple[float, ...]
    tempi: tuple[object, ...]

    def __len__(self) -> int:
        return len(self.chiusure)

    @classmethod
    def da_candele(cls, candele: Sequence[object]) -> SerieChiuse:
        """Costruisce le serie da candele già filtrate (`CandleSeries.closed`)."""
        return cls(
            aperture=tuple(float(c.open) for c in candele),  # type: ignore[attr-defined]
            massimi=tuple(float(c.high) for c in candele),  # type: ignore[attr-defined]
            minimi=tuple(float(c.low) for c in candele),  # type: ignore[attr-defined]
            chiusure=tuple(float(c.close) for c in candele),  # type: ignore[attr-defined]
            volumi=tuple(float(c.tick_volume) for c in candele),  # type: ignore[attr-defined]
            tempi=tuple(c.time for c in candele),  # type: ignore[attr-defined]
        )

    def richiedi(self, minimo: int, nome: str = "analisi") -> None:
        _richiedi(self.chiusure, minimo, nome)

    @property
    def ultima_chiusura(self) -> float:
        return self.chiusure[-1]
