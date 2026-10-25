/**
 * Experiments API (spec §F.1): designed model comparisons — conditions ×
 * apps × repeats — with their lifecycle, live status, results statistics
 * and automation (schedules, post-run). Wire types live in
 * `./types/experiments`; the shared preview / comparability shapes below are
 * the server's existing preview payload, extended by `ExperimentPreviewV2`.
 */
import { apiFetch } from "./core";
import type {
  GenerationBatchSummary,
  LLMParams,
  NamedPresetRef,
} from "./generation";
import type {
  CancelBody,
  CancelResult,
  CellsPayload,
  CloneBody,
  ConditionBulkResult,
  ConditionSetSpec,
  CronPreview,
  ExperimentConditionV2,
  ExperimentErrorBody,
  ExperimentCreateV2,
  ExperimentDetail,
  ExperimentListQuery,
  ExperimentPreviewV2,
  ExperimentResults,
  ExperimentState,
  ExperimentStatusV2Payload,
  ExperimentUpdateV2,
  LaunchBody,
  LaunchResult,
  PaginatedExperiments,
  PostRunConfig,
  PostRunOut,
  PostRunSpec,
  RequeueLegacyResult,
  ResultsQuery,
  ResumeBody,
  ScheduleOut,
  ScheduleRunNowResult,
  ScheduleSpec,
} from "./types/experiments";

export type * from "./types/experiments";

/** agent: cells run the sandboxed coding-agent harness; scaffolding: template prompts. */
export type ExperimentMode = "agent" | "scaffolding";

// ── Shared preview shapes (server: services/experiments.py) ───────────

export interface ExperimentCondition {
  id: number;
  label: string;
  /** Profile pk; optional (null) for agent-mode conditions. */
  profile: number | null;
  model: number;
  /** Agent harness for agent-mode experiments ("" = experiment default). */
  harness: string;
  /** Harness preset pk; when set, `harness` is the preset's harness name. */
  harness_preset?: number | null;
  harness_preset_ref?: NamedPresetRef | null;
  model_name: string | null;
  bundle_slug: string | null;
  bundle_version: number | null;
  param_overrides: LLMParams;
  created_at: string;
}

export interface ExperimentLaunchError {
  code: string;
  detail: string;
  condition_id: number;
}

export interface ExperimentPreview {
  total_jobs: number;
  /** Cells a launch would create now (failed / cancelled cells are re-run). */
  pending_jobs?: number;
  conditions: number;
  apps: number;
  repeats: number;
  estimated_cost: number;
  /** budget_upper_bound for agent mode (budget cap × jobs), heuristic for scaffolding. */
  cost_estimate_kind?: "budget_upper_bound" | "heuristic";
  mode?: ExperimentMode;
  stack_slug?: string | null;
  stacks?: string[];
  presets?: { sampling: NamedPresetRef | null; budget: NamedPresetRef | null };
  per_condition: {
    condition_id: number;
    label: string;
    model: string;
    model_id?: string;
    harness?: string;
    harness_preset?: NamedPresetRef | null;
    profile?: string | null;
    bundle_slug: string | null;
    bundle_version: number | null;
    stack_slug?: string;
    jobs: number;
    estimated_cost: number;
  }[];
  /** What each condition will actually run with, and where conditions differ. */
  comparability?: ExperimentComparability | null;
  /** Problems that make a launch refuse (fix them first). */
  launch_errors?: ExperimentLaunchError[];
}

export interface ExperimentComparabilityRow {
  condition_id: number;
  label: string;
  /** Model slug. */
  model: string;
  model_name: string;
  harness: string;
  harness_version: string;
  harness_preset: NamedPresetRef | null;
  /** Non-default options only. */
  harness_options: Record<string, unknown>;
  sampling_policy: string;
  /** Explicit params the gateway sends (seed, provider and max_tokens excluded). */
  forced_params: Record<string, unknown>;
  output_cap: number | null;
  /** "default" or the explicit routing dict. */
  provider_routing: "default" | "not_applicable" | Record<string, unknown>;
  seed_forwarded: boolean;
  dropped_params: Record<string, string>;
  context_window: number;
  context_window_source: "catalog" | "fallback" | string;
  stack_slug: string;
  /** "slug@v" or null. */
  profile: string | null;
  param_overrides: Record<string, unknown>;
  /** Local endpoints: where the condition runs. */
  endpoint?: { slug: string; kind: string; max_concurrency?: number } | null;
  /** Every problem, blocking or not. */
  problems: string[];
  /** The blocking subset of `problems`. */
  errors?: string[];
}

export interface ExperimentComparability {
  conditions: ExperimentComparabilityRow[];
  differs_on: string[];
  has_problems: boolean;
  has_errors?: boolean;
}

// ── helpers ───────────────────────────────────────────────────────────

const BASE = "/generation/experiments";

interface FetchOptions {
  signal?: AbortSignal;
}

function post(body: unknown, options: FetchOptions = {}): RequestInit {
  return {
    method: "POST",
    body: JSON.stringify(body ?? {}),
    signal: options.signal,
  };
}

