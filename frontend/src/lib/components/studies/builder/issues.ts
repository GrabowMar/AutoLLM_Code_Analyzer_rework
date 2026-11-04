/**
 * Plan-rail issues: the server's registered `Issue` rows (`contracts/issues.py`) plus client-side checks
 * share one shape and one renderer (`SummaryIssues`). Errors block Freeze / Start; warnings and notes
 * inform. A quick fix (`IssueFix`) maps to an action the builder applies to the draft.
 */
import type { Issue, IssueFix, IssueSeverity } from "$lib/api/v3/types";

export interface SummaryIssue {
  code: string;
  severity: IssueSeverity;
  detail: string;
  /** Prefix naming what the issue is about (a model, a condition, a cell count). */
  subject?: string;
  fixes?: IssueFix[];
  /** Cell keys the issue concerns (the PlanRail links the first one to the cell drawer). */
  cells?: string[];
  path?: string | null;
}

/** A quick fix shown under an issue (a link or a button). */
export interface IssueAction {
  label: string;
  href?: string;
  onclick?: () => void;
}

/** Titles for the codes the builder shows most; any other code is humanized (`config` registry summaries win). */
const TITLES: Record<string, string> = {
  estimate_exceeds_cap: "Estimate above the study cap",
  cap_admits_fewer_blocks: "Study cap stops before the last block",
  combination_pruned: "Combination dropped",
  unbalanced_grid: "Unbalanced grid",
  aliasing: "Aliased main effects",
  incompatible_style: "Style does not fit the stack",
  starter_missing: "Starter missing",
  prose_variant_in_unit: "Prose variant inside a unit",
  mixed_parents: "Slot sets of different tasks",
  primary_feature_missing: "Primary features missing",
  adaptive_stop_in_confirmatory: "Adaptive stop in a confirmatory study",
  harness_not_enabled: "Harness not enabled",
  harness_exploratory_only: "Harness for exploratory studies only",
  few_clusters: "Few units",
  plan_stale: "Plan out of date",
  invalid_design: "Design incomplete",
  model_alias_in_confirmatory: "Model alias in a confirmatory study",
  free_model_rate_limit: "Free model rate limits",
};

export function issueTitle(
  code: string,
  summaries?: Record<string, string>,
): string {
  return (
    TITLES[code] ??
    summaries?.[code] ??
    code.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())
  );
}

export const SEVERITY_ORDER: IssueSeverity[] = ["error", "warn", "info"];

export const SEVERITY_LABELS: Record<IssueSeverity, string> = {
  error: "Errors",
  warn: "Warnings",
  info: "Notes",
};

export function groupIssues(
  issues: SummaryIssue[],
): { severity: IssueSeverity; items: SummaryIssue[] }[] {
  return SEVERITY_ORDER.map((severity) => ({
    severity,
    items: issues.filter((issue) => issue.severity === severity),
  })).filter((group) => group.items.length > 0);
}

/** Server issues → summary rows (unknown severities become notes; repeated code+message pairs merge their cells). */
export function fromIssues(
  issues: readonly Issue[] | null | undefined,
): SummaryIssue[] {
  const out: SummaryIssue[] = [];
  const seen = new Map<string, SummaryIssue>();
  for (const issue of issues ?? []) {
    const key = `${issue.code}\u0000${issue.message}`;
    const existing = seen.get(key);
    if (existing) {
      existing.cells = [
        ...new Set([...(existing.cells ?? []), ...(issue.cells ?? [])]),
      ];
      continue;
    }
    const row: SummaryIssue = {
      code: issue.code,
      severity: SEVERITY_ORDER.includes(issue.severity)
        ? issue.severity
        : "info",
      detail: issue.message || issueTitle(issue.code),
      fixes: issue.fix ?? [],
      cells: issue.cells ?? [],
      path: issue.path ?? null,
    };
    seen.set(key, row);
    out.push(row);
  }
  for (const row of out) {
    if (row.cells && row.cells.length > 1)
      row.subject = `${row.cells.length} cells`;
  }
  return out;
}

export function hasErrors(issues: readonly SummaryIssue[]): boolean {
  return issues.some((issue) => issue.severity === "error");
}

/** The quick-fix actions the builder knows how to apply (`IssueFix.action`). */
export type FixAction =
  | { kind: "set_repeats"; repeats: number }
  | { kind: "set_study_cap"; cap: number }
  | { kind: "set_knob"; values: Record<string, unknown> }
  | { kind: "move_prose_variant" }
  | { kind: "unknown"; action: string };

export function parseFix(fix: IssueFix): FixAction {
  const params = fix.params ?? {};
  switch (fix.action) {
    case "set_repeats": {
      const repeats = Number(params.repeats);
      return Number.isInteger(repeats) && repeats > 0
        ? { kind: "set_repeats", repeats }
        : { kind: "unknown", action: fix.action };
    }
    case "set_study_cap":
    case "set_cap": {
      const cap = Number(params.study_cap ?? params.cap);
      return Number.isFinite(cap) && cap > 0
        ? { kind: "set_study_cap", cap }
        : { kind: "unknown", action: fix.action };
    }
    case "set_knob":
      return { kind: "set_knob", values: { ...params } };
    case "move_prose_variant":
      return { kind: "move_prose_variant" };
    default:
      return { kind: "unknown", action: fix.action };
  }
}
