export type Role = "admin" | "member";

export interface Me {
  id: string;
  email: string;
  name: string;
  role: Role;
  must_change_password: boolean;
  last_login_at: string | null;
  teams: { id: string; name: string; role: Role }[];
}

export interface TeamSummary {
  id: string;
  name: string;
  my_role: Role;
  members: number;
  active_keys: number;
  spend_30d_usd: number;
}

export interface MemberRow {
  type: "user" | "service";
  user_id?: string;
  key_id?: string;
  name: string;
  email: string | null;
  role: Role | "service";
  disabled: boolean;
  last_active: string | null;
}

export interface TeamKey {
  id: string;
  name: string;
  team: string;
  key_prefix: string;
  user_id: string | null;
  owner: string | null;
  kind: "personal" | "service";
  allowed_models: string[] | null;
  rpm_limit: number | null;
  tpm_limit: number | null;
  daily_budget_usd: number | null;
  created_at: string | null;
  last_used?: string | null;
  key?: string;
}

export interface OrgKey {
  id: string;
  name: string;
  project: string;
  key_prefix: string;
  allowed_models: string[] | null;
  rpm_limit: number | null;
  tpm_limit: number | null;
  daily_budget_usd: number | null;
  user_id: string | null;
  created_at: string | null;
  revoked_at: string | null;
}

export interface UsageGroup {
  provider?: string | null;
  model?: string | null;
  project?: string | null;
  api_key_id?: string | null;
  deployment?: string | null;
  requests: number;
  errors: number;
  fallbacks: number;
  cache_hits: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number | null;
  unpriced_requests: number;
  avg_latency_ms: number | null;
  avg_ttft_ms: number | null;
  p50_latency_ms?: number | null;
  p95_latency_ms?: number | null;
}

export interface UsageReport {
  since: string;
  group_by: string;
  total: {
    requests: number;
    errors: number;
    fallbacks: number;
    cache_hits: number;
    cache_hit_rate: number;
    total_tokens: number;
    cost_usd: number | null;
    unpriced_requests: number;
    cost_complete: boolean;
  };
  groups: UsageGroup[];
}

export interface TeamUsage {
  team: string;
  scope: "team" | "mine";
  since: string;
  total: { requests: number; cache_hits: number; total_tokens: number; cost_usd: number | null; unpriced_requests: number };
  groups: UsageGroup[];
}

export interface DailyPoint {
  date: string;
  requests: number;
  cost_usd: number;
  cache_hits: number;
}

export interface SavingsRow {
  requests: number;
  measured_requests: number;
  cache_hits: number;
  actual_cost_usd: number;
  baseline_cost_usd: number;
  cache_avoided_usd: number;
  savings_usd: number;
  savings_pct: number | null;
  complete: boolean;
  project?: string | null;
}

export interface SavingsReport {
  since: string;
  baseline: string;
  group_by: string;
  total: SavingsRow;
  groups: SavingsRow[];
}

export interface Preset {
  label: string;
  type: string;
  base_url: string | null;
  default_model: string;
  models: string[];
  capabilities: string[];
  needs_key: boolean;
  key_url: string | null;
  auth_header: string;
  note: string | null;
  free_tier: boolean;
}

export interface ProviderRow {
  name: string;
  source: "gateway.toml" | "panel";
  preset: string | null;
  type: string;
  base_url: string | null;
  default_model: string;
  capabilities: string[] | null;
  enabled: boolean;
  key_hint: string | null;
  timeout_seconds: number;
  health: "closed" | "open" | "half_open" | null;
}

export interface TestResult {
  ok: boolean;
  model: string;
  latency_ms: number;
  reply?: string;
  error?: string;
  models?: string[];
  /** lightest model this key can use, from the live list */
  recommended?: string | null;
}

export interface AliasRow {
  alias: string;
  source: "gateway.toml" | "panel";
  overrides_toml: boolean;
  targets: string[];
  max_output_tokens: number | null;
  cache_ttl_seconds: number | null;
}

export interface PriceRow {
  key: string;
  source: "gateway.toml" | "panel";
  input_per_million: number;
  output_per_million: number;
}

export interface Person {
  id: string;
  email: string;
  name: string;
  role: Role;
  must_change_password: boolean;
  last_login_at: string | null;
  disabled: boolean;
  teams: { id: string; name: string; role: Role }[];
}

export interface ProviderHealth {
  provider: string;
  state: "closed" | "open" | "half_open";
  available: boolean;
  total_requests: number;
  total_failures: number;
  error_rate: number;
  in_flight: number;
  latency_ewma_ms: number | null;
  last_error: string | null;
}

export interface BudgetStatus {
  id: string;
  scope: "org" | "team" | "user";
  target: string;
  target_name?: string | null;
  period: "daily" | "monthly";
  label: string;
  limit_usd: number;
  spent_usd: number;
  reserved_usd: number;
  used_pct: number;
  limit_reached: boolean;
  period_start: string;
  resets_at: string;
}

export interface BudgetAlert {
  id: number;
  budget_id: string;
  budget: string;
  threshold_pct: number;
  spent_usd: number;
  limit_usd: number;
  period_start: string;
  created_at: string;
}
