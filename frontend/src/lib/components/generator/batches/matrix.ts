/**
 * Results matrix for one batch: rows = task (app spec; free-form → "Task"),
 * columns = condition, cells = that condition's trials on that task.
 *
 * Column identity is the experiment condition when the job has one (two
 * conditions may share model and harness preset but differ in profile or
 * param overrides, so they must not share a column); otherwise model ×
 * harness preset (agent) or model × profile (scaffolding).
 *
 * Aggregates follow the validity rules: excluded trials and trials replaced
 * by a retry never count; "k/n passed" only counts trials with a conformance
 * result (failed runs are reported next to it, not folded into n).
 */
import type { GenerationJobList, JobPhase } from "$lib/api/generation";
import type {
  CellCode,
  ExperimentResults,
  ResultsMatrixRun,
} from "$lib/api/types/experiments";
import {
  isGradedOutcome,
  presetRefLabel,
  trialOutcome,
  type TrialOutcome,
} from "./batch-utils";

export interface MatrixRow {
  key: string;
  label: string;
  /** App spec slug; null for a free-form task. */
  slug: string | null;
}

export interface MatrixColumn {
  key: string;
  /** Model display name. */
  model: string;
  /** OpenRouter id, e.g. "openai/gpt-5". */
  modelId: string | null;
  /** Harness preset / harness / profile; "" when the mode has no second axis. */
  variant: string;
  conditionId: number | null;
}

export interface MatrixTrial {
  job: GenerationJobList;
  /** "Trial 2" (repeat_index + 1) or "#2" when the run has no repeat index. */
  label: string;
  outcome: TrialOutcome;
  /** A failed/cancelled attempt that a later retry of the same trial replaced. */
  superseded: boolean;
}

export interface CellStats {
  /** Trials that count (not superseded). */
  total: number;
  active: number;
  finished: number;
  failed: number;
  cancelled: number;
  /** Trials with a conformance result, excluding validity "exclude". */
  graded: number;
  passed: number;
  /** Mean pass rate over graded trials with a pass rate; null when none. */
  meanPassRate: number | null;
  warn: number;
  excluded: number;
  superseded: number;
  /** Trials of one cell ran under more than one seed-free configuration. */
  mixedConfigs: boolean;
}

export interface MatrixCell {
  key: string;
  row: MatrixRow;
  column: MatrixColumn;
  trials: MatrixTrial[];
  stats: CellStats;
}

export interface ResultsMatrix {
  rows: MatrixRow[];
  columns: MatrixColumn[];
  cells: Map<string, MatrixCell>;
  /** Per-column aggregate over every task. */
  columnStats: Map<string, CellStats>;
}

const FREE_FORM_ROW = "__task__";

export function cellKey(rowKey: string, columnKey: string): string {
  return `${rowKey}::${columnKey}`;
}

function rowFor(job: GenerationJobList): MatrixRow {
  if (!job.app_slug && !job.template_name) {
    return { key: FREE_FORM_ROW, label: "Task", slug: null };
  }
  const key = job.app_slug ?? `name:${job.template_name}`;
  return {
    key,
    label: job.template_name || job.app_slug || "Task",
    slug: job.app_slug ?? null,
  };
}

function variantFor(job: GenerationJobList, mode: string): string {
  if (mode === "agent") {
    return presetRefLabel(job.harness_preset) || job.harness || "";
  }
  if (mode === "scaffolding") return job.profile_ref || "Auto profile";
  return "";
}

function columnFor(job: GenerationJobList, mode: string): MatrixColumn {
  const modelId = job.model_id_str ?? null;
  const model = job.model_name || modelId || "Unknown model";
  const variant = variantFor(job, mode);
  if (job.condition_id != null) {
    return {
      key: `c:${job.condition_id}`,
      model,
      modelId,
      variant,
      conditionId: job.condition_id,
    };
  }
  let axis = "";
  if (mode === "agent") {
    axis = job.harness_preset
      ? `p:${job.harness_preset.id}`
      : `h:${job.harness ?? ""}`;
  } else if (mode === "scaffolding") {
    axis = `pr:${job.profile_ref ?? ""}`;
  }
  return {
    key: `m:${modelId ?? model}|${axis}`,
    model,
    modelId,
    variant,
    conditionId: null,
  };
}

