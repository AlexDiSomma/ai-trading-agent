"""Enum di dominio.

Stanno tutti qui perche' attraversano piu' bounded context (il regime serve allo
scanner, all'AI, al journal e al backtest): duplicarli porterebbe a stringhe
divergenti fra moduli e a confronti che falliscono in silenzio.
"""

from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    """Quanto in profondita' il sistema e' autorizzato ad agire."""

    OBSERVE = "OBSERVE"  # solo analisi, nessun piano operativo
    SIGNAL = "SIGNAL"  # genera TradePlan, non esegue
    PAPER = "PAPER"  # simula l'esecuzione
    LIVE = "LIVE"  # invia ordini reali


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Timeframe(StrEnum):
    """Timeframe operativi.

    Il valore e' la sigla MT5 perche' e' anche quella che l'utente legge nella
    dashboard; la conversione verso le costanti numeriche di MetaTrader 5 sta in
    `brokers/mt5/mapping.py`, cosi' il dominio non dipende dal broker.
    """

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    @property
    def minutes(self) -> int:
        return _TIMEFRAME_MINUTES[self]

    @property
    def role(self) -> str:
        """A cosa serve il timeframe nella gerarchia multi-timeframe.

        Serve a impedire la decisione presa solo sul grafico veloce: un setup
        senza struttura superiore che lo sostenga viene scartato.
        """
        return _TIMEFRAME_ROLE[self]


_TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
    Timeframe.W1: 10080,
    Timeframe.MN1: 43200,
}

_TIMEFRAME_ROLE: dict[Timeframe, str] = {
    Timeframe.MN1: "structure",
    Timeframe.W1: "structure",
    Timeframe.D1: "structure",
    Timeframe.H4: "structure",
    Timeframe.H1: "trend",
    Timeframe.M30: "setup",
    Timeframe.M15: "setup",
    Timeframe.M5: "execution",
    Timeframe.M1: "execution",
}


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class Decision(StrEnum):
    """Esito della valutazione di un asset. WAIT e' un esito di pari dignita'."""

    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    NONE = "NONE"


class MarketRegime(StrEnum):
    STRONG_TREND_UP = "STRONG_TREND_UP"
    TREND_UP = "TREND_UP"
    STRONG_TREND_DOWN = "STRONG_TREND_DOWN"
    TREND_DOWN = "TREND_DOWN"
    RANGING = "RANGING"
    BREAKOUT = "BREAKOUT"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    CHAOTIC = "CHAOTIC"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    UNCERTAIN = "UNCERTAIN"


class Sentiment(StrEnum):
    VERY_BEARISH = "VERY_BEARISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    BULLISH = "BULLISH"
    VERY_BULLISH = "VERY_BULLISH"


class RiskVerdict(StrEnum):
    """Le quattro risposte possibili del Risk Engine. Nessun'altra."""

    APPROVED = "APPROVED"
    REDUCED_SIZE = "REDUCED_SIZE"
    REJECTED = "REJECTED"
    WAIT = "WAIT"


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"

    @property
    def can_trade(self) -> bool:
        """Fail closed: si opera solo se tutto e' sano, non 'se non e' rotto'."""
        return self is HealthStatus.HEALTHY


class PositionSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class QuoteState(StrEnum):
    """Stato della quotazione di uno strumento.

    Distinzione deliberata e richiesta dall'architettura: *nessuna quotazione*,
    *dato vecchio* e *quotazione valida* sono tre cose diverse, e confonderle
    porta a decidere su prezzi che non esistono.

    Attenzione a cosa e' accertato e cosa e' inferito: `NOT_TRADEABLE` e
    `NO_QUOTE` vengono dal broker (`trade_mode`, bid/ask a zero), mentre
    `MARKET_CLOSED` e' una **deduzione** da prove osservabili, perche' l'API
    Python di MetaTrader 5 non espone gli orari di sessione (ADR 0011).
    """

    TRADEABLE = "TRADEABLE"
    WIDE_SPREAD = "WIDE_SPREAD"
    STALE = "STALE"
    MARKET_CLOSED = "MARKET_CLOSED"
    NO_QUOTE = "NO_QUOTE"
    NOT_TRADEABLE = "NOT_TRADEABLE"
    CLOSE_ONLY = "CLOSE_ONLY"
    #: Quotazione presente ma incoerente (ask sotto bid, prezzo lontanissimo
    #: dall'ultima candela): non e' un prezzo su cui ragionare.
    INCONSISTENT = "INCONSISTENT"
    UNKNOWN = "UNKNOWN"

    @property
    def allows_new_positions(self) -> bool:
        """Solo una quotazione valida consente di aprire. Fail closed."""
        return self is QuoteState.TRADEABLE


