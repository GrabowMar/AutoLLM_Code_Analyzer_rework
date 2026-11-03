import { apiFetch } from "./core";

/**
 * Sample (run) page insights (`/api/generation/runs/{id}/…`): code profile, phase timeline and
 * code-quality metrics. All read-only, derived on read from what the GenerationRun already stores.
 * Missing values are `null` (not measured), never 0. Only the caller's own runs are readable.
 */

// ── Code profile ──────────────────────────────────────────────────────

export type CodeLayer =
  | "backend"
  | "frontend"
  | "tests"
  | "config"
  | "excluded";

/**
 * Source LOC of one scope. Every key is null when the run has no collected tree; `source_files`,
 * `test_files` are absent then.
 */
export interface SourceLoc {
  backend_loc: number | null;
  frontend_loc: number | null;
  /** The per-KLOC denominator of the study tables (reports/services/loc.py). */
  total_loc: number | null;
  source_files?: number | null;
  test_loc: number | null;
  test_files?: number | null;
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
    | (string & {});
  all_lines: number;
}

/**
 * The collector's `RunFile.origin`, or `parent` for a slot variant's overlaid parent file
 * (spec 3.11 `RunView`).
 */
export type FileOrigin =
  | "starter"
  | "authored"
  | "modified"
  | "slot"
  | "artifact"
  | "parent"
  | (string & {});

export interface OriginRow {
  origin: FileOrigin;
  files: number;
  source_files: number;
  source_loc: number;
  test_files: number;
  test_loc: number;
}

/** `known: false` when no files were collected (every count is then not measured). */
export interface CodeOrigins {
  known: boolean;
  counts: Record<string, number>;
  by_origin: OriginRow[];
  per_file: Record<string, FileOrigin>;
  /** Starter files the model deleted. */
  deleted_starter_files: string[];
  /** Files left out of the source counts, by `RunFile.excluded` reason. */
  excluded_by_reason: Record<string, number>;
}

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

export type DependencyStatus = "starter" | "added" | "repinned" | "unknown";

export interface DeclaredDependency {
  package: string;
  spec: string;
  ecosystem: "pypi" | "npm" | (string & {});
  manifest: string;
  status?: DependencyStatus;
  starter_spec?: string | null;
  declared_by_app?: boolean;
}

/** Manifest dependencies vs the run's starter; every list is null when it cannot be decided. */
export interface CodeDependencies {
  has_manifests: boolean;
  declared: DeclaredDependency[];
  starter: DeclaredDependency[];
  /** The pinned starter (`slug@version`), null for a free-choice stack. */
  starter_ref: string | null;
  model_added: string[] | null;
  repinned:
    | {
        package: string;
        spec: string;
        starter_spec: string;
        ecosystem: string | null;
      }[]
    | null;
  dropped_starter: string[] | null;
  /** Python packages of the run's resolved lock; null when no lock was resolved. */
  resolved_python:
    | {
        package: string;
        version: string | null;
        declared: boolean;
      }[]
    | null;
  counts: Record<
    | "declared"
    | "starter"
    | "model_added"
    | "repinned"
    | "dropped_starter"
    | "resolved_python",
    number | null
  >;
}

export interface CodeProfile {
  run_id: string;
  version: number;
  /** Run kind (`app_agentic`, `slot_*`, `reference`). */
  kind: string;
  /** Model scope: the files the model wrote or changed (Appendix A #4). */
  loc: SourceLoc;
  /** App scope: the whole buildable tree. */
  loc_app: SourceLoc;
  /** Every stored line (lockfiles, docs, scratch folders included). */
  all_lines: number | null;
  all_files: number;
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

export async function getRunCodeProfile(
  runId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<CodeProfile> {
  const res = await apiFetch(`/generation/runs/${runId}/code-profile/`, {
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
  /** Evaluation stages carry their stage status. */
  status?: string | null;
}

export interface TimelineSegment {
  /** `queue`, `attempt_<n>` (one per RunAttempt), `run` (no attempt recorded) or `evaluation`. */
  key: "queue" | "run" | "evaluation" | `attempt_${number}` | (string & {});
  label: string;
  kind: "wait" | "work";
  start: string | null;
  end: string | null;
  seconds: number | null;
  source: string;
  approximate: boolean;
  note: string | null;
  parts: TimelinePart[];
  /** Attempt segments. */
  termination?: string | null;
  budget_exhausted?: string | null;
  oom_killed?: boolean;
  /** The evaluation segment. */
  evaluation_id?: number;
  status?: string;
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
  queue_seconds: number | null;
  llm_queue_wait_s: number | null;
  run_after: string | null;
  requeued_at: string | null;
  waiting_reason: "retry_backoff" | null;
  infra_errors: Record<string, unknown>[];
  infra_error_count: number;
  executor_lost_count: number | null;
  relay_interruptions: unknown;
  recorded: boolean;
}

/** The study cell a run fills (null for a run outside any study). */
export interface StudyCellRef {
  study_id: string;
  study_name: string | null;
  study_status: string | null;
  condition_id: number | null;
  condition_label: string | null;
  cell_id: number | null;
  unit_key: string | null;
  stage: string | null;
  repeat_index: number | null;
  /** 1-based trial number (repeat_index + 1). */
  repeat_number: number | null;
  planned_repeats: number | null;
  cell_attempt: number | null;
  /** A run beyond the frozen plan (never in the confirmatory sample). */
  beyond_plan: boolean;
  /** Runs in this cell (retries included). */
  cell_runs: number | null;
  is_current_in_cell: boolean | null;
}

export interface RunTimeline {
  run_id: string;
  kind: string;
  status: string;
  phase: string;
  outcome: string | null;
  live: boolean;
  milestones: { key: string; label: string; at: string }[];
  segments: TimelineSegment[];
  total_seconds: number | null;
  run_seconds: number | null;
  duration_seconds: number | null;
  execution: ExecutionRecord;
  study: StudyCellRef | null;
  notes: string[];
}

export async function getRunTimeline(
  runId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<RunTimeline> {
  const res = await apiFetch(`/generation/runs/${runId}/timeline/`, {
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

export interface RunQualityMetrics {
  run_id: string;
  selection: string;
  measured: number;
  tools: QualityToolRow[];
  notes: string[];
}

export async function getRunQualityMetrics(
  runId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<RunQualityMetrics> {
  const res = await apiFetch(`/generation/runs/${runId}/quality-metrics/`, {
    signal: opts.signal,
  });
  return res.json();
}
