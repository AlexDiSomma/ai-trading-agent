"""Sistema di configurazione.

Regole che questo modulo fa rispettare:

* **Nessun segreto nel codice.** Tutto arriva da variabili d'ambiente o da
  `.env`, che è escluso da git. Le credenziali sono `SecretStr`: finiscono nei
  log come `**********` anche se qualcuno le stampa per sbaglio.
* **Configurazione incoerente = avvio fallito.** Meglio non partire che partire
  con limiti di rischio assurdi o con MODE=LIVE per distrazione.
* **Un solo punto di verità.** `get_settings()` è in cache: i moduli non
  rileggono l'ambiente ciascuno a modo proprio.

Il blocco dei limiti di rischio vive in `risk_limits.py` perché ha un ciclo di
vita diverso: è il contratto dell'utente, non un parametro tecnico.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.config.paths import ENV_FILE, PROJECT_ROOT, resolve
from backend.config.risk_limits import RiskLimits
from backend.core.enums import Environment, TradingMode

_BASE_CONFIG = SettingsConfigDict(
    env_file=ENV_FILE,
    env_file_encoding="utf-8",
    extra="ignore",
    populate_by_name=True,
)


class AppSettings(BaseSettings):
    """Modalità operativa e sicurezze globali. Prefisso APP_."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="APP_")

    env: Environment = Environment.DEVELOPMENT
    debug: bool = True
    timezone: str = "UTC"

    # Il default è PAPER: un clone del repository non può trovarsi a operare
    # con denaro reale per omissione.
    mode: TradingMode = TradingMode.PAPER

    # Doppio consenso per il denaro reale: la modalità da sola non basta.
    enable_live_trading: bool = False

    kill_switch_file: Path = Path("var/KILL_SWITCH")
    kill_switch_enabled: bool = False

    @field_validator("kill_switch_file")
    @classmethod
    def _absolute_kill_switch(cls, value: Path) -> Path:
        return resolve(value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_mode(self) -> TradingMode:
        """La modalità con cui il sistema opera davvero.

        MODE=LIVE senza ENABLE_LIVE_TRADING=true **non** è un errore di avvio ma
        un declassamento a PAPER: fail closed. Un errore fatale qui tenterebbe
        chi ha fretta a mettere `true` per far partire il processo, ottenendo
        l'esatto contrario della sicurezza voluta.
        """
        if self.mode is TradingMode.LIVE and not self.enable_live_trading:
            return TradingMode.PAPER
        return self.mode

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mode_was_downgraded(self) -> bool:
        return self.effective_mode is not self.mode

    @property
    def can_send_orders(self) -> bool:
        """Solo LIVE invia ordini al broker. PAPER li simula."""
        return self.effective_mode is TradingMode.LIVE


class LogSettings(BaseSettings):
    """Logging strutturato. Prefisso LOG_."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["console", "json"] = "console"
    file: Path | None = Path("logs/backend.log")

    @field_validator("file")
    @classmethod
    def _absolute_log_file(cls, value: Path | None) -> Path | None:
        return resolve(value) if value is not None else None


class DatabaseSettings(BaseSettings):
    """PostgreSQL. Prefisso DB_."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="DB_")

    host: str = "localhost"
    port: int = 5432
    name: str = "trading_agent"
    user: str = "trading"
    password: SecretStr = SecretStr("")
    pool_size: int = Field(default=10, ge=1, le=100)
    echo: bool = False

    # Secondi di attesa per aprire una connessione. Serve a non far durare
    # secondi il controllo di stato quando il database e' spento: asyncpg
    # altrimenti prova SSL e non-SSL su IPv6 e IPv4, uno dopo l'altro.
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @property
    def async_dsn(self) -> str:
        """DSN per SQLAlchemy async (asyncpg)."""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_dsn(self) -> str:
        """DSN sincrono: lo usa Alembic, che non è asincrono."""
        return (
            f"postgresql+psycopg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def safe_dsn(self) -> str:
        """Versione mostrabile nei log e nell'endpoint di health."""
        return f"postgresql://{self.user}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    """Redis: cache dei contesti e code dei worker. Prefisso REDIS_."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: SecretStr | None = None

    # Secondi di attesa per la connessione. Basso di proposito: Redis e' una
    # cache locale, e un controllo di stato che resta appeso tre secondi rende
    # lenta la pagina che deve dire proprio che Redis non risponde.
    connect_timeout_seconds: float = Field(default=1.5, gt=0, le=30)

    @property
    def dsn(self) -> str:
        auth = f":{self.password.get_secret_value()}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"

    @property
    def safe_dsn(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


class MT5Settings(BaseSettings):
    """Connessione al terminale MetaTrader 5 (broker XM). Prefisso MT5_."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="MT5_")

    enabled: bool = True

    # Terminale finto: permette di sviluppare e testare senza Windows e senza
    # MT5 aperto. Non è un fallback automatico — va chiesto esplicitamente,
    # altrimenti un errore di connessione passerebbe per dati veri.
    use_mock: bool = False

    login: int | None = None
    password: SecretStr | None = None
    server: str | None = None
    terminal_path: Path | None = None
    timeout_ms: int = Field(default=10_000, ge=1_000, le=120_000)
    portable: bool = False

    # Oltre questa età un tick non è più una informazione sul presente.
    max_tick_age_seconds: float = Field(default=15.0, gt=0)

    @property
    def missing_credentials(self) -> tuple[str, ...]:
        """Variabili necessarie per il terminale reale che non sono compilate."""
        if not self.enabled or self.use_mock:
            return ()
        return tuple(
            name
            for name, value in (
                ("MT5_LOGIN", self.login),
                ("MT5_PASSWORD", self.password),
                ("MT5_SERVER", self.server),
            )
            if value in (None, "")
        )

    @property
    def is_ready(self) -> bool:
        return self.enabled and not self.missing_credentials

    # Nota deliberata: le credenziali mancanti **non** sono un errore di
    # validazione. Se lo fossero, il backend non si avvierebbe e non potrebbe
    # dire cosa manca: resterebbe un processo morto e un utente che indovina.
    # L'incoerenza viene invece segnalata in `startup_warnings()`, mostrata in
    # System Health, e fa fallire la costruzione del broker con un messaggio
    # esplicito. Il trading resta bloccato comunque: fail closed.


class ExternalSettings(BaseSettings):
    """Fonti esterne della Global Intelligence (fase 4.5, ADR 0018). Prefisso EXT_.

    XM/MT5 resta la fonte autorevole per gli strumenti negoziabili; queste fonti
    forniscono dati di riferimento e macro che XM non ha o non ha in forma usabile
    (VIX come indice e non come future in scadenza, curva dei rendimenti,
    calendario economico). Confronto delle alternative in docs/EXTERNAL_PROVIDERS.md.

    `none` disattiva una fonte: la dimensione corrispondente risulta allora non
    disponibile e lo dichiara, mai "tutto tranquillo".
    """

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="EXT_")

    calendar_provider: Literal["forexfactory", "none"] = "forexfactory"
    vix_provider: Literal["cboe", "none"] = "cboe"
    rates_provider: Literal["us_treasury", "none"] = "us_treasury"
    #: Nessun provider macro scelto: FRED è il candidato (chiave gratuita).
    macro_provider: Literal["none"] = "none"
    #: News e sentiment rinviati: interfaccia sì, provider no.
    news_provider: Literal["none"] = "none"
    http_timeout_seconds: float = Field(default=20.0, gt=0, le=120)


class AISettings(BaseSettings):
    """Provider LLM. Prefisso AI_ (le chiavi API non hanno prefisso)."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="AI_")

    provider: Literal["anthropic", "openai", "null"] = "anthropic"

    # Con `required=True` un provider non raggiungibile significa NO TRADE.
    # L'alternativa — proseguire senza AI — equivarrebbe a cambiare strategia
    # di nascosto nel momento peggiore.
    required: bool = True

    model: str = "claude-opus-5"

    # Temperatura 0: due analisi dello stesso contesto devono dare la stessa
    # risposta, altrimenti il backtest non è riproducibile.
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=8_000, ge=256)
    #: Diario immutabile delle analisi AI (fase 5A). Relativo alla radice del progetto.
    journal_dir: Path = Path("var/ai_journal")
    timeout_seconds: float = Field(default=90.0, gt=0)
    daily_cost_limit_usd: float = Field(default=10.0, ge=0)

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_MODEL")

    @property
    def has_credentials(self) -> bool:
        match self.provider:
            case "anthropic":
                return self.anthropic_api_key is not None
            case "openai":
                return self.openai_api_key is not None
            case _:
                return True


class ApiSettings(BaseSettings):
    """Server HTTP e accesso dal frontend. Prefisso API_."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="API_")

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins_raw: str = Field(default="http://localhost:3000", alias="API_CORS_ORIGINS")

    # Il frontend non parla mai con il broker: passa da qui, con un token.
    auth_token: SecretStr | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


class PaperSettings(BaseSettings):
    """MVP operativo in PAPER: scheduler, costi simulati, conti paper. Prefisso PAPER_.

    Nessuna di queste opzioni abilita il LIVE: l'esecuzione paper non parla con il
    broker se non per leggere prezzi e calcolare valori (ADR 0004).
    """

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="PAPER_")

    #: Lo scheduler parte con il backend. Falso: dashboard senza ciclo automatico.
    autopilot_enabled: bool = True
    #: Ogni quanto si scansiona il mercato e si decidono nuove operazioni.
    scan_interval_seconds: int = Field(default=300, ge=30)
    #: Ogni quanto si gestiscono le posizioni aperte (SL/TP/invalidazione/tempo).
    manage_interval_seconds: int = Field(default=20, ge=5)
    #: Ogni quanto si rinnova l'analisi AI del contesto globale (fase 5A).
    market_analysis_interval_seconds: int = Field(default=3600, ge=300)
    #: Tesi AI al massimo per ciclo di scansione: controlla il costo del modello.
    max_ai_theses_per_cycle: int = Field(default=3, ge=0, le=20)
    #: Strumenti candidati, i migliori dello scanner, considerati per ciclo.
    top_opportunities: int = Field(default=4, ge=1, le=20)
    #: Saldo iniziale dei conti paper. None: il saldo del conto XM alla prima partenza.
    initial_balance: float | None = Field(default=None, gt=0)
    #: Slittamento simulato, in punti, su ingressi a mercato/stop e uscite allo stop.
    slippage_points: float = Field(default=2.0, ge=0)
    #: Commissione per lotto e per lato, in valuta del conto (XM Standard: 0).
    commission_per_lot: float = Field(default=0.0, ge=0)
    #: Cartella dei conti paper (file JSON, scrittura atomica).
    data_dir: Path = Path("var/paper")
    #: Quale conto paper alimenterebbe il LIVE quando (e se) tutti i gate saranno veri.
    live_source: Literal["QUANT", "AI"] = "AI"


class NewsSettings(BaseSettings):
    """Notizie da fonti gratuite (RSS). Prefisso NEWS_."""

    model_config = _BASE_CONFIG | SettingsConfigDict(env_prefix="NEWS_")

    enabled: bool = True
    #: Ogni quanto si leggono i feed. I feed RSS non vanno interrogati troppo spesso.
    interval_seconds: int = Field(default=600, ge=300)
    #: Classificazione AI (impatto, asset, sintesi in italiano), a lotti.
    ai_classification: bool = True
    max_ai_items: int = Field(default=20, ge=0, le=50)
    #: Minuti di freno sulle nuove aperture dopo una notizia ad alto impatto confermata.
    brake_minutes: int = Field(default=60, ge=0, le=720)
    data_dir: Path = Path("var/news")


class Settings(BaseSettings):
    """Configurazione completa. Unico oggetto passato in giro per injection."""

    model_config = _BASE_CONFIG

    app: AppSettings = Field(default_factory=AppSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    mt5: MT5Settings = Field(default_factory=MT5Settings)
    ai: AISettings = Field(default_factory=AISettings)
    external: ExternalSettings = Field(default_factory=ExternalSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    paper: PaperSettings = Field(default_factory=PaperSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    def startup_warnings(self) -> list[str]:
        """Avvisi da loggare all'avvio e da mostrare in System Health.

        Non sono errori: sono condizioni in cui il sistema funziona ma non fa
        quello che l'utente potrebbe credere. Nel progetto precedente era
        esattamente questo il problema — un sistema che sembrava rotto perché
        nessuno diceva perché non operava.
        """
        warnings: list[str] = []
        if self.app.mode_was_downgraded:
            warnings.append(
                "APP_MODE=LIVE ignorato: APP_ENABLE_LIVE_TRADING è false. "
                "Il sistema opera in PAPER."
            )
        if self.mt5.use_mock:
            warnings.append(
                "MT5 in modalità mock: i prezzi NON sono quelli del broker. "
                "Nessun dato di questa sessione è utilizzabile per decidere."
            )
        if not self.mt5.enabled:
            warnings.append("Connettore MT5 disabilitato: nessun dato di mercato disponibile.")
        elif self.mt5.missing_credentials:
            warnings.append(
                "Credenziali MT5 mancanti ("
                + ", ".join(self.mt5.missing_credentials)
                + "): compila .env oppure imposta MT5_USE_MOCK=true per lavorare con il "
                "terminale finto. Finche' mancano, il sistema non ha dati di mercato."
            )
        if self.ai.provider == "null" and self.ai.required:
            warnings.append(
                "AI_PROVIDER=null con AI_REQUIRED=true: nessuna decisione verrà prodotta."
            )
        if self.ai.provider != "null" and not self.ai.has_credentials:
            warnings.append(
                f"Chiave API mancante per il provider AI '{self.ai.provider}': "
                "le analisi AI non partiranno."
            )
        if self.app.can_send_orders:
            warnings.append(
                "MODALITÀ LIVE ATTIVA: gli ordini vengono inviati al broker con denaro reale."
            )
        if self.api.auth_token is None and self.app.env is Environment.PRODUCTION:
            warnings.append("API_AUTH_TOKEN non impostato in produzione: API non protetta.")
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configurazione in cache.

    La cache serve anche a far fallire l'avvio una volta sola e nel punto
    giusto: la prima lettura avviene nello startup di FastAPI, non a metà di una
    richiesta o, peggio, a metà di un invio d'ordine.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Svuota la cache. Serve ai test, che cambiano l'ambiente a ogni caso."""
    get_settings.cache_clear()
