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
  RulePrevalence,
  RunPhase,
  RunStatus,
  RunTarget,
  RunToolResult,
  ScopeCounts,
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
  completed:
    "No tool failed. Tools skipped because the app was down, the tool was unavailable or the run was cancelled measured nothing.",
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
  /** Whether the result says something about the code (completed only; nothing to read is not measured). */
  measured: boolean;
  /** Work in flight. */
  live: boolean;
}

const SKIP_STATES: Record<SkipReason, ResultState> = {
  // Nothing in the tree for the tool to read: not measured, never a clean zero (spec 8.8).
  not_applicable: {
    label: "Not applicable",
    tone: "neutral",
    measured: false,
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

/** Ranks of CVSS scores some tools print as the level ("7.0"): critical 9+, high 7+, medium 4+, else low. */
function cvssRank(word: string): number | null {
  if (!/^\d+(\.\d+)?$/.test(word)) return null;
  const score = Number(word);
  if (score <= 0) return 5;
  return score >= 9 ? 0 : score >= 7 ? 1 : score >= 4 ? 2 : 3;
}

/** Rank of a tool's own level word, 0 = most severe (the server's `runs.level_rank` mirrors this). */
export function levelRank(label: string): number {
  const word = label.trim().toLowerCase();
  return LEVEL_INDEX.get(word)?.rank ?? cvssRank(word) ?? 7;
}

/** Levels (as the tools printed them) most severe first, then by count. */
export function sortLevels<T extends { value: string; count: number }>(
  levels: readonly T[],
): T[] {
  return [...levels].sort(
    (a, b) =>
      levelRank(a.value) - levelRank(b.value) ||
      b.count - a.count ||
      a.value.localeCompare(b.value),
  );
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

/**
 * A finished run's findings by scope (`code_findings`, `dependency_findings`,
 * `runtime_findings`; null = no tool of that scope measured the run), or null
 * for a summary without the split (active, or never finished).
 */
export function runScopeCounts(
  summary: Record<string, any> | null | undefined,
): ScopeCounts | null {
  const s = summary ?? {};
  if (!("code_findings" in s)) return null;
  const n = (v: unknown) => (typeof v === "number" ? v : null);
  return {
    code: n(s.code_findings),
    dependencies: n(s.dependency_findings),
    runtime: n(s.runtime_findings),
  };
}

/** "102 advisories · 3 runtime": the findings that are not code issues (empty when there are none). */
export function nonCodeText(scopes: ScopeCounts | null | undefined): string {
  if (!scopes) return "";
  return [
    scopes.dependencies ? `${scopes.dependencies} advisories` : "",
    scopes.runtime ? `${scopes.runtime} runtime` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The run Overview's "Tools measured" subtitle of a finished run. A tool
 * with no result at all (the run failed while preparing, or was cancelled
 * while queued) measured nothing too, and so did a not-applicable tool
 * (nothing to read), so "all measured" needs every tool completed.
 */
export function toolsMeasuredNote(c: {
  total: number;
  measured: number;
  failed: number;
  notApplicable: number;
}): string {
  const notMeasured = Math.max(
    0,
    c.total - c.measured - c.failed - c.notApplicable,
  );
  return (
    [
      c.failed ? `${c.failed} failed` : "",
      notMeasured ? `${notMeasured} not measured` : "",
      c.notApplicable
        ? `${c.notApplicable} had nothing to read (not measured)`
        : "",
    ]
      .filter(Boolean)
      .join(" · ") || "all measured"
  );
}

/**
 * What `summary.truncated_inputs` means for the findings: generated code cut
 * off at the token limit (`backend`, `frontend`), or the agent's final
 * response cut off (`agent`). Empty when nothing was cut off.
 */
export function truncationNote(parts: readonly string[]): string {
  const code = parts.filter((p) => p !== "agent");
  const agent = parts.includes("agent");
  if (code.length === 0) {
    return agent
      ? "The agent's final response was cut off at the model's token limit: the app may be unfinished."
      : "";
  }
  return `The generated ${code.join(" and ")} code was cut off at the model's token limit${
    agent ? ", and so was the agent's final response" : ""
  }: these findings describe an incomplete app.`;
}

export interface RunToolCounts {
  /** Tools in the run. */
  total: number;
  /** Completed tools only. */
  measured: number;
  /** Skipped as not applicable (nothing in the tree to read): not measured, never a zero. */
  notApplicable: number;
  failed: number;
  /** Skipped because the app was down, the tool unavailable or the run cancelled: said nothing. */
  unmeasured: number;
  /** Finished, yet no tool measured anything: its findings count is not a clean zero. */
  nothingMeasured: boolean;
}

/**
 * Tool outcome counts of a finished run from its summary (`tools_completed`,
 * `tools_failed`, `tools_not_applicable`, `tools_unmeasured`); null while
 * active or for a summary without them.
 */
export function runToolCounts(run: {
  status: string;
  tool_slugs: string[];
  summary: Record<string, any> | null | undefined;
}): RunToolCounts | null {
  const s = run.summary ?? {};
  if (isActive(run.status) || s.tools_run == null) return null;
  const n = (v: unknown) => (typeof v === "number" ? v : 0);
  const notApplicable = n(s.tools_not_applicable);
  const measured = n(s.tools_completed);
  // Older summaries lack the skip counters: what neither completed, failed nor was
  // not applicable said nothing either.
  const unmeasured =
    typeof s.tools_unmeasured === "number"
      ? s.tools_unmeasured
      : Math.max(
          0,
          run.tool_slugs.length - measured - notApplicable - n(s.tools_failed),
        );
  return {
    total: run.tool_slugs.length,
    measured,
    notApplicable,
    failed: n(s.tools_failed),
    unmeasured,
    nothingMeasured: run.tool_slugs.length > 0 && measured === 0,
  };
}

// --- Targets ------------------------------------------------------------------------

/**
 * Two-line identity of what a run analyzed: the generated run's task (or the
 * pasted code / deleted app label) and model · stack.
 */
export function targetLines(target: RunTarget | null | undefined): {
  title: string;
  sub: string;
} {
  if (!target) return { title: "—", sub: "" };
  if (target.kind !== "run") return { title: target.label, sub: "" };
  const title =
    target.task_title || target.stack_ref || target.label || "Generated app";
  const sub = [
    shortModelName(target.model_name),
    target.task_title ? target.stack_ref : "",
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

/**
 * A text cell a spreadsheet would read as a formula (`=`, `+`, `-`, `@`, tab,
 * CR first) gets a leading apostrophe; numbers, and text that is a number
 * ("-1.5"), stay as they are.
 */
function spreadsheetSafe(cell: string | number): string {
  if (typeof cell === "number") return String(cell);
  if (!/^[=+\-@\t\r]/.test(cell)) return cell;
  return /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(cell) ? cell : `'${cell}`;
}

/**
 * RFC 4180 CSV of *rows* (first row = header), made to be opened in a
 * spreadsheet: text that would run as a formula is neutralized.
 */
export function toCsv(rows: (string | number | null | undefined)[][]): string {
  return rows
    .map((row) =>
      row
        .map((cell) => {
          const s = cell == null ? "" : spreadsheetSafe(cell);
          return /[",\n\r]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
        })
        .join(","),
    )
    .join("\r\n");
}

/** "81 in .tmp/ · 3 in node_modules/": where the stored files the analysis left out are. */
export function excludedDirsText(dirs: Record<string, number>): string {
  return Object.entries(dirs)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([dir, n]) => `${n} in ${dir || "(other)"}/`)
    .join(" · ");
}

/** "15/30 apps · 4/6 of this model": how common a rule is (a tool that measured no app reads "—"). */
export function prevalenceText(p: RulePrevalence): string {
  const corpus =
    p.apps_measured > 0
      ? `${p.apps_with_rule}/${p.apps_measured} apps`
      : "— apps";
  if (p.model_apps_measured == null) return corpus;
  const model =
    p.model_apps_measured > 0
      ? `${p.model_apps_with_rule ?? 0}/${p.model_apps_measured}`
      : "—";
  return `${corpus} · ${model} of this model`;
}

/** The tooltip of a prevalence figure: what is counted, over which apps. */
export function prevalenceTitle(p: RulePrevalence, scopeLabel: string): string {
  return (
    `Apps in ${scopeLabel || "the corpus"} whose latest ${p.tool} result reports ${p.rule_id}, ` +
    `out of the ${p.apps_measured} app${p.apps_measured === 1 ? "" : "s"} ${p.tool} measured ` +
    "(completed or nothing to read); repeats count separately. Descriptive, no test."
  );
}

/**
 * Prevalence rows by `tool + rule`; for rule-only lookups, the rule's most frequent row in the
 * run (`byRule`) and every tool's row of that rule id, most findings in the run first (`byRuleAll`).
 */
export function prevalenceIndex(rows: readonly RulePrevalence[]): {
  byToolRule: Map<string, RulePrevalence>;
  byRule: Map<string, RulePrevalence>;
  byRuleAll: Map<string, RulePrevalence[]>;
} {
  const byToolRule = new Map<string, RulePrevalence>();
  const byRule = new Map<string, RulePrevalence>();
  const byRuleAll = new Map<string, RulePrevalence[]>();
  for (const row of rows) {
    byToolRule.set(prevalenceKey(row.tool, row.rule_id), row);
    const seen = byRule.get(row.rule_id);
    if (!seen || row.count_in_run > seen.count_in_run)
      byRule.set(row.rule_id, row);
    byRuleAll.set(row.rule_id, [...(byRuleAll.get(row.rule_id) ?? []), row]);
  }
  for (const list of byRuleAll.values()) {
    list.sort(
      (a, b) => b.count_in_run - a.count_in_run || a.tool.localeCompare(b.tool),
    );
  }
  return { byToolRule, byRule, byRuleAll };
}

/**
 * The prevalence lines of one rule id in a list whose rows pool every tool that reports
 * that id (ruff and flake8 both say `F401`): one line per tool, each named when several
 * tools report it, so one tool's figures never stand for another's.
 */
export function rulePrevalenceLines(
  rows: readonly RulePrevalence[],
): { row: RulePrevalence; text: string }[] {
  return rows.map((row) => ({
    row,
    text:
      rows.length > 1
        ? `${row.tool}: ${prevalenceText(row)}`
        : prevalenceText(row),
  }));
}

/**
 * The run Overview's note on stored files in dependency / cache / scratch directories:
 * whether this run's tools left them out, or read them (a run from before that rule,
 * whose findings may be about them). They are never in the LOC.
 */
export function excludedPathsNote(e: {
  count: number;
  dirs: Record<string, number>;
  left_out?: boolean | null;
  findings_in_excluded?: number;
}): string {
  const one = e.count === 1;
  const files = `${e.count.toLocaleString()} stored file${one ? "" : "s"}`;
  const where = excludedDirsText(e.dirs);
  const notSource =
    "dependency, cache and scratch directories are not the app's source";
  if (e.left_out === true) {
    return `${files} ${one ? "was" : "were"} left out of the analysis and of the LOC (${where}): ${notSource}.`;
  }
  if (e.left_out === false) {
    const n = e.findings_in_excluded ?? 0;
    return (
      `${files} (${where}) ${one ? "is" : "are"} not counted in the LOC: ${notSource}. ` +
      `This run predates leaving them out of the analysis: its tools read ${one ? "it" : "them"}` +
      (n > 0
        ? `, and ${n.toLocaleString()} of its findings ${n === 1 ? "is" : "are"} in ${one ? "it" : "them"}`
        : "") +
      ". Re-run the analysis to measure the app's own source only."
    );
  }
  return `${files} (${where}) ${one ? "is" : "are"} not counted in the LOC: ${notSource}; today's analyses leave ${one ? "it" : "them"} out.`;
}

export function prevalenceKey(tool: string, ruleId: string): string {
  return `${tool}\u0000${ruleId}`;
}

/**
 * The cross-tool issue class (the study's dedup unit) when it says more than
 * the rule itself: "" for the `tool:rule` fallback or a class equal to the rule.
 */
export function issueClassLabel(
  cls: string | null | undefined,
  tool: string,
  rule: string,
): string {
  if (!cls) return "";
  if (cls === rule || cls === `${tool}:${rule || "unknown"}`) return "";
  return cls;
}
