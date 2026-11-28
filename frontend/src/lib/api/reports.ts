import { apiFetch } from "./core";

export type ReportStatus = "pending" | "generating" | "completed" | "failed";

/**
 * Stored report type values (unchanged in v3, so existing rows keep working). Two of them
 * have new meanings: `template_comparison` is the **task comparison** (config `task_slug`,
 * optional `task_version`, `filter_models`, `treatment_hash`, `prompt_hash`) and
 * `experiment_report` the **study conditions report** (config `study_id`).
 */
export type ReportType =
  | "model_analysis"
  | "template_comparison"
  | "tool_analysis"
  | "generation_analytics"
  | "comprehensive"
  | "experiment_report"
  | "study_report";

/** Display labels of the report types (the backend `Report.Type` labels). */
export const REPORT_TYPE_LABELS: Record<ReportType, string> = {
  model_analysis: "Model analysis",
  template_comparison: "Task comparison",
  tool_analysis: "Tool analysis",
  generation_analytics: "Generation analytics",
  comprehensive: "Comprehensive",
  experiment_report: "Experiment conditions",
  study_report: "Experiment report",
};

/** `config` of a task comparison (`template_comparison`). */
export interface TaskComparisonConfig {
  task_slug: string;
  task_version?: number;
  filter_models?: string[];
  treatment_hash?: string;
  prompt_hash?: string;
  /** Sensitivity analysis: leave truncated runs out. Omit for intention-to-treat (the default). */
  exclude_truncated?: boolean;
}

/** `config` of a study conditions report (`experiment_report`). */
export interface StudyConditionsConfig {
  study_id: string;
  /**
   * Sensitivity analysis: leave truncated runs out. Omit for intention-to-treat; a locked
   * analysis plan's `exclusions.truncated` wins over it.
   */
  exclude_truncated?: boolean;
}

/**
 * `config` of a study report: a study and/or explicit runs (none: all my apps), or, with
 * `selection: "latest_unique"`, the newest usable run per model and task pooled across experiments
 * (`study_ids` is the candidate pool; `study_id` is then refused).
 */
export interface StudyReportConfig {
  study_id?: string;
  run_ids?: string[];
  /** Cross-experiment sample: the newest usable run per (model, task) unit. */
  selection?: "latest_unique";
  /** Candidate experiments for `selection`; empty or absent = every experiment you can see. */
  study_ids?: string[];
  /** `LLMModel.model_id`s to keep; empty or absent = every model. */
  models?: string[];
  /** Task refs `slug@version`, or a bare `slug` for any version; empty or absent = every task. */
  tasks?: string[];
  /** Complete cases: keep only tasks for which every group has a selected run. */
  common_tasks_only?: boolean;
  /** One versioned instrument for every app; missing evaluations never fall back to another suite. */
  eval_suite?: string;
  /**
   * Sensitivity analysis: leave truncated runs out. Send it only when the researcher chose it
   * (an explicit value that differs from a locked plan's exclusions adds a validity note).
   */
  exclude_truncated?: boolean;
  ranking_criteria?: unknown;
  /**
   * What counts as the delivered app. Unset: tests and the agent's scratch scripts are counted apart.
   * `with_scratch` counts scratch scripts as app code, `all` counts test code too.
   */
  code_scope?: "app" | "app_named" | "with_scratch" | "all";
}

/** A unit's state in a cross-experiment selection (`report_data.selection.matrix[].status`). */
export type SelectionCellStatus =
  | "selected"
  | "attrition"
  | "missing"
  | "in_flight";

/** Why an attempt of a unit is not in the sample. */
export type SupersededReason =
  | "newer_usable_run"
  | "unusable_attempt"
  | "in_flight";

export interface SelectionStudy {
  id: string;
  name: string;
  status: string;
  /** Every run of the experiment visible to the user, before exclusions and filters. */
  runs_total?: number;
  runs_considered: number;
  runs_selected: number;
}

export interface SelectionCounts {
  candidates: number;
  selected: number;
  superseded: number;
  attrition_cells: number;
  missing_cells: number;
  in_flight_cells: number;
  pilot_excluded: number;
  beyond_plan_excluded: number;
  reference_excluded: number;
  filtered_out: number;
  /** Runs of a plan the experiment has since replaced. */
  superseded_plan_excluded: number;
  dropped_tasks: number;
  /** Selected runs left out with their dropped tasks (complete cases). */
  dropped_runs: number;
  /** Selected runs left out as truncated (`exclude_truncated`, before complete cases). */
  truncated_excluded: number;
}

