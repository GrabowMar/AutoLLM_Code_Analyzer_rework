/**
 * Display helpers for analysis runs (list, detail, ad-hoc preflight,
 * application tab, dashboard).
 *
 * Research-validity conventions enforced here:
 * - `null` is missing data: formatters return "—" and every NA comes with a
 *   reason (`*NaReason`), never a 0.
 * - A count from a run whose tools did not all succeed is a lower bound; the
 *   cell helpers say so instead of presenting it as the full count.
 * - Static findings and AI consensus are separate measurements.
 */

import type {
  AnalysisRunListItem,
  FileOrigin,
  Finding,
  Measurement,
  OutcomeKey,
  RunPurpose,
  RunSummary,
  ToolFailureReason,
  ToolResult,
  ToolStatus,
} from "$lib/api/analysis";
import {
  isMeasurement,
  isRunActive,
  isRunSummary,
  TOOL_NO_DATA_STATUSES,
} from "$lib/api/analysis";
import { formatNumber } from "$lib/utils/formatters";

/** Catalog slug of the AI reviewer (always the last tool of a plan). */
export const AI_TOOL_SLUG = "ai_review";

/** Tooltip of a missing value when no specific reason is known (see `NaValue`). */
export const NA_REASON =
  "Not available (NA): the value could not be measured or computed (e.g. a required tool failed or n is too small). It is never counted as 0.";

// ── Labels ────────────────────────────────────────────────────────────

export const PURPOSE_LABELS: Record<RunPurpose, string> = {
  research: "Study",
  adhoc: "Ad-hoc",
  canary: "Self-test",
};

export const FAILURE_REASON_LABELS: Record<
  Exclude<ToolFailureReason, "">,
  string
> = {
  tool_error: "tool error",
  timeout: "timed out",
  oom: "out of memory",
  parse_error: "unparseable output",
  infra_error: "infrastructure error",
  unavailable: "unavailable",
  invalid_config: "invalid config",
  invalid_output: "invalid output",
  budget: "budget exhausted",
  cancelled: "cancelled",
};

export function failureReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "";
  return (
    FAILURE_REASON_LABELS[reason as Exclude<ToolFailureReason, "">] ??
    reason.replace(/_/g, " ")
  );
}

export const ORIGIN_LABELS: Record<FileOrigin, string> = {
  authored: "authored",
  added: "added",
  modified: "modified",
  scaffold: "scaffold",
  platform: "platform",
  unknown: "unknown",
};

export const CATEGORY_LABELS: Record<string, string> = {
  security: "Security",
  secrets: "Secrets",
  bug: "Bug",
  type: "Type",
  maintainability: "Maintainability",
  dead_code: "Dead code",
  duplication: "Duplication",
  complexity: "Complexity",
  style: "Style",
  container: "Container",
  dependency: "Dependency",
  other: "Other",
  lint: "Lint",
  types: "Types",
  ai: "AI",
};

export function categoryLabel(category: string | null | undefined): string {
  if (!category) return "—";
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
}

/** "running:pylint" → "running pylint", "waiting_for_slot" → "waiting for a slot". */
export function phaseLabel(phase: string | null | undefined): string {
  if (!phase) return "";
  if (phase.startsWith("running:")) return `running ${phase.slice(8)}`;
  const named: Record<string, string> = {
    waiting_for_slot: "waiting for a slot",
    loading_sample: "loading the sample",
    image: "preparing the analyzer image",
    reviewing: "AI review",
    finalizing: "finalizing",
    cancelling: "cancelling",
    requeued: "requeued",
  };
  return named[phase] ?? phase.replace(/_/g, " ");
}

// ── Formatting (null → "—") ───────────────────────────────────────────

export function shortHash(
  value: string | null | undefined,
  length = 12,
): string {
  return value ? value.slice(0, length) : "—";
}

export function shortId(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : "—";
}

export function fmtNum(value: number | null | undefined, digits = 1): string {
  return formatNumber(value, digits);
}

