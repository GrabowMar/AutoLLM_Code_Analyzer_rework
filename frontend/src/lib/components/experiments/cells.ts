/**
 * The experiment's condition × app × repeat grid (`GET /cells/`, spec §F.1)
 * as flat arrays indexed by `(ci * apps + ai) * repeats + r`, patched in O(1)
 * by SSE `job` events. Pure functions: the live store wraps a grid in
 * `$state` so only the squares that change re-render.
 */
import type { JobEvent } from "$lib/api/types/events";
import type {
  CellCode,
  CellsPayload,
  ProgressCounts,
} from "$lib/api/types/experiments";
import { EMPTY_PROGRESS } from "./format";

export interface CellGrid {
  conditions: CellsPayload["conditions"];
  apps: CellsPayload["apps"];
  repeats: number;
  /** null = no job for that cell yet (e.g. a draft, or repeats raised before a top-up). */
  codes: (CellCode | null)[];
  jobIds: (string | null)[];
  passRates: (number | null)[];
  attempts: number[];
  /** condition id → row index; app id → column index. */
  conditionIndex: Map<number, number>;
  appIndex: Map<number, number>;
}

export interface CellStateInfo {
  label: string;
  /** ProgressCounts key the state counts under. */
  bucket: keyof ProgressCounts;
  /** Square classes (tokens only; shape carries meaning alongside hue). */
  square: string;
  /** Legend order. */
  order: number;
}

export const CELL_STATES: Record<CellCode, CellStateInfo> = {
  c: {
    label: "Passed every check",
    bucket: "completed",
    square: "bg-success",
    order: 0,
  },
  f: {
    label: "Failed checks",
    bucket: "completed",
    square: "bg-destructive",
    order: 1,
  },
  u: {
    label: "Completed, not graded",
    bucket: "completed",
    square: "border-2 border-success bg-transparent",
    order: 2,
  },
  x: {
    label: "Run failed",
    bucket: "failed",
    square: "border-2 border-destructive bg-destructive/15",
    order: 3,
  },
  k: {
    label: "Cancelled",
    bucket: "cancelled",
    square: "bg-muted-foreground/40",
    order: 4,
  },
  e: {
    label: "Evaluating",
    bucket: "evaluating",
    square: "bg-info/60",
    order: 5,
  },
  q: {
    label: "Waiting for evaluation",
    bucket: "eval_queued",
    square: "border-2 border-info/70 bg-info/10",
    order: 6,
  },
  r: {
    label: "Running",
    bucket: "running",
    square: "bg-info animate-pulse motion-reduce:animate-none",
    order: 7,
  },
  w: {
    label: "Waiting for its model to load",
    bucket: "pending",
    square: "border-2 border-dashed border-info/70 bg-transparent",
    order: 8,
  },
  b: {
    label: "Retry backoff",
    bucket: "pending",
    square: "border-2 border-warning bg-warning/15",
    order: 9,
  },
  p: {
    label: "Queued",
    bucket: "pending",
    square: "border border-muted-foreground/50 bg-transparent",
    order: 10,
  },
};

/** Square classes of a cell without a job. */
export const EMPTY_SQUARE = "border border-dashed border-border bg-transparent";

const TERMINAL: ReadonlySet<CellCode> = new Set(["c", "f", "u", "x", "k"]);
const ACTIVE: ReadonlySet<CellCode> = new Set(["p", "b", "w", "r", "q", "e"]);

export function isTerminalCode(code: CellCode | null | undefined): boolean {
  return !!code && TERMINAL.has(code);
}

export function isActiveCode(code: CellCode | null | undefined): boolean {
  return !!code && ACTIVE.has(code);
}

export function cellIndex(
  grid: Pick<CellGrid, "apps" | "repeats">,
  ci: number,
  ai: number,
  r: number,
): number {
  return (ci * grid.apps.length + ai) * grid.repeats + r;
}

