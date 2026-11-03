/**
 * Pure formatting of pattern results (spec 7.9, 9.7, 10.3 §4): prevalence proportions with Wilson CIs,
 * ITT vs measured n, flags, comparison rows and the CI-bar geometry.
 *
 * Validity rules carried here: prevalence is presence/absence, never a severity; a row without measured
 * variants is "not measured" (never 0 %); detector errors and n/a variants are counted apart and never folded
 * into "absent"; planned rows show p with the plan-adjusted p and the decision at the plan's alpha, exploratory rows show q,
 * and all of them are withheld while interim.
 */
import type {
  ClusterStats,
  ComparisonRow,
  PooledRow,
  PrevalenceRow,
} from "$lib/api/v3/types";

const DASH = "—";

function finite(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** A proportion as a percentage with one decimal (0 % and 100 % without). */
export function fmtProportion(p: number | null | undefined): string {
  if (!finite(p)) return DASH;
  const pct = p * 100;
  return `${pct === 0 || pct === 100 ? pct.toFixed(0) : pct.toFixed(1)}%`;
}

/** `[12.3%, 45.6%]`, or "—" when the interval is missing. */
export function fmtCI(
  low: number | null | undefined,
  high: number | null | undefined,
): string {
  if (!finite(low) || !finite(high)) return DASH;
  return `[${fmtProportion(low)}, ${fmtProportion(high)}]`;
}

/** The headline of a prevalence row: "40.0% [21.9%, 61.3%]" or "not measured". */
export function fmtPrevalence(
  row: Pick<PrevalenceRow, "p" | "ci_low" | "ci_high" | "n_measured">,
): string {
  if (!row.n_measured || !finite(row.p)) return "not measured";
  return `${fmtProportion(row.p)} ${fmtCI(row.ci_low, row.ci_high)}`;
}

/**
 * The n of a row: "k/n measured · ITT N". The ITT n counts every planned variant (also those without output),
 * so a gap between the two is attrition. Provisional live rows have no ITT n.
 */
export function fmtN(
  row: Pick<PrevalenceRow, "k" | "n_measured" | "n_itt">,
): string {
  const measured = `${row.k}/${row.n_measured} measured`;
  return row.n_itt === null || row.n_itt === undefined
    ? measured
    : `${measured} · ITT ${row.n_itt}`;
}

/** What the measured n leaves out: errors, n/a and missing outputs, each named. */
export function fmtUnmeasured(
  row: Pick<PrevalenceRow, "errors" | "na" | "missing">,
): string {
  const parts: string[] = [];
  if (row.errors)
    parts.push(`${row.errors} detector error${row.errors === 1 ? "" : "s"}`);
  if (row.na) parts.push(`${row.na} n/a`);
  if (row.missing) parts.push(`${row.missing} without output`);
  return parts.join(" · ");
}

export function fmtMethod(method: string | null | undefined): string {
  switch (method) {
    case "wilson":
      return "Wilson 95%";
    case "wilson_repeated_looks":
      return "Wilson, repeated-looks adjusted";
    default:
      return method ?? DASH;
  }
}

export interface RowFlag {
  code: string;
  label: string;
  title: string;
}

/** Flags shown next to a prevalence row (status by text, never by colour alone). */
export function prevalenceFlags(row: PrevalenceRow): RowFlag[] {
  const flags: RowFlag[] = [];
  if (row.provisional)
    flags.push({
      code: "provisional",
      label: "provisional",
      title: "Live estimate from primary features; the PatternRun replaces it.",
    });
  if (row.adaptive_n)
    flags.push({
      code: "adaptive_n",
      label: "adaptive n",
      title: `The n of this cell was decided by a precision stop${row.looks ? ` (${row.looks} looks)` : ""}; the interval is adjusted for repeated looks.`,
    });
  if (row.low_effective_n)
    flags.push({
      code: "low_effective_n",
      label: "low effective n",
      title:
        "More than half the variants are exact duplicates: the effective sample is smaller than n.",
    });
  if (row.bounds)
    flags.push({
      code: "bounds",
      label: `bounds ${fmtProportion(row.bounds.low)}–${fmtProportion(row.bounds.high)}`,
      title:
        "Many variants are unmeasured; worst/best-case bounds treat all of them as absent/present.",
    });
  if (row.conditional_on)
    flags.push({
      code: "conditional",
      label: "| tests pass",
      title: "Among variants whose slot tests all pass.",
    });
  return flags;
}

/** Audit cell: "precision 0.90 · NPV 0.95", or the audit status. */
export function fmtAudit(
  row: Pick<PrevalenceRow, "audit" | "audit_status">,
): string {
  if (row.audit) {
    const part = (label: string, v: number | null) =>
      `${label} ${finite(v) ? v.toFixed(2) : DASH}`;
    return `${part("precision", row.audit.precision)} · ${part("NPV", row.audit.npv)}`;
  }
  switch (row.audit_status) {
    case undefined:
    case "not_audited":
      return "not audited";
    case "pending":
    case "in_progress":
      return "audit in progress";
    default:
      return row.audit_status.replace(/_/g, " ");
  }
}

/** Geometry of a CI bar on a 0–1 axis, in percent of the track width. */
export function ciBar(
  low: number | null | undefined,
  high: number | null | undefined,
  p: number | null | undefined,
): { left: number; width: number; point: number | null } | null {
  if (!finite(low) || !finite(high)) return null;
  const clamp = (v: number) => Math.min(100, Math.max(0, v * 100));
  const left = clamp(Math.min(low, high));
  const right = clamp(Math.max(low, high));
  return {
    left,
    width: Math.max(right - left, 0.5),
    point: finite(p) ? clamp(p) : null,
  };
}

// ── Comparisons ────────────────────────────────────────────────────────────

/** A signed difference in percentage points. */
export function fmtDiff(diff: number | null | undefined): string {
  if (!finite(diff)) return DASH;
  const pts = diff * 100;
  const sign = pts > 0 ? "+" : pts < 0 ? "−" : "±";
  return `${sign}${Math.abs(pts).toFixed(1)} pts`;
}

export function fmtDiffCI(
  low: number | null | undefined,
  high: number | null | undefined,
): string {
  if (!finite(low) || !finite(high)) return DASH;
  const f = (v: number) => `${v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}`;
  return `[${f(low)}, ${f(high)}]`;
}

type SignificanceFields = Pick<
  ComparisonRow,
  "p" | "q" | "planned" | "interim"
> &
  Partial<
    Pick<ComparisonRow, "p_adjusted" | "correction" | "alpha" | "significant">
  >;

function fmtP(label: string, value: number): string {
  return value < 0.001 ? `${label} < 0.001` : `${label} = ${value.toFixed(3)}`;
}

/**
 * Planned rows: "p = 0.012 (adj. holm 0.036)" — the raw p and the p adjusted over the planned family by the
 * plan's correction (spec 7.9 multiplicity); exploratory rows: "q = 0.04" (BH); "withheld (interim)" while
 * interim.
 */
export function fmtSignificance(row: SignificanceFields): string {
  if (row.interim) return "withheld (interim)";
  if (!row.planned) return finite(row.q) ? fmtP("q", row.q) : DASH;
  if (!finite(row.p)) return DASH;
  const raw = fmtP("p", row.p);
  if (!finite(row.p_adjusted)) return raw;
  const adj = row.p_adjusted < 0.001 ? "< 0.001" : row.p_adjusted.toFixed(3);
  return `${raw} (adj. ${row.correction || "?"} ${adj})`;
}

/**
 * The decision for a planned row at the plan's alpha, from the multiplicity-adjusted p: "significant (α 0.05)"
 * / "not significant (α 0.05)"; "withheld (interim)" while interim; null for exploratory rows (no decision) or
 * when the backend gave none.
 */
export function fmtDecision(row: SignificanceFields): string | null {
  if (!row.planned) return null;
  if (row.interim) return "withheld (interim)";
  if (row.significant === true || row.significant === false) {
    const alpha = finite(row.alpha) ? ` (α ${row.alpha})` : "";
    return `${row.significant ? "significant" : "not significant"}${alpha}`;
  }
  return null;
}

/** A pooled row in the comparison shape: its raw permutation p stands in for `p`. */
export function pooledSignificance(row: PooledRow): SignificanceFields {
  return {
    p: row.permutation_p ?? row.p_value ?? null,
    q: row.q ?? null,
    planned: !!row.planned,
    interim: row.interim,
    p_adjusted: row.p_adjusted,
    correction: row.correction,
    alpha: row.alpha,
    significant: row.significant,
  };
}

/** The n behind a comparison: pairs for paired rows, both arms for independent ones. */
export function fmtComparisonN(row: ComparisonRow): string {
  if (row.method === "paired")
    return row.n_pairs !== undefined ? `${row.n_pairs} pairs` : DASH;
  if (row.n_a === undefined || row.n_b === undefined) return DASH;
  return `${row.k_a ?? DASH}/${row.n_a} vs ${row.k_b ?? DASH}/${row.n_b}`;
}

// ── Clusters ───────────────────────────────────────────────────────────────

export function fmtClusterStats(
  stats: ClusterStats | null | undefined,
): string {
  if (!stats) return DASH;
  const parts = [`n ${stats.n}`, `${stats.distinct} distinct`];
  if (finite(stats.top1_share))
    parts.push(`top cluster ${fmtProportion(stats.top1_share)}`);
  if (finite(stats.exact_dup_share))
    parts.push(`exact duplicates ${fmtProportion(stats.exact_dup_share)}`);
  if (finite(stats.entropy))
    parts.push(`entropy ${stats.entropy.toFixed(2)} bits`);
  if (finite(stats.chao1)) parts.push(`Chao1 ${stats.chao1.toFixed(1)}`);
  return parts.join(" · ");
}

/** Group rows by a key, keeping first-seen order. */
export function groupBy<T>(
  rows: T[],
  key: (row: T) => string,
): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const row of rows) {
    const k = key(row);
    const list = groups.get(k);
    if (list) list.push(row);
    else groups.set(k, [row]);
  }
  return [...groups.entries()];
}

/** Feature column key of a variant row (`"<feature>:<scope>"`) → its two parts. */
export function splitFeatureKey(key: string): {
  feature: string;
  scope: string;
} {
  const i = key.lastIndexOf(":");
  return i > 0
    ? { feature: key.slice(0, i), scope: key.slice(i + 1) }
    : { feature: key, scope: "" };
}

export const FEATURE_STATUS_TEXT: Record<string, string> = {
  present: "present",
  absent: "absent",
  error: "detector error",
  not_applicable: "n/a",
};
