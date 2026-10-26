/**
 * Statistics API (`/api/statistics/`).
 *
 * Totals and severity come from each analysed job's canonical measured run
 * (a re-analysis never inflates them); AI-reviewer consensus is reported
 * apart from the static measurement. Tool effectiveness counts only
 * succeeded executions as scans. Analyzer health comes from the canary
 * self-tests on the current analyzer image, not from catalog flags.
 */
import { apiFetch } from "./core";

export interface StatisticsRequestOptions {
  signal?: AbortSignal;
}

export interface TrendPoint {
  date: string;
  label: string;
  total: number;
  completed: number;
  failed: number;
  /** Finished with a required tool that did not succeed. */
  partial?: number;
}

export interface AnalysisTrends {
  days: number;
  total: number;
  series: TrendPoint[];
}

/** Self-test verdict of a tool on the current analyzer image. */
export type AnalyzerHealthStatus =
  "passed" | "failed" | "untested" | "disabled";

export interface AnalyzerHealthRow {
  /** Tool slug. */
  name: string;
  /** Tool category (security, lint, …). */
  type: string;
  display_name: string;
  /** True only when enabled AND passing the latest self-test. */
  available: boolean;
  message: string;
  /** "" from an older backend. */
  status?: AnalyzerHealthStatus | "";
  version?: string;
}

/** The latest self-test (canary) run on the current analyzer image. */
export interface AnalyzerSelftest {
  passed: boolean | null;
  run_id: string | null;
  image_tag: string | null;
  evaluated_at: string | null;
}

export interface AnalyzerHealth {
  total: number;
  /** Tools that passed the latest self-test (and are enabled). */
  online: number;
  offline: number;
  by_type: Record<string, Record<string, number>>;
  analyzers: AnalyzerHealthRow[];
  /** null when no self-test has run on this image (or it could not be read). */
  selftest?: AnalyzerSelftest | null;
}

export interface CodeGenerationStats {
  total_apps: number;
  completed: number;
  failed: number;
  running: number;
  success_rate: number;
  avg_duration_seconds: number;
  total_tokens: number;
  total_cost_usd: number;
  /** SLOC of included files of the analysed jobs (lockfiles/build output excluded). */
  total_lines_of_code: number;
  unique_templates: number;
  /** SLOC of the code the model wrote (measured scope) of the analysed jobs. */
  measured_lines_of_code?: number;
  /** Analysed jobs the LOC totals cover (0 → the totals are not available). */
  loc_jobs_covered?: number;
  unique_stacks?: number;
}

/** Shared rankings scoring — mss/empirical/composite are 0..1 scales. */
export interface ModelComparisonRow {
  model_id: string;
  name: string;
  provider: string;
  apps: number;
  apps_completed: number;
  success_rate: number;
  avg_duration_seconds: number;
  mss: number;
  empirical_quality: number | null;
  composite: number;
  smoke_pass_rate: number | null;
  /** Analysed trials (complete, eligible, canonical runs of the reference plan). */
  n_trials: number;
  /** Counted static findings of the analysed trials. */
  findings: Record<string, number>;
  /** AI consensus findings (opinions, never scored). */
  ai_findings: Record<string, number>;
  issues?: Record<string, number> | null;
  /** null when no analysed trial has a static measurement. */
  measured_sloc?: number | null;
  plan_hash?: string | null;
}

export interface RecentActivityItem {
  kind: string;
  id: string;
  title: string;
  status: string;
  created_at: string;
}

export interface SeverityBucket {
  severity: string;
  count: number;
  percent: number;
}

/**
 * Severity mix. `distribution`/`total` add static and AI together — show
 * `by_source.static` (counted static findings) and `by_source.ai` (AI
 * consensus) separately instead.
 */
export interface SeverityDistribution {
  total: number;
  distribution: SeverityBucket[];
  by_source?: {
    static: Record<string, number>;
    ai: Record<string, number>;
  } | null;
}

export interface StatisticsOverview {
  total_models: number;
  models_in_use: number;
  total_apps: number;
  apps_completed: number;
  apps_failed: number;
  apps_running: number;
  apps_pending: number;
  apps_success_rate: number;
  total_analyses: number;
  analyses_completed: number;
  analyses_failed: number;
  analyses_running: number;
  analyses_pending: number;
  /** Completed (every required tool succeeded) of the finished analyses, %. */
  analyses_success_rate: number;
  avg_analysis_duration_seconds: number;
  /** Counted static findings of each analysed job's canonical run. */
  total_findings: number;
  analyses_partial?: number;
  analyses_cancelled?: number;
  /** AI consensus findings of the same runs (opinions). */
  total_ai_findings?: number;
  /** Jobs with a complete measured run (0 → the finding totals are not available). */
  measured_jobs?: number;
}