function query(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    q.set(key, String(value));
  }
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

// ── list / read / write ───────────────────────────────────────────────

export async function listExperiments(
  q: ExperimentListQuery = {},
  options: FetchOptions = {},
): Promise<PaginatedExperiments> {
  const res = await apiFetch(
    `${BASE}/${query({
      page: q.page,
      per_page: q.per_page,
      status: q.status,
      mode: q.mode,
      search: q.search,
      endpoint: q.endpoint,
      ordering: q.ordering,
    })}`,
    { signal: options.signal },
  );
  return res.json();
}

/** An experiment detail, plus the launch batch when the call also launched it. */
export type CreatedExperiment = ExperimentDetail & {
  batch?: LaunchResult["batch"];
};

/**
 * The detail of a create / clone response: a plain `ExperimentDetail` (200),
 * or a `LaunchResult` (`{experiment, batch, …}`, 201) when it also launched.
 */
export function createdExperiment(
  data: ExperimentDetail | LaunchResult,
): CreatedExperiment {
  if (
    "experiment" in data &&
    data.experiment &&
    typeof data.experiment === "object"
  ) {
    return { ...data.experiment, batch: data.batch };
  }
  return data as ExperimentDetail;
}

/**
 * One call creates the experiment with its conditions; `launch` also launches
 * it. A refused launch throws `{detail, code, errors, experiment_id}` (the
 * draft was kept).
 */
export async function createExperiment(
  body: ExperimentCreateV2,
  options: { launch?: boolean } = {},
): Promise<CreatedExperiment> {
  const res = await apiFetch(
    `${BASE}/${options.launch ? "?launch=true" : ""}`,
    post(body),
  );
  return createdExperiment(await res.json());
}

/** Stateless plan for an unsaved design (the composer's plan rail). */
export async function previewDraft(
  body: ExperimentCreateV2,
  signal?: AbortSignal,
): Promise<ExperimentPreviewV2> {
  const res = await apiFetch(`${BASE}/preview/`, post(body, { signal }));
  return res.json();
}

export async function getExperiment(
  id: string,
  options: FetchOptions = {},
): Promise<ExperimentDetail> {
  const res = await apiFetch(`${BASE}/${id}/`, { signal: options.signal });
  return res.json();
}

