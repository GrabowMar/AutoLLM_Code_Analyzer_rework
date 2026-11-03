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
  /** "run": a generated app (GenerationRun); "code": pasted files; "deleted": the app was deleted since. */
  kind: "run" | "code" | "deleted";
  label: string;
  /** The GenerationRun id (the key keeps the AnalysisRun FK name, D5). */
  job_id?: string | null;
  /** GenerationRun status, outcome and kind. */
  job_status?: string;
  outcome?: string;
  run_kind?: string;
  model_name?: string;
  model_id?: string;
  task_title?: string;
  task_slug?: string;
  /** Pinned refs (`slug@v`). */
  task_ref?: string;
  stack_ref?: string;
  study_id?: string | null;
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
   * `config`, `warnings`, `skip_reason`; the instrument's fingerprints
   * `command_sha`, `parser_sha` and `patterns_sha` (static tools; a
   * not-applicable skip has `patterns_sha` but no `tool_version`).
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
 * `tools_run` / `tools_completed` / `tools_failed`, `tools_not_applicable`
 * (nothing in the tree to read: not measured, never a zero) / `tools_unmeasured`
 * (app not running, tool unavailable, cancelled: measured nothing),
 * `tool_applicable` (`{slug: true | false | null}`; null = not decided),
 * `code_findings` / `dependency_findings` (known-vulnerability advisories) /
 * `runtime_findings` (dynamic tools) — each `null` when no tool of that scope
 * measured the run (unmeasured, not zero) —, `truncated_inputs`
 * (`[validity.termination]` when the generation run was truncated),
 * `metrics_by_tool` (detail only).
 * `severity_counts` is a normalization kept for aggregate statistics — never
 * display it.
 */
export type RunSummary = Record<string, any>;

/** Code findings outside / inside test files; null when no code tool measured the run. */
export interface CodeParts {
  source: number | null;
  tests: number | null;
  /** Code findings in non-source dirs (node_modules, .tmp/ …) of runs from before the exclusion rule. */
  excluded?: number | null;
}

/** The run's findings split by scope; a scope no tool of the run measured is null. */
export interface ScopeCounts {
  code: number | null;
  dependencies: number | null;
  runtime: number | null;
}

export interface AnalysisRunListItem {
  id: string;
  name: string;
  status: RunStatus;
  tool_slugs: string[];
  summary: RunSummary;
  error_message: string;
  /** The analyzed GenerationRun (FK name kept, D5). */
  generation_job_id: string | null;
  /** Shared by the runs launched together (`POST runs/batch/`). */
  launch_id: string | null;
  /** "app" for a generation run (the whole app tree, spec 8.8); "" for pasted code. */
  code_scope: "app" | "";
  /** H(sorted analyzed paths). */
  code_scope_paths_sha: string;
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
  /** `origin`: the file's RunFile origin (`parent` for a slot variant's overlaid file); "" for pasted code. */
  source_files: { path: string; lines: number; origin: FileOrigin | "" }[];
}

/**
 * RunFile origin of an analyzed file. Model/slot attribution happens at stats time from this
 * tag; "" (a URL, a package-only advisory, pasted code) is unattributed, never model code.
 */
export type FileOrigin =
  | "starter"
  | "authored"
  | "modified"
  | "slot"
  | "parent";

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
  /**
   * A code finding located in test code (tests/, *.test.*, conftest.py, smoke
   * scripts — the study's `is_test_path`): counted apart from the app's code.
   */
  in_tests?: boolean;
  /** RunFile origin of the finding's file; "" when the path is not a file of the analyzed tree. */
  file_origin?: FileOrigin | "";
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
  /**
   * Over all of the run's findings: code findings, dependency advisories,
   * dynamic tools' findings; null for a scope no tool of the run measured.
   */
  scopes?: ScopeCounts;
  /** The code findings split by test files (counted apart, as in the study); null when code is unmeasured. */
  code_parts?: CodeParts;
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
  /** GenerationRun id; null for pasted code. */
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
  /** Analyses of one GenerationRun. */
  generation_job_id?: string;
  /** Runs launched together (`createBatch`). */
  launch_id?: string;
  tool_slug?: string;
  search?: string;
  sort?:
    | "created_desc"
    | "created_asc"
    | "name_asc"
    | "duration_desc"
    | "findings_desc"
    /** Code findings (advisories and dynamic tools' findings left out). */
    | "code_findings_desc";
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
  /**
   * code (what the model wrote) | dependencies (known-vulnerability advisories) |
   * runtime (dynamic tools) | source / tests (code findings outside / inside test files); "" = all.
   */
  scope?: FindingScope | CodePart | "";
}

