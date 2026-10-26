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
  submitted: "success",
  running: "info",
  generating: "info",
  building: "info",
  in_progress: "info",
  processing: "info",
  submitting: "info",
  // Analysis study: protocol frozen, ready to launch.
  locked: "info",
  partial: "warning",
  warning: "warning",
  degraded: "warning",
  timeout: "warning",
  budget_exhausted: "warning",
  resource_limit: "warning",
  truncated: "warning",
  interrupted: "warning",
  stale: "warning",
  // A tool that did not run leaves a gap in the data: never a neutral "done".
  skipped: "warning",
  missing: "warning",
  not_launched: "warning",
  // Self-test without evidence: no proof the tool works on this image.
  unverified: "warning",
  untested: "warning",
  canary_unknown: "warning",
  image_missing: "warning",
  harness_killed: "danger",
  build_failed: "danger",
  start_failed: "danger",
  infra_error: "warning",
  not_applicable: "neutral",
  harness_error: "danger",
  failed: "danger",
  // A timed-out tool produced no data, exactly like a failed one.
  timed_out: "danger",
  invalid_output: "danger",
  canary_failed: "danger",
  error: "danger",
  rejected: "danger",
  unhealthy: "danger",
  queued: "neutral",
  pending: "neutral",
  cancelled: "neutral",
  draft: "neutral",
  not_run: "neutral",
};

/** Statuses that represent work in flight; StatusBadge pulses their dot. */
export const liveStatuses = new Set([
  "queued",
  "running",
  "generating",
  "submitting",
  "building",
  "in_progress",
  "processing",
]);

/**
 * AI finding grounding verdicts (StatusBadge `tones`): only exact and
 * relocated evidence can be counted; the rest is unreliable evidence.
 */
export const groundingTones: Partial<Record<string, Tone>> = {
  exact: "success",
  relocated: "info",
  ungrounded: "warning",
  invalid_file: "danger",
  out_of_range: "danger",
};

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
