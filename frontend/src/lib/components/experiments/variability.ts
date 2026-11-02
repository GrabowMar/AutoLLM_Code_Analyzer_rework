/**
 * Display helpers for run-to-run variability (the Results tab's
 * "Run-to-run variation" section, the app page's same-setup panel and the
 * model page's variability panel). Pure functions, unit-tested.
 *
 * Missing values render as "—" (unmeasured is never 0). Shares are 0–1.
 */
import { seriesColor } from "$lib/components/charts/theme";
import type {
  ComponentSource,
  PlannerRow,
  ReplicateRelation,
  VariabilityCell,
} from "$lib/api/variability";

export const DASH = "—";

const isNum = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);

/** Compact number: up to 3 significant digits below 100, grouped integers above. */
export function formatCompact(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  const abs = Math.abs(v);
  if (abs === 0) return "0";
  if (abs >= 1_000_000)
    return `${(v / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 10_000) return `${(v / 1_000).toFixed(0)}K`;
  if (abs >= 100) return Math.round(v).toLocaleString("en-US");
  if (abs >= 10) return String(Number(v.toFixed(1)));
  if (abs >= 1) return String(Number(v.toFixed(2)));
  return String(Number(v.toPrecision(2)));
}

/** A metric value in its unit: "$0.0321", "1.8M", "231 s", "100%", "1,141". */
export function formatMetricValue(
  v: number | null | undefined,
  unit: string,
): string {
  if (!isNum(v)) return DASH;
  switch (unit) {
    case "USD":
      return Math.abs(v) >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
    case "share":
      return `${Math.round(v * 100)}%`;
    case "%":
      return `${v.toFixed(Math.abs(v) >= 10 ? 0 : 1)}%`;
    case "s":
      return Math.abs(v) >= 120
        ? `${(v / 60).toFixed(1)} min`
        : `${Math.round(v)} s`;
    default:
      return formatCompact(v);
  }
}

/** A 0–1 share as a percentage ("45%"). */
export function formatShare(v: number | null | undefined): string {
  return isNum(v) ? `${Math.round(v * 100)}%` : DASH;
}

/** A coefficient of variation, Jaccard index or ICC: two decimals. */
export function formatRatio(v: number | null | undefined, digits = 2): string {
  return isNum(v) ? v.toFixed(digits) : DASH;
}

/** "a/b" counts with a share, e.g. "5/6 (83%)". */
export function formatAgreement(agree: number, total: number): string {
  if (!total) return DASH;
  return `${agree}/${total} (${formatShare(agree / total)})`;
}

export const COMPONENT_ORDER: ComponentSource[] = [
  "condition",
  "app",
  "condition_x_app",
  "repeat",
];

export const COMPONENT_LABEL: Record<ComponentSource, string> = {
  condition: "Condition",
  app: "App",
  condition_x_app: "Condition × app",
  repeat: "Repeat (run-to-run)",
};

/** Categorical slot of a variance source (chart-1..4; never status hues). */
export function componentColor(source: ComponentSource): string {
  return seriesColor(COMPONENT_ORDER.indexOf(source) + 1);
}

/** A padded [lo, hi] axis domain for *values* (a flat set gets a symmetric pad; shares stay in 0–1). */
export function stripDomain(values: number[], unit = ""): [number, number] {
  const vals = values.filter(isNum);
  if (unit === "share") return [0, 1];
  if (vals.length === 0) return [0, 1];
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.1 || 1;
    return [lo - pad, hi + pad];
  }
  const pad = (hi - lo) * 0.06;
  lo -= pad;
  hi += pad;
  if (Math.min(...vals) >= 0) lo = Math.max(0, lo);
  return [lo, hi];
}

/** Position of *v* on [lo, hi] as a CSS percentage (clamped). */
export function domainPos(v: number, [lo, hi]: [number, number]): string {
  const t = hi === lo ? 0.5 : (v - lo) / (hi - lo);
  return `${(Math.min(1, Math.max(0, t)) * 100).toFixed(2)}%`;
}

/** The cells' measured values of *metric* (for a shared axis). */
export function cellValues(cells: VariabilityCell[], metric: string): number[] {
  const out: number[] = [];
  for (const c of cells)
    for (const v of c.values[metric] ?? []) if (isNum(v)) out.push(v);
  return out;
}

/** Planner rows as apps × repeats: `grid[apps][repeats]`, with the sorted axes. */
export function plannerGrid(rows: PlannerRow[]): {
  apps: number[];
  repeats: number[];
  at: (apps: number, repeats: number) => PlannerRow | undefined;
} {
  const apps = [...new Set(rows.map((r) => r.apps))].sort((a, b) => a - b);
  const repeats = [...new Set(rows.map((r) => r.repeats))].sort(
    (a, b) => a - b,
  );
  const index = new Map(rows.map((r) => [`${r.apps}:${r.repeats}`, r]));
  return { apps, repeats, at: (a, r) => index.get(`${a}:${r}`) };
}

export const RELATION_LABEL: Record<ReplicateRelation, string> = {
  self: "This run",
  replicate: "Replicate",
  replay: "Replay",
  rerun: "Rerun",
};

export const RELATION_HELP: Record<ReplicateRelation, string> = {
  self: "The run this page shows",
  replicate:
    "Another repeat of the same design (same configuration, its own seed)",
  replay: "Same configuration and the same seed (exact replay)",
  rerun:
    "Same configuration, run outside the designed sample (an ad-hoc rerun or a superseded retry)",
};

/** "repeats 2" / "repeats 2–3" from a design's min/max. */
export function repeatsLabel(min: number | null, max: number | null): string {
  if (min == null || max == null) return DASH;
  return min === max ? `r = ${min}` : `r = ${min}–${max}`;
}

/** The first 8 characters of a job id. */
export const shortId = (id: string): string => id.slice(0, 8);