/** One group of the selection: a model (pooled across experiments and conditions) or one treatment of it. */
export interface SelectionGroup {
  group: string;
  model: string;
  treatment_hash: string | null;
  conditions: number[];
  /** Experiment ids the group's selected runs come from. */
  studies: string[];
  /** Budget presets (`ResourcePolicy` refs) of the selected runs. */
  resource_policies: string[];
  /** Selected runs per Budget preset ref. */
  resource_policy_runs?: Record<string, number>;
  /** Runs in the analysed sample (after the truncation and complete-case exclusions). */
  tasks_selected: number;
  tasks_in_scope: number;
  /** Selected runs per outcome. */
  outcomes: Record<string, number>;
  /** Selected runs left out as truncated. */
  truncated_excluded?: number;
  /** Selected runs left out with a dropped task. */
  dropped_runs?: number;
}

/** Why a selected run is not in the sample. */
export type SelectionExclusion = "truncated" | "dropped_task";

/** One (group, task) unit of the selection matrix. */
export interface SelectionCell {
  group: string;
  task: string;
  status: SelectionCellStatus;
  run_id: string | null;
  study_id: string | null;
  study_name: string | null;
  outcome: string | null;
  created_at: string | null;
  /** Attempts of the unit (selected + superseded). */
  attempts: number;
  superseded_run_ids: string[];
  /** False unless the cell's run is in the analysed sample (every unselected unit; a dropped or truncated run). */
  in_sample?: boolean;
  /** Why a selected run is left out: truncated, or its task was dropped for complete cases. */
  excluded?: SelectionExclusion | null;
}

/** An attempt left out of the sample, and why. */
export interface SelectionSuperseded {
  run_id: string;
  group: string;
  task: string;
  study_id: string | null;
  study_name: string | null;
  outcome: string | null;
  usable: boolean | null;
  created_at: string | null;
  /** The selected run that replaced it; null when the unit has no selected run. */
  replaced_by: string | null;
  /** The unit's newest attempt when it has no selected run (attrition, in flight). */
  recorded_attempt?: string | null;
  reason: SupersededReason | string;
}

export interface SelectionLaunch {
  /** `AnalysisRun.launch_id`, or "none". */
  launch_id: string;
  example_name: string | null;
  analysis_runs: number;
  results_selected: number;
  results_superseded: number;
}

/** The same breakdown by analysis name without its run suffix ("FINAL static"). */
export interface SelectionAnalysisLabel {
  label: string;
  example_name: string | null;
  analysis_runs: number;
  results_selected: number;
  results_superseded: number;
}

/** Which analysis results of the selected runs count (per run and tool, the newest measured one). */
export interface SelectionAnalyses {
  rule: string;
  analysis_runs_total: number;
  analysis_runs_contributing: number;
  analysis_runs_superseded: number;
  tool_results_total: number;
  tool_results_selected: number;
  tool_results_superseded: number;
  older_measured_preferred: number;
  by_launch: SelectionLaunch[];
  by_label: SelectionAnalysisLabel[];
  tools_mixed_launches: { tool: string; launches: number }[];
  tools_mixed_labels: { tool: string; labels: string[] }[];
}

/** `report_data.selection` of a study report with `selection: "latest_unique"`. */
export interface ReportSelection {
  mode: "latest_unique";
  rule: string;
  filters: {
    study_ids: string[];
    models: string[] | null;
    tasks: string[] | null;
    common_tasks_only: boolean;
    /** Truncated selected runs are left out (sensitivity). */
    exclude_truncated?: boolean;
  };
  studies: SelectionStudy[];
  counts: Partial<SelectionCounts>;
  groups: SelectionGroup[];
  tasks: string[];
  matrix: SelectionCell[];
  superseded: SelectionSuperseded[];
  analyses: SelectionAnalyses | null;
  /** Tasks every group has a run in the sample for (always computed). */
  common_tasks: string[];
  /** Tasks left out by `common_tasks_only` (empty when it is off). */
  dropped_tasks: string[];
  /** Per group, the dropped tasks it has no run for, and why (complete cases). */
  dropped_by_group?: {
    group: string;
    tasks: number;
    reasons: Record<string, number>;
  }[];
}

/**
 * The exclude-truncated part of a report config: `{exclude_truncated: true}` when the researcher
 * ticked the sensitivity option, else nothing. An untouched option must not send `false`: the
 * backend treats any explicit value that differs from a locked plan's exclusions as an override
 * and notes it in the report.
 */