function byTrialOrder(a: GenerationJobList, b: GenerationJobList): number {
  const ra = a.repeat_index ?? Number.MAX_SAFE_INTEGER;
  const rb = b.repeat_index ?? Number.MAX_SAFE_INTEGER;
  if (ra !== rb) return ra - rb;
  return a.created_at.localeCompare(b.created_at);
}

/** Retry keeps the repeat index; the older failed/cancelled attempt no longer counts. */
function markSuperseded(jobs: GenerationJobList[]): Set<string> {
  const superseded = new Set<string>();
  const byRepeat = new Map<number, GenerationJobList[]>();
  for (const job of jobs) {
    if (job.repeat_index == null) continue;
    const list = byRepeat.get(job.repeat_index) ?? [];
    list.push(job);
    byRepeat.set(job.repeat_index, list);
  }
  for (const attempts of byRepeat.values()) {
    if (attempts.length < 2) continue;
    const newest = attempts.reduce((a, b) =>
      b.created_at > a.created_at ? b : a,
    );
    for (const job of attempts) {
      if (
        job !== newest &&
        (job.status === "failed" || job.status === "cancelled")
      ) {
        superseded.add(job.id);
      }
    }
  }
  return superseded;
}

export function emptyStats(): CellStats {
  return {
    total: 0,
    active: 0,
    finished: 0,
    failed: 0,
    cancelled: 0,
    graded: 0,
    passed: 0,
    meanPassRate: null,
    warn: 0,
    excluded: 0,
    superseded: 0,
    mixedConfigs: false,
  };
}

/** Aggregate trials (superseded ones are only counted as such). */
export function computeStats(trials: MatrixTrial[]): CellStats {
  const stats = emptyStats();
  const fingerprints = new Set<string>();
  let rateSum = 0;
  let rateCount = 0;
  for (const trial of trials) {
    if (trial.superseded) {
      stats.superseded += 1;
      continue;
    }
    const { job } = trial;
    stats.total += 1;
    if (job.status === "pending" || job.status === "running") stats.active += 1;
    else stats.finished += 1;
    if (job.status === "failed") stats.failed += 1;
    if (job.status === "cancelled") stats.cancelled += 1;
    if (job.validity === "warn") stats.warn += 1;
    if (job.validity === "exclude") stats.excluded += 1;
    if (job.config_fingerprint) fingerprints.add(job.config_fingerprint);
    if (isGradedOutcome(job)) {
      stats.graded += 1;
      if (job.eval_status === "passed") stats.passed += 1;
      if (job.pass_rate != null) {
        rateSum += job.pass_rate;
        rateCount += 1;
      }
    }
  }
  stats.meanPassRate = rateCount ? rateSum / rateCount : null;
  stats.mixedConfigs = fingerprints.size > 1;
  return stats;
}

function compareColumns(a: MatrixColumn, b: MatrixColumn): number {
  return (
    a.model.localeCompare(b.model) ||
    a.variant.localeCompare(b.variant) ||
    (a.conditionId ?? 0) - (b.conditionId ?? 0)
  );
}

function compareRows(a: MatrixRow, b: MatrixRow): number {
  if (a.key === FREE_FORM_ROW) return -1;
  if (b.key === FREE_FORM_ROW) return 1;
  return a.label.localeCompare(b.label);
}

