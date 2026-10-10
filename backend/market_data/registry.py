"""Registro dei simboli del broker e risoluzione dei nomi.

Il problema concreto da cui nasce questo modulo: su XM l'oro si chiama `GOLD` e
gli indici `US30Cash`, non `XAUUSD` e `US30`. Un nome sbagliato in
`RISK_ALLOWED_SYMBOLS` rende lo strumento **invisibile** al sistema, e il sintomo
è un'assenza, che è la cosa più difficile da notare.

La regola che governa tutto il file: **gli alias sono suggerimenti, mai
sostituzioni.** Se l'utente scrive `XAUUSD`, il sistema non opera su `GOLD` al
suo posto: dice che `XAUUSD` non esiste e che il candidato più probabile è
`GOLD`. Sostituire in silenzio significherebbe operare su uno strumento che
l'utente non ha autorizzato — e la whitelist è il contratto.

I candidati vengono ricavati **dai simboli del broker**, non da una tabella
scritta a mano: normalizzando i nomi (`US30Cash` → `US30`) la maggior parte dei
casi si risolve da sola e il codice non invecchia quando XM aggiunge strumenti.
La tabella resta solo per i casi che nessuna normalizzazione può dedurre, come
`XAUUSD` → `GOLD`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from backend.brokers.base import MarketDataReader
from backend.core.clock import Clock, SystemClock, age_seconds
from backend.core.enums import AssetClass
from backend.core.logging import get_logger

logger = get_logger(__name__)

#: Per quanto tempo fidarsi dell'elenco dei simboli. Il catalogo di un broker
#: cambia raramente, ma cambia: XM aggiunge e ritira strumenti.
TTL_SECONDI = 6 * 3_600

#: Suffissi che i broker aggiungono ai nomi degli indici e dei contratti.
#: Rimuoverli permette di riconoscere che `US30Cash` è l'`US30` richiesto.
SUFFISSI = ("CASH", "SPOT", "IDX", "INDEX", "MICRO", "MINI", "ECN", "PRO", "RAW", "M", "Z")

#: Alias che nessuna normalizzazione può dedurre: il nome commerciale di un
#: metallo non assomiglia al suo codice ISO. Restano **suggerimenti**.
ALIAS_NOTI: dict[str, tuple[str, ...]] = {
    "XAUUSD": ("GOLD",),
    "XAGUSD": ("SILVER",),
    "XPTUSD": ("PLATINUM",),
    "XPDUSD": ("PALLADIUM",),
    "GOLD": ("XAUUSD",),
    "SILVER": ("XAGUSD",),
    "NAS100": ("US100", "USTEC", "NASDAQ"),
    "SPX500": ("US500", "SP500"),
    "SP500": ("US500", "SPX500"),
    "DJ30": ("US30",),
    "GER40": ("DE40", "DAX40"),
    "UK100": ("FTSE100",),
    "WTI": ("USOIL", "OIL"),
    "BRENT": ("UKOIL",),
}


class MatchKind(StrEnum):
    """Come il nome richiesto è stato collegato a un simbolo del broker."""

    EXACT = "EXACT"
    #: Differisce solo per maiuscole: utilizzabile, ma va corretto in `.env`
    #: perché al broker si invia sempre la sua grafia.
    CASE_INSENSITIVE = "CASE_INSENSITIVE"
    #: Esiste un candidato probabile, ma **non** viene usato automaticamente.
    ALIAS_SUGGESTION = "ALIAS_SUGGESTION"
    #: Più candidati equivalenti: deve scegliere l'utente.
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"

    @property
    def usable(self) -> bool:
        """Solo una corrispondenza certa autorizza a operare sul simbolo."""
        return self in (MatchKind.EXACT, MatchKind.CASE_INSENSITIVE)


@dataclass(frozen=True, slots=True)
class SymbolResolution:
    """Esito della risoluzione di un nome."""

    requested: str
    match: MatchKind
    #: Grafia da inviare al broker. `None` se la corrispondenza non è utilizzabile.
    canonical: str | None = None
    candidates: tuple[str, ...] = ()
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.match.usable and self.canonical is not None


@dataclass(frozen=True, slots=True)
class WhitelistIssue:
    """Problema su una voce della whitelist dell'utente."""

    requested: str
    severity: str  # "errore" oppure "avviso"
    message: str
    suggestions: tuple[str, ...] = ()