export function fmtInt(value: number | null | undefined): string {
  return value == null ? "—" : Math.round(value).toLocaleString();
}

/** A 0..1 rate as a percentage ("97.7%"). */
export function fmtRate(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(digits).replace(/\.0+$/, "")}%`;
}

/** A value that already is a percentage (jscpd duplication). */
export function fmtPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value == null) return "—";
  return `${formatNumber(value, digits)}%`;
}

export function fmtUsd(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value === 0) return "$0";
  if (Math.abs(value) < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

export function fmtBytes(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function fmtSeconds(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value.toFixed(1)} s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** Compact relative time ("5m ago"); absolute date after a month. */
export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function fmtDateTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

/** "path:12" / "path:12–14" / "path". */
export function fmtLocation(
  path: string,
  line: number | null | undefined,
  end?: number | null,
): string {
  if (!line) return path;
  if (end && end > line) return `${path}:${line}–${end}`;
  return `${path}:${line}`;
}

export function cweUrl(cwe: string): string | null {
  const match = /^CWE-(\d+)$/i.exec(cwe.trim());
  return match
    ? `https://cwe.mitre.org/data/definitions/${match[1]}.html`
    : null;
}

// ── Tool problems ─────────────────────────────────────────────────────

/** A tool that produced no trustworthy data. */
export interface ToolProblem {
  slug: string;
  status: ToolStatus;
  reason: string;
  required: boolean;
  message: string;
  isAi: boolean;
}

/** Tools of a run that failed, timed out, were skipped or cancelled. */
export function toolProblems(results: ToolResult[]): ToolProblem[] {
  return results
    .filter((r) =>
      (TOOL_NO_DATA_STATUSES as readonly string[]).includes(r.status),
    )
    .map((r) => ({
      slug: r.tool_slug,
      status: r.status,
      reason: r.failure_reason,
      required: r.required,
      message: r.error_message,
      isAi: r.tool_kind === "ai",
    }));
}

/** "semgrep (timed out)" */
export function problemLabel(
  problem: Pick<ToolProblem, "slug" | "status" | "reason">,
): string {
  const why =
    failureReasonLabel(problem.reason) || problem.status.replace(/_/g, " ");
  return `${problem.slug} (${why})`;
}

const WARNING_RE =
  /^(\S+)( \[required\])? (failed|timed_out|cancelled|skipped)(?: \(([a-z_]+)\))?/;

/**
 * Tool problems of a list item, read from `summary.warnings` (the list API
 * has no per-tool statuses). The backend writes one line per tool that did
 * not succeed: `<slug>[ [required]] <status>[ (<reason>)][: <detail>]`.
 */
export function summaryToolProblems(run: AnalysisRunListItem): ToolProblem[] {
  const warnings = Array.isArray(run.summary?.warnings)
    ? run.summary.warnings
    : [];
  const slugs = new Set(run.tool_slugs);
  const out: ToolProblem[] = [];
  for (const line of warnings) {
    const match = WARNING_RE.exec(line);
    if (!match || !slugs.has(match[1])) continue;
    out.push({
      slug: match[1],
      status: match[3] as ToolStatus,
      reason: match[4] ?? "",
      required: !!match[2],
      message: line,
      isAi: match[1] === AI_TOOL_SLUG,
    });
  }
  return out;
}

// ── List cells: counts with honest NA / lower-bound semantics ─────────

export interface CountCell {
  value: number | null;
  /** Why the value is NA, or why it is only a lower bound. */
  note: string;
  /** Some tools that could add to the count did not succeed. */
  lowerBound: boolean;
}

/**
 * Tools of a list item without data, from its live status counts (the
 * authority) and the summary warnings (which name them). When the counts
 * show more problems than the warnings name, the unnamed ones are unknown
 * and the cells below stay conservative.
 */
