/**
 * Platform reports API (`/api/reports/`).
 *
 * Report data schema v2: every analysis number comes from the canonical
 * measured runs of the report's jobs under ONE analysis plan;
 * `analysis_coverage` counts the jobs that could not be measured and
 * `provenance` names the plan, rules, weights and run ids. Missing data is
 * `null` (never 0): mean is null when n=0, stdev null below n=2, and a
 * pairwise delta is null when either side has no observations.
 *
 * Type names avoid the study-report types of `$lib/api/analysis`
 * (`ReportStatus`, `ReportSummary`, `ReportTable`, …).
 */
import { apiFetch } from "./core";
import type {
  AnalysisCoverage,
  FindingsRollup,
  RankingVariance,
  SensitivityResponse,
} from "./rankings";

export type ReportGenerationStatus =
  "pending" | "generating" | "completed" | "failed";

export type ReportType =
  | "model_analysis"
  | "template_comparison"
  | "tool_analysis"
  | "generation_analytics"
  | "comprehensive"
  | "experiment_report";

export const REPORT_TYPES: readonly ReportType[] = [
  "experiment_report",
  "model_analysis",
  "template_comparison",
  "tool_analysis",
  "generation_analytics",
  "comprehensive",
];

/** Generator config keys the backend understands (all optional except per type). */
export interface ReportConfig {
  model_id?: string;
  template_slug?: string;
  experiment_id?: string;
  tool_name?: string;
  filter_model?: string;
  filter_models?: string[];
  bundle_key?: string;
  prompt_hash?: string;
  days?: number;
  /** Pin the analysis plan (64-char hash); default: the plan that measured most jobs. */
  plan_hash?: string;
  exclude_truncated?: boolean;
  exclude_invalid?: boolean;
  exclude_empty_output?: boolean;
  [key: string]: unknown;
}

export interface GenerateReportPayload {
  report_type: ReportType;
  config?: ReportConfig;
  title?: string;
  description?: string;
  expires_in_days?: number | null;
}

/** Compact denormalized summary for list rendering (keys depend on the type). */
export interface ReportListSummary {
  plan_hash?: string | null;
  measured_jobs?: number | null;
  mixed_plans?: boolean | null;
  total_findings?: number | null;
  total_loc?: number | null;
  total_jobs?: number;
  success_rate?: number | null;
  model_id?: string | null;
  template_slug?: string | null;
  model_count?: number;
  experiment_id?: string | null;
  experiment_name?: string | null;
  condition_count?: number;
  tool?: string | null;
  tool_count?: number;
  total_executions?: number;
  window_days?: number;
  platform?: Record<string, number | null>;
  [key: string]: unknown;
}

export interface ReportListItem {
  id: string;
  report_id: string;
  report_type: ReportType;
  title: string;
  description: string;
  status: ReportGenerationStatus;
  progress_percent: number;
  summary: ReportListSummary;
  error_message: string;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
  generation_job_id: string | null;
  analysis_run_id?: string | null;
  /** Legacy alias of `analysis_run_id`. */
  analysis_task_id: string | null;
}

export interface ReportDetail extends ReportListItem {
  config: ReportConfig;
  /** Typed per report type when `schema_version === 2` (see `reportDataV2`). */
  report_data: Record<string, unknown>;
}

export interface ReportListParams {
  report_type?: ReportType;
  status?: ReportGenerationStatus;
  limit?: number;
  offset?: number;
}

export interface ReportListResponse {
  reports: ReportListItem[];
  pagination: { total: number; limit: number; offset: number };
}

// ── Report data v2 ────────────────────────────────────────────────────

/** `_stat`: summary of the known values; `n_missing` trials lack the value. */
export interface ReportStatValue {
  mean: number | null;
  median?: number | null;
  stdev: number | null;
  n: number;
  n_missing?: number;
  /** Cost only: sum of the known values. */
  total?: number | null;
}

/** A binary outcome (e.g. `has_high_security`). */
export interface ReportShareValue {
  share_true: number | null;
  n: number;
  n_missing: number;
}

export type ReportOutcomeValue = ReportStatValue | ReportShareValue;

export function isShareValue(v: unknown): v is ReportShareValue {
  return !!v && typeof v === "object" && "share_true" in v;
}

export interface ReportProvenance {
  report_schema_version: number;
  generated_at: string;
  scoring_version?: string;
  severity_weights?: Record<string, number>;
  plan_hash?: string | null;
  plan_explicit?: boolean;
  plans?: Record<string, number>;
  mixed_plans?: boolean;
  complete_only?: boolean;
  exclusion_rules?: Record<string, boolean | number>;
  /** Canonical runs every analysis number comes from. */
  run_ids?: string[];
  /** Tool analysis: what the report spans. */
  scope?: string;
}

/** Generation-side summary of a group of jobs. */
export interface ReportJobSummary {
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  /** Of the finished jobs; null when none finished. */
  success_rate: number | null;
  avg_duration: number | null;
  by_status: Record<string, number>;
  /** Completed jobs whose generation was cut off. */
  truncated_jobs: number;
  total_cost: number | null;
}

