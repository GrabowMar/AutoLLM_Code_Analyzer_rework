/**
 * Semantic tones for status and severity badges. Every status in the app maps
 * to one of five tones; tones resolve to theme tokens, so both modes pass 4.5:1.
 * Render statuses with <StatusBadge>, not with these classes directly.
 */

import type { CellCode } from "$lib/api/types/experiments";

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

/**
 * Experiment cell state codes (`CellsPayload.cells[i][3]`, spec §F.1):
 * p pending, b backoff, w waiting for model prefetch, r running, q eval_queued,
 * e evaluating, c completed-pass, f completed-fail, u completed-ungraded,
 * x failed (no result), k cancelled.
 */
export type { CellCode };

/** A failed run is missing data (warning), distinct from a graded fail (danger). */
export const cellCodeTones: Record<CellCode, Tone> = {
  p: "neutral",
  b: "warning",
  w: "info",
  r: "info",
  q: "neutral",
  e: "info",
  c: "success",
  f: "danger",
  u: "neutral",
  x: "warning",
  k: "neutral",
};

export const cellCodeLabels: Record<CellCode, string> = {
  p: "Pending",
  b: "Retrying after backoff",
  w: "Waiting for model to load",
  r: "Running",
  q: "Queued for evaluation",
  e: "Evaluating",
  c: "Passed",
  f: "Failed tests",
  u: "Completed, not graded",
  x: "Run failed",
  k: "Cancelled",
};

/** Cell codes whose work is in flight (pulse like `liveStatuses`). */
export const liveCellCodes: ReadonlySet<CellCode> = new Set(["r", "e", "w"]);

/** Solid fill per tone for small status squares (grids, slot meters). */
export const toneFillClasses: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground/30",
};

export function cellTone(code: string | null | undefined): Tone {
  return cellCodeTones[(code ?? "") as CellCode] ?? "neutral";
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
