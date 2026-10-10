/** Forma di `GET /api/dashboard`. I campi annidati meno usati restano generici. */

export interface Condizione {
  subject: string;
  timeframe: string;
  measure: string;
  operator: string;
  level: number;
  description: string;
}

export interface Piano {
  book: "QUANT" | "AI";
  canonical: string;
  broker_symbol: string;
  direction: "LONG" | "SHORT";
  entry_type: "MARKET" | "STOP" | "LIMIT";
  entry_price: number;
  stop: number;
  target: number;
  rr: number;
  atr_h1: number;
  created_at: string;
  valid_until: string;
  expires_at: string;
  invalidations: Condizione[];
  strategy_version: string;
  ai_confidence_label: string | null;
  reasons: string[];
}

export interface Posizione {
  position_id: string;
  plan: Piano;
  status: "PENDING" | "OPEN";
  opened_at: string | null;
  entry_fill: number | null;
  volume: number;
  risk_amount: number;
  swap: number;
  commission: number;
  last_price: number | null;
  unrealized_pnl: number | null;
  notes: string[];
  trailing_rule?: string | null;
  stop_r?: number | null;
  picco_r?: number;
}

export interface Trade {
  position_id: string;
  plan: Piano;
  opened_at: string;
  closed_at: string;
  entry_fill: number;
  exit_price: number;
  exit_reason: string;
  volume: number;
  risk_amount: number;
  gross_pnl: number;
  commission: number;
  swap: number;
  spread_cost: number;
  slippage_cost: number;
  net_pnl: number;
  r_multiple: number;
  mae_r: number;
  mfe_r: number;
  ambiguous_bar: boolean;
  trailing_rule?: string | null;
  price_r?: number | null;
  shadow_r?: Record<string, number>;
}

export interface Riassunto {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  expectancy_r: number | null;
  total_r: number;
  net_pnl: number;
}

export interface OmbraStop {
  trades: number;
  actual_r: number;
  rules: Record<string, { name: string; description: string; trades: number; total_r: number; difference_r: number; better: number; worse: number }>;
}

export interface SaluteAI {
  ok: boolean | null;
  kind: string | null;
  message: string | null;
  since: string | null;
  last_ok_at: string | null;
  last_error_at: string | null;
  consecutive_failures: number;
  usage_today: { day?: string; calls?: number; failures?: number; input?: number; output?: number };
}

export interface Performance {
  book: string;
  currency: string;
  initial_balance: number;
  balance: number;
  unrealized_pnl: number;
  equity: number;
  return_pct: number;
  max_drawdown_ccy: number;
  max_drawdown_pct: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy_r: number | null;
  expectancy_ccy: number | null;
  total_r: number;
  net_pnl: number;
  exit_reasons: Record<string, number>;
  ambiguous_bars: number;
  costs: Record<string, number>;
  mean_mae_r: number | null;
  mean_mfe_r: number | null;
  mean_hours: number | null;
  equity_curve: { t: string; equity: number }[];
  by_strategy_version: Record<string, { trades: number; total_r: number; expectancy_r: number | null; win_rate: number | null }>;
  trailing_shadow?: OmbraStop;
  correlation?: { correlated: Riassunto; independent: Riassunto; without_correlated_total_r: number; groups: string[] };
  by_trailing_rule?: Record<string, Riassunto>;
}

export interface Libro {
  performance: Performance;
  open_positions: Posizione[];
  closed_trades: Trade[];
  rejections: { at: string; symbol: string; reasons: string[] }[];
  equity_now: number;
}

export interface Opportunita {
  symbol: string;
  canonical: string;
  score: number;
  direction_hint: string;
  factors: Record<string, number>;
  regime: string;
  alignment: number;
  quality: string;
  quote_state: string;
  spread_atr: number | null;
  tradeable: boolean;
  blocking_reasons: string[];
}

export interface Tesi {
  at: string;
  status: string;
  plan_status: string;
  record_id: string;
  model: string;
  failure_reasons: string[];
  decision: string | null;
  setup: string | null;
  confidence_label: string | null;
  scenario: string | null;
  wait_reason: string | null;
  supporting: string[];
  contradictory: string[];
  invalidations: string[];
}

