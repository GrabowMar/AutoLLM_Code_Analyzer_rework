import { apiFetch } from "./core";

export interface RankingFindings {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

/** Spread over apps: repeats are averaged per (condition, app) first; `n_apps` units, `n_jobs` runs. */
export interface RankingVariance {
  n_jobs: number;
  n_apps?: number;
  density_per_kloc_mean: number | null;
  density_per_kloc_stdev: number | null;
  smoke_pass_rate_stdev: number | null;
}

export interface RankingInstrumentSet {
  tools_counted: string[];
  tools_left_out: string[];
  apps_measured: number;
}

export interface RankingRow {
  model_id: string;
  model_name: string;
  provider: string;
  is_free: boolean;
  context_length: number | null;
  price_per_million_input: number | null;
  price_per_million_output: number | null;
  apps: number;
  apps_completed: number;
  apps_failed?: number;
  /** Mean generation time (s); null until an app completed. */
  avg_duration: number | null;
  /**
   * Code issues the empirical score counts, by normalized level; null when
   * the instrument set measured no app of the model.
   */
  findings: RankingFindings | null;
  /** Known-vulnerability advisories; null when unmeasured or no advisory scanner is in the instrument set. */
  dependency_findings?: number | null;
  /** Dynamic tools' findings; null when unmeasured or no dynamic tool is in the instrument set. */
  runtime_findings?: number | null;
  /** Findings in test code; null when unmeasured. */
  test_findings?: number | null;
  /** How many experiment conditions the row pools (harness, prompt profile or arm). */
  conditions_pooled?: number;
  /** Set when the row pools several conditions; study reports compare them separately. */
  pooling_note?: string | null;
  /** The tools that measured every compared app (the empirical score's instrument). */
  instrument_set?: RankingInstrumentSet | null;
  ai_findings: RankingFindings | null;
  n_trials: number;
  functional_pass_rate: number | null;
  variance: RankingVariance | null;
  benchmark_score: number;
  cost_efficiency_score: number;
  accessibility_score: number;
  adoption_score: number;
  mss_score: number;
  empirical_quality_score: number | null;
  composite_score: number;
  [key: string]: unknown;
}

export interface RankingsListParams {
  page?: number;
  per_page?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  search?: string;
  provider?: string;
  max_price?: number;
  min_context?: number;
  min_composite?: number;
  include_free?: boolean;
  has_benchmarks?: boolean;
}

export interface RankingsPagination {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface RankingsResponse {
  rankings: RankingRow[];
  pagination: RankingsPagination;
  statistics: RankingsStatistics;
  filters_applied: Record<string, unknown>;
}

export interface RankingsStatistics {
  total: number;
  with_benchmarks: number;
  free_models: number;
  avg_mss: number;
  unique_providers: number;
}

export interface RankingsStatus {
  total_models: number;
  models_with_benchmarks: number;
  total_benchmark_rows: number;
  benchmarks: Record<string, number>;
}

export interface RankingsTopResponse {
  models: RankingRow[];
  count: number;
  weights: Record<string, number> | null;
}

function _qs(params: Record<string, unknown>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

export function exportRankingsUrl(): string {
  return "/api/rankings/export/";
}

export async function getRankings(
  params: RankingsListParams = {},
): Promise<RankingsResponse> {
  const res = await apiFetch(
    `/rankings/${_qs(params as Record<string, unknown>)}`,
  );
  return res.json();
}

export async function getRankingsStatus(): Promise<RankingsStatus> {
  const res = await apiFetch("/rankings/status/");
  return res.json();
}

export async function getTopModels(count = 10): Promise<RankingsTopResponse> {
  const res = await apiFetch(`/rankings/top/?count=${count}`);
  return res.json();
}

export async function refreshRankings(): Promise<RankingsStatus> {
  const res = await apiFetch("/rankings/refresh/", { method: "POST" });
  return res.json();
}

export interface SensitivityRankEntry {
  model_id: string;
  empirical_quality: number | null;
}

export interface SensitivityScheme {
  scheme: string;
  weights: Record<string, number>;
  ranking: SensitivityRankEntry[];
  kendall_tau: number;
  adjacent_swaps: string[][];
}

export interface SensitivityResponse {
  models_evaluated: number;
  baseline_ranking: SensitivityRankEntry[];
  schemes: SensitivityScheme[];
}

export async function getRankingsSensitivity(): Promise<SensitivityResponse> {
  const res = await apiFetch("/rankings/sensitivity/");
  return res.json();
}
