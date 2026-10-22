/**
 * Display helpers shared by the batch list, the batch page and the job page:
 * mode labels, phase-aware job status, per-trial outcome and number formats.
 */
import type {
  GenerationBatchCounts,
  GenerationBatchSummary,
  GenerationJobList,
  PresetRef,
} from "$lib/api/generation";
import type { Tone } from "$lib/constants/colors";

export const BATCH_MODE_LABELS: Record<string, string> = {
  agent: "Agent",
  scaffolding: "Template scaffolding",
  custom: "Single prompt",
};

export function batchModeLabel(mode: string | null | undefined): string {
  return BATCH_MODE_LABELS[mode ?? ""] ?? (mode || "Unknown");
}

const EMPTY_COUNTS: GenerationBatchCounts = {
  pending: 0,
  queued: 0,
  running: 0,
  completed: 0,
  failed: 0,
  cancelled: 0,
};

/** Counts with every key present (older payloads may omit some). */
export function batchCounts(
  batch: Pick<GenerationBatchSummary, "counts"> | null | undefined,
): GenerationBatchCounts {
  return { ...EMPTY_COUNTS, ...(batch?.counts ?? {}) };
}

export function finishedCount(counts: GenerationBatchCounts): number {
  return counts.completed + counts.failed + counts.cancelled;
}

export function activeCount(counts: GenerationBatchCounts): number {
  return counts.pending + counts.queued + counts.running;
}

/** "2 tasks × 3 models × 2 harness presets × 3 trials" (factors of 0 omitted). */
export function batchDesign(batch: GenerationBatchSummary): string {
  const parts: string[] = [];
  const plural = (n: number, word: string) =>
    `${n} ${word}${n === 1 ? "" : "s"}`;
  if (batch.app_count > 0) parts.push(plural(batch.app_count, "task"));
  else if (batch.mode === "agent") parts.push("free-form task");
  if (batch.model_count > 0) parts.push(plural(batch.model_count, "model"));
  // Legacy batches (before presets) only record harness names.
  const presets = batch.harness_presets?.length ?? 0;
  const harnesses = batch.harnesses?.length ?? 0;
  if (presets > 1) parts.push(plural(presets, "harness preset"));
  else if (!presets && harnesses > 1) parts.push(`${harnesses} harnesses`);
  if (batch.trials) parts.push(plural(batch.trials, "trial"));
  return parts.join(" × ");
}

export function presetRefLabel(ref: PresetRef | null | undefined): string {
  return ref ? `${ref.slug} v${ref.version}` : "";
}

/** Live status of one job: the persisted phase refines pending/running. */
export function jobDisplayStatus(
  job: Pick<GenerationJobList, "status" | "phase">,
): string {
  const live = job.status === "pending" || job.status === "running";
  if (live && job.phase === "queued") return "queued";
  if (live && job.phase === "evaluating") return "evaluating";
  return job.status;
}

/** Tones for statuses `statusTone()` does not know (StatusBadge `tones`). */
export const jobPhaseTones: Partial<Record<string, Tone>> = {
  queued: "neutral",
  evaluating: "info",
  pending: "neutral",
  cancelled: "neutral",
};

/** Conformance outcomes that grade a sample (infra errors / n/a do not). */
const GRADED_EVAL = new Set([
  "passed",
  "failed",
  "build_failed",
  "start_failed",
]);

export type TrialOutcome =
  | "passed"
  | "not_passed"
  | "eval_error"
  | "unevaluated"
  | "done"
  | "evaluating"
  | "running"
  | "queued"
  | "pending"
  | "failed"
  | "cancelled";

/** What one trial contributed so far, for dots, legends and aggregation. */
export function trialOutcome(
  job: GenerationJobList,
  graded: boolean,
): TrialOutcome {
  const status = jobDisplayStatus(job);
  if (status === "queued" || status === "evaluating" || status === "pending")
    return status;
  if (status === "running") return "running";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  if (status !== "completed") return "pending";
  if (!graded) return "done";
  if (job.eval_status === "passed") return "passed";
  if (job.eval_status && GRADED_EVAL.has(job.eval_status)) return "not_passed";
  if (job.eval_status === "infra_error") return "eval_error";
  return "unevaluated";
}

export function isGradedOutcome(job: GenerationJobList): boolean {
  return (
    job.status === "completed" &&
    !!job.eval_status &&
    GRADED_EVAL.has(job.eval_status) &&
    job.validity !== "exclude"
  );
}

export const TRIAL_OUTCOME_LABELS: Record<TrialOutcome, string> = {
  passed: "Passed every check",
  not_passed: "Failed checks",
  eval_error: "Evaluation error",
  unevaluated: "Not evaluated yet",
  done: "Completed",
  evaluating: "Evaluating",
  running: "Running",
  queued: "Queued",
  pending: "Pending",
  failed: "Run failed",
  cancelled: "Cancelled",
};

/**
 * Dot styles: solid = finished, hollow = waiting, pulsing = in flight. Shape
 * carries meaning alongside hue, and every dot also has a text label.
 */
export const TRIAL_DOT_CLASSES: Record<TrialOutcome, string> = {
  passed: "bg-success",
  not_passed: "bg-destructive",
  eval_error: "bg-warning",
  unevaluated: "border-2 border-success bg-transparent",
  done: "bg-success",
  evaluating: "bg-info",
  running: "bg-info",
  queued: "border-2 border-info bg-transparent",
  pending: "border-2 border-muted-foreground/60 bg-transparent",
  failed: "border-2 border-destructive bg-transparent",
  cancelled: "bg-muted-foreground/50",
};

/** USD with enough precision for single runs: "$0.00", "$0.0042", "$1.37". */
export function formatUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (value === 0) return "$0.00";
  if (Math.abs(value) < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

/** 0..1 → "87%"; null → "—". */
export function formatPassRate(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

/** Short id for display ("7cc3c519"). */
export function shortId(id: string | null | undefined): string {
  return (id ?? "").slice(0, 8);
}

/** Ninja's 404 body is `{detail: "Not Found"}`; apiFetch throws the body. */
export function isNotFoundError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const detail = (error as Record<string, unknown>).detail;
  return typeof detail === "string" && /not found/i.test(detail);
}