export function buildResultsMatrix(
  jobs: GenerationJobList[],
  mode: string,
  graded: boolean,
): ResultsMatrix {
  const rows = new Map<string, MatrixRow>();
  const columns = new Map<string, MatrixColumn>();
  const grouped = new Map<
    string,
    { row: MatrixRow; column: MatrixColumn; jobs: GenerationJobList[] }
  >();

  for (const job of jobs) {
    const row = rowFor(job);
    const column = columnFor(job, mode);
    if (!rows.has(row.key)) rows.set(row.key, row);
    if (!columns.has(column.key)) columns.set(column.key, column);
    const key = cellKey(row.key, column.key);
    const entry = grouped.get(key) ?? {
      row: rows.get(row.key)!,
      column: columns.get(column.key)!,
      jobs: [],
    };
    entry.jobs.push(job);
    grouped.set(key, entry);
  }

  const cells = new Map<string, MatrixCell>();
  const columnTrials = new Map<string, MatrixTrial[]>();
  for (const [key, entry] of grouped) {
    const sorted = [...entry.jobs].sort(byTrialOrder);
    const superseded = markSuperseded(sorted);
    const trials = sorted.map((job, i) => ({
      job,
      label:
        job.repeat_index != null
          ? `Trial ${job.repeat_index + 1}`
          : `#${i + 1}`,
      outcome: trialOutcome(job, graded),
      superseded: superseded.has(job.id),
    }));
    cells.set(key, {
      key,
      row: entry.row,
      column: entry.column,
      trials,
      stats: computeStats(trials),
    });
    const list = columnTrials.get(entry.column.key) ?? [];
    list.push(...trials);
    columnTrials.set(entry.column.key, list);
  }

  const columnStats = new Map<string, CellStats>();
  for (const [key, trials] of columnTrials)
    columnStats.set(key, computeStats(trials));

  return {
    rows: [...rows.values()].sort(compareRows),
    columns: [...columns.values()].sort(compareColumns),
    cells,
    columnStats,
  };
}

/** First job in matrix order — the template for "New batch like this". */
export function representativeJob(
  matrix: ResultsMatrix,
): GenerationJobList | null {
  for (const row of matrix.rows) {
    for (const column of matrix.columns) {
      const cell = matrix.cells.get(cellKey(row.key, column.key));
      const trial = cell?.trials.find((t) => !t.superseded) ?? cell?.trials[0];
      if (trial) return trial.job;
    }
  }
  return null;
}

// ── Experiment results (server-computed) ──────────────────────────────

/** Minimal condition info for column headers (from the leaderboard or the detail). */
export interface ResultsConditionInfo {
  id: number;
  label: string;
  model: string;
  modelId?: string | null;
  /** Harness preset / profile; "" when unknown. */
  variant?: string;
}

const RUN_STATUS: Record<CellCode, { status: string; phase?: JobPhase }> = {
  p: { status: "pending" },
  b: { status: "pending" },
  w: { status: "pending" },
  r: { status: "running", phase: "running" },
  q: { status: "running", phase: "eval_queued" },
  e: { status: "running", phase: "evaluating" },
  c: { status: "completed" },
  f: { status: "completed" },
  u: { status: "completed" },
  x: { status: "failed" },
  k: { status: "cancelled" },
};

/** One results run as a job list item, so the matrix renders it like any trial. */
export function jobFromResultsRun(
  run: ResultsMatrixRun,
  app: { slug: string; name: string },
  condition: ResultsConditionInfo,
): GenerationJobList {
  const state = RUN_STATUS[run.code] ?? { status: "pending" };
  return {
    id: run.job_id,
    mode: "",
    status: state.status,
    phase: state.phase ?? "",
    model_name: condition.model,
    model_id_str: condition.modelId ?? null,
    template_name: app.name,
    app_slug: app.slug,
    started_at: null,
    completed_at: null,
    duration_seconds: null,
    error_message: "",
    created_at: "",
    condition_id: condition.id,
    repeat_index: run.repeat_index,
    eval_status:
      run.code === "c" ? "passed" : run.code === "f" ? "failed" : null,
    pass_rate: run.pass_rate,
    termination: run.termination,
    validity:
      run.valid === false ? "exclude" : run.valid === true ? "valid" : null,
  };
}