/** SLOC of the measured runs (the same jobs the findings come from). */
export interface ReportLocBlock {
  source: string;
  measured_loc: number | null;
  total_loc: number | null;
  counted: number;
  per_job: {
    job_id: string;
    run_id: string;
    measured_loc: number | null;
    total_loc: number | null;
  }[];
}

/** LOC of generation jobs resolved by the sample resolver (generation analytics). */
export interface ReportGenerationLoc {
  backend_loc: number | null;
  frontend_loc: number | null;
  total_loc: number | null;
  measured_loc: number | null;
  counted: number;
  unavailable: number;
  per_job: {
    job_id: string;
    backend_loc: number | null;
    frontend_loc: number | null;
    total_loc: number | null;
    measured_loc: number | null;
  }[];
}

export interface ReportFunctionalStats {
  jobs_probed: number;
  jobs_passed: number;
  jobs_unknown: number;
  pass_rate: ReportStatValue;
}

/** Trial-level statistics of one group (model, condition, template row). */
export interface ReportTrialStats {
  /** Eligible completed jobs. */
  trials: number;
  /** Trials with a canonical measured run. */
  analyzed: number;
  truncated_excluded: number;
  excluded: number;
  excluded_reasons: Record<string, number>;
  analysis: AnalysisCoverage;
  prompt_hashes: string[];
  mixed_prompt_versions: boolean;
  findings_total: ReportStatValue;
  critical_high: ReportStatValue;
  issues_total: ReportStatValue;
  weighted_score: ReportStatValue;
  weighted_per_kloc: ReportStatValue;
  ai_findings_total: ReportStatValue;
  ai_critical_high: ReportStatValue;
  /** One entry per key of `measurement.outcomes()`. */
  outcomes: Record<string, ReportOutcomeValue>;
  cost: ReportStatValue;
  duration: ReportStatValue;
  tokens: ReportStatValue;
  functional: ReportFunctionalStats;
}

/** The /rankings empirical inputs of a group (counts are zero-filled; read `n_*`). */
export interface ReportEmpirical {
  n_trials: number;
  n_static: number;
  n_ai: number;
  findings: FindingsRollup;
  issues: FindingsRollup;
  ai_findings: FindingsRollup;
  measured_sloc: number | null;
  functional_pass_rate: number | null;
  functional_n: number;
  variance: RankingVariance;
}

/** Generation summary + measured analysis of one group of jobs. */
export interface ReportGroupRow {
  generation: ReportJobSummary;
  loc: ReportLocBlock;
  /** null when no measured run has an available static measurement. */
  findings: FindingsRollup | null;
  total_findings: number | null;
  issues: FindingsRollup | null;
  total_issues: number | null;
  /** null when no measured run has a successful AI review. */
  ai_findings: FindingsRollup | null;
  ai_total: number | null;
  stats: ReportTrialStats;
  empirical: ReportEmpirical;
}

/** Per-tool execution outcome in the measured runs. */
export interface ReportToolExecutionRow {
  analyzer: string;
  tasks: number;
  succeeded: number;
  statuses: Record<string, number>;
  versions: string[];
}

export interface ReportToolMetrics {
  results_with_metrics: number;
  numeric: Record<string, { avg: number; min: number; max: number }>;
  distributions: Record<string, Record<string, number>>;
}

/** Tool behaviour across every non-canary execution. */
export interface ReportToolBehaviourRow {
  analyzer: string;
  executions: number;
  statuses: Record<string, number>;
  failure_reasons: Record<string, number>;
  versions: string[];
  /** Findings of the succeeded executions. */
  total: number;
  counted: number;
  by_severity: FindingsRollup;
  scans: number;
  failed: number;
  timed_out: number;
  not_applicable: number;
  /** Of the executions that actually ran on files; null when none did. */
  success_rate: number | null;
  findings_per_scan: number | null;
  mean_duration_s: number | null;
}

export interface ModelAnalysisReportData extends ReportGroupRow {
  schema_version: 2;
  model: { model_id: string; model_name: string; provider: string };
  tools: ReportToolExecutionRow[];
  metrics_by_tool: Record<string, ReportToolMetrics>;
  analysis_coverage: AnalysisCoverage;
  provenance: ReportProvenance;
}

export interface TemplateComparisonRow extends ReportGroupRow {
  model_id: string;
  model_name: string;
  provider: string;
  bundle_key: string;
}

export interface TemplateComparisonReportData {
  schema_version: 2;
  template: { slug: string; name: string; category: string };
  models: TemplateComparisonRow[];
  total_models: number;
  bundle_keys: string[];
  mixed_bundle_versions: boolean;
  sensitivity: SensitivityResponse;
  analysis_coverage: AnalysisCoverage;
  provenance: ReportProvenance;
}

