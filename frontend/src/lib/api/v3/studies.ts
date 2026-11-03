/**
 * Studies client (spec 9.4), mounted at `/api/generation/`.
 *
 * Every helper takes an optional `{signal}` so a superseded request (the PlanRail's debounced resolve, a
 * results refetch) can be aborted. Errors are the `apiFetch` throw: the `ErrorOut` body
 * (`{detail, code, issues?}`), readable with `formatApiError` / `apiErrorStatus` from `$lib/api/core`.
 * Operation ids (backend `study/api.py`) are noted per helper.
 */
import { apiFetch } from "../core";
import { GENERATION_API, type RequestOptions } from "./library";
import type {
  AdhocIn,
  AdhocOut,
  AttritionRow,
  CellDiffOut,
  CloneIn,
  ConditionPauseOut,
  CronPreviewOut,
  ExpandOut,
  LaunchIn,
  LaunchOut,
  Page,
  PlanOut,
  PostRunIn,
  PostRunOut,
  PromptOverrideIn,
  ResolveIn,
  ResolvedCellDetailOut,
  ResolvedCellOut,
  ScheduleIn,
  ScheduleOut,
  StudyCellsPayload,
  StudyCellsQuery,
  StudyCreateIn,
  StudyListOut,
  StudyListQuery,
  StudyOut,
  StudyPatchIn,
  StudyPromptOverrideOut,
  StudyResultsOut,
  StudyResultsQuery,
  TopUpIn,
  TopUpOut,
} from "./types";

const seg = encodeURIComponent;