export type FindingScope = "code" | "dependencies" | "runtime";
/** The two parts of the code scope: the app's own source, and test code (counted apart). */
export type CodePart = "source" | "tests";

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
}): Promise<{ launch_id: string; run_ids: string[] }> {
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
  params?: { tool_slug?: string; scope?: FindingScope | CodePart | "" },
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

// --- Context ------------------------------------------------------------------------

/** The generation setup behind an analyzed app (read through RunView): its study cell and treatment. */
export interface RunSetup {
  /** GenerationRun id. */
  id: string;
  /** Run kind (`app_agentic`, `slot_*`, `reference`). */
  kind: string;
  status: string;
  outcome: string;
  usable: boolean | null;
  /** LLMModel primary key (model pages), id string, display name, URL slug. */
  model_pk: number | null;
  model_id: string;
  model_name: string;
  model_slug: string;
  /** Pinned task version (`slug@v`), its slug and title, and the prose variant the model got. */
  task_ref: string;
  task_slug: string;
  task_title: string;
  prose_variant: string;
  /** `task:<slug>@<v>` (app) or the slot target (slot). */
  unit_key: string;
  stack_ref: string;
  study_id: string | null;
  study_name: string;
  condition_id: number | null;
  condition_label: string;
  /** `pilot` | `main` | "" (ad hoc). */
  stage: string;
  /** 0-based repeat within the (condition, unit) cell. */
  repeat_index: number | null;
  /** The study's planned repeats per cell. */
  planned_repeats: number | null;
  harness: string | null;
  harness_version: string;
  config_fingerprint: string | null;
  run_fingerprint: string | null;
}

/**
 * Source size by the study's definition: non-empty, non-comment lines of source files. For a
 * generation run the counted scope is `model` (what the model wrote); `app_total` is the source
 * LOC of the whole app tree the tools read (starter included). Pasted code: scope `code`.
 */
export interface SourceLoc {
  scope: "model" | "code";
  total: number;
  /** Source LOC of the whole analyzed app tree; null for pasted code. */
  app_total: number | null;
  files: number;
  /** `tests` is null when the code has no paths (unknown, not zero). */
  by_layer: {
    backend: number | null;
    frontend: number | null;
    tests: number | null;
  };
  test_files: number | null;
  /** Stored files that are not source (manifests, lockfiles, docs, configs). */
  other_files: number | null;
  /**
   * Stored files in dependency / cache / scratch directories (not the app's source, never
   * in the LOC). `left_out`: this run's tools left them out (true), read them because the
   * run predates that rule (false: `findings_in_excluded` of its findings lie in them),
   * or unknown (null: no tool ran, or the run did not finish).
   */
  excluded_paths: {
    count: number;
    sample: string[];
    dirs: Record<string, number>;
    left_out?: boolean | null;
    findings_in_excluded?: number;
  };
}

export interface CodeSplitRow {
  tool: string;
  findings_in_tests: number;
  findings_in_source: number;
  /** Findings in excluded non-source dirs (pre-rule runs only); neither tests nor source. */
  findings_in_excluded?: number;
}

/** How common one of the run's rules is across the corpus (per-tool latest results). */
export interface RulePrevalence {
  tool: string;
  rule_id: string;
  rule_name: string;
  title: string;
  /** The rule's one cross-tool class in this run; "" when its findings span several classes. */
  issue_class: string;
  scope: FindingScope;
  count_in_run: number;
  /** Apps whose latest result of the tool reports the rule / apps that tool measured. */
  apps_with_rule: number;
  apps_measured: number;
  /** The same for this model's apps; null without a model (pasted code). */
  model_apps_with_rule: number | null;
  model_apps_measured: number | null;
}

/** `study`: the analyzed run's study (current runs, main stage); `mine`: the caller's apps. */
export type ContextScope = "study" | "mine";

export interface RunContext {
  run_id: string;
  /** The corpus rule prevalence is counted over; null when there is none. */
  scope: ContextScope | null;
  scopes_available: ContextScope[];
  scope_label: string;
  /** Analyzed apps in the corpus / of this model. */
  n: number;
  model_n: number | null;
  self_in_corpus: boolean;
  /** The study corpus while the study still runs: the counts are interim. */
  interim?: boolean;
  job: RunSetup | null;
  source_loc: SourceLoc | null;
  test_split: CodeSplitRow[];
  rule_prevalence: RulePrevalence[];
  notes: string[];
}

/** Setup line, source LOC by layer, test-code split and rule prevalence of a run. */
export async function getRunContext(
  runId: string,
  scope: ContextScope | "" = "",
  options?: RequestOptions,
): Promise<RunContext> {
  const res = await apiFetch(
    `/analysis/runs/${runId}/context/${query({ scope })}`,
    { signal: options?.signal },
  );
  return res.json();
}
