import { apiFetch } from "./core";

/**
 * Application-page insights (`/api/generation/jobs/{id}/…`): code profile,
 * phase timeline and code-quality metrics. All read-only, derived on read
 * from what the job already stores. Missing values are `null` (unknown),
 * never 0.
 */

// ── Code profile ──────────────────────────────────────────────────────

export type CodeLayout = "files" | "stage_blobs" | "single_blob" | "none";
export type CodeLayer =
  | "backend"
  | "frontend"
  | "tests"
  | "config"
  | "excluded";

export interface SourceLoc {
  backend_loc: number | null;
  frontend_loc: number | null;
  /** The per-KLOC denominator of the study tables (reports/services/loc.py). */
  total_loc: number | null;
  source_files: number | null;
  /** null: a single-blob result (test code cannot be told apart). */
  test_loc: number | null;
  test_files: number | null;
}

export interface CodeLayerRow {
  layer: CodeLayer;
  label: string;
  files: number;
  all_lines: number;
  /** null for config and excluded files (not source by definition). */
  source_loc: number | null;
}

export interface LanguageRow {
  language: string;
  files: number;
  source_loc: number;
}

export interface ExcludedGroup {
  root: string;
  files: number;
  all_lines: number;
  sample_paths: string[];
}

export interface NonSourceFile {
  path: string;
  kind:
    | "lockfile"
    | "manifest"
    | "container"
    | "docs"
    | "minified"
    | "config"
    | string;
  all_lines: number;
}

export type FileOrigin = "scaffold" | "added" | "modified" | "unknown" | string;

export interface OriginRow {
  origin: FileOrigin;
  files: number;
  source_files: number;
  source_loc: number;
  test_files: number;
  test_loc: number;
}

export type CodeOrigins =
  | { known: false; reason: "not_agent" | "no_manifest" | CodeLayout | string }
  | {
      known: true;
      counts: Record<string, number>;
      by_origin: OriginRow[];
      per_file: Record<string, FileOrigin>;
      deleted_scaffold_files: string[];
      protected_modified: string[];
      skipped: {
        path: string | null;
        reason: string | null;
        size: number | null;
        origin: string | null;
      }[];
    };

export interface CodeStructure {
  approximate: true;
  method: string;
  models: number;
  model_names: string[];
  route_decorators: number;
  frontend_component_files: number;
  frontend_page_files: number;
  component_definitions: number;
}

export type DependencyStatus = "scaffold" | "added" | "repinned" | "unknown";

export interface DeclaredDependency {
  package: string;
  spec: string;
  ecosystem: "pypi" | "npm" | string;
  manifest: string;
  status?: DependencyStatus;
  scaffold_spec?: string | null;
  declared_by_app?: boolean;
}

export interface CodeDependencies {
  has_manifests: boolean;
  declared: DeclaredDependency[];
  scaffold: DeclaredDependency[];
  scaffold_stack: {
    slug: string;
    pinned_version: number | null;
    version: number | null;
    source: "pinned" | "latest" | "missing";
  } | null;
  model_added: string[] | null;
  repinned:
    | {
        package: string;
        spec: string;
        scaffold_spec: string;
        ecosystem: string | null;
      }[]
    | null;
  dropped_scaffold: string[] | null;
  /**
   * pip freeze of the evaluation image. `declared_by`: the manifests the image was
   * built from ("app" for agent runs, "scaffold" for template runs), "healing" for a
   * requirement the platform added, null for transitive / tooling packages.
   */
  resolved_python:
    | {
        package: string;
        version: string | null;
        declared: boolean;
        declared_by: "app" | "scaffold" | "healing" | null;
      }[]
    | null;
  reported_by_generator: string[] | null;
  counts: Record<
    | "declared"
    | "scaffold"
    | "model_added"
    | "repinned"
    | "dropped_scaffold"
    | "resolved_python",
    number | null
  >;
}

export interface CodeProfile {
  job_id: string;
  version: number;
  mode: string;
  layout: CodeLayout;
  loc: SourceLoc;
  /** Every stored line (lockfiles, docs, scratch folders included), as metrics.lines_of_code counts. */
  all_lines: number | null;
  all_files: number;
  recorded_lines_of_code: number | null;
  all_lines_ratio: number | null;
  layers: CodeLayerRow[];
  languages: LanguageRow[];
  excluded: ExcludedGroup[];
  non_source_files: NonSourceFile[];
  origins: CodeOrigins;
  structure: CodeStructure | null;
  dependencies: CodeDependencies;
  definitions: { source_loc: string; all_lines: string };
  notes: string[];
}