function query(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(
    params as Record<string, unknown>,
  )) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function getJson<T>(path: string, options?: RequestOptions): Promise<T> {
  const res = await apiFetch(`${GENERATION_API}${path}`, {
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

async function send<T>(
  method: string,
  path: string,
  body: unknown,
  options?: RequestOptions,
): Promise<T> {
  const res = await apiFetch(`${GENERATION_API}${path}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options?.signal,
  });
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const study = (id: string) => `/studies/${seg(id)}`;

// ── Collection ──────────────────────────────────────────────────────────

/** `generation_studies_list`. */
export function listStudies(
  params: StudyListQuery = {},
  options?: RequestOptions,
): Promise<Page<StudyListOut>> {
  return getJson(`/studies/${query(params)}`, options);
}

/** `generation_studies_create`: a draft (the design is validated; 400 `invalid_design` otherwise). */
export function createStudy(
  body: StudyCreateIn,
  options?: RequestOptions,
): Promise<StudyOut> {
  return send("POST", "/studies/", body, options);
}

/** `generation_studies_adhoc`: resolve and (by default) launch in one call. */
export function createAdhocStudy(
  body: AdhocIn,
  options?: RequestOptions,
): Promise<AdhocOut> {
  return send("POST", "/studies/adhoc/", body, options);
}

/** `generation_studies_cron_preview`. */
export function cronPreview(
  cron: string,
  timezone = "UTC",
  options?: RequestOptions,
): Promise<CronPreviewOut> {
  return getJson(`/studies/cron-preview/${query({ cron, timezone })}`, options);
}

// ── One study ───────────────────────────────────────────────────────────

/** `generation_studies_get`. */
export function getStudy(
  id: string,
  options?: RequestOptions,
): Promise<StudyOut> {
  return getJson(`${study(id)}/`, options);
}

/** `generation_studies_update` (design and analysis plan: before launch only; 409 `study_locked`). */
export function updateStudy(
  id: string,
  body: StudyPatchIn,
  options?: RequestOptions,
): Promise<StudyOut> {
  return send("PATCH", `${study(id)}/`, body, options);
}

/** `generation_studies_resolve`: the plan of the saved design (ephemeral unless `persist`). */
export function resolveStudy(
  id: string,
  body: ResolveIn = {},
  options?: RequestOptions,
): Promise<PlanOut> {
  return send("POST", `${study(id)}/resolve/`, body, options);
}

/** `generation_studies_freeze`: persist the plan (Start needs a fresh, error-free frozen plan). */
export function freezePlan(
  id: string,
  dropIncompatible = false,
  options?: RequestOptions,
): Promise<PlanOut> {
  return send(
    "POST",
    `${study(id)}/freeze-plan/`,
    { drop_incompatible: dropIncompatible },
    options,
  );
}

/** `generation_studies_plan_cells`. */
export function listPlanCells(
  id: string,
  planHash: string,
  params: {
    condition?: number;
    unit?: number;
    page?: number;
    page_size?: number;
  } = {},
  options?: RequestOptions,
): Promise<Page<ResolvedCellOut>> {
  return getJson(
    `${study(id)}/plans/${seg(planHash)}/cells/${query(params)}`,
    options,
  );
}

/** `generation_studies_plan_cell`: stimuli with source spans, effective roles/params, identity. */
export function getPlanCell(
  id: string,
  planHash: string,
  cellKey: string,
  options?: RequestOptions,
): Promise<ResolvedCellDetailOut> {
  return getJson(
    `${study(id)}/plans/${seg(planHash)}/cells/${seg(cellKey)}/`,
    options,
  );
}

/** `generation_studies_plan_diff`. */
export function diffPlanCells(
  id: string,
  planHash: string,
  a: string,
  b: string,
  options?: RequestOptions,
): Promise<CellDiffOut> {
  return send(
    "POST",
    `${study(id)}/plans/${seg(planHash)}/diff/`,
    { a, b },
    options,
  );
}

/** `generation_studies_launch` (409 `plan_stale`; 400 when `confirm_usd` < p90 is required). */
export function launchStudy(
  id: string,
  body: LaunchIn,
  options?: RequestOptions,
): Promise<LaunchOut> {
  return send("POST", `${study(id)}/launch/`, body, options);
}

/** `generation_studies_expand`: pilot → main. */
export function expandStudy(
  id: string,
  body: { override_gates?: boolean; reason?: string | null } = {},
  options?: RequestOptions,
): Promise<ExpandOut> {
  return send("POST", `${study(id)}/expand/`, body, options);
}

/** `generation_studies_pause`. */
export function pauseStudy(
  id: string,
  reason?: string,
  options?: RequestOptions,
): Promise<StudyOut> {
  return send(
    "POST",
    `${study(id)}/pause/`,
    { reason: reason ?? null },
    options,
  );
}

/** `generation_studies_resume`. */
export function resumeStudy(
  id: string,
  options?: RequestOptions,
): Promise<StudyOut> {
  return send("POST", `${study(id)}/resume/`, undefined, options);
}

/** `generation_studies_cancel` (pending runs are cancelled after commit). */
export function cancelStudy(
  id: string,
  options?: RequestOptions,
): Promise<StudyOut> {
  return send("POST", `${study(id)}/cancel/`, undefined, options);
}

/** `generation_studies_top_up`: only infra / missing cells (a usable run is never superseded). */
export function topUpStudy(
  id: string,
  body: TopUpIn,
  options?: RequestOptions,
): Promise<TopUpOut> {
  return send("POST", `${study(id)}/top-up/`, body, options);
}

/** `generation_studies_add_replicates`: exploratory only; the runs are `beyond_plan`. */
export function addReplicates(
  id: string,
  replicates: number,
  options?: RequestOptions,
): Promise<{ created_runs: number; [key: string]: unknown }> {
  return send("POST", `${study(id)}/add-replicates/`, { replicates }, options);
}

/** `generation_studies_clone` (a draft; `version_diffs` lists re-pinned levels). */
export function cloneStudy(
  id: string,
  body: CloneIn,
  options?: RequestOptions,
): Promise<StudyOut> {
  return send("POST", `${study(id)}/clone/`, body, options);
}

/** `generation_studies_condition_pause` / `_resume`. */
export function setConditionPaused(
  id: string,
  conditionId: number,
  paused: boolean,
  options?: RequestOptions,
): Promise<ConditionPauseOut> {
  return send(
    "POST",
    `${study(id)}/conditions/${conditionId}/${paused ? "pause" : "resume"}/`,
    undefined,
    options,
  );
}

/** `generation_studies_cells`. */
export function getStudyCells(
  id: string,
  params: StudyCellsQuery = {},
  options?: RequestOptions,
): Promise<StudyCellsPayload> {
  return getJson(`${study(id)}/cells/${query(params)}`, options);
}

/** `generation_studies_results` (intention-to-treat by default). */
export function getStudyResults(
  id: string,
  params: StudyResultsQuery = {},
  options?: RequestOptions,
): Promise<StudyResultsOut> {
  return getJson(`${study(id)}/results/${query(params)}`, options);
}

/** `generation_studies_attrition`. */
export function getStudyAttrition(
  id: string,
  stage: "pilot" | "main" = "main",
  options?: RequestOptions,
): Promise<AttritionRow[]> {
  return getJson(`${study(id)}/attrition/${query({ stage })}`, options);
}

/** `generation_studies_export`: the study archive URL (a plain link downloads it). */
export function studyExportUrl(id: string): string {
  return `/api${GENERATION_API}${study(id)}/export/?format=zip`;
}

/** `generation_studies_prompt_overrides`. */
export function listPromptOverrides(
  id: string,
  options?: RequestOptions,
): Promise<StudyPromptOverrideOut[]> {
  return getJson(`${study(id)}/prompt-overrides/`, options);
}

/** `generation_studies_prompt_override_create`: forks the owner as a user version and swaps the level. */
export function createPromptOverride(
  id: string,
  body: PromptOverrideIn,
  options?: RequestOptions,
): Promise<StudyPromptOverrideOut> {
  return send("POST", `${study(id)}/prompt-overrides/`, body, options);
}

// ── Schedules and post-runs ─────────────────────────────────────────────

export function listSchedules(
  id: string,
  options?: RequestOptions,
): Promise<ScheduleOut[]> {
  return getJson(`${study(id)}/schedules/`, options);
}

export function createSchedule(
  id: string,
  body: ScheduleIn,
  options?: RequestOptions,
): Promise<ScheduleOut> {
  return send("POST", `${study(id)}/schedules/`, body, options);
}

export function updateSchedule(
  id: string,
  scheduleId: number,
  body: Partial<Omit<ScheduleIn, "action">>,
  options?: RequestOptions,
): Promise<ScheduleOut> {
  return send("PATCH", `${study(id)}/schedules/${scheduleId}/`, body, options);
}

export function deleteSchedule(
  id: string,
  scheduleId: number,
  options?: RequestOptions,
): Promise<void> {
  return send(
    "DELETE",
    `${study(id)}/schedules/${scheduleId}/`,
    undefined,
    options,
  );
}

export function runScheduleNow(
  id: string,
  scheduleId: number,
  options?: RequestOptions,
): Promise<Record<string, unknown>> {
  return send(
    "POST",
    `${study(id)}/schedules/${scheduleId}/run-now/`,
    undefined,
    options,
  );
}

export function listPostRuns(
  id: string,
  options?: RequestOptions,
): Promise<PostRunOut[]> {
  return getJson(`${study(id)}/post-runs/`, options);
}

export function createPostRun(
  id: string,
  body: PostRunIn,
  options?: RequestOptions,
): Promise<PostRunOut> {
  return send("POST", `${study(id)}/post-runs/`, body, options);
}