function listProblems(run: AnalysisRunListItem): {
  named: ToolProblem[];
  total: number;
  unnamed: number;
} {
  const t = run.tools;
  const total =
    (t?.failed ?? 0) +
    (t?.timed_out ?? 0) +
    (t?.skipped ?? 0) +
    (t?.cancelled ?? 0);
  const named = summaryToolProblems(run);
  return { named, total, unnamed: Math.max(0, total - named.length) };
}

function pendingCell(run: AnalysisRunListItem): CountCell | null {
  if (isRunActive(run.status)) {
    return {
      value: null,
      note: "The run is still in progress",
      lowerBound: false,
    };
  }
  if (!isRunSummary(run.summary)) {
    return {
      value: null,
      note: "The run finished without a measurement",
      lowerBound: false,
    };
  }
  const t = run.tools;
  if (t && t.total > 0 && (t.succeeded ?? 0) + (t.not_applicable ?? 0) === 0) {
    return {
      value: null,
      note:
        run.status === "cancelled"
          ? "Cancelled before any tool produced data"
          : "No tool produced data",
      lowerBound: false,
    };
  }
  return null;
}

/** Counted static findings of a list item. */
export function staticFindingsCell(run: AnalysisRunListItem): CountCell {
  const early = pendingCell(run);
  if (early) return early;
  const summary = run.summary as RunSummary;
  const staticSlugs = run.tool_slugs.filter((s) => s !== AI_TOOL_SLUG);
  if (staticSlugs.length === 0) {
    return {
      value: null,
      note: "No static tool in this plan",
      lowerBound: false,
    };
  }
  const { named, unnamed } = listProblems(run);
  const staticNamed = named.filter((p) => !p.isAi);
  // Unnamed problems may be static tools: count them against static.
  const staticMissing = staticNamed.length + unnamed;
  if (staticMissing >= staticSlugs.length) {
    return {
      value: null,
      note: "No static tool produced data",
      lowerBound: false,
    };
  }
  if (staticMissing > 0) {
    const names = staticNamed.map(problemLabel);
    if (unnamed > 0)
      names.push(`${unnamed} more tool${unnamed === 1 ? "" : "s"}`);
    return {
      value: summary.total_findings,
      note: `Lower bound: ${names.join(", ")} did not succeed`,
      lowerBound: true,
    };
  }
  return { value: summary.total_findings, note: "", lowerBound: false };
}

/** AI consensus findings (counted clusters) of a list item. */
export function aiConsensusCell(run: AnalysisRunListItem): CountCell {
  if (!run.tool_slugs.includes(AI_TOOL_SLUG)) {
    return {
      value: null,
      note: "No AI reviewer in this plan",
      lowerBound: false,
    };
  }
  const early = pendingCell(run);
  if (early) return early;
  const summary = run.summary as RunSummary;
  const { named, unnamed } = listProblems(run);
  const problem = named.find((p) => p.isAi);
  if (problem) {
    return {
      value: null,
      note: `AI review ${failureReasonLabel(problem.reason) || problem.status.replace(/_/g, " ")}`,
      lowerBound: false,
    };
  }
  if (unnamed > 0) {
    return {
      value: null,
      note: "Some tools did not succeed and the AI review may be one of them: open the run",
      lowerBound: false,
    };
  }
  return { value: summary.ai_findings, note: "", lowerBound: false };
}

/** Measured SLOC of a list item (null until the sample is resolved). */
export function measuredSloc(run: AnalysisRunListItem): number | null {
  const sloc = run.summary?.sloc;
  return sloc && typeof sloc.measured === "number" ? sloc.measured : null;
}

/** "5/15" tools finished (succeeded, failed, ...) of a list item or detail. */
export function toolProgress(run: Pick<AnalysisRunListItem, "tools">): {
  done: number;
  total: number;
} {
  const t = run.tools;
  const total = t?.total ?? 0;
  const pending = (t?.pending ?? 0) + (t?.running ?? 0);
  return { done: Math.max(0, total - pending), total };
}

