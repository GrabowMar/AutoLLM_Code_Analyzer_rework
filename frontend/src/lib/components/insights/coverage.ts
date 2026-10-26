/**
 * Analysis-coverage funnel vocabulary: where a set of completed jobs went
 * under the reference plan. Only `measured` jobs are in the numbers; every
 * other bucket is missing data (never zero findings).
 */
import type { RankingAnalysisFunnel } from "$lib/api/rankings";
import { exclusionLabel } from "./format";

export type FunnelKey = Exclude<
  keyof RankingAnalysisFunnel,
  "excluded_reasons"
>;

export interface FunnelSegment {
  key: FunnelKey;
  label: string;
  /** CSS color (status tokens; identity also carried by the legend text). */
  color: string;
  help: string;
}

export const FUNNEL_SEGMENTS: readonly FunnelSegment[] = [
  {
    key: "measured",
    label: "Measured",
    color: "var(--success)",
    help: "Complete canonical run under the reference plan — the only jobs in the numbers",
  },
  {
    key: "excluded",
    label: "Excluded",
    color: "var(--muted-foreground)",
    help: "Left out by the eligibility rules",
  },
  {
    key: "incomplete",
    label: "Incomplete",
    color: "var(--warning)",
    help: "Only partial runs: a required tool did not succeed",
  },
  {
    key: "analysis_failed",
    label: "Analysis failed",
    color: "var(--destructive)",
    help: "Every analysis run of the job failed",
  },
  {
    key: "other_plan",
    label: "Other plan",
    color: "var(--chart-3)",
    help: "Analysed only under a different plan — not comparable",
  },
  {
    key: "in_progress",
    label: "In progress",
    color: "var(--info)",
    help: "Analysis queued or running",
  },
  {
    key: "not_analysed",
    label: "Not analysed",
    color: "color-mix(in oklab, var(--muted-foreground) 35%, transparent)",
    help: "No analysis run yet",
  },
];

/** Jobs in the funnel (falls back to the bucket sum when `jobs` is absent). */
export function funnelTotal(
  c: Partial<RankingAnalysisFunnel> & { jobs?: number },
): number {
  if (typeof c.jobs === "number") return c.jobs;
  return FUNNEL_SEGMENTS.reduce((sum, s) => sum + (c[s.key] ?? 0), 0);
}

/** Jobs that are not in the numbers. */
export function funnelGapCount(c: Partial<RankingAnalysisFunnel>): number {
  return FUNNEL_SEGMENTS.filter((s) => s.key !== "measured").reduce(
    (sum, s) => sum + (c[s.key] ?? 0),
    0,
  );
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** One line per non-empty gap bucket, e.g. "2 jobs: analysis failed". */
export function funnelGapLines(c: Partial<RankingAnalysisFunnel>): string[] {
  const lines: string[] = [];
  for (const seg of FUNNEL_SEGMENTS) {
    if (seg.key === "measured") continue;
    const n = c[seg.key] ?? 0;
    if (!n) continue;
    let line = `${plural(n, "job")} ${seg.label.toLowerCase()} — ${seg.help.charAt(0).toLowerCase()}${seg.help.slice(1)}`;
    if (seg.key === "excluded" && c.excluded_reasons) {
      const reasons = Object.entries(c.excluded_reasons)
        .filter(([, count]) => count > 0)
        .map(([code, count]) => `${exclusionLabel(code)} ×${count}`);
      if (reasons.length) line += ` (${reasons.join(", ")})`;
    }
    lines.push(line);
  }
  return lines;
}
