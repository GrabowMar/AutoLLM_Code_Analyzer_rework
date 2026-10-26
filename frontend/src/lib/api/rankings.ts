/**
 * Rankings API (`/api/rankings/`).
 *
 * Every endpoint is scoped to the requesting user's generation jobs and
 * honours the same filters, so the list, the sensitivity analysis and the
 * CSV export always describe one population measured under one analysis
 * plan (`comparability` names it and counts the jobs left out).
 */
import { apiFetch } from "./core";

/** Severities, most severe first. */
export const RANKING_SEVERITIES = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
] as const;
export type RankingSeverity = (typeof RANKING_SEVERITIES)[number];

/** Counts per severity (zero-filled; check `n_static`/`n_ai` before reading). */
export type FindingsRollup = Record<RankingSeverity, number>;
/** @deprecated Use `FindingsRollup`. */
export type RankingFindings = FindingsRollup;

/** Per-trial spread of the empirical inputs (stdev is null below n=2). */
export interface RankingVariance {
  n_jobs: number;
  density_per_kloc_mean: number | null;
  density_per_kloc_stdev: number | null;
  smoke_pass_rate_stdev: number | null;
}

/**
 * Where a set of completed jobs went under the reference plan. Only
 * `measured` jobs feed the empirical numbers; every other job is missing
 * data, never zero findings.
 */
export interface RankingAnalysisFunnel {
  measured: number;
  excluded: number;
  excluded_reasons: Record<string, number>;
  incomplete: number;
  other_plan: number;
  analysis_failed: number;
  in_progress: number;
  not_analysed: number;
}

/**
 * Analysis coverage of a set of jobs under one plan (rankings
 * `comparability`, report `analysis_coverage`, trial `stats.analysis`).
 */
export interface AnalysisCoverage extends RankingAnalysisFunnel {
  /** Reference plan; null when nothing was analysed. */
  plan_hash: string | null;
  /** True when the caller pinned the plan, false when it was auto-selected. */
  plan_explicit: boolean;
  /** Only complete runs count (partial runs are reported as `incomplete`). */
  complete_only: boolean;
  /** Plan hash → number of jobs with a canonical run under it. */
  plans: Record<string, number>;
  mixed_plans: boolean;
  jobs: number;
}

export interface RankingsScopeFilters {
  prompt_hash: string | null;
  bundle_key: string | null;
  experiment_id: string | null;
  plan_hash: string | null;
  user_scoped: boolean;
}

/** `comparability` of the list/sensitivity responses. */
export interface RankingsComparability extends AnalysisCoverage {
  scoring_version: string;
  severity_weights: Record<RankingSeverity, number>;
  /** Weighted issue density (per KLOC) that scores zero. */
  density_cap: number;
  density_basis: string;
  filters: RankingsScopeFilters;
}

export interface RankingRow {
  model_id: string;
  model_name: string;
  provider: string;
  is_free: boolean;
  context_length: number | null;
  price_per_million_input: number | null;
  price_per_million_output: number | null;
  /** All of the model's jobs in scope / the completed ones. */
  apps: number;
  apps_completed: number;
  avg_duration: number;
  /** Counted static findings of the analysed trials (meaningful only when `n_static > 0`). */
  findings: FindingsRollup;
  /** Counted static issues (deduplicated across tools) — the score's numerator. */
  issues?: FindingsRollup | null;
  /** AI consensus findings (opinions; never scored; meaningful only when `n_ai > 0`). */
  ai_findings: FindingsRollup | null;
  /** Analysed trials: complete, eligible jobs measured under the reference plan. */
  n_trials: number;
  /** Analysed trials whose static measurement is available. */
  n_static?: number;
  /** Analysed trials with a successful AI review. */
  n_ai?: number;
  /** Measured SLOC of the static-measured trials (null when none). */
  measured_sloc?: number | null;
  local_loc?: number;
  functional_pass_rate: number | null;
  functional_n?: number;
  variance: RankingVariance | null;
  /** null when the model has no completed job in scope. */
  analysis?: RankingAnalysisFunnel | null;
  run_ids?: string[];
  plan_hash?: string | null;
  scoring_version?: string;
  benchmark_score: number;
  cost_efficiency_score: number;
  accessibility_score: number;
  adoption_score: number;
  mss_score: number;
  empirical_quality_score: number | null;
  composite_score: number;
  /** Benchmark scores (`humaneval`, `mbpp`, `swebench`, …) and metadata pass through. */
  [key: string]: unknown;
}

