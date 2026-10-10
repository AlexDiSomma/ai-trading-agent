"""Limiti di rischio: il contratto che l'utente impone al sistema.

Principio fondamentale del progetto: **l'utente definisce il rischio, l'AI
decide come operare dentro quel rischio.**

Da cui tre proprietà di questa classe:

* è `frozen`: nessun modulo può alzare un limite a runtime. Un tentativo di
  assegnazione solleva un errore, non viene ignorato in silenzio;
* non importa nulla da `ai/` né da `risk/`: è un dato, letto da entrambi;
* le validazioni sono incrociate (es. il rischio per trade non può superare il
  rischio complessivo aperto): una configurazione incoerente blocca l'avvio,
  non si scopre al primo ordine.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.config.paths import ENV_FILE


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


class RiskLimits(BaseSettings):
    """Limiti invalicabili. Prefisso RISK_ nelle variabili d'ambiente."""

    model_config = SettingsConfigDict(
        env_prefix="RISK_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    account_currency: str = "EUR"

    # Capitale che l'utente accetta di mettere al lavoro. Non è l'equity del
    # conto: può essere una frazione di essa.
    risk_budget: float = Field(default=1000.0, gt=0)

    # --- perdite massime (percentuali sull'equity di inizio periodo) ---
    daily_max_loss_pct: float = Field(default=2.0, gt=0, le=100)
    weekly_max_loss_pct: float = Field(default=5.0, gt=0, le=100)
    max_drawdown_pct: float = Field(default=10.0, gt=0, le=100)
    #: Obiettivo di profitto giornaliero: raggiunto, nessuna nuova apertura fino a
    #: domani (le posizioni aperte restano gestite). None = nessun obiettivo.
    daily_profit_target_pct: float | None = Field(default=None, gt=0, le=100)

    # --- rischio per operazione e complessivo ---
    max_risk_per_trade_pct: float = Field(default=0.5, gt=0, le=100)
    max_open_risk_pct: float = Field(default=1.5, gt=0, le=100)
    max_open_positions: int = Field(default=3, ge=1, le=100)
    max_position_size_lots: float = Field(default=0.5, gt=0)
    max_leverage: float = Field(default=30.0, gt=0)
    max_correlated_exposure_pct: float = Field(default=1.0, gt=0, le=100)

    # --- qualità minima dell'operazione ---
    minimum_rr: float = Field(default=1.8, ge=0.1)

    # Spread massimo accettabile, espresso come frazione dell'ATR del timeframe
    # di esecuzione. E' il controllo **primario** perche' e' l'unico confrontabile
    # fra strumenti: 35 punti sono 3,5 pip su EURUSD e un ventesimo di punto
    # indice su US100Cash, dove lo spread normale e' 616 punti. Misurato sul conto
    # XM reale, il rapporto spread/ATR(M5) vale 0.09 su EURUSD e 0.03 su GOLD e
    # US100Cash: 0.30 lascia margine e taglia gli allargamenti anomali.
    max_spread_atr_ratio: float = Field(default=0.30, gt=0, le=5)

    # Tetto assoluto in punti, di sicurezza: serve quando l'ATR non e' ancora
    # calcolabile (storia troppo corta). Volutamente largo, perche' un valore
    # stretto qui escluderebbe in silenzio interi gruppi di strumenti.
    max_spread_points: float = Field(default=1000.0, gt=0)
    max_slippage_points: float = Field(default=20.0, gt=0)
    min_stop_distance_atr: float = Field(default=0.5, gt=0)
    max_stop_distance_atr: float = Field(default=3.0, gt=0)
    min_confidence: float = Field(default=0.65, ge=0, le=1)

    # --- perimetro operativo ---
    # Le liste restano stringhe: pydantic-settings proverebbe a interpretare un
    # campo `tuple` come JSON e un valore CSV farebbe fallire l'avvio.
    allowed_symbols_raw: str = Field(default="EURUSD,XAUUSD", alias="RISK_ALLOWED_SYMBOLS")
    allowed_sessions_raw: str = Field(default="08:00-17:30", alias="RISK_ALLOWED_SESSIONS")
    allowed_weekdays_raw: str = Field(default="0,1,2,3,4", alias="RISK_ALLOWED_WEEKDAYS")

    # --- filtro eventi macro ---
    block_high_impact_before_minutes: int = Field(default=60, ge=0)
    block_high_impact_after_minutes: int = Field(default=30, ge=0)

    # Scala "confidence -> quota del rischio massimo utilizzabile".
    # Formato: "0.65:0.25,0.75:0.50,0.85:0.75,0.92:1.00".
    # Sotto la prima soglia non si opera. Il 100% resta quello dell'utente:
    # questa scala può solo ridurre, mai aumentare.
    confidence_ladder_raw: str = Field(
        default="0.65:0.25,0.75:0.50,0.85:0.75,0.92:1.00",
        alias="RISK_CONFIDENCE_LADDER",
    )

    # ------------------------------------------------------------------
    # Validazioni
    # ------------------------------------------------------------------
    @field_validator("account_currency")
    @classmethod
    def _currency_upper(cls, value: str) -> str:
        if len(value) != 3:
            raise ValueError("La valuta del conto va indicata con 3 lettere, es. EUR")
        return value.upper()

    @model_validator(mode="after")
    def _check_coherence(self) -> Self:
        if self.max_risk_per_trade_pct > self.max_open_risk_pct:
            raise ValueError(
                "max_risk_per_trade_pct non può superare max_open_risk_pct: "
                "un singolo trade rischierebbe più di tutto il portafoglio consentito"
            )
        if self.daily_max_loss_pct > self.weekly_max_loss_pct:
            raise ValueError(
                "daily_max_loss_pct non può superare weekly_max_loss_pct: "
                "il limite settimanale sarebbe inutile"
            )
        if self.weekly_max_loss_pct > self.max_drawdown_pct:
            raise ValueError(
                "weekly_max_loss_pct non può superare max_drawdown_pct: "
                "si raggiungerebbe il drawdown massimo prima del limite settimanale"
            )
        if self.min_stop_distance_atr >= self.max_stop_distance_atr:
            raise ValueError("min_stop_distance_atr deve essere minore di max_stop_distance_atr")
        if not self.allowed_symbols:
            raise ValueError(
                "RISK_ALLOWED_SYMBOLS è vuota: senza whitelist il sistema non opera su nulla"
            )
        if not self.confidence_ladder:
            raise ValueError("RISK_CONFIDENCE_LADDER non è interpretabile")
        return self

    # ------------------------------------------------------------------
    # Viste derivate
    # ------------------------------------------------------------------
    @property
    def allowed_symbols(self) -> tuple[str, ...]:
        """Strumenti autorizzati, con la grafia esatta usata dal broker.

        I nomi **non** vengono portati in maiuscolo: su XM gli strumenti si
        chiamano `GOLD`, `SILVER`, `US30Cash`, `US100Cash`, e `US30CASH` non
        esiste. Normalizzarli renderebbe la whitelist inutilizzabile proprio
        sugli indici. Il confronto avviene senza distinzione di maiuscole
        (`canonical_symbol`), la grafia inviata al broker resta la sua.
        """
        return _split_csv(self.allowed_symbols_raw)

    @property
    def allowed_weekdays(self) -> tuple[int, ...]:
        """0 = lunedì. Il weekend è escluso di default."""
        return tuple(sorted({int(d) for d in _split_csv(self.allowed_weekdays_raw)}))

    @property
    def allowed_sessions(self) -> tuple[tuple[time, time], ...]:
        sessions: list[tuple[time, time]] = []
        for window in _split_csv(self.allowed_sessions_raw):
            start_raw, _, end_raw = window.partition("-")
            sessions.append((_parse_hhmm(start_raw), _parse_hhmm(end_raw)))
        return tuple(sessions)

    @property
    def confidence_ladder(self) -> tuple[tuple[float, float], ...]:
        steps: list[tuple[float, float]] = []
        for step in _split_csv(self.confidence_ladder_raw):
            threshold_raw, _, share_raw = step.partition(":")
            steps.append((float(threshold_raw), float(share_raw)))
        return tuple(sorted(steps))

    # ------------------------------------------------------------------
    # Interrogazioni usate dal Risk Engine (fase 6)
    # ------------------------------------------------------------------
    def canonical_symbol(self, symbol: str) -> str | None:
        """La grafia ufficiale di uno strumento autorizzato, o None.

        Serve a tollerare `gold` scritto a mano nella dashboard e inviare
        comunque `GOLD` al terminale.
        """
        cercato = symbol.strip().lower()
        for nome in self.allowed_symbols:
            if nome.lower() == cercato:
                return nome
        return None

    def is_symbol_allowed(self, symbol: str) -> bool:
        return self.canonical_symbol(symbol) is not None

    def is_session_open(self, broker_time: datetime) -> bool:
        """Vero se `broker_time` cade in una sessione autorizzata.

        Il parametro è l'ora del **server del broker**: una sessione definita
        sull'ora del PC si sposterebbe con l'ora legale e con i viaggi.
        """
        if broker_time.weekday() not in self.allowed_weekdays:
            return False
        moment = broker_time.time()
        for start, end in self.allowed_sessions:
            if start <= end:
                if start <= moment <= end:
                    return True
            # Finestra che scavalca la mezzanotte (es. sessione asiatica).
            elif moment >= start or moment <= end:
                return True
        return False

    def risk_share_for_confidence(self, confidence: float) -> float:
        """Quota del rischio massimo utilizzabile per quella confidence.

        Restituisce 0.0 sotto la soglia minima: meno convinzione significa meno
        rischio, mai più. Il risultato è comunque un moltiplicatore <= 1 di un
        limite deciso dall'utente.
        """
        if confidence < self.min_confidence:
            return 0.0
        share = 0.0
        for threshold, value in self.confidence_ladder:
            if confidence >= threshold:
                share = value
        return min(share, 1.0)


def _parse_hhmm(raw: str) -> time:
    raw = raw.strip()
    try:
        hours, _, minutes = raw.partition(":")
        return time(hour=int(hours), minute=int(minutes))
    except ValueError as exc:  # pragma: no cover - solo messaggio d'errore
        raise ValueError(f"Orario non valido '{raw}': usa il formato HH:MM") from exc