export async function updateExperiment(
  id: string,
  body: ExperimentUpdateV2,
): Promise<ExperimentDetail> {
  const res = await apiFetch(`${BASE}/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  return res.json();
}

export async function deleteExperiment(
  id: string,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`${BASE}/${id}/`, { method: "DELETE" });
  return res.json();
}

// ── conditions (draft only) ───────────────────────────────────────────

/** Replaces the draft's condition set. */
export async function putConditions(
  id: string,
  spec: ConditionSetSpec,
): Promise<ExperimentConditionV2[]> {
  const res = await apiFetch(`${BASE}/${id}/conditions/`, {
    method: "PUT",
    body: JSON.stringify(spec),
  });
  return res.json();
}

export async function addConditionsBulk(
  id: string,
  spec: ConditionSetSpec,
): Promise<ConditionBulkResult> {
  const res = await apiFetch(`${BASE}/${id}/conditions/bulk/`, post(spec));
  return res.json();
}

export async function deleteCondition(
  id: string,
  conditionId: number,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`${BASE}/${id}/conditions/${conditionId}/`, {
    method: "DELETE",
  });
  return res.json();
}

// ── preview and lifecycle ─────────────────────────────────────────────

/** Plan of a saved experiment (a draft, or the cells a top-up would create). */
export async function getPreview(
  id: string,
  options: FetchOptions = {},
): Promise<ExperimentPreviewV2> {
  const res = await apiFetch(`${BASE}/${id}/preview/`, {
    signal: options.signal,
  });
  return res.json();
}

/**
 * Launch a draft, or top up failed / cancelled cells (`mode: "top_up"`; a
 * finished experiment is reopened). Blocked → throws `{detail, code, errors}`.
 */
export async function launchExperiment(
  id: string,
  body: LaunchBody = {},
): Promise<LaunchResult> {
  const res = await apiFetch(`${BASE}/${id}/launch/`, post(body));
  return res.json();
}

export async function pauseExperiment(id: string): Promise<ExperimentState> {
  const res = await apiFetch(`${BASE}/${id}/pause/`, post({}));
  return res.json();
}

export async function resumeExperiment(
  id: string,
  body: ResumeBody = {},
): Promise<ExperimentState> {
  const res = await apiFetch(`${BASE}/${id}/resume/`, post(body));
  return res.json();
}

/** `all` lets running evaluations finish (they are graded); `pending` only drops queued cells. */
export async function cancelExperiment(
  id: string,
  body: CancelBody = {},
): Promise<CancelResult> {
  const res = await apiFetch(`${BASE}/${id}/cancel/`, post(body));
  return res.json();
}

export async function archiveExperiment(id: string): Promise<ExperimentDetail> {
  const res = await apiFetch(`${BASE}/${id}/archive/`, post({}));
  return res.json();
}

/** A draft copy; with `launch: true` it is launched too (refused → throws with `experiment_id`). */
export async function cloneExperiment(
  id: string,
  body: CloneBody = {},
): Promise<CreatedExperiment> {
  const res = await apiFetch(`${BASE}/${id}/clone/`, post(body));
  return createdExperiment(await res.json());
}

// ── read-only views ───────────────────────────────────────────────────

export async function getStatus(
  id: string,
  options: FetchOptions = {},
): Promise<ExperimentStatusV2Payload> {
  const res = await apiFetch(`${BASE}/${id}/status/`, {
    signal: options.signal,
  });
  return res.json();
}

export async function getCells(
  id: string,
  options: FetchOptions = {},
): Promise<CellsPayload> {
  const res = await apiFetch(`${BASE}/${id}/cells/`, {
    signal: options.signal,
  });
  return res.json();
}

export async function getResults(
  id: string,
  q: ResultsQuery = {},
  options: FetchOptions = {},
): Promise<ExperimentResults> {
  const res = await apiFetch(
    `${BASE}/${id}/results/${query({
      baseline: q.baseline,
      exclude_invalid: q.exclude_invalid,
      exclude_truncated: q.exclude_truncated,
      common_cells: q.common_cells,
      fingerprint: q.fingerprint,
      metric: q.metric,
    })}`,
    { signal: options.signal },
  );
  return res.json();
}

export async function listBatches(
  id: string,
): Promise<GenerationBatchSummary[]> {
  const res = await apiFetch(`${BASE}/${id}/batches/`);
  return res.json();
}

/** Moves this experiment's thread-era PENDING / RUNNING rows into the executor queue. */
export async function requeueLegacy(id: string): Promise<RequeueLegacyResult> {
  const res = await apiFetch(`${BASE}/${id}/jobs/requeue-legacy/`, post({}));
  return res.json();
}

/** The export as a downloadable Blob (pretty-printed JSON, or the jobs as CSV). */
export async function exportExperiment(
  id: string,
  format: "json" | "csv" = "json",
): Promise<Blob> {
  const res = await apiFetch(`${BASE}/${id}/export/?format=${format}`);
  if (format === "csv")
    return new Blob([await res.text()], { type: "text/csv" });
  const data: unknown = await res.json();
  return new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
}

// ── automation ────────────────────────────────────────────────────────

export async function listSchedules(id: string): Promise<ScheduleOut[]> {
  const res = await apiFetch(`${BASE}/${id}/schedules/`);
  return res.json();
}

export async function createSchedule(
  id: string,
  spec: ScheduleSpec,
): Promise<ScheduleOut> {
  const res = await apiFetch(`${BASE}/${id}/schedules/`, post(spec));
  return res.json();
}

export async function updateSchedule(
  id: string,
  scheduleId: number,
  patch: Partial<ScheduleSpec>,
): Promise<ScheduleOut> {
  const res = await apiFetch(`${BASE}/${id}/schedules/${scheduleId}/`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return res.json();
}

export async function deleteSchedule(
  id: string,
  scheduleId: number,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`${BASE}/${id}/schedules/${scheduleId}/`, {
    method: "DELETE",
  });
  return res.json();
}

export async function runScheduleNow(
  id: string,
  scheduleId: number,
): Promise<ScheduleRunNowResult> {
  const res = await apiFetch(
    `${BASE}/${id}/schedules/${scheduleId}/run-now/`,
    post({}),
  );
  return res.json();
}

/** Validates a cron expression and lists its next five runs in `tz`. */
export async function cronPreview(
  cron: string,
  tz: string,
  options: FetchOptions = {},
): Promise<CronPreview> {
  const res = await apiFetch(
    `/generation/cron-preview/${query({ cron, tz })}`,
    {
      signal: options.signal,
    },
  );
  return res.json();
}

export async function getPostRun(id: string): Promise<PostRunConfig> {
  const res = await apiFetch(`${BASE}/${id}/post-run/`);
  return res.json();
}

export async function putPostRun(
  id: string,
  spec: PostRunSpec,
): Promise<PostRunConfig> {
  const res = await apiFetch(`${BASE}/${id}/post-run/`, {
    method: "PUT",
    body: JSON.stringify(spec),
  });
  return res.json();
}

/** Starts a manual post-run (analyses for jobs that have none yet, then the report). */
export async function runPostRun(id: string): Promise<PostRunOut> {
  const res = await apiFetch(`${BASE}/${id}/post-run/run/`, post({}));
  return res.json();
}

/** The 400 body of a refused create / launch, or null for any other error. */
export function experimentErrorBody(
  error: unknown,
): ExperimentErrorBody | null {
  if (!error || typeof error !== "object") return null;
  const body = error as Record<string, unknown>;
  if (typeof body.code !== "string" || !Array.isArray(body.errors)) return null;
  return body as unknown as ExperimentErrorBody;
}
