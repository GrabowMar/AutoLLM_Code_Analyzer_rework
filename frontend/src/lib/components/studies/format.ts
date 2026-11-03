/**
 * Display helpers of the study pages (list, builder, record, budget). Missing values are an em dash
 * ("not measured"), never 0: an unknown spend, rate or estimate is `null` on the wire and stays `—`.
 */
import type { Tone } from "$lib/constants/colors";
import type { StudyKind, StudyMode, StudyStatus } from "$lib/api/v3/types";

export const DASH = "—";

export const MODE_LABELS: Record<StudyMode, string> = {
  app: "Agentic app",
  slot: "Slot",
};
export const KIND_LABELS: Record<StudyKind, string> = {
  exploratory: "Exploratory",
  confirmatory: "Confirmatory",
};

export const STUDY_STATUS_TONES: Record<StudyStatus, Tone> = {
  draft: "neutral",
  planned: "neutral",
  piloting: "info",
  running: "info",
  paused: "warning",
  completed: "success",
  cancelled: "neutral",
  stopped: "warning",
};

/** Run outcome taxonomy (6.12) and run lifecycle statuses. */
export const OUTCOME_LABELS: Record<string, string> = {
  tested: "Tested",
  output_invalid: "Output invalid",
  build_fail: "Build failed",
  start_fail: "Start failed",
  infra_error: "Infra error",
  excluded: "Excluded",
};

export const RUN_TONES: Record<string, Tone> = {
  tested: "success",
  output_invalid: "danger",
  build_fail: "danger",
  start_fail: "danger",
  infra_error: "warning",
  excluded: "neutral",
  pending: "neutral",
  queued: "neutral",
  running: "info",
  eval_queued: "neutral",
  evaluating: "info",
  completed: "success",
  failed: "warning",
  cancelled: "neutral",
};

export const CELL_STATE_TONES: Record<string, Tone> = {
  planned: "neutral",
  queued: "neutral",
  running: "info",
  done: "success",
  infra_pending: "warning",
  excluded: "neutral",
};

/** Why a study or condition is paused (closed vocabulary, `PausedEvent.reason`). */
export const PAUSE_LABELS: Record<string, string> = {
  manual: "Paused by you",
  budget: "Study cap reached",
  quota: "Model quota reached (5h / week)",
  spend_cap: "A spend cap blocks new runs",
  infra: "Infrastructure problems",
};

/** Paused for money (the header chip turns into a badge for these). */
export const BUDGET_PAUSE_REASONS: ReadonlySet<string> = new Set([
  "budget",
  "quota",
  "spend_cap",
]);

export function pauseLabel(reason: string | null | undefined): string {
  if (!reason) return "";
  if (reason.startsWith("rule:"))
    return `Stop rule ${reason.slice(5).replace(/_/g, " ")}`;
  return PAUSE_LABELS[reason] ?? reason.replace(/_/g, " ");
}

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return DASH;
  return OUTCOME_LABELS[outcome] ?? outcome.replace(/_/g, " ");
}

export function runTone(statusOrOutcome: string | null | undefined): Tone {
  return RUN_TONES[(statusOrOutcome ?? "").toLowerCase()] ?? "neutral";
}

export const ACTIVE_STATUSES: ReadonlySet<StudyStatus> = new Set<StudyStatus>([
  "piloting",
  "running",
  "paused",
]);
export const TERMINAL_STATUSES: ReadonlySet<StudyStatus> = new Set<StudyStatus>(
  ["completed", "cancelled", "stopped"],
);

export function isActive(
  status: StudyStatus | string | null | undefined,
): boolean {
  return ACTIVE_STATUSES.has(status as StudyStatus);
}

export function isTerminal(
  status: StudyStatus | string | null | undefined,
): boolean {
  return TERMINAL_STATUSES.has(status as StudyStatus);
}

/** Editable design (draft or planned, not locked). */
export function isEditable(
  status: StudyStatus | string | null | undefined,
  locked = false,
): boolean {
  return !locked && (status === "draft" || status === "planned");
}

/**
 * USD with precision that fits the magnitude: `$0.0042`, `$0.31`, `$12.40`, `$1,240`; `null` → `—`.
 * Zero is printed as `$0.00` (a measured zero, e.g. a local endpoint), never as "Free".
 */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return DASH;
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (abs === 0) return "$0.00";
  if (abs < 0.01) return `${sign}$${abs.toFixed(4)}`;
  if (abs < 1000) return `${sign}$${abs.toFixed(2)}`;
  return `${sign}$${Math.round(abs).toLocaleString("en-US")}`;
}

/** A 0..1 fraction as a percentage (`0.125` → `12.5%`, `null` → `—`). */
export function formatPct(
  value: number | null | undefined,
  decimals = 0,
): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return DASH;
  const pct = value * 100;
  return `${pct.toFixed(decimals)}%`;
}

/** Seconds → "45 s" / "12 min" / "3.4 h" / "2.1 d" (`null` → `—`). */
export function formatSeconds(seconds: number | null | undefined): string {
  if (
    seconds === null ||
    seconds === undefined ||
    !Number.isFinite(seconds) ||
    seconds < 0
  )
    return DASH;
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)} min`;
  if (seconds < 36 * 3600) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} d`;
}

/** A range of seconds ("2.1 h – 4.0 h"); equal ends print once. */
export function formatSecondsRange(
  low: number | null | undefined,
  high: number | null | undefined,
): string {
  const a = formatSeconds(low);
  const b = formatSeconds(high);
  return a === b ? a : `${a} – ${b}`;
}

/** Token counts: "1.2M", "34k", "512" (`null` → `—`). */
export function formatTokenCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return DASH;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(Math.round(value));
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** First UUID block upper-cased, the study's accession id (`STU-955A5BD5`). */
export function accessionId(id: string): string {
  return `STU-${(id.split("-")[0] ?? id).slice(0, 8).toUpperCase()}`;
}

export function shortHash(hash: string | null | undefined, n = 12): string {
  return hash ? hash.slice(0, n) : DASH;
}

/** A pinned ref without its hash (`stack@3#abcdef123456` → `stack@3`). */
export function refLabel(ref: string | null | undefined): string {
  if (!ref) return DASH;
  return ref.split("#", 1)[0];
}

/** Spend against a cap as a 0..1 fraction (null when either side is unknown or the cap is 0). */
export function spendFraction(
  spent: number | null | undefined,
  cap: number | null | undefined,
): number | null {
  if (
    spent === null ||
    spent === undefined ||
    cap === null ||
    cap === undefined ||
    !(cap > 0)
  )
    return null;
  return spent / cap;
}

/** Trigger a browser download of a blob (export zips, CSV). */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
