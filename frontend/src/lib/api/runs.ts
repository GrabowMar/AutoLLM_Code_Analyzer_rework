// Client for the analysis runs API (`/analysis/runs/`): runs of the analyzer
// workspace's tools over a generated app (or pasted code), their per-tool
// results and the normalized findings.
import { apiFetch } from "./core";

export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

/** What a running run is doing between tools (`progress` events / `phase`). */
export type RunPhase =
  | ""
  | "waiting_for_workspace"
  | "preparing"
  | "running_tools";

export type ToolResultStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped";

/** `summary.skip_reason` of a skipped tool result. Only `not_applicable` is a measurement. */
export type SkipReason =
  | "not_applicable"
  | "app_not_running"
  | "unavailable"
  | "cancelled";

export interface RunTarget {
  /** "job": a generated app; "code": pasted files; "deleted": the app was deleted since. */
  kind: "job" | "code" | "deleted";
  label: string;
  job_id?: string | null;
  job_status?: string;
  mode?: string;
  model_name?: string;
  model_id?: string;
  app_name?: string;
  app_slug?: string;
  stack_slug?: string;
  experiment_id?: string | null;
  file_count?: number | null;
}

/** Live counters of an active (pending/running) run. */
export interface RunProgress {
  total: number;
  done: number;
  running: string[];
  findings: number;
}

export interface RunToolResult {
  id: string;
  tool_slug: string;
  category: string;
  status: ToolResultStatus;
  /**
   * `finding_count`, `severity_counts` (normalized — don't display), `tool_version`,
   * `config`, `warnings`, `skip_reason`.
   */
  summary: Record<string, any>;
  /** Aggregate tool metrics, e.g. radon's { average_complexity, rank_distribution }. */
  metrics: Record<string, any>;
  error_message: string;
  started_at: string | null;
  duration_seconds: number | null;
}

export interface ToolResultDetail extends RunToolResult {
  /** `command`, `exit_code`, `stdout` (first 30k chars), `target_url` (dynamic tools). */
  raw_output: Record<string, any>;
  tool_name: string;
  tool_docs_url: string;
  findings_count: number;
}

/**
 * Run summary. Finished runs: `total_findings`, `reported_severity_counts`
 * (levels as the tools printed them, case-folded), `unrated_findings`,
 * `tools_run` / `tools_completed` / `tools_failed`, `truncated_inputs`,
 * `metrics_by_tool` (detail only). `severity_counts` is a normalization kept for
 * aggregate statistics — never display it.
 */
export type RunSummary = Record<string, any>;

export interface AnalysisRunListItem {
  id: string;
  name: string;
  status: RunStatus;
  tool_slugs: string[];
  summary: RunSummary;
  error_message: string;
  generation_job_id: string | null;
  batch_id: string | null;
  target: RunTarget;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  phase: RunPhase;
  progress: RunProgress | null;
}

export interface AnalysisRunDetail extends AnalysisRunListItem {
  results: RunToolResult[];
  source_files: { path: string; lines: number }[];
}

export interface RunFinding {
  id: string;
  /** Normalized level (a heuristic for tools that rate nothing): never display it. */
  severity: string;
  /** The level exactly as the tool printed it; "" when the tool rates nothing. */
  tool_severity: string;
  category: string;
  confidence: string;
  title: string;
  description: string;
  suggestion: string;
  file_path: string;
  line_number: number | null;
  column_number: number | null;
  code_snippet: string;
  rule_id: string;
  tool_slug: string;
  /** Standardized extras (see `FindingDetail`): rule_name, docs_url, cwe, package, url, … */
  tool_specific_data: Record<string, any>;
  /** With `group`: the other findings (often of other tools) that report the same issue. */
  duplicates?: RunFinding[];
  /** With `group`: the issue's cross-tool class (e.g. `vulnerable-dependency:pyjwt`). */
  issue_class?: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export type PaginatedRuns = Paginated<AnalysisRunListItem>;
export type PaginatedRunFindings = Paginated<RunFinding> & {
  /** With `group`: the findings behind the `total` issues. */
  findings_total?: number | null;
};

export interface FacetValue {
  value: string;
  count: number;
  label?: string;
}

export interface FindingFacets {
  total: number;
  tools: FacetValue[];
  /** Tool-reported levels (case-folded), most severe first (`rank` 0 = most severe). */
  levels: { value: string; count: number; rank: number }[];
  /** Findings of tools that rate nothing. */
  unrated: number;
  categories: FacetValue[];
  files: FacetValue[];
  files_total: number;
  rules: FacetValue[];
  rules_total: number;
}

export interface RunStats {
  total: number;
  active: number;
  by_status: Partial<Record<RunStatus, number>>;
}

export interface RunPreviewTarget {
  job_id: string | null;
  label: string;
  files: number;
  lines: number;
  app_running: boolean;
}

export interface RunPreviewTool {
  slug: string;
  name: string;
  known: boolean;
  installed: boolean;
  install_status: string;
  mode: string;
  kind: string;
  /** Targets the tool would run on / skip. */
  runs: number;
  not_applicable: number;
  app_not_running: number;
}

export interface RunPreview {
  workspace_status: string;
  targets: RunPreviewTarget[];
  tools: RunPreviewTool[];
  tool_runs: number;
  warnings: string[];
}

export interface ListRunsParams {
  page?: number;
  per_page?: number;
  /** Comma-separated statuses; "active" = pending + running. */
  status?: string;
  generation_job_id?: string;
  batch_id?: string;
  tool_slug?: string;
  search?: string;
  sort?:
    | "created_desc"
    | "created_asc"
    | "name_asc"
    | "duration_desc"
    | "findings_desc";
}

export interface FindingsParams {
  page?: number;
  per_page?: number;
  tool_slug?: string;
  /** A tool-reported level (case-insensitive) or "unrated". */
  tool_severity?: string;
  category?: string;
  file_path?: string;
  rule_id?: string;
  search?: string;
  sort?: "severity" | "file" | "tool" | "rule";
  /** One item per issue: findings of several tools that report the same thing collapse. */
  group?: boolean;
}

interface RequestOptions {
  signal?: AbortSignal;
}

function query(params: object = {}): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    sp.set(key, String(value));
  }
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}