export interface Dashboard {
  system: {
    version: string;
    mode: string;
    live_enabled: boolean;
    broker_connected: boolean;
    broker_error: string | null;
    broker_time_source: string;
    kill_switch: { engaged: boolean; reason: string | null };
    autopilot: {
      running: boolean;
      started_at?: string | null;
      cycles?: Record<string, number>;
      last?: Record<string, string | null>;
      last_error: Record<string, string>;
      busy?: string | null;
    };
    ai_provider: string;
    ai_health?: SaluteAI;
  };
  account: {
    login: number;
    server: string;
    currency: string;
    balance: number;
    equity: number;
    margin_free: number;
    account_type: string;
    leverage: number;
  } | null;
  global: {
    as_of: string;
    market_phase: string;
    regime: string;
    regime_reasons: string[];
    risk_sentiment: { label?: string; score?: number | null; agreement?: number | null };
    volatility: string;
    vix: number | null;
    currency_strength: { currency: string; score: number; label: string }[];
    event_risk: Record<string, unknown>;
    next_events: { title?: string; currency?: string; scheduled_at?: string; phase?: string; importance?: string }[];
    data_quality: string;
    data_quality_reasons: string[];
    source_quality: { domain: string; quality: string; configured: boolean }[];
    known_limitations: string[];
  } | null;
  opportunities: Opportunita[];
  ai_analysis: {
    at: string;
    status: string;
    model: string;
    failure_reasons: string[];
    summary: string | null;
    view_on_regime: string | null;
    themes: { title: string; description: string }[];
    scenarios: { name: string; likelihood: number; horizon_hours: number; narrative: string }[];
    uncertainties: { description: string; missing: string | null; impact: string }[];
    confidence_label: string | null;
  } | null;
  ai_theses: Record<string, Tesi>;
  decisions: { at: string; book?: string; symbol: string; outcome: string; direction?: string; rr?: number; reasons?: string[] }[];
  books: Record<string, Libro>;
  comparison: {
    quant: Record<string, number | null>;
    ai: Record<string, number | null>;
    expectancy_r_difference: number | null;
    verdict: string;
    note: string;
  } | null;
  events: { type: string; at: string; book?: string; symbol?: string; reason?: string; net?: number; r?: number; price?: number }[];
  settings: { risk: Record<string, unknown>; paper: Record<string, unknown> };
  controls: Comandi | null;
  risk_notes: string[];
  news_summary: {
    high_24h: number;
    brakes: Record<string, string[]>;
    latest: { id: string; title: string; summary_it: string | null; impact: string; source: string; published_at: string; confirmed: boolean; assets: string[] }[];
  } | null;
  live: {
    readiness: { ready: boolean; evaluated_at: string; gates: { name: string; ok: boolean; detail: string }[]; missing: string[] } | null;
    breaker: { tripped: boolean; reason: string | null; consecutive_failures: number };
    gateway_built: boolean;
    notes: string[];
  } | null;
}

export interface Comandi {
  paused: boolean;
  symbols: Record<string, boolean>;
  strategies: Record<string, boolean>;
  inverted?: Record<string, boolean>;
  quant_target_r?: number | null;
  trailing?: { books: Record<string, string | null>; rules: { id: string; name: string; description: string }[] };
  log: { at: string; action: string }[];
  kill_switch: { engaged: boolean; reason: string | null; since: string | null };
  risk: {
    editable: Record<string, { value: number | string | null; min: number | null; max: number | null }>;
    overrides: Record<string, unknown>;
    audit: { at: string; field: string; old: unknown; new: unknown; reason: string }[];
    live_locked: boolean;
  };
}

export interface Notizia {
  id: string;
  title: string;
  url: string;
  source: string;
  publisher: string;
  level: "UFFICIALE" | "TESTATA" | "SPECIALIZZATA";
  published_at: string;
  excerpt: string;
  assets: string[];
  themes: string[];
  impact: "LOW" | "MEDIUM" | "HIGH";
  summary_it: string | null;
  why: string | null;
  unverified: boolean | null;
  publishers: string[];
  confirmed: boolean;
}

export interface RispostaNotizie {
  items: Notizia[];
  sources: Record<string, { ok: boolean; at: string; items?: number; error?: string; level: string }>;
  brakes: Record<string, string[]>;
  stats: { total_kept: number; discarded_irrelevant: number; high_24h: number; last_collection: string | null };
  brake_minutes: number;
}