export function excludeTruncatedConfig(excludeTruncated: boolean): {
  exclude_truncated?: true;
} {
  return excludeTruncated ? { exclude_truncated: true } : {};
}

export type ReportExportFormat = "md" | "tex" | "json";

/** Download URL of a completed report as Markdown, LaTeX tables or JSON. */
export function reportExportUrl(
  reportId: string,
  format: ReportExportFormat,
): string {
  return `/api/reports/${encodeURIComponent(reportId)}/export/?format=${format}`;
}

/** One curated table of a report, as the server renders it (`renderers.report_tables`). */
export interface ReportTable {
  title: string;
  /** Column headers. */
  columns: string[];
  /** Cells as the server formatted them, in column order; "-" = no value (unmeasured is not zero). */
  rows: string[][];
  note: string;
  /** Rows before the server's row cap (the note says when rows were cut). */
  totalRows: number;
}

export interface ReportDocument {
  tables: ReportTable[];
}

/** `GET /reports/{id}/tables/` (cells formatted as in the Markdown export). */
export interface ReportTablesResponse {
  report_id: string;
  report_type: ReportType;
  title: string;
  tables: {
    title: string;
    columns: { key: string; header: string }[];
    rows: Record<string, string>[];
    note?: string;
    total_rows?: number;
  }[];
}

/** The tables endpoint's keyed rows as cells in column order (a missing cell is ""). */
export function reportDocument(data: ReportTablesResponse): ReportDocument {
  return {
    tables: (data.tables ?? []).map((t) => ({
      title: t.title,
      columns: t.columns.map((c) => c.header),
      rows: t.rows.map((row) => t.columns.map((c) => row[c.key] ?? "")),
      note: t.note ?? "",
      totalRows: t.total_rows ?? t.rows.length,
    })),
  };
}

export interface SummaryItem {
  label: string;
  value: string;
  href?: string;
  /** The summary key, when the label is not the key humanized. */
  key?: string;
}

/** Summary keys whose label is not their humanized key (final-assessment study reports). */
const SUMMARY_LABELS: Record<string, string> = {
  ranking_criteria: "Ranking criteria",
  whole_cohort_pass_rate: "Pass rate, failures counted as 0",
};

/** The ranking presets a study report records (`protocol.ranking_preset`). */
export const RANKING_PRESETS: Record<string, string> = {
  final_assessment:
    "Final assessment: failures counted as 0, cost at list price",
  default: "Default: conditional rates, billed cost",
  report: "Set in the report",
  analysis_plan: "Pre-registered in the analysis plan",
};

function summaryValue(key: string, v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    // `*_rate` summary values are fractions (0..1).
    if (key.endsWith("_rate")) return `${(v * 100).toFixed(1)}%`;
    return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }
  if (Array.isArray(v))
    return v.map((x) => summaryValue("", x)).join(", ") || "—";
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length
      ? entries
          .map(([k, x]) => `${k.replaceAll("_", " ")}: ${summaryValue(k, x)}`)
          .join(", ")
      : "—";
  }
  return String(v);
}

/**
 * The groups sharing the top of a report's ranking, re-read from its data:
 * a study conditions report's leaderboard (the top mean pass rate; none when
 * nothing is graded), or a study report's TOPSIS ranking (every row ranked
 * 1). null when the data has neither.
 */
function topGroups(
  data: Record<string, unknown> | null | undefined,
): { names: string[]; noun: string } | null {
  const results = data?.results as { leaderboard?: unknown } | undefined;
  if (Array.isArray(results?.leaderboard)) {
    const graded = (
      results.leaderboard as {
        label?: string;
        pass_rate_mean?: number | null;
      }[]
    ).filter((r) => typeof r.pass_rate_mean === "number");
    const best = Math.max(...graded.map((r) => r.pass_rate_mean as number));
    return {
      names: graded
        .filter((r) => Math.abs((r.pass_rate_mean as number) - best) < 1e-9)
        .map((r) => r.label ?? ""),
      noun: "conditions",
    };
  }
  const ranking = (data?.ranking as { ranking?: unknown } | undefined)?.ranking;
  if (
    Array.isArray(ranking) &&
    ranking.length > 0 &&
    ranking.every((r) => !!r && typeof r === "object" && "topsis_rank" in r)
  ) {
    const rows = ranking as { model?: string; topsis_rank?: number | null }[];
    const grouped = (data?.protocol as { grouped_by?: string } | undefined)
      ?.grouped_by;
    return {
      names: rows.filter((r) => r.topsis_rank === 1).map((r) => r.model ?? ""),
      noun: grouped === "condition" ? "conditions" : "models",
    };
  }
  return null;
}