class MarketStatus(StrEnum):
    """Stato del mercato di **uno** strumento, con il grado di certezza nel nome.

    Asimmetria voluta: l'apertura si puo' *confermare* (una quotazione fresca e'
    una prova diretta che i prezzi scorrono), la chiusura si puo' solo
    *dedurre* — l'API Python di MT5 non espone gli orari di sessione (ADR 0011).
    Non esiste `CLOSED_CONFIRMED`, ed e' deliberato: un'inferenza non diventa mai
    certezza cambiando nome.
    """

    OPEN_CONFIRMED = "OPEN_CONFIRMED"
    CLOSED_INFERRED = "CLOSED_INFERRED"
    UNKNOWN = "UNKNOWN"


class QuoteQualityStatus(StrEnum):
    """Qualita' di una quotazione come dato, indipendente dalla politica di rischio.

    Lo spread troppo ampio o lo strumento disabilitato dal broker non stanno qui:
    sono decisioni su *se operare*, non su *se il dato e' buono*.
    """

    VALID = "VALID"
    STALE = "STALE"
    MISSING = "MISSING"
    MARKET_CLOSED = "MARKET_CLOSED"
    INCONSISTENT = "INCONSISTENT"


class MarketPhase(StrEnum):
    """Fase complessiva del mercato, dall'insieme degli strumenti.

    Stessa asimmetria di `MarketStatus`: l'apertura si conferma, la chiusura si
    deduce. Prima i valori erano `OPEN` e `CLOSED`, e "CLOSED" presentava come
    fatto una conclusione nostra.
    """

    OPEN_CONFIRMED = "OPEN_CONFIRMED"
    PARTIALLY_OPEN = "PARTIALLY_OPEN"
    CLOSED_INFERRED = "CLOSED_INFERRED"
    UNKNOWN = "UNKNOWN"


