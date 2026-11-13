/**
 * Which tables can be drawn and how: one horizontal bar per row (the identity column as the
 * label), one metric at a time (a share or a number column), with its 95% interval as a
 * whisker when the table has one — an adjacent "… CI" column ("[0.96, 1]"), or inline in the
 * cell ("75.0% (9/12) [60.1, 88.0]"). Ranks and p-values are never charted.
 */
import type { ReportTable } from "$lib/api/reports";
import { isBlank, shareOf, type TablePlan } from "./format";

export interface ChartMetric {
  index: number;
  header: string;
  kind: "share" | "num";
  /** Column holding the metric's interval, if any. */
  ciIndex?: number;
}

export interface ChartSpec {
  labelIndex: number;
  /** A second identity column, when the first repeats (tool + rule). */
  label2Index?: number;
  /** Measures, the default first: shares with an interval, shares, then numbers that vary. */
  metrics: ChartMetric[];
}

export interface ChartRow {
  label: string;
  value: number;
  lo?: number;
  hi?: number;
  /** The cell as the server formatted it (shown beside the bar). */
  text: string;
  /** Position in the table (for matching and stable order). */
  index: number;
}

const NOT_CHARTED = /(rank|^p\b|^p \(|^q \(|< ?0\.05|^n$)/i;
const INTERVAL = /^\[\s*([+\-−]?[\d.e+-]+)\s*,\s*([+\-−]?[\d.e+-]+)\s*\]$/i;

/** "[0.96, 1]" → [0.96, 1]; also the trailing "[60.1, 88.0]" of a rate cell (÷100 when the cell is a percentage). */
export function parseInterval(cell: string): [number, number] | null {
  const tail = /\[([^\]]+)\]\s*$/.exec(cell.trim());
  if (!tail) return null;
  const m = INTERVAL.exec(`[${tail[1]}]`);
  if (!m) return null;
  let lo = Number(m[1].replace("−", "-"));
  let hi = Number(m[2].replace("−", "-"));
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  if (/^\d+(\.\d+)?%/.test(cell.trim())) {
    lo /= 100;
    hi /= 100;
  }
  return lo <= hi ? [lo, hi] : [hi, lo];
}

/** A cell as a number for plotting (shares 0..1; plain numbers as written). */
export function metricValue(
  cell: string,
  kind: "share" | "num",
): number | null {
  if (isBlank(cell)) return null;
  if (kind === "share") return shareOf(cell);
  const v = Number(cell.replace(/,/g, "").replace("−", "-").replace(/%$/, ""));
  return Number.isFinite(v) ? v : null;
}

/** How to chart a table, or null when it is not a chartable data table. */
export function chartSpec(
  table: ReportTable,
  plan: TablePlan,
): ChartSpec | null {
  if (plan.layout !== "grid" || table.rows.length < 2) return null;
  const label = plan.columns.find(
    (c) => c.kind === "text" || c.kind === "wide",
  );
  if (!label) return null;
  const intervalCols = plan.columns.filter(
    (c) =>
      /\bci\b|interval/i.test(c.header) &&
      table.rows.some((r) => !isBlank(r[c.index])) &&
      table.rows.every(
        (r) => isBlank(r[c.index]) || INTERVAL.test((r[c.index] ?? "").trim()),
      ),
  );
  const metrics: ChartMetric[] = [];
  for (const c of plan.columns) {
    if ((c.kind !== "share" && c.kind !== "num") || NOT_CHARTED.test(c.header))
      continue;
    const values = table.rows
      .map((r) => metricValue(r[c.index] ?? "", c.kind as "share" | "num"))
      .filter((v) => v != null);
    if (values.length < 2) continue;
    // The interval column right after the metric (or one further, past a count).
    const ci = intervalCols.find(
      (i) => i.index > c.index && i.index <= c.index + 2,
    );
    metrics.push({
      index: c.index,
      header: c.header,
      kind: c.kind,
      ciIndex: ci?.index,
    });
  }
  // Inline intervals ("75.0% (9/12) [60, 88]") come with the share column itself.
  if (!metrics.length) return null;
  const varies = (m: ChartMetric) =>
    new Set(table.rows.map((r) => metricValue(r[m.index] ?? "", m.kind))).size >
    1;
  const score = (m: ChartMetric) =>
    (m.kind === "share" ? 4 : 0) +
    (m.ciIndex != null ? 2 : 0) +
    (varies(m) ? 1 : 0);
  const ordered = metrics
    .map((m, i) => ({ m, i }))
    .sort((a, b) => score(b.m) - score(a.m) || a.i - b.i)
    .map((x) => x.m);
  const labels = table.rows.map((r) => r[label.index] ?? "");
  let label2Index: number | undefined;
  if (new Set(labels).size < labels.length) {
    label2Index = plan.columns.find(
      (c) =>
        c.index !== label.index && (c.kind === "text" || c.kind === "wide"),
    )?.index;
  }
  return { labelIndex: label.index, label2Index, metrics: ordered };
}

/** The plotted rows of one metric (rows without a value are left out). */
export function chartRows(
  table: ReportTable,
  spec: ChartSpec,
  metric: ChartMetric,
): ChartRow[] {
  const out: ChartRow[] = [];
  table.rows.forEach((r, index) => {
    const text = r[metric.index] ?? "";
    const value = metricValue(text, metric.kind);
    if (value == null) return;
    const ci =
      metric.ciIndex != null
        ? parseInterval(r[metric.ciIndex] ?? "")
        : parseInterval(text);
    out.push({
      label:
        spec.label2Index != null
          ? `${r[spec.labelIndex] ?? ""} · ${r[spec.label2Index] ?? ""}`
          : (r[spec.labelIndex] ?? ""),
      value,
      lo: ci?.[0],
      hi: ci?.[1],
      text,
      index,
    });
  });
  return out;
}

/** The axis domain: [0, 1] for shares; from min(0, …) to max for numbers (a zero line when it spans 0). */
export function chartDomain(
  rows: { value: number; lo?: number; hi?: number }[],
  kind: "share" | "num",
): [number, number] {
  if (kind === "share") {
    const lo = Math.min(0, ...rows.map((r) => r.lo ?? r.value));
    return [lo, Math.max(1, ...rows.map((r) => r.hi ?? r.value))];
  }
  const lo = Math.min(0, ...rows.map((r) => r.lo ?? r.value));
  const hi = Math.max(0, ...rows.map((r) => r.hi ?? r.value));
  return hi === lo ? [lo, lo + 1] : [lo, hi];
}