export function buildGrid(payload: CellsPayload): CellGrid {
  const repeats = Math.max(1, payload.repeats);
  const size = payload.conditions.length * payload.apps.length * repeats;
  const grid: CellGrid = {
    conditions: payload.conditions,
    apps: payload.apps,
    repeats,
    codes: new Array<CellCode | null>(size).fill(null),
    jobIds: new Array<string | null>(size).fill(null),
    passRates: new Array<number | null>(size).fill(null),
    attempts: new Array<number>(size).fill(0),
    conditionIndex: new Map(payload.conditions.map((c, i) => [c.id, i])),
    appIndex: new Map(payload.apps.map((a, i) => [a.id, i])),
  };
  for (const [ci, ai, r, s, jobId, passRate, attempt] of payload.cells) {
    if (ci < 0 || ci >= payload.conditions.length) continue;
    if (ai < 0 || ai >= payload.apps.length || r < 0 || r >= repeats) continue;
    const i = cellIndex(grid, ci, ai, r);
    grid.codes[i] = s;
    grid.jobIds[i] = jobId;
    grid.passRates[i] = passRate;
    grid.attempts[i] = attempt;
  }
  return grid;
}

/** Cell code of a `job` event (backoff / prefetch waits only come from `/cells/`). */
export function codeForJobEvent(
  event: Pick<JobEvent, "status" | "phase" | "pass_rate">,
): CellCode {
  switch (event.status) {
    case "pending":
      return "p";
    case "running":
      if (event.phase === "eval_queued") return "q";
      if (event.phase === "evaluating") return "e";
      // Legacy thread-era rows (RUNNING + phase "queued") never started; the
      // server's `cell_code` shows them as pending too.
      if (event.phase === "queued") return "p";
      return "r";
    case "completed":
      if (event.pass_rate == null) return "u";
      return event.pass_rate >= 0.9999 ? "c" : "f";
    case "failed":
      return "x";
    case "cancelled":
      return "k";
    default:
      return "p";
  }
}

export interface CellPatch {
  index: number;
  previous: CellCode | null;
  next: CellCode;
}

/**
 * Applies a `job` event in place. Ignores events for unknown cells and stale
 * events (an older attempt of the job the cell already shows). Returns the
 * change, or null when nothing changed.
 */
export function applyJobEvent(
  grid: CellGrid,
  event: JobEvent,
): CellPatch | null {
  if (
    event.condition_id == null ||
    event.app_id == null ||
    event.repeat_index == null
  )
    return null;
  const ci = grid.conditionIndex.get(event.condition_id);
  const ai = grid.appIndex.get(event.app_id);
  if (ci == null || ai == null) return null;
  if (event.repeat_index < 0 || event.repeat_index >= grid.repeats) return null;
  const i = cellIndex(grid, ci, ai, event.repeat_index);
  const sameJob = grid.jobIds[i] === event.job_id;
  if (sameJob && (event.attempt ?? 0) < grid.attempts[i]) return null;
  const previous = grid.codes[i];
  const next = codeForJobEvent(event);
  // A late event of a replaced job (a top-up created a newer row) must not
  // overwrite the newer job's live state.
  if (
    !sameJob &&
    grid.jobIds[i] != null &&
    isActiveCode(previous) &&
    TERMINAL.has(next)
  ) {
    return null;
  }
  grid.codes[i] = next;
  grid.jobIds[i] = event.job_id;
  grid.passRates[i] =
    event.status === "completed" ? (event.pass_rate ?? null) : null;
  grid.attempts[i] = Math.max(
    sameJob ? grid.attempts[i] : 0,
    event.attempt ?? 0,
  );
  if (previous === next && sameJob) return null;
  return { index: i, previous, next };
}

/** Moves one cell between ProgressCounts buckets (returns a new object). */
export function adjustCounts(
  counts: ProgressCounts,
  patch: CellPatch,
): ProgressCounts {
  const out = { ...counts };
  if (patch.previous) {
    const from = CELL_STATES[patch.previous].bucket;
    out[from] = Math.max(0, out[from] - 1);
  }
  out[CELL_STATES[patch.next].bucket] += 1;
  return out;
}

