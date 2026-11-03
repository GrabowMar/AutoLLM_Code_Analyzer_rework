/**
 * Semantic tones for status and severity badges. Every status in the app maps
 * to one of five tones; tones resolve to theme tokens, so both modes pass 4.5:1.
 * Render statuses with <StatusBadge>, not with these classes directly.
 */

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export const toneClasses: Record<Tone, string> = {
  success: "border-success/30 bg-success/10 text-success-fg",
  warning: "border-warning/30 bg-warning/10 text-warning-fg",
  danger: "border-destructive/30 bg-destructive/10 text-danger-fg",
  info: "border-info/30 bg-info/10 text-info-fg",
  neutral: "border-border bg-muted text-muted-foreground",
};

const statusTones: Record<string, Tone> = {
  completed: "success",
  succeeded: "success",
  success: "success",
  active: "success",
  healthy: "success",
  passed: "success",
  ready: "success",
  approved: "success",
  running: "info",
  generating: "info",
  building: "info",
  in_progress: "info",
  processing: "info",
  partial: "warning",
  warning: "warning",
  degraded: "warning",
  timeout: "warning",
  budget_exhausted: "warning",
  resource_limit: "warning",
  harness_killed: "danger",
  build_failed: "danger",
  start_failed: "danger",
  infra_error: "warning",
  not_applicable: "neutral",
  // v3 run outcomes (spec 6.12): an infra error or an excluded run is attrition
  // (missing data), a build/start/output failure is a measured failure.
  tested: "success",
  output_invalid: "danger",
  build_fail: "danger",
  start_fail: "danger",
  excluded: "neutral",
  // Evaluation status: "not measured" is missing data, never a pass or a zero.
  measured: "success",
  not_measured: "warning",
  // v3 terminations (spec 5.1); "completed" and "cancelled" are listed elsewhere.
  wall_clock: "warning",
  idle_timeout: "warning",
  max_turns: "warning",
  harness_crash: "danger",
  llm_error: "danger",
  infra_interrupted: "warning",
  oom_killed: "danger",
  // Study lifecycle (draft/running/paused/completed/cancelled are listed elsewhere).
  planned: "neutral",
  piloting: "info",
  stopped: "warning",
  // Study cell states (planned/queued/running/excluded are listed elsewhere).
  done: "success",
  infra_pending: "warning",
  harness_error: "danger",
  failed: "danger",
  error: "danger",
  rejected: "danger",
  unhealthy: "danger",
  // Execution pipeline (spec §B.3 / §C.3): queued states stay neutral like
  // "pending"; work the executor is doing is info; waits it will retry are warning.
  queued: "neutral",
  eval_queued: "neutral",
  evaluating: "info",
  warming: "info",
  warming_model: "info",
  backoff: "warning",
  retry_backoff: "warning",
  paused: "warning",
  draining: "warning",
  cancelled: "neutral",
  // Endpoints and relay sessions.
  online: "success",
  offline: "danger",
  disabled: "neutral",
  standby: "neutral",
  // Human evaluation: task lifecycle, participants, assignments, app hosting.
  // A skipped / expired assignment or an unreachable app is missing data (warning).
  draft: "neutral",
  open: "success",
  closed: "neutral",
  archived: "neutral",
  invited: "neutral",
  withdrawn: "neutral",
  assigned: "neutral",
  submitted: "success",
  skipped: "warning",
  expired: "warning",
  starting: "info",
  stopping: "info",
  idle: "neutral",
  unavailable: "warning",
};

/** Statuses that represent work in flight; StatusBadge pulses their dot. */
export const liveStatuses = new Set([
  "running",
  "generating",
  "building",
  "in_progress",
  "processing",
  "evaluating",
  "warming",
  "warming_model",
  "provisioning",
  "installing",
  "updating",
]);

/** Containers: "running" is the healthy steady state, not work in progress. */
export const containerTones: Partial<Record<string, Tone>> = {
  running: "success",
};

export function statusTone(
  status: string | null | undefined,
  overrides?: Partial<Record<string, Tone>>,
): Tone {
  const key = (status ?? "").toLowerCase();
  return overrides?.[key] ?? statusTones[key] ?? "neutral";
}

/** Study cell states (`StudyCell.state`, spec 3). */
export type CellState =
  | "planned"
  | "queued"
  | "running"
  | "done"
  | "infra_pending"
  | "excluded";

/** Run outcomes (`GenerationRun.outcome`, spec 6.12); "" while the run has none. */
export type RunOutcome =
  | "output_invalid"
  | "build_fail"
  | "start_fail"
  | "tested"
  | "infra_error"
  | "excluded";

/**
 * An infra error (`infra_pending`, `infra_error`) or an excluded cell is attrition: missing data
 * (warning / neutral), never a failure. Build, start and output failures are measured failures.
 */
export const cellStateTones: Record<CellState, Tone> = {
  planned: "neutral",
  queued: "neutral",
  running: "info",
  done: "success",
  infra_pending: "warning",
  excluded: "neutral",
};

export const cellStateLabels: Record<CellState, string> = {
  planned: "Planned",
  queued: "Queued",
  running: "Running",
  done: "Done",
  infra_pending: "Infrastructure retry pending",
  excluded: "Excluded",
};

export const outcomeTones: Record<RunOutcome, Tone> = {
  tested: "success",
  output_invalid: "danger",
  build_fail: "danger",
  start_fail: "danger",
  infra_error: "warning",
  excluded: "neutral",
};

export const outcomeLabels: Record<RunOutcome, string> = {
  tested: "Tested",
  output_invalid: "Output invalid",
  build_fail: "Build failed",
  start_fail: "Start failed",
  infra_error: "Infrastructure error",
  excluded: "Excluded",
};

/** Cell states whose work is in flight (pulse like `liveStatuses`). */
export const liveCellStates: ReadonlySet<CellState> = new Set(["running"]);

/** Solid fill per tone for small status squares (grids, slot meters). */
export const toneFillClasses: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground/30",
};

/**
 * The tone of a study cell: a done cell is coloured by its current run's outcome (a done cell
 * without a known outcome is neutral, never "success"); any other cell by its state.
 */
export function cellTone(
  state: string | null | undefined,
  outcome?: string | null,
): Tone {
  if (state === "done") {
    return outcomeTones[(outcome ?? "") as RunOutcome] ?? "neutral";
  }
  return cellStateTones[(state ?? "") as CellState] ?? "neutral";
}

/** Findings severity. Critical is solid so it reads apart from high without a sixth hue. */
export const severityColors: Record<string, string> = {
  critical: "border-transparent bg-destructive text-destructive-foreground",
  high: toneClasses.danger,
  medium: toneClasses.warning,
  low: toneClasses.info,
  info: toneClasses.neutral,
};

/** User-picked avatar/accent swatches — raw hues on purpose, they are the user's choice. */
export const avatarColorClasses: Record<string, string> = {
  blue: "bg-blue-500",
  indigo: "bg-indigo-500",
  purple: "bg-purple-500",
  pink: "bg-pink-500",
  red: "bg-red-500",
  orange: "bg-orange-500",
  amber: "bg-amber-500",
  green: "bg-green-500",
  teal: "bg-teal-500",
  cyan: "bg-cyan-500",
};