function post(path: string, body: unknown, options?: RequestOptions) {
  return apiFetch(path, {
    method: "POST",
    body: JSON.stringify(body),
    signal: options?.signal,
  });
}

// --- Runs ---------------------------------------------------------------------

export async function listRuns(
  params?: ListRunsParams,
  options?: RequestOptions,
): Promise<PaginatedRuns> {
  const res = await apiFetch(`/analysis/runs/${query(params)}`, {
    signal: options?.signal,
  });
  return res.json();
}

export async function getRunStats(): Promise<RunStats> {
  const res = await apiFetch("/analysis/runs/stats/");
  return res.json();
}

export async function getRun(
  runId: string,
  options?: RequestOptions,
): Promise<AnalysisRunDetail> {
  const res = await apiFetch(`/analysis/runs/${runId}/`, {
    signal: options?.signal,
  });
  return res.json();
}

export interface CreateRunPayload {
  name?: string;
  tool_slugs: string[];
  generation_job_id?: string | null;
  /** `{path: content}`; the file names decide which tools apply. */
  source_code?: Record<string, string> | null;
  auto_start?: boolean;
}

export async function createRun(
  payload: CreateRunPayload,
): Promise<AnalysisRunDetail> {
  const res = await post("/analysis/runs/", payload);
  return res.json();
}

/** One run per app, queued one after another. */
export async function createBatch(payload: {
  name?: string;
  tool_slugs: string[];
  generation_job_ids: string[];
}): Promise<{ batch_id: string; run_ids: string[] }> {
  const res = await post("/analysis/runs/batch/", payload);
  return res.json();
}

/** Where each tool would run (free; nothing is created). */
export async function previewRuns(
  payload: {
    tool_slugs: string[];
    generation_job_ids?: string[];
    source_code?: Record<string, string> | null;
  },
  options?: RequestOptions,
): Promise<RunPreview> {
  const res = await post("/analysis/runs/preview/", payload, options);
  return res.json();
}

export async function cancelRun(runId: string): Promise<AnalysisRunDetail> {
  const res = await apiFetch(`/analysis/runs/${runId}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

/** A new run on the same code: all tools, `tool_slugs`, or only those that measured nothing. */
export async function rerunRun(
  runId: string,
  payload: {
    tool_slugs?: string[];
    only_unmeasured?: boolean;
    name?: string;
  } = {},
): Promise<AnalysisRunDetail> {
  const res = await post(`/analysis/runs/${runId}/rerun/`, payload);
  return res.json();
}

/** 409 while the run is active (cancel it first). */
export async function deleteRun(runId: string): Promise<void> {
  await apiFetch(`/analysis/runs/${runId}/`, { method: "DELETE" });
}

/** Cancel or delete several runs; active runs are never deleted (`skipped`). */
export async function bulkRunAction(
  action: "cancel" | "delete",
  ids: string[],
): Promise<{ affected: number; skipped: number }> {
  const res = await post("/analysis/runs/bulk/", { action, ids });
  return res.json();
}

export async function getRunResult(
  runId: string,
  resultId: string,
): Promise<ToolResultDetail> {
  const res = await apiFetch(`/analysis/runs/${runId}/results/${resultId}/`);
  return res.json();
}

// --- Findings -------------------------------------------------------------------

export async function getRunFindings(
  runId: string,
  params?: FindingsParams,
  options?: RequestOptions,
): Promise<PaginatedRunFindings> {
  const res = await apiFetch(
    `/analysis/runs/${runId}/findings/${query(params)}`,
    { signal: options?.signal },
  );
  return res.json();
}

export async function getFindingFacets(
  runId: string,
  params?: { tool_slug?: string },
  options?: RequestOptions,
): Promise<FindingFacets> {
  const res = await apiFetch(
    `/analysis/runs/${runId}/findings/facets/${query(params)}`,
    { signal: options?.signal },
  );
  return res.json();
}

/** Every finding matching *params* (pages of 200, at most `limit`), for export. */
export async function getAllRunFindings(
  runId: string,
  params: Omit<FindingsParams, "page" | "per_page"> = {},
  limit = 5000,
): Promise<RunFinding[]> {
  const all: RunFinding[] = [];
  for (let page = 1; all.length < limit; page++) {
    const data = await getRunFindings(runId, {
      ...params,
      page,
      per_page: 200,
    });
    all.push(...data.items);
    if (page >= data.pages) break;
  }
  return all.slice(0, limit);
}
