/**
 * Tipi dello stato di sistema, allineati a `backend/schemas/health.py`.
 *
 * Sono scritti a mano e non generati: in fase 1 il contratto è piccolo e la
 * generazione automatica da OpenAPI aggiungerebbe un passo di build per
 * guadagnare poco. Quando le pagine diventeranno molte conviene generarli
 * (`openapi-typescript`) e questa nota diventerà la motivazione del cambio.
 */

export type HealthStatus = "HEALTHY" | "DEGRADED" | "UNHEALTHY" | "UNKNOWN";

export type TradingMode = "OBSERVE" | "SIGNAL" | "PAPER" | "LIVE";

export interface ComponentHealth {
  name: string;
  status: HealthStatus;
  detail: string;
  latency_ms: number | null;
  checked_at: string;
  critical: boolean;
  metadata: Record<string, string | number | boolean | null>;
}

export interface SystemHealth {
  status: HealthStatus;
  version: string;
  environment: string;
  declared_mode: TradingMode;
  effective_mode: TradingMode;
  kill_switch_engaged: boolean;
  kill_switch_reason: string | null;
  uptime_seconds: number;
  checked_at: string;
  components: ComponentHealth[];
  warnings: string[];
  can_trade: boolean;
  blocking_reasons: string[];
}
