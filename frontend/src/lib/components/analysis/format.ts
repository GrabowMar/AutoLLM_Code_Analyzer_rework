/**
 * Display helpers for analysis runs: statuses, phases, tool-result states,
 * elapsed time and tool-reported levels.
 *
 * Levels: a finding shows only the level its tool printed (`tool_severity`).
 * The normalized `severity` is a heuristic for tools that rate nothing and is
 * never displayed; `levelTone` colors a tool's own word, it doesn't rate it.
 */
import type { Tone } from "$lib/constants/colors";
import { toneClasses } from "$lib/constants/colors";
import type {
  RunPhase,
  RunStatus,
  RunTarget,
  RunToolResult,
  SkipReason,
} from "$lib/api/runs";
import { shortModelName } from "$lib/components/table";

export const ACTIVE_STATUSES: ReadonlySet<string> = new Set([
  "pending",
  "running",
]);

export function isActive(status: string | null | undefined): boolean {
  return ACTIVE_STATUSES.has(status ?? "");
}

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  pending: "Queued",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** What each run status means, for filter chips and badges' titles. */
export const RUN_STATUS_HELP: Record<RunStatus, string> = {
  pending: "Waiting for its turn: runs of one workspace execute one at a time.",
  running: "Tools are running.",
  completed: "Every tool measured the code (or had nothing to read).",
  partial: "Some tools failed; the others' findings stand.",
  failed: "No tool produced a result.",
  cancelled: "Stopped before every tool ran.",
};

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status as RunStatus] ?? status;
}

export const PHASE_LABELS: Record<Exclude<RunPhase, "">, string> = {
  waiting_for_workspace:
    "Waiting for the workspace — another analysis of yours is running",
  preparing: "Preparing the workspace and copying the code in",
  running_tools: "Running tools",
};

export function phaseLabel(phase: string | null | undefined): string {
  return PHASE_LABELS[phase as Exclude<RunPhase, "">] ?? "";
}

export interface ResultState {
  label: string;
  tone: Tone;
  /** Whether the result says something about the code (completed, or nothing to read). */
  measured: boolean;
  /** Work in flight. */
  live: boolean;
}

const SKIP_STATES: Record<SkipReason, ResultState> = {
  not_applicable: {
    label: "Not applicable",
    tone: "neutral",
    measured: true,
    live: false,
  },
  app_not_running: {
    label: "App not running",
    tone: "warning",
    measured: false,
    live: false,
  },
  unavailable: {
    label: "Unavailable",
    tone: "warning",
    measured: false,
    live: false,
  },
  cancelled: {
    label: "Cancelled",
    tone: "neutral",
    measured: false,
    live: false,
  },
};

/** State of one tool in a run; `result` is undefined while the tool is still queued. */
export function resultState(result: RunToolResult | undefined): ResultState {
  if (!result || result.status === "pending") {
    return { label: "Queued", tone: "neutral", measured: false, live: false };
  }
  switch (result.status) {
    case "running":
      return { label: "Running", tone: "info", measured: false, live: true };
    case "completed":
      return {
        label: "Completed",
        tone: "success",
        measured: true,
        live: false,
      };
    case "failed":
      return { label: "Failed", tone: "danger", measured: false, live: false };
    default: {
      const reason = (result.summary?.skip_reason ?? "") as SkipReason;
      return (
        SKIP_STATES[reason] ?? {
          label: "Skipped",
          tone: "neutral",
          measured: false,
          live: false,
        }
      );
    }
  }
}

/** Findings a tool result stored (failed results can keep some). */
export function resultFindingCount(result: RunToolResult | undefined): number {
  const n = result?.summary?.finding_count;
  return typeof n === "number" ? n : 0;
}

/** Seconds since `iso` at `nowMs` (0 when unknown or in the future). */
export function elapsedSince(
  iso: string | null | undefined,
  nowMs: number,
): number {
  if (!iso) return 0;
  return Math.max(0, (nowMs - new Date(iso).getTime()) / 1000);
}

/** Clock-style elapsed time: "0:42", "12:03", "1:02:03". */
export function formatClock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/** Compact duration: "850ms", "42s", "3m 12s", "1h 04m". */
export function formatSpan(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
  }
  return `${Math.floor(seconds / 3600)}h ${String(Math.floor((seconds % 3600) / 60)).padStart(2, "0")}m`;
}

// --- Tool-reported levels -------------------------------------------------------

// Common level words across tools, most severe first: used to order a run's
// reported levels and to tint a tool's own word. Not a rating of the finding.
const LEVEL_WORDS: [string, number, Tone | "critical"][] = [
  ["critical", 0, "critical"],
  ["blocker", 0, "critical"],
  ["fatal", 1, "danger"],
  ["error", 1, "danger"],
  ["high", 1, "danger"],
  ["severe", 1, "danger"],
  ["major", 2, "warning"],
  ["medium", 2, "warning"],
  ["moderate", 2, "warning"],
  ["warning", 2, "warning"],
  ["warn", 2, "warning"],
  ["low", 3, "info"],
  ["minor", 3, "info"],
  ["refactor", 4, "neutral"],
  ["convention", 4, "neutral"],
  ["style", 4, "neutral"],
  ["note", 4, "neutral"],
  ["info", 5, "neutral"],
  ["informational", 5, "neutral"],
  ["unknown", 6, "neutral"],
];
const LEVEL_INDEX = new Map(
  LEVEL_WORDS.map(([w, rank, tone]) => [w, { rank, tone }]),
);

export function levelRank(label: string): number {
  return LEVEL_INDEX.get(label.trim().toLowerCase())?.rank ?? 7;
}

/** Badge classes for a tool's own level word (critical is solid, like the severity palette). */
export function levelClasses(label: string): string {
  const tone = LEVEL_INDEX.get(label.trim().toLowerCase())?.tone ?? "neutral";
  if (tone === "critical") {
    return "border-transparent bg-destructive text-destructive-foreground";
  }
  return toneClasses[tone];
}

/** `summary.reported_severity_counts` of a finished run, most severe first. */
export function reportedLevels(
  summary: Record<string, any> | null | undefined,
): [string, number][] {
  const counts = (summary?.reported_severity_counts ?? {}) as Record<
    string,
    number
  >;
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .sort((a, b) => levelRank(a[0]) - levelRank(b[0]) || b[1] - a[1]);
}

// --- Targets ------------------------------------------------------------------------

/** Two-line identity of what a run analyzed: app (or pasted code) and model · stack. */
export function targetLines(target: RunTarget | null | undefined): {
  title: string;
  sub: string;
} {
  if (!target) return { title: "—", sub: "" };
  if (target.kind !== "job") return { title: target.label, sub: "" };
  const title = target.app_name || target.stack_slug || "Generated app";
  const sub = [
    shortModelName(target.model_name),
    target.app_name ? target.stack_slug : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return { title, sub };
}

export function runDisplayName(run: { name: string; id: string }): string {
  return run.name || `Analysis ${run.id.slice(0, 8)}`;
}

/** Download *text* as a file named *filename*. */
export function downloadText(
  filename: string,
  text: string,
  type = "text/plain",
): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** RFC 4180 CSV of *rows* (first row = header). */
export function toCsv(rows: (string | number | null | undefined)[][]): string {
  return rows
    .map((row) =>
      row
        .map((cell) => {
          const s = cell == null ? "" : String(cell);
          return /[",\n\r]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
        })
        .join(","),
    )
    .join("\r\n");
}
