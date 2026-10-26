/**
 * Display helpers shared by the insights pages (rankings, statistics,
 * reports). Missing data renders through `NaValue` ("—" + reason), never as
 * 0: these helpers return `null` for missing input so callers must decide.
 */

export const DASH = "—";

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Integer with locale grouping, or null. */
export function fmtInt(v: number | null | undefined): string | null {
  return isNum(v) ? Math.round(v).toLocaleString() : null;
}

/** Number with at most `digits` fraction digits, or null. */
export function fmtNum(
  v: number | null | undefined,
  digits = 2,
): string | null {
  if (!isNum(v)) return null;
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

/** A 0..1 share as a percentage ("91.2%"), or null. */
export function fmtShare(
  v: number | null | undefined,
  digits = 1,
): string | null {
  return isNum(v) ? `${(v * 100).toFixed(digits)}%` : null;
}

/** A 0..1 score on the 0..100 display scale ("91.2"), or null. */
export function fmtScore(v: number | null | undefined): string | null {
  return isNum(v) ? (v * 100).toFixed(1) : null;
}

/** USD with enough precision for sub-cent LLM costs, or null. */
export function fmtUsd(v: number | null | undefined): string | null {
  if (!isNum(v)) return null;
  if (v === 0) return "$0";
  if (Math.abs(v) < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

/** Seconds → "42s" / "3m 12s", or null. */
export function fmtSeconds(v: number | null | undefined): string | null {
  if (!isNum(v)) return null;
  if (v < 60) return `${v.toFixed(v < 10 ? 1 : 0)}s`;
  const m = Math.floor(v / 60);
  return `${m}m ${Math.round(v - m * 60)}s`;
}

/** Signed number ("+2.5" / "−1.2"), or null. */
export function fmtSigned(
  v: number | null | undefined,
  digits = 2,
): string | null {
  if (!isNum(v)) return null;
  const abs = fmtNum(Math.abs(v), digits);
  if (v > 0) return `+${abs}`;
  if (v < 0) return `−${abs}`;
  return "0";
}

/** First characters of a plan/sample hash for display. */
export function shortHash(hash: string | null | undefined, n = 10): string {
  return hash ? hash.slice(0, n) : "";
}

/** `excluded_reasons` → "truncated" etc. */
export function humanize(key: string): string {
  return key.replace(/_/g, " ");
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Run detail page of a canonical run. */
export function runHref(runId: string): string {
  return `/analysis/${encodeURIComponent(runId)}`;
}

/** Generated application (generation job) page. */
export function jobHref(jobId: string): string {
  return `/applications/${encodeURIComponent(jobId)}`;
}

/** Runs list filtered to one analysis plan. */
export function planRunsHref(planHash: string): string {
  return `/analysis?plan_hash=${encodeURIComponent(planHash)}`;
}

export interface OutcomeLabel {
  label: string;
  /** AI-reviewer outcomes are opinions: shown apart from static ones. */
  ai: boolean;
  digits: number;
  /** Render the value as a percentage of 0..1. */
  share?: boolean;
}

/** Labels of `measurement.OUTCOMES` (unknown keys fall back to the key). */
export const OUTCOME_LABELS: Record<string, OutcomeLabel> = {
  static_findings_per_kloc: {
    label: "Static findings / KLOC",
    ai: false,
    digits: 2,
  },
  static_issues_per_kloc: {
    label: "Static issues / KLOC",
    ai: false,
    digits: 2,
  },
  security_issues_per_kloc: {
    label: "Security issues / KLOC",
    ai: false,
    digits: 2,
  },
  high_plus_issues_per_kloc: {
    label: "High+ issues / KLOC",
    ai: false,
    digits: 2,
  },
  has_high_security: {
    label: "Has a high/critical security issue",
    ai: false,
    digits: 1,
    share: true,
  },
  weighted_per_kloc: {
    label: "Severity-weighted issues / KLOC",
    ai: false,
    digits: 2,
  },
  static_issues: { label: "Static issues", ai: false, digits: 1 },
  security_issues: { label: "Security issues", ai: false, digits: 1 },
  high_plus_issues: { label: "High+ issues", ai: false, digits: 1 },
  complexity_max: { label: "Max cyclomatic complexity", ai: false, digits: 1 },
  complexity_mean: {
    label: "Mean cyclomatic complexity",
    ai: false,
    digits: 2,
  },
  duplication_pct: { label: "Duplicated lines %", ai: false, digits: 2 },
  functional_pass_rate: {
    label: "Functional pass rate",
    ai: false,
    digits: 1,
    share: true,
  },
  measured_sloc: { label: "Measured SLOC", ai: false, digits: 0 },
  suppression_markers_per_kloc: {
    label: "Suppression markers / KLOC",
    ai: false,
    digits: 2,
  },
  ai_consensus_per_kloc: {
    label: "AI consensus findings / KLOC",
    ai: true,
    digits: 2,
  },
  ai_grounding_rate: {
    label: "AI grounding rate",
    ai: true,
    digits: 1,
    share: true,
  },
  ai_mean_pairwise_jaccard: {
    label: "AI sample agreement (Jaccard)",
    ai: true,
    digits: 2,
  },
};

export function outcomeLabel(key: string): OutcomeLabel {
  return (
    OUTCOME_LABELS[key] ?? {
      label: humanize(key),
      ai: key.startsWith("ai_"),
      digits: 2,
    }
  );
}

/** Human labels of exclusion reason codes. */
export const EXCLUSION_LABELS: Record<string, string> = {
  truncated: "generation truncated",
  invalid: "agent marked invalid",
  empty_output: "nothing written by the model",
  below_min_sloc: "below the minimum SLOC",
  functional_missing: "no functional result",
  no_measurement: "no stored measurement",
  unsupported_measurement: "unsupported measurement schema",
  measurement_error: "measurement could not be read",
};

export function exclusionLabel(code: string): string {
  return EXCLUSION_LABELS[code] ?? humanize(code);
}