class AssetClass(StrEnum):
    """Classe di attivita'.

    Serve perche' le valute dichiarate dal broker non bastano: XM dichiara
    `GOLD` come USD/USD/USD, quindi un raggruppamento per sola valuta metterebbe
    oro e indici americani nello stesso secchiello "USD" e farebbe sembrare
    rischio concentrato due posizioni che non hanno nulla in comune.
    """

    FX = "FX"
    METAL = "METAL"
    INDEX = "INDEX"
    ENERGY = "ENERGY"
    CRYPTO = "CRYPTO"
    STOCK = "STOCK"
    OTHER = "OTHER"


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class LevelKind(StrEnum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class StructureTrend(StrEnum):
    """Struttura di mercato dalla successione dei massimi e minimi confermati."""

    UPTREND = "UPTREND"  # massimi e minimi crescenti
    DOWNTREND = "DOWNTREND"  # massimi e minimi decrescenti
    RANGE = "RANGE"  # successione mista
    UNDEFINED = "UNDEFINED"  # non abbastanza swing confermati


class StructureBreak(StrEnum):
    """Rottura di struttura rispetto all'ultimo swing confermato."""

    NONE = "NONE"
    BREAK_UP = "BREAK_UP"
    BREAK_DOWN = "BREAK_DOWN"


class BreakoutKind(StrEnum):
    """Classificazione probabilistica, non una certezza."""

    NONE = "NONE"
    COMPRESSION = "COMPRESSION"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKOUT_DOWN = "BREAKOUT_DOWN"
    FALSE_BREAKOUT_UP = "FALSE_BREAKOUT_UP"
    FALSE_BREAKOUT_DOWN = "FALSE_BREAKOUT_DOWN"
    RETEST = "RETEST"


class DataQuality(StrEnum):
    """Quanto ci si puo' fidare di un'analisi.

    `INSUFFICIENT` non significa "analisi un po' peggiore": significa che i
    numeri non vanno usati. Un indicatore su una serie troppo corta restituisce
    valori plausibili e privi di significato.
    """

    GOOD = "GOOD"
    SHORT_HISTORY = "SHORT_HISTORY"
    STALE = "STALE"
    SIMULATED = "SIMULATED"
    INSUFFICIENT = "INSUFFICIENT"

    @property
    def usable_for_decisions(self) -> bool:
        return self is DataQuality.GOOD


# ======================================================================
# Intelligence globale (fase 4)
# ======================================================================
class GlobalRegime(StrEnum):
    """Regime globale dei mercati, deciso da regole quantitative (nessun LLM).

    `MIXED` e `UNCERTAIN` non sono ripieghi: sono risposte. `MIXED` dice che le
    evidenze ci sono e si contraddicono; `UNCERTAIN` che le evidenze non bastano.
    """

    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    MIXED = "MIXED"
    TRANSITION = "TRANSITION"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    UNCERTAIN = "UNCERTAIN"


class GlobalDataQuality(StrEnum):
    """Qualità dei dati di una dimensione del contesto globale, o del contesto.

    Tre livelli e non cinque come `DataQuality`: a livello globale la domanda è
    solo "posso fidarmi, con riserva, o no".
    """

    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    INSUFFICIENT = "INSUFFICIENT"

    @property
    def rank(self) -> int:
        return {"GOOD": 0, "DEGRADED": 1, "INSUFFICIENT": 2}[self.value]


class StrengthLabel(StrEnum):
    """Forza o direzione di una valuta o di un gruppo di strumenti.

    `MIXED` (componenti in disaccordo) e `UNKNOWN` (dati insufficienti) restano
    distinti da `NEUTRAL` (dati sufficienti, nessuna direzione): sono tre
    situazioni diverse e non vanno appiattite.
    """

    STRONG = "STRONG"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class VolatilityLevel(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class VolatilityTrend(StrEnum):
    EXPANDING = "EXPANDING"
    STABLE = "STABLE"
    COMPRESSING = "COMPRESSING"
    UNKNOWN = "UNKNOWN"


class RelationshipState(StrEnum):
    """Stato di una relazione intermarket misurata sui dati, mai presunta.

    Fase 4.5: "una correlazione non significa relazione confermata". Gli stati
    confrontano la correlazione **corrente** con la **base storica** (finestre che
    non si sovrappongono), con un test di significatività:

    * `INSUFFICIENT_DATA` — campione troppo piccolo per dire qualcosa;
    * `NO_BASELINE` — la base storica non è significativamente diversa da zero:
      non c'è una relazione di cui verificare la tenuta;
    * `UNSTABLE` — la base esiste in media ma cambia segno fra le finestre;
    * `DIVERGING` — la corrente ha segno opposto alla base, in modo significativo;
    * `WEAKENING` — stesso segno, ma significativamente più debole della base;
    * `CONSISTENT` — corrente compatibile con la base. **Coerente con la storia,
      non "confermata"**: è l'assenza di una differenza misurabile.
    """

    CONSISTENT = "CONSISTENT"
    WEAKENING = "WEAKENING"
    DIVERGING = "DIVERGING"
    UNSTABLE = "UNSTABLE"
    NO_BASELINE = "NO_BASELINE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class IntermarketState(StrEnum):
    """Stato complessivo delle relazioni intermarket.

    `CONSISTENT` sostituisce `CONFIRMED` (fase 4.5): le relazioni si comportano
    come nella loro storia, il che non conferma niente sul futuro.
    """

    CONSISTENT = "CONSISTENT"
    DIVERGENT = "DIVERGENT"
    MIXED = "MIXED"
    INSUFFICIENT = "INSUFFICIENT"


class UncertaintyLevel(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class ConflictSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReferenceGroup(StrEnum):
    """Ruolo di uno strumento nell'universo di riferimento globale."""

    FX = "FX"
    EQUITY_INDEX = "EQUITY_INDEX"
    METAL = "METAL"
    ENERGY = "ENERGY"


class SeriesStatus(StrEnum):
    """Stato di una serie di candele nell'universo di riferimento.

    * `FRESH` — ultima barra chiusa recente, con età **verificata** (ADR 0015);
    * `UNVERIFIED` — dati presenti ma età non verificabile (ora broker ignota):
      usati, ma il contesto non può essere `GOOD`;
    * `CLOSED` — mercato dedotto chiuso: dati dell'ultima sessione, usati e
      dichiarati come tali;
    * `STALE` — mercato aperto ma barre ferme: **esclusi** dai calcoli;
    * `INSUFFICIENT_HISTORY` — troppo poche barre: esclusi;
    * `MISSING` — simbolo assente o illeggibile: esclusi.
    """

    FRESH = "FRESH"
    UNVERIFIED = "UNVERIFIED"
    CLOSED = "CLOSED"
    STALE = "STALE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MISSING = "MISSING"

    @property
    def usable(self) -> bool:
        return self in (SeriesStatus.FRESH, SeriesStatus.UNVERIFIED, SeriesStatus.CLOSED)


# ======================================================================
# Fonti esterne (fase 4.5)
# ======================================================================
class DataCategory(StrEnum):
    """Categoria di un dato, indipendente dalla fonte che lo fornisce (ADR 0018).

    * `TRADABLE_INSTRUMENT` — negoziabile sul conto: la fonte autorevole è il
      broker (XM/MT5) per prezzi, specifiche, margine, esecuzione;
    * `REFERENCE_INSTRUMENT` — un prezzo di mercato usato come riferimento, anche
      se non negoziabile su XM (VIX, DXY, i cross usati per la forza delle valute);
    * `MACRO_SERIES` — una serie macroeconomica o di tassi (rendimenti dei titoli
      di Stato, inflazione, occupazione).
    """

    TRADABLE_INSTRUMENT = "TRADABLE_INSTRUMENT"
    REFERENCE_INSTRUMENT = "REFERENCE_INSTRUMENT"
    MACRO_SERIES = "MACRO_SERIES"


class ProviderKind(StrEnum):
    MARKET_REFERENCE = "MARKET_REFERENCE"
    ECONOMIC_CALENDAR = "ECONOMIC_CALENDAR"
    MACRO_DATA = "MACRO_DATA"
    RATES = "RATES"
    NEWS = "NEWS"


class ProviderState(StrEnum):
    """Stato di un provider esterno all'ultima interrogazione."""

    AVAILABLE = "AVAILABLE"
    #: Configurato ma l'ultima lettura è fallita (rete, formato, limite di uso).
    FAILED = "FAILED"
    #: Dati ricevuti ma più vecchi di quanto la fonte dovrebbe garantire.
    STALE = "STALE"
    #: Non configurato: nessun provider scelto per questa categoria.
    NOT_CONFIGURED = "NOT_CONFIGURED"


class EventImportance(StrEnum):
    """Importanza di un evento di calendario. Unifica il segnaposto della fase 0."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    HOLIDAY = "HOLIDAY"
    UNKNOWN = "UNKNOWN"

    @property
    def blocks_trading(self) -> bool:
        """Eventi attorno ai quali la finestra di blocco dell'utente si applica."""
        return self in (EventImportance.HIGH, EventImportance.CRITICAL)


class PersistenceRequirement(StrEnum):
    """Quanto un componente dipende dalla persistenza (ADR 0019).

    * `BEST_EFFORT` — l'intelligence read-only: senza database si degrada e lo
      dichiara, ma continua a osservare;
    * `REQUIRED` — stato del rischio ed esecuzione (fasi 6 e 11): senza una
      persistenza affidabile non si opera. Fail closed.
    """

    BEST_EFFORT = "BEST_EFFORT"
    REQUIRED = "REQUIRED"


# ======================================================================
# Metadati delle fonti e qualità per dominio (correzioni pre-fase 5)
# ======================================================================
class SourceDomain(StrEnum):
    """Dominio di provenienza di un dato: ognuno ha la sua qualità (ADR 0021)."""

    BROKER_DATA = "BROKER_DATA"
    MARKET_REFERENCE = "MARKET_REFERENCE"
    CALENDAR = "CALENDAR"
    MACRO = "MACRO"
    NEWS = "NEWS"


class DataFrequency(StrEnum):
    TICK = "TICK"
    INTRADAY_BARS = "INTRADAY_BARS"
    DAILY = "DAILY"
    WEEKLY_SCHEDULE = "WEEKLY_SCHEDULE"
    NONE = "NONE"


class IntendedUse(StrEnum):
    """A cosa serve un dato, e quindi dove NON va usato.

    `STRUCTURAL_CONTEXT` (VIX e Tesoro giornalieri) descrive lo sfondo dei giorni,
    non quello delle ore: non entra mai nei timeframe intraday. `CROSS_CHECK` (DXY
    ricostruito) verifica un'altra misura, non decide nulla da solo.
    """

    INTRADAY_CONTEXT = "INTRADAY_CONTEXT"
    STRUCTURAL_CONTEXT = "STRUCTURAL_CONTEXT"
    EVENT_RISK = "EVENT_RISK"
    CROSS_CHECK = "CROSS_CHECK"


class EventPhase(StrEnum):
    """Fase di un evento di calendario rispetto all'istante di analisi.

    * `PRE_EVENT` — prima dell'apertura della finestra di blocco;
    * `EVENT_WINDOW` — dentro la finestra (60' prima / 30' dopo, limiti utente) e
      senza valore pubblicato;
    * `POST_EVENT` — finestra chiusa, valore pubblicato non disponibile;
    * `ACTUAL_AVAILABLE` — il valore pubblicato è noto **e lo era già** all'istante
      di analisi (nessun hindsight).
    """

    PRE_EVENT = "PRE_EVENT"
    EVENT_WINDOW = "EVENT_WINDOW"
    POST_EVENT = "POST_EVENT"
    ACTUAL_AVAILABLE = "ACTUAL_AVAILABLE"