def normalizza(nome: str) -> str:
    """Forma confrontabile di un nome: maiuscolo, senza separatori né suffissi."""
    base = re.sub(r"[^A-Z0-9]", "", nome.upper())
    for suffisso in SUFFISSI:
        if len(base) > len(suffisso) + 2 and base.endswith(suffisso):
            return base[: -len(suffisso)]
    return base


def classifica_asset(percorso: str, nome: str) -> AssetClass:
    """Classe di attività dal percorso di categoria dichiarato dal broker.

    Il percorso viene dal broker (`Derivatives\\Spot Metals\\GOLD`), quindi è un
    dato e non una congettura. Serve perché le valute dichiarate non bastano: XM
    dichiara `GOLD` come USD/USD/USD, e un raggruppamento per sola valuta
    metterebbe oro e indici americani nello stesso secchiello.

    Il nome è solo un ripiego per i broker che non compilano il percorso.
    """
    p = percorso.upper()
    # L'ordine conta: su XM la categoria delle criptovalute si chiama
    # "Cryptocurrencies", che contiene "CURRENC". Controllare prima il forex
    # classificherebbe Bitcoin come cambio — difetto trovato dai test il
    # 2026-10-04.
    if "CRYPTO" in p:
        return AssetClass.CRYPTO
    if "METAL" in p:
        return AssetClass.METAL
    if "FOREX" in p or "CURRENC" in p:
        return AssetClass.FX
    if "INDIC" in p or "INDEX" in p:
        return AssetClass.INDEX
    if "ENERG" in p or "OIL" in p or "GAS" in p:
        return AssetClass.ENERGY
    if "STOCK" in p or "SHARE" in p or "EQUIT" in p or "ETF" in p:
        return AssetClass.STOCK

    # Ripiego sul nome, dichiarato come tale.
    n = nome.upper()
    if n in ("GOLD", "SILVER", "PLATINUM", "PALLADIUM") or n.startswith(("XAU", "XAG")):
        return AssetClass.METAL
    if re.fullmatch(r"[A-Z]{6}", n):
        return AssetClass.FX
    if any(k in n for k in ("US30", "US100", "US500", "DE40", "UK100", "JP225", "NAS", "SPX")):
        return AssetClass.INDEX
    if any(k in n for k in ("OIL", "WTI", "BRENT", "NGAS")):
        return AssetClass.ENERGY
    if any(k in n for k in ("BTC", "ETH", "XRP", "SOL", "DOGE")):
        return AssetClass.CRYPTO
    return AssetClass.OTHER