export function countsFromGrid(grid: CellGrid): ProgressCounts {
  const out = { ...EMPTY_PROGRESS };
  for (const code of grid.codes) if (code) out[CELL_STATES[code].bucket] += 1;
  return out;
}

export interface RowSummary {
  /** Cells in the design (conditions × apps × repeats share). */
  total: number;
  /** Cells with a job. */
  started: number;
  done: number;
  active: number;
  failed: number;
  /** Graded cells (passed + failed checks). */
  graded: number;
  passed: number;
  /** Mean pass rate over graded cells; null when none. */
  passRate: number | null;
}

export function rowSummary(grid: CellGrid, ci: number): RowSummary {
  const per = grid.apps.length * grid.repeats;
  const start = ci * per;
  const summary: RowSummary = {
    total: per,
    started: 0,
    done: 0,
    active: 0,
    failed: 0,
    graded: 0,
    passed: 0,
    passRate: null,
  };
  let rateSum = 0;
  let rateCount = 0;
  for (let i = start; i < start + per; i++) {
    const code = grid.codes[i];
    if (!code) continue;
    summary.started += 1;
    if (TERMINAL.has(code)) summary.done += 1;
    else summary.active += 1;
    if (code === "x") summary.failed += 1;
    if (code === "c" || code === "f") {
      summary.graded += 1;
      if (code === "c") summary.passed += 1;
      const rate = grid.passRates[i];
      if (rate != null) {
        rateSum += rate;
        rateCount += 1;
      }
    }
  }
  summary.passRate = rateCount ? rateSum / rateCount : null;
  return summary;
}

export type GridGroupBy = "endpoint" | "none";
export type GridSort = "sequence" | "label" | "progress" | "pass";

export interface ConditionGroup {
  key: string;
  /** Endpoint slug, "OpenRouter", or "" when ungrouped. */
  label: string;
  /** Row indexes in display order. */
  rows: number[];
}

export const OPENROUTER_GROUP = "openrouter";

export function endpointGroupKey(slug: string | null | undefined): string {
  return slug || OPENROUTER_GROUP;
}

/**
 * Rows grouped by endpoint (OpenRouter last) and sorted inside each group.
 * "sequence" keeps the server's order (conditions are listed in launch order).
 */
export function groupConditions(
  grid: Pick<CellGrid, "conditions">,
  groupBy: GridGroupBy,
  sort: GridSort,
  summaries: RowSummary[],
): ConditionGroup[] {
  const indexes = grid.conditions.map((_, i) => i);
  const compare = (a: number, b: number): number => {
    switch (sort) {
      case "label":
        return grid.conditions[a].label.localeCompare(grid.conditions[b].label);
      case "progress": {
        const pa = summaries[a]
          ? summaries[a].done / Math.max(1, summaries[a].total)
          : 0;
        const pb = summaries[b]
          ? summaries[b].done / Math.max(1, summaries[b].total)
          : 0;
        return pb - pa || a - b;
      }
      case "pass": {
        const ra = summaries[a]?.passRate ?? -1;
        const rb = summaries[b]?.passRate ?? -1;
        return rb - ra || a - b;
      }
      default:
        return a - b;
    }
  };
  if (groupBy === "none") {
    return [{ key: "all", label: "", rows: indexes.sort(compare) }];
  }
  const groups = new Map<string, number[]>();
  for (const i of indexes) {
    const key = endpointGroupKey(grid.conditions[i].endpoint_slug);
    const list = groups.get(key);
    if (list) list.push(i);
    else groups.set(key, [i]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => {
      if (a === OPENROUTER_GROUP) return 1;
      if (b === OPENROUTER_GROUP) return -1;
      return a.localeCompare(b);
    })
    .map(([key, rows]) => ({
      key,
      label: key === OPENROUTER_GROUP ? "OpenRouter" : key,
      rows: rows.sort(compare),
    }));
}