export interface ToolAnalysisReportData {
  schema_version: 2;
  tool_filter: string | null;
  tools: ReportToolBehaviourRow[];
  metrics_by_tool: Record<string, ReportToolMetrics>;
  total_findings: number;
  total_tasks: number;
  total_executions: number;
  provenance: ReportProvenance;
}

export interface GenerationAnalyticsReportData {
  schema_version: 2;
  window_days: number;
  since: string;
  summary: ReportJobSummary;
  loc: ReportGenerationLoc;
  by_model: {
    model_id: string | null;
    model_name: string | null;
    jobs: number;
    completed: number;
    success_rate: number | null;
    avg_duration: number | null;
  }[];
}

export interface ComprehensiveReportData {
  schema_version: 2;
  generated_at: string;
  generation_analytics: GenerationAnalyticsReportData;
  tool_analysis: ToolAnalysisReportData;
  rankings_sensitivity: SensitivityResponse;
  platform: {
    total_models: number;
    total_jobs: number;
    total_tasks: number;
    measured_jobs: number;
    plans_in_use: number;
    total_findings: number | null;
    total_issues: number | null;
    total_ai_findings: number | null;
  };
}

export interface ExperimentConditionRow extends ReportGroupRow {
  condition_id: number;
  label: string;
  model_id: string;
  model_name: string;
  bundle_key: string;
}

/** Descriptive A/B difference of one metric (b − a); null when a side has n=0. */
export interface ReportDelta {
  a: number;
  b: number;
  n_a: number;
  n_b: number;
  delta: number;
  pct_change: number | null;
}

export interface ReportConditionComparison {
  condition_a: { id: number; label: string };
  condition_b: { id: number; label: string };
  deltas: Record<string, ReportDelta | null>;
}

export interface ExperimentReportData {
  schema_version: 2;
  experiment: {
    id: string;
    name: string;
    slug: string;
    hypothesis: string;
    status: string;
    repeats: number;
  };
  conditions: ExperimentConditionRow[];
  total_conditions: number;
  comparisons: ReportConditionComparison[];
  analysis_coverage: AnalysisCoverage;
  provenance: ReportProvenance;
}

export interface ReportDataByType {
  model_analysis: ModelAnalysisReportData;
  template_comparison: TemplateComparisonReportData;
  tool_analysis: ToolAnalysisReportData;
  generation_analytics: GenerationAnalyticsReportData;
  comprehensive: ComprehensiveReportData;
  experiment_report: ExperimentReportData;
}

export type TypedReportData = {
  [K in ReportType]: { type: K; data: ReportDataByType[K] };
}[ReportType];

/**
 * The report's data typed by its report type, or null for a legacy (v1)
 * or unknown payload — render those with a generic viewer.
 */
export function reportDataV2(report: {
  report_type: string;
  report_data: Record<string, unknown> | null;
}): TypedReportData | null {
  const data = report.report_data;
  if (!data || data.schema_version !== 2) return null;
  if (!(REPORT_TYPES as readonly string[]).includes(report.report_type)) {
    return null;
  }
  return {
    type: report.report_type,
    data,
  } as unknown as TypedReportData;
}

// ── Requests ──────────────────────────────────────────────────────────

export interface ReportsRequestOptions {
  signal?: AbortSignal;
}

export async function deleteReport(
  reportId: string,
): Promise<{ success: boolean; message: string }> {
  const res = await apiFetch(`/reports/${reportId}/`, { method: "DELETE" });
  return res.json();
}

/** Starts generation in the background (400 `{success, message}` on bad config). */
export async function generateReport(
  payload: GenerateReportPayload,
): Promise<ReportListItem> {
  const res = await apiFetch("/reports/generate/", {
    method: "POST",
    body: JSON.stringify({ config: {}, ...payload }),
  });
  return res.json();
}

export async function getReport(
  reportId: string,
  options: ReportsRequestOptions = {},
): Promise<ReportDetail> {
  const res = await apiFetch(`/reports/${reportId}/`, {
    signal: options.signal,
  });
  return res.json();
}

/** 400 `{message: "Report not ready (status: …)"}` until the report completes. */
export async function getReportData(reportId: string): Promise<{
  report_id: string;
  report_type: ReportType;
  title: string;
  status: ReportGenerationStatus;
  progress: number;
  data: Record<string, unknown> | null;
}> {
  const res = await apiFetch(`/reports/${reportId}/data/`);
  return res.json();
}

export async function getReports(
  params: ReportListParams = {},
  options: ReportsRequestOptions = {},
): Promise<ReportListResponse> {
  const query = new URLSearchParams();
  if (params.report_type) query.set("report_type", params.report_type);
  if (params.status) query.set("status", params.status);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  const res = await apiFetch(`/reports/${qs ? `?${qs}` : ""}`, {
    signal: options.signal,
  });
  return res.json();
}

/** True while the report is still being produced (poll it). */
export function isReportInFlight(status: ReportGenerationStatus): boolean {
  return status === "pending" || status === "generating";
}