/** Public sort keys accepted by `sort_by`. */
export type RankingSortKey =
  | "mss"
  | "empirical"
  | "composite"
  | "benchmark"
  | "cost_efficiency"
  | "accessibility"
  | "adoption"
  | "n_trials"
  | "apps"
  | "name"
  | "price"
  | "context";

/** Which jobs and plan the empirical columns are computed over. */
export interface RankingsScope {
  experiment_id?: string;
  prompt_hash?: string;
  bundle_key?: string;
  /** Pin the analysis plan (64-char hash); default: the plan that measured most jobs. */
  plan_hash?: string;
}

/** Scope + row filters shared by the list, sensitivity and export endpoints. */
export interface RankingsFilters extends RankingsScope {
  search?: string;
  provider?: string;
  max_price?: number;
  min_context?: number;
  min_composite?: number;
  include_free?: boolean;
  has_benchmarks?: boolean;
}

export interface RankingsListParams extends RankingsFilters {
  page?: number;
  per_page?: number;
  sort_by?: RankingSortKey;
  sort_dir?: "asc" | "desc";
}

export interface RankingsPagination {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface RankingsStatistics {
  total: number;
  with_benchmarks: number;
  free_models: number;
  avg_mss: number;
  unique_providers: number;
  /** Models with an empirical score (a usable static measurement). */
  measured_models?: number;
}

export interface RankingsResponse {
  count?: number;
  rankings: RankingRow[];
  pagination: RankingsPagination;
  statistics: RankingsStatistics;
  filters_applied: Record<string, unknown>;
  comparability?: RankingsComparability | null;
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
  weights?: Record<string, number> | null;
}

export interface SensitivityRankEntry {
  model_id: string;
  empirical_quality: number | null;
}

export interface SensitivityScheme {
  scheme: string;
  weights: Record<RankingSeverity, number>;
  ranking: SensitivityRankEntry[];
  /** A placeholder (1.0) when `defined` is false — never evidence of stability. */
  kendall_tau: number;
  /** False below two measured models. */
  defined?: boolean;
  adjacent_swaps: string[][];
}

export interface SensitivityResponse {
  models_evaluated: number;
  /** False below two measured models: every tau is then a placeholder. */
  defined?: boolean;
  scoring_version?: string;
  baseline_ranking: SensitivityRankEntry[];
  schemes: SensitivityScheme[];
  comparability?: RankingsComparability | null;
}

export interface RankingsRequestOptions {
  signal?: AbortSignal;
}

/** `?a=1&b=x` from a params object; `undefined`, `null` and `""` are left out. */
function _qs(params: object): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params) as [string, unknown][]) {
    if (v === undefined || v === null || v === "") continue;
    usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

/**
 * CSV download URL carrying the current scope, filters and sort, so the
 * file describes exactly the population on screen.
 */
export function exportRankingsUrl(
  params: RankingsFilters & {
    sort_by?: RankingSortKey;
    sort_dir?: "asc" | "desc";
  } = {},
): string {
  return `/api/rankings/export/${_qs(params)}`;
}

export async function getRankings(
  params: RankingsListParams = {},
  options: RankingsRequestOptions = {},
): Promise<RankingsResponse> {
  const res = await apiFetch(`/rankings/${_qs(params)}`, {
    signal: options.signal,
  });
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

/** Rank stability under alternative severity weights (same filters as the list). */
export async function getRankingsSensitivity(
  filters: RankingsFilters = {},
  options: RankingsRequestOptions = {},
): Promise<SensitivityResponse> {
  const res = await apiFetch(`/rankings/sensitivity/${_qs(filters)}`, {
    signal: options.signal,
  });
  return res.json();
}

/** Total of a severity rollup. */
export function rollupTotal(
  rollup: Partial<FindingsRollup> | null | undefined,
): number {
  if (!rollup) return 0;
  return RANKING_SEVERITIES.reduce((sum, s) => sum + (rollup[s] ?? 0), 0);
}

/** Jobs of a funnel that are not in the empirical numbers. */
export function unmeasuredCount(
  funnel: RankingAnalysisFunnel | null | undefined,
): number {
  if (!funnel) return 0;
  return (
    funnel.excluded +
    funnel.incomplete +
    funnel.other_plan +
    funnel.analysis_failed +
    funnel.in_progress +
    funnel.not_analysed
  );
}