class SymbolRegistry:
    """Catalogo dei simboli del broker, con risoluzione dei nomi.

    Il catalogo viene caricato una volta e tenuto in cache: su questo conto sono
    1639 simboli, e rileggerli a ogni risoluzione sarebbe una chiamata MT5
    inutile per un dato che cambia una volta al mese.
    """

    def __init__(
        self,
        broker: MarketDataReader,
        *,
        clock: Clock | None = None,
        ttl_seconds: float = TTL_SECONDI,
    ) -> None:
        self._broker = broker
        self._clock = clock or SystemClock()
        self._ttl = ttl_seconds
        self._nomi: tuple[str, ...] = ()
        self._per_normalizzato: dict[str, list[str]] = {}
        self._per_minuscolo: dict[str, str] = {}
        self._caricato_a: datetime | None = None

    # ------------------------------------------------------------------
    # Caricamento
    # ------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._caricato_a is not None

    @property
    def loaded_at(self) -> datetime | None:
        return self._caricato_a

    @property
    def is_stale(self) -> bool:
        if self._caricato_a is None:
            return True
        return age_seconds(self._caricato_a, now=self._clock.now()) > self._ttl

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._nomi

    async def refresh(self) -> int:
        """Rilegge l'elenco dei simboli dal broker. Restituisce quanti ne ha trovati."""
        nomi = await self._broker.list_symbols()
        self._nomi = tuple(nomi)
        self._per_minuscolo = {nome.lower(): nome for nome in nomi}
        indice: dict[str, list[str]] = {}
        for nome in nomi:
            indice.setdefault(normalizza(nome), []).append(nome)
        self._per_normalizzato = indice
        self._caricato_a = self._clock.now()
        logger.info("registro.caricato", simboli=len(nomi))
        return len(nomi)

    async def ensure_loaded(self) -> None:
        """Carica il catalogo se assente o scaduto."""
        if self.is_stale:
            await self.refresh()

    # ------------------------------------------------------------------
    # Risoluzione
    # ------------------------------------------------------------------
    def resolve(self, nome: str) -> SymbolResolution:
        """Collega un nome a un simbolo del broker, senza mai sostituirlo in silenzio."""
        richiesto = nome.strip()
        if not self.loaded:
            return SymbolResolution(
                requested=richiesto,
                match=MatchKind.NOT_FOUND,
                note="Catalogo dei simboli non caricato: chiamare refresh().",
            )

        if richiesto in self._nomi:
            return SymbolResolution(richiesto, MatchKind.EXACT, canonical=richiesto)

        minuscolo = richiesto.lower()
        if minuscolo in self._per_minuscolo:
            canonico = self._per_minuscolo[minuscolo]
            return SymbolResolution(
                requested=richiesto,
                match=MatchKind.CASE_INSENSITIVE,
                canonical=canonico,
                note=(
                    f"Il broker lo scrive '{canonico}'. Funziona, ma conviene "
                    "correggere la grafia in .env: al broker si invia sempre la sua."
                ),
            )

        candidati = self._candidati(richiesto)
        if len(candidati) == 1:
            return SymbolResolution(
                requested=richiesto,
                match=MatchKind.ALIAS_SUGGESTION,
                candidates=candidati,
                note=(
                    f"'{richiesto}' non esiste su questo conto. Il candidato più "
                    f"probabile è '{candidati[0]}', ma non viene usato automaticamente: "
                    "correggi RISK_ALLOWED_SYMBOLS se è quello che intendevi."
                ),
            )
        if len(candidati) > 1:
            return SymbolResolution(
                requested=richiesto,
                match=MatchKind.AMBIGUOUS,
                candidates=candidati,
                note=(
                    f"'{richiesto}' non esiste e ci sono più candidati: "
                    f"{', '.join(candidati)}. Deve scegliere l'utente."
                ),
            )
        return SymbolResolution(
            requested=richiesto,
            match=MatchKind.NOT_FOUND,
            note=f"'{richiesto}' non esiste su questo conto e non ha candidati simili.",
        )

    def _candidati(self, nome: str) -> tuple[str, ...]:
        """Candidati plausibili, ricavati dai simboli del broker."""
        trovati: list[str] = []

        normalizzato = normalizza(nome)
        trovati.extend(self._per_normalizzato.get(normalizzato, []))

        for alias in ALIAS_NOTI.get(nome.upper(), ()):
            trovati.extend(self._per_normalizzato.get(normalizza(alias), []))

        # Un simbolo del broker il cui nome normalizzato inizia con quello
        # richiesto: copre `US500` → `US500Cash` anche senza suffisso noto.
        if not trovati and len(normalizzato) >= 4:
            for chiave, nomi in self._per_normalizzato.items():
                if chiave.startswith(normalizzato) or normalizzato.startswith(chiave):
                    trovati.extend(nomi)

        # Ordine stabile e senza duplicati: i nomi più corti prima, perché
        # `US500Cash` è un candidato migliore di `US500Cash-DEC26`.
        unici = sorted(set(trovati), key=lambda n: (len(n), n))
        return tuple(unici[:5])

    def search(self, testo: str, limite: int = 50) -> tuple[str, ...]:
        """Ricerca libera, per la scoperta dei nomi dalla dashboard."""
        cercato = testo.lower()
        return tuple(sorted(n for n in self._nomi if cercato in n.lower())[:limite])

    # ------------------------------------------------------------------
    # Controllo della whitelist
    # ------------------------------------------------------------------
    def check_whitelist(self, whitelist: tuple[str, ...]) -> tuple[WhitelistIssue, ...]:
        """Verifica la whitelist dell'utente contro il catalogo del broker.

        È il controllo che trasforma un'assenza silenziosa in un messaggio: una
        voce sbagliata in `.env` non produce errori, produce uno strumento che il
        sistema non guarda mai. Il risultato finisce in System Health.
        """
        problemi: list[WhitelistIssue] = []
        for voce in whitelist:
            esito = self.resolve(voce)
            if esito.match is MatchKind.EXACT:
                continue
            if esito.match is MatchKind.CASE_INSENSITIVE:
                problemi.append(
                    WhitelistIssue(
                        requested=voce,
                        severity="avviso",
                        message=esito.note,
                        suggestions=(esito.canonical,) if esito.canonical else (),
                    )
                )
                continue
            problemi.append(
                WhitelistIssue(
                    requested=voce,
                    severity="errore",
                    message=esito.note,
                    suggestions=esito.candidates,
                )
            )
        return tuple(problemi)