/**
 * A report's summary card: unmeasured values read "—" (never 0), rates as
 * percentages, and a report with tied leaders says so instead of naming the
 * first group. With the study conditions or study report's data, the leader is
 * re-read from its leaderboard or ranking, so summaries stored before ties
 * were detected are right too.
 */
export function summaryItems(
  summary: Record<string, unknown> | null | undefined,
  data?: Record<string, unknown> | null,
): SummaryItem[] {
  const s = { ...(summary ?? {}) };
  const top = "leader" in s ? topGroups(data) : null;
  if (top) {
    s.leader = top.names.length === 1 ? top.names[0] : null;
    s.tied_leaders = top.names.length > 1 ? top.names.length : 0;
  }
  const items: SummaryItem[] = [];
  for (const [key, v] of Object.entries(s)) {
    if (key === "tied_leaders") continue;
    const label = key
      .replaceAll("_", " ")
      .replace(/^./, (c) => c.toUpperCase());
    if (
      key === "leader" &&
      v == null &&
      typeof s.tied_leaders === "number" &&
      s.tied_leaders > 1
    ) {
      items.push({
        label,
        value: `Tie: ${s.tied_leaders} ${top?.noun ?? "conditions"} share the top`,
      });
      continue;
    }
    if (key === "study_id" && typeof v === "string" && v) {
      items.push({
        label: "Experiment",
        value: v.slice(0, 8),
        href: `/experiments/${v}`,
      });
      continue;
    }
    if (key in SUMMARY_LABELS) {
      const value =
        key === "ranking_criteria" && typeof v === "string"
          ? (RANKING_PRESETS[v] ?? v)
          : summaryValue(key, v);
      items.push({ label: SUMMARY_LABELS[key], value, key });
      continue;
    }
    items.push({ label, value: summaryValue(key, v) });
  }
  return items;
}

/** The report's curated tables (those of the Markdown/LaTeX export); 400 while it is not completed. */
export async function getReportTables(
  reportId: string,
): Promise<ReportDocument> {
  const res = await apiFetch(
    `/reports/${encodeURIComponent(reportId)}/tables/`,
  );
  return reportDocument((await res.json()) as ReportTablesResponse);
}

export interface GenerateReportPayload {
  report_type: ReportType;
  config?: Record<string, unknown>;
  title?: string;
  description?: string;
  expires_in_days?: number | null;
}

export interface ReportDetail extends ReportSummary {
  config: Record<string, unknown>;
  report_data: Record<string, unknown>;
}

export interface ReportListParams {
  report_type?: ReportType;
  status?: ReportStatus;
  limit?: number;
  offset?: number;
}

export interface ReportListResponse {
  reports: ReportSummary[];
  pagination: { total: number; limit: number; offset: number };
}

export interface ReportSummary {
  id: string;
  report_id: string;
  report_type: ReportType;
  title: string;
  description: string;
  status: ReportStatus;
  progress_percent: number;
  summary: Record<string, unknown>;
  error_message: string;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
  /** The GenerationRun the report is about (the field keeps its v2 name, D5). */
  generation_job_id: string | null;
  analysis_task_id: string | null;
}

export async function deleteReport(
  reportId: string,
): Promise<{ success: boolean; message: string }> {
  const res = await apiFetch(`/reports/${reportId}/`, { method: "DELETE" });
  return res.json();
}

export async function generateReport(
  payload: GenerateReportPayload,
): Promise<ReportSummary> {
  const res = await apiFetch("/reports/generate/", {
    method: "POST",
    body: JSON.stringify({ config: {}, ...payload }),
  });
  return res.json();
}

export async function getReport(reportId: string): Promise<ReportDetail> {
  const res = await apiFetch(`/reports/${reportId}/`);
  return res.json();
}

export async function getReportData(reportId: string): Promise<{
  report_id: string;
  report_type: ReportType;
  title: string;
  status: ReportStatus;
  progress: number;
  data: Record<string, unknown>;
}> {
  const res = await apiFetch(`/reports/${reportId}/data/`);
  return res.json();
}

export async function getReports(
  params: ReportListParams = {},
): Promise<ReportListResponse> {
  const query = new URLSearchParams();
  if (params.report_type) query.set("report_type", params.report_type);
  if (params.status) query.set("status", params.status);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  const res = await apiFetch(`/reports/${qs ? `?${qs}` : ""}`);
  return res.json();
}