export interface ToolEffectivenessRow {
  /** Tool slug. */
  name: string;
  /** Tool category. */
  type: string;
  /** Succeeded executions only. */
  scans: number;
  /** Findings of the succeeded executions. */
  findings: number;
  /** 0 when `scans` is 0 — read `scans` first. */
  avg_per_scan: number;
  top_rule: string;
  /** All executions in the canonical runs. */
  runs?: number;
  failed?: number;
  timed_out?: number;
  not_applicable?: number;
  skipped?: number;
  /** succeeded / (succeeded + failed + timed out); null with no attempted execution. */
  success_rate?: number | null;
  versions?: string[];
}

export interface TopFindingRow {
  title: string;
  severity: string;
  count: number;
  rule_id?: string;
  tool?: string;
  is_ai?: boolean;
}

export interface StatisticsDashboard {
  overview: StatisticsOverview;
  severity: SeverityDistribution;
  trends: AnalysisTrends;
  models: ModelComparisonRow[];
  tools: ToolEffectivenessRow[];
  top_findings: TopFindingRow[];
  code_generation: CodeGenerationStats;
  analyzer_health: AnalyzerHealth;
  recent_activity: RecentActivityItem[];
}

export interface StatisticsModelsParams {
  limit?: number;
  experiment_id?: string;
  prompt_hash?: string;
  bundle_key?: string;
  plan_hash?: string;
}

function _qs(params: object): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params) as [string, unknown][]) {
    if (v === undefined || v === null || v === "") continue;
    usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

async function getJson<T>(
  path: string,
  options: StatisticsRequestOptions = {},
): Promise<T> {
  const res = await apiFetch(path, { signal: options.signal });
  return res.json();
}

export function getStatisticsAnalyzerHealth(
  options?: StatisticsRequestOptions,
): Promise<AnalyzerHealth> {
  return getJson("/statistics/analyzer-health/", options);
}

export function getStatisticsCodeGeneration(
  options?: StatisticsRequestOptions,
): Promise<CodeGenerationStats> {
  return getJson("/statistics/code-generation/", options);
}

export function getStatisticsDashboard(
  options?: StatisticsRequestOptions,
): Promise<StatisticsDashboard> {
  return getJson("/statistics/dashboard/", options);
}

/** Per-model rollup on the rankings scoring; `limit` (or a params object). */
export function getStatisticsModels(
  params: number | StatisticsModelsParams = {},
  options?: StatisticsRequestOptions,
): Promise<ModelComparisonRow[]> {
  const query = typeof params === "number" ? { limit: params } : params;
  return getJson(`/statistics/models/${_qs(query)}`, options);
}

export function getStatisticsOverview(
  options?: StatisticsRequestOptions,
): Promise<StatisticsOverview> {
  return getJson("/statistics/overview/", options);
}

export function getStatisticsRecentActivity(
  limit = 20,
  options?: StatisticsRequestOptions,
): Promise<RecentActivityItem[]> {
  return getJson(`/statistics/recent-activity/?limit=${limit}`, options);
}

export function getStatisticsSeverity(
  options?: StatisticsRequestOptions,
): Promise<SeverityDistribution> {
  return getJson("/statistics/severity/", options);
}

export function getStatisticsTools(
  options?: StatisticsRequestOptions,
): Promise<ToolEffectivenessRow[]> {
  return getJson("/statistics/tools/", options);
}

export function getStatisticsTopFindings(
  limit = 10,
  options?: StatisticsRequestOptions,
): Promise<TopFindingRow[]> {
  return getJson(`/statistics/top-findings/?limit=${limit}`, options);
}

export function getStatisticsTrends(
  days = 14,
  options?: StatisticsRequestOptions,
): Promise<AnalysisTrends> {
  return getJson(`/statistics/trends/?days=${days}`, options);
}

/** Severity counts as a `SeverityDistribution` (e.g. static only, for a donut). */
export function severityDistributionOf(
  counts: Record<string, number> | null | undefined,
): SeverityDistribution {
  const order = ["critical", "high", "medium", "low", "info"];
  const total = order.reduce((sum, s) => sum + (counts?.[s] ?? 0), 0);
  return {
    total,
    distribution: order.map((severity) => {
      const count = counts?.[severity] ?? 0;
      return {
        severity,
        count,
        percent: total ? Math.round((count / total) * 1000) / 10 : 0,
      };
    }),
    by_source: null,
  };
}