/** Terminal run whose required tools all succeeded or were not applicable. */
export function isRunComplete(run: AnalysisRunListItem): boolean {
  return !isRunActive(run.status) && run.is_complete;
}

/** A terminal run has tools that can be requeued. */
export function retriableTools(results: ToolResult[]): ToolResult[] {
  return results.filter((r) =>
    (TOOL_NO_DATA_STATUSES as readonly string[]).includes(r.status),
  );
}

// ── Outcomes ──────────────────────────────────────────────────────────

export interface OutcomeLabel {
  label: string;
  /** How to print the value. */
  format: "rate" | "count" | "density" | "metric" | "percent" | "binary";
  group: "static" | "ai" | "metrics" | "sample";
}

/** Research outcomes of a run (the contract keys `measurement.outcomes()` guarantees). */
export const OUTCOME_LABELS: Record<OutcomeKey, OutcomeLabel> = {
  static_findings_per_kloc: {
    label: "Static findings / KLOC",
    format: "density",
    group: "static",
  },
  static_issues_per_kloc: {
    label: "Static issues / KLOC",
    format: "density",
    group: "static",
  },
  security_issues_per_kloc: {
    label: "Security issues / KLOC",
    format: "density",
    group: "static",
  },
  high_plus_issues_per_kloc: {
    label: "High+ issues / KLOC",
    format: "density",
    group: "static",
  },
  weighted_per_kloc: {
    label: "Weighted score / KLOC",
    format: "density",
    group: "static",
  },
  static_issues: { label: "Static issues", format: "count", group: "static" },
  security_issues: {
    label: "Security issues",
    format: "count",
    group: "static",
  },
  high_plus_issues: {
    label: "High+ issues",
    format: "count",
    group: "static",
  },
  has_high_security: {
    label: "Has a high/critical security issue",
    format: "binary",
    group: "static",
  },
  ai_consensus_per_kloc: {
    label: "AI consensus / KLOC",
    format: "density",
    group: "ai",
  },
  ai_grounding_rate: {
    label: "AI grounding rate",
    format: "rate",
    group: "ai",
  },
  ai_mean_pairwise_jaccard: {
    label: "AI agreement (mean Jaccard)",
    format: "metric",
    group: "ai",
  },
  complexity_max: {
    label: "Max cyclomatic complexity",
    format: "metric",
    group: "metrics",
  },
  complexity_mean: {
    label: "Mean cyclomatic complexity",
    format: "metric",
    group: "metrics",
  },
  duplication_pct: {
    label: "Duplication",
    format: "percent",
    group: "metrics",
  },
  suppression_markers_per_kloc: {
    label: "Suppression markers / KLOC",
    format: "density",
    group: "sample",
  },
  functional_pass_rate: {
    label: "Functional pass rate",
    format: "rate",
    group: "sample",
  },
  measured_sloc: { label: "Measured SLOC", format: "count", group: "sample" },
};

export function formatOutcome(
  key: string,
  value: number | boolean | null | undefined,
): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  const meta = OUTCOME_LABELS[key as OutcomeKey];
  switch (meta?.format) {
    case "rate":
      return fmtRate(value);
    case "percent":
      return fmtPercent(value);
    case "count":
      return fmtInt(value);
    case "density":
      return fmtNum(value, 2);
    default:
      return fmtNum(value, 2);
  }
}

function belowDensityFloor(m: Measurement): string {
  const floor = m.outcome_config?.min_sloc_for_density;
  const measured = m.sloc?.measured;
  if (
    typeof floor === "number" &&
    typeof measured === "number" &&
    measured < floor
  ) {
    return `Measured SLOC (${measured}) is below the density floor (${floor})`;
  }
  return "";
}

function staticUnavailable(m: Measurement): string {
  if (m.static?.available) return "";
  const failed = m.static?.required_failed ?? m.required_failed ?? [];
  return failed.length
    ? `Required static tool(s) did not succeed: ${failed.join(", ")}`
    : "Static measurements are not available";
}