/**
 * The Results tab's matrix from `results.matrix` (spec §G.5): rows = apps,
 * columns = conditions (leaderboard order). k/n and means are the server's,
 * so the matrix applies exactly the leaderboard's filters (common cells,
 * exclusions); the trial dots come from the runs.
 */
export function fromResultsMatrix(
  results: Pick<ExperimentResults, "matrix" | "leaderboard">,
  conditions: readonly ResultsConditionInfo[] = [],
): ResultsMatrix {
  const info = new Map<number, ResultsConditionInfo>();
  for (const c of conditions) info.set(c.id, c);
  for (const row of results.leaderboard) {
    if (!info.has(row.condition_id)) {
      info.set(row.condition_id, {
        id: row.condition_id,
        label: row.label,
        model: row.model.name,
        variant: row.harness,
      });
    }
  }
  const conditionOf = (id: number): ResultsConditionInfo =>
    info.get(id) ?? {
      id,
      label: `Condition #${id}`,
      model: `Condition #${id}`,
    };

  const order = new Map(results.leaderboard.map((r, i) => [r.condition_id, i]));
  const conditionIds = [
    ...new Set([
      ...results.leaderboard.map((r) => r.condition_id),
      ...results.matrix.cells.map((c) => c.condition_id),
    ]),
  ].sort((a, b) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9) || a - b);

  const columns: MatrixColumn[] = conditionIds.map((id) => {
    const c = conditionOf(id);
    return {
      key: `c:${id}`,
      model: c.model,
      modelId: c.modelId ?? null,
      variant: c.variant ?? "",
      conditionId: id,
    };
  });
  const columnByCondition = new Map(columns.map((c) => [c.conditionId!, c]));
  const rows: MatrixRow[] = results.matrix.apps.map((a) => ({
    key: `a:${a.id}`,
    label: a.name || a.slug,
    slug: a.slug,
  }));
  const rowByApp = new Map(results.matrix.apps.map((a, i) => [a.id, rows[i]]));
  const appById = new Map(results.matrix.apps.map((a) => [a.id, a]));

  const cells = new Map<string, MatrixCell>();
  const columnTrials = new Map<string, MatrixTrial[]>();
  for (const cell of results.matrix.cells) {
    const row = rowByApp.get(cell.app_id);
    const column = columnByCondition.get(cell.condition_id);
    const app = appById.get(cell.app_id);
    if (!row || !column || !app) continue;
    const condition = conditionOf(cell.condition_id);
    const trials: MatrixTrial[] = [...cell.runs]
      .sort((a, b) => a.repeat_index - b.repeat_index)
      .map((run) => {
        const job = jobFromResultsRun(run, app, condition);
        return {
          job,
          label: `Trial ${run.repeat_index + 1}`,
          outcome: trialOutcome(job, true),
          superseded: false,
        };
      });
    const stats = computeStats(trials);
    stats.graded = cell.n;
    stats.passed = cell.k;
    stats.meanPassRate = cell.mean;
    const key = cellKey(row.key, column.key);
    cells.set(key, { key, row, column, trials, stats });
    const list = columnTrials.get(column.key) ?? [];
    list.push(...trials);
    columnTrials.set(column.key, list);
  }

  const columnStats = new Map<string, CellStats>();
  for (const column of columns) {
    const stats = computeStats(columnTrials.get(column.key) ?? []);
    const lb = results.leaderboard.find(
      (r) => r.condition_id === column.conditionId,
    );
    if (lb) {
      stats.graded = lb.n_graded;
      stats.passed = lb.k_passed;
      stats.meanPassRate = lb.pass_rate_mean;
    }
    columnStats.set(column.key, stats);
  }
  return { rows, columns, cells, columnStats };
}
