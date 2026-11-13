/**
 * Comparing two reports of the same type, table by table: tables pair by title (then by
 * position), rows by their first cell (the identity column: model, condition, item, tool),
 * and numeric cells give a signed delta. Cells stay the server's text; a delta is only
 * shown when both sides are numbers in the same unit (both percentages or both plain).
 */
import type { ReportDocument, ReportTable } from "$lib/api/reports";
import { isBlank } from "./format";

/** The other report's table matching `table` (same title; else same position and columns). */
export function matchTable(
  table: ReportTable,
  index: number,
  other: ReportDocument | null,
): ReportTable | null {
  if (!other) return null;
  const byTitle = other.tables.filter((t) => t.title === table.title);
  if (byTitle.length === 1) return byTitle[0];
  // A study report groups by model, or by condition when scoped to one experiment.
  const key = groupless(table.title);
  const byGroup = other.tables.filter((t) => groupless(t.title) === key);
  if (byTitle.length === 0 && byGroup.length === 1) return byGroup[0];
  const same = other.tables[index];
  if (
    same &&
    same.columns.length === table.columns.length &&
    same.columns.every((c, i) => c === table.columns[i])
  )
    return same;
  return byTitle[0] ?? null;
}

/** A title with its grouping word neutralised ("Issues by model" ≡ "Issues by condition"). */
function groupless(title: string): string {
  return title
    .toLowerCase()
    .replace(/\b(by|per|across) (model|condition)s?\b/g, "$1 group");
}

/** Rows of the other table keyed by their first cell (the first occurrence wins). */
export function rowIndex(table: ReportTable | null): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const r of table?.rows ?? []) {
    const k = r[0] ?? "";
    if (!out.has(k)) out.set(k, r);
  }
  return out;
}

/** The value of `header` in the other table's row (column matched by header, not position). */
export function otherCell(
  other: ReportTable | null,
  otherRow: string[] | undefined,
  header: string,
): string | null {
  if (!other || !otherRow) return null;
  const i = other.columns.indexOf(header);
  return i < 0 ? null : (otherRow[i] ?? "");
}

function num(value: string): { v: number; pct: boolean } | null {
  const m = /^([+\-−]?\d[\d,]*(?:\.\d+)?(?:e[+-]?\d+)?)(%)?/i.exec(
    value.trim(),
  );
  if (!m) return null;
  const v = Number(m[1].replace(/,/g, "").replace("−", "-"));
  return Number.isFinite(v) ? { v, pct: !!m[2] } : null;
}

export interface Delta {
  /** "+0.011", "−3", "+1.2 pp". */
  text: string;
  sign: -1 | 0 | 1;
}

/** this − other, when both cells are numbers in the same unit; null otherwise. */
export function cellDelta(
  current: string,
  previous: string | null,
): Delta | null {
  if (previous == null || isBlank(current) || isBlank(previous)) return null;
  const a = num(current);
  const b = num(previous);
  if (!a || !b || a.pct !== b.pct) return null;
  const d = a.v - b.v;
  const sign = Math.abs(d) < 1e-12 ? 0 : d > 0 ? 1 : -1;
  const mag = Math.abs(d);
  const digits = mag >= 100 ? 0 : mag >= 10 ? 1 : mag >= 1 ? 2 : 3;
  const body = Number(mag.toFixed(digits)).toLocaleString(undefined, {
    maximumFractionDigits: digits,
  });
  return {
    text: `${sign > 0 ? "+" : sign < 0 ? "−" : "±"}${body}${a.pct ? " pp" : ""}`,
    sign,
  };
}

/** Keys of rows only in the other table (gone from this report). */
export function rowsOnlyInOther(
  table: ReportTable,
  other: ReportTable | null,
): string[] {
  if (!other) return [];
  const here = new Set(table.rows.map((r) => r[0] ?? ""));
  return other.rows.map((r) => r[0] ?? "").filter((k) => !here.has(k));
}