function aiUnavailable(m: Measurement): string {
  if (!m.ai) return "No AI reviewer in this plan";
  if (m.ai.available) return "";
  const why =
    failureReasonLabel(m.ai.failure_reason) || m.ai.status.replace(/_/g, " ");
  return `AI review did not succeed (${why})`;
}

/** Why an outcome of a finalized run is NA. */
export function outcomeNaReason(
  key: string,
  m: Partial<Measurement> | null | undefined,
): string {
  if (!isMeasurement(m))
    return "The run has not finished, so nothing is measured yet";
  const floor = belowDensityFloor(m);
  switch (key) {
    case "static_findings_per_kloc":
    case "static_issues_per_kloc":
    case "security_issues_per_kloc":
    case "high_plus_issues_per_kloc":
    case "weighted_per_kloc":
      return staticUnavailable(m) || floor || "Not measured";
    case "static_issues":
    case "security_issues":
    case "high_plus_issues":
      return staticUnavailable(m) || "Not measured";
    case "has_high_security":
      return "No high/critical security issue was found, but a security tool did not succeed";
    case "ai_consensus_per_kloc":
      return aiUnavailable(m) || floor || "Not measured";
    case "ai_grounding_rate":
      return aiUnavailable(m) || "The reviewers reported no findings to ground";
    case "ai_mean_pairwise_jaccard":
      return (
        aiUnavailable(m) || "Agreement needs at least two successful samples"
      );
    case "complexity_max":
    case "complexity_mean":
      return m.metrics?.complexity
        ? "No functions were measured"
        : "No complexity tool (lizard, radon) succeeded or is in the plan";
    case "duplication_pct":
      return m.metrics?.duplication
        ? "No lines were measured"
        : "The duplication tool (jscpd) did not succeed or is not in the plan";
    case "functional_pass_rate":
      return `No functional evaluation (${(m.functional?.status ?? "unknown").replace(/_/g, " ")})`;
    case "suppression_markers_per_kloc":
      return floor || "Not measured";
    default:
      return "Not available";
  }
}

// ── Findings ──────────────────────────────────────────────────────────

/** Why a finding does not count toward the measurement ("" when counted). */
export function uncountedReason(finding: Finding): string {
  if (finding.counted) return "";
  if (finding.is_ai) {
    if (!finding.is_consensus)
      return "Not reported by enough review samples (no consensus)";
    if (
      finding.grounding &&
      !["exact", "relocated"].includes(finding.grounding)
    ) {
      return `Evidence is ${finding.grounding.replace(/_/g, " ")}`;
    }
    if (!finding.in_measured_scope) return "Outside the measured scope";
    if (UNCOUNTED_CATEGORIES.includes(finding.category)) {
      return `The ${categoryLabel(finding.category).toLowerCase()} category is not counted by this plan`;
    }
    return "Consensus member: the cluster is counted once (another sample's finding represents it)";
  }
  if (!finding.in_measured_scope) {
    return finding.file_origin === "scaffold" ||
      finding.file_origin === "platform"
      ? `In ${finding.file_origin} code, not code the model wrote`
      : "Outside the measured scope";
  }
  if (UNCOUNTED_CATEGORIES.includes(finding.category)) {
    return `The ${categoryLabel(finding.category).toLowerCase()} category is not counted by this plan`;
  }
  return "Not counted by this plan's outcome rules";
}

/** Categories the default outcome rules leave uncounted (style unless `count_style`, metric findings). */
const UNCOUNTED_CATEGORIES: readonly string[] = [
  "style",
  "complexity",
  "duplication",
];

/** Read a typed value out of a finding's `tool_specific_data`. */
export function tsd<T>(finding: Finding, key: string): T | undefined {
  const data = finding.tool_specific_data;
  return data && key in data ? (data[key] as T) : undefined;
}