export async function getJobCodeProfile(
  jobId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<CodeProfile> {
  const res = await apiFetch(`/generation/jobs/${jobId}/code-profile/`, {
    signal: opts.signal,
  });
  return res.json();
}

// ── Timeline ──────────────────────────────────────────────────────────

export interface TimelinePart {
  key: string;
  label: string;
  seconds: number | null;
  source: string;
  approximate: boolean;
}

export interface TimelineSegment {
  /** `sandbox_wait`: a pre-executor run started on dispatch, then waited for a sandbox. */
  key:
    | "queue"
    | "sandbox_wait"
    | "generation"
    | "other"
    | "evaluation"
    | "run"
    | string;
  label: string;
  kind: "wait" | "work";
  start: string | null;
  end: string | null;
  seconds: number | null;
  source: string;
  approximate: boolean;
  note: string | null;
  parts: TimelinePart[];
  /** Numeric split from the agent trajectory (`result_data.agent.trajectory.time`), when recorded. */
  trajectory_time?: Record<string, number> | null;
  /** Evaluation ran outside this run's span (a later re-evaluation). */
  separate?: boolean;
}

export interface ExecutionAttempt {
  attempt: number | null;
  reason: string | null;
  error: string | null;
  at: string | null;
  cost: number | null;
  total_tokens: number | null;
  infra_errors: number | null;
}

export interface ExecutionRecord {
  lane: string;
  endpoint_slug: string | null;
  priority: number;
  sequence: number | null;
  attempt: number;
  policy: string | null;
  executor: string | null;
  started_seq: number | null;
  run_order: number | null;
  queue_seconds: number | null;
  llm_queue_wait_s: number | null;
  prefetch_load_s: number | null;
  run_after: string | null;
  requeued_at: string | null;
  waiting_reason: "retry_backoff" | null;
  attempts: ExecutionAttempt[];
  infra_errors: Record<string, unknown>[];
  infra_error_count: number;
  executor_lost_count: number | null;
  relay_interruptions: unknown;
  /** A requested re-evaluation is queued or running (the flag is dropped when it finishes). */
  reevaluated: boolean;
  prior_attempts: Partial<
    Record<
      "cost" | "prompt_tokens" | "completion_tokens" | "total_tokens",
      number | null
    >
  > | null;
  recorded: boolean;
}

export interface ExperimentCell {
  experiment_id: string;
  experiment_name: string | null;
  experiment_status: string | null;
  condition_id: number | null;
  condition_label: string | null;
  condition_method: string | null;
  condition_model: string | null;
  app_slug: string | null;
  repeat_index: number | null;
  /** 1-based trial number (repeat_index + 1). */
  repeat_number: number | null;
  planned_repeats: number | null;
  /** Jobs in this cell (retries / top-ups included). */
  cell_jobs: number;
  is_current_in_cell: boolean;
}

export interface JobTimeline {
  job_id: string;
  mode: string;
  status: string;
  phase: string;
  live: boolean;
  milestones: { key: string; label: string; at: string }[];
  segments: TimelineSegment[];
  total_seconds: number | null;
  run_seconds: number | null;
  duration_seconds: number | null;
  evaluation_in_run: boolean | null;
  execution: ExecutionRecord;
  experiment: ExperimentCell | null;
  notes: string[];
}

export async function getJobTimeline(
  jobId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<JobTimeline> {
  const res = await apiFetch(`/generation/jobs/${jobId}/timeline/`, {
    signal: opts.signal,
  });
  return res.json();
}

// ── Quality metrics ───────────────────────────────────────────────────

export interface QualityToolRow {
  tool_slug: string;
  tool_name: string;
  kind: "complexity" | "duplication" | "other";
  status: "completed" | "failed" | "skipped" | "not_run" | string;
  measured: boolean;
  skip_reason?: string | null;
  error?: string | null;
  metrics: Record<string, unknown>;
  derived: Record<string, number | null>;
  tool_version?: string | null;
  parser_sha?: string | null;
  command_sha?: string | null;
  patterns_sha?: string | null;
  result_id?: string;
  result_created_at?: string | null;
  duration_seconds?: number | null;
  /** `name` is null for another user's run (only its id is shown). */
  run: {
    id: string;
    name: string | null;
    status: string;
    created_at: string | null;
    accessible: boolean;
  } | null;
}

export interface JobQualityMetrics {
  job_id: string;
  selection: string;
  measured: number;
  tools: QualityToolRow[];
  notes: string[];
}

export async function getJobQualityMetrics(
  jobId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<JobQualityMetrics> {
  const res = await apiFetch(`/generation/jobs/${jobId}/quality-metrics/`, {
    signal: opts.signal,
  });
  return res.json();
}
