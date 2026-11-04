/**
 * StudyLive — live data of one study's Operations section (spec 10.3 §6, 10.7; port of the v2
 * experiments `live.svelte.ts`).
 *
 * - On start: the study, its cells and its live spend (`GET /budget/studies/{id}/`), then one SSE
 *   connection to `study:<id>`.
 * - Events patch the store in place: `run` moves a cell's current run (and its count bucket), `tick`
 *   replaces the rollups, `spend` updates spent / list / reserved (the strip never waits for a poll),
 *   `budget_alert` marks a crossed threshold, `state` / `paused` / `stopped` update the status and refetch
 *   the study, `post_run` goes to the page.
 * - Fallback: while SSE is reconnecting, or nothing arrived for 20 s while runs are in flight, the study and
 *   the budget are polled every 5 s and the cells every 15 s. A hidden tab does not poll; becoming visible
 *   resyncs. Once the study is terminal and nothing is in flight, the connection closes.
 *
 * Every dependency is injectable so the patching and fallback logic is unit-tested.
 */
import { formatApiError } from "$lib/api/core";
import { subscribe as sseSubscribe, type SseEvent } from "$lib/api/sse";
import { getStudyBudget } from "$lib/api/v3/budget";
import { getStudy, getStudyCells } from "$lib/api/v3/studies";
import type {
  StudyBudgetOut,
  StudyCellOut,
  StudyCellsPayload,
  StudyEvent,
  StudyOut,
  StudyPostRunEvent,
  StudyRunEvent,
  StudyStatus,
  StudyTickEvent,
} from "$lib/api/v3/types";
import { isTerminal } from "../format";

export type LiveConnection =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

export interface ActivityItem {
  key: string;
  ts: string;
  runId: string;
  cellId: number | null;
  conditionId: number | null;
  unitKey: string | null;
  repeat: number | null;
  status: string;
  outcome: string;
  attempt: number;
}

export interface LiveDeps {
  getStudy: (
    id: string,
    options?: { signal?: AbortSignal },
  ) => Promise<StudyOut>;
  getCells: (
    id: string,
    options?: { signal?: AbortSignal },
  ) => Promise<StudyCellsPayload>;
  getBudget: (
    id: string,
    options?: { signal?: AbortSignal },
  ) => Promise<StudyBudgetOut>;
  subscribe: (
    channels: string[],
    onEvent: (e: SseEvent) => void,
    options: { onState?: (s: "open" | "reconnecting" | "closed") => void },
  ) => () => void;
  now: () => number;
  hidden: () => boolean;
}

const defaultDeps: LiveDeps = {
  getStudy: (id, options) => getStudy(id, options),
  getCells: (id, options) => getStudyCells(id, {}, options),
  getBudget: (id, options) => getStudyBudget(id, options),
  subscribe: sseSubscribe,
  now: () => Date.now(),
  hidden: () => typeof document !== "undefined" && document.hidden,
};

export const SILENCE_MS = 20_000;
export const STATUS_POLL_MS = 5_000;
export const CELLS_POLL_MS = 15_000;
export const TICKER_MS = 1_000;
export const ACTIVITY_LIMIT = 50;
/** A burn-rate window: spend deltas of the last 10 minutes. */
export const BURN_WINDOW_MS = 10 * 60_000;

export interface LiveCallbacks {
  onState?: (status: StudyStatus) => void;
  onPostRun?: (event: StudyPostRunEvent) => void;
  onTick?: () => void;
}

const IN_FLIGHT_CELL_STATES = new Set(["queued", "running"]);
const ACTIVE_RUN_STATUSES = new Set(["pending", "running"]);

/** A cell whose current run is still being generated or evaluated. */
export function cellInFlight(cell: StudyCellOut): boolean {
  if (IN_FLIGHT_CELL_STATES.has(cell.state)) return true;
  return (
    cell.current_run !== null &&
    ACTIVE_RUN_STATUSES.has(cell.current_run.status)
  );
}

/** The cell state a `run` event implies (the server's state machine, simplified for display). */
export function cellStateForRun(
  event: Pick<StudyRunEvent, "status" | "outcome">,
  previous: string,
): string {
  if (event.status === "pending") return "queued";
  if (event.status === "running") return "running";
  if (event.status === "cancelled")
    return previous === "done" ? "done" : "excluded";
  if (event.outcome === "infra_error" || event.status === "failed")
    return "infra_pending";
  if (event.outcome === "excluded") return "excluded";
  if (event.status === "completed") return "done";
  return previous;
}

/** Apply a `run` event to a cells payload (pure; returns null when the cell is unknown). */
export function applyRunEvent(
  payload: StudyCellsPayload,
  event: StudyRunEvent,
): StudyCellsPayload | null {
  const index = payload.cells.findIndex((c) => c.id === event.cell_id);
  if (index < 0) return null;
  const cell = payload.cells[index];
  const state = cellStateForRun(event, cell.state);
  const run = {
    id: event.run_id,
    status: event.status,
    phase: event.phase,
    outcome: event.outcome,
    outcome_reason:
      cell.current_run?.id === event.run_id
        ? cell.current_run.outcome_reason
        : "",
    usable:
      cell.current_run?.id === event.run_id ? cell.current_run.usable : null,
    attempt: event.attempt,
    cell_attempt:
      cell.current_run?.id === event.run_id
        ? cell.current_run.cell_attempt
        : cell.attempts,
  };
  const next: StudyCellOut = {
    ...cell,
    state,
    outcome: event.outcome || cell.outcome,
    current_run: run,
  };
  const cells = payload.cells.slice();
  cells[index] = next;
  const counts = { ...payload.counts };
  if (state !== cell.state) {
    counts[cell.state] = Math.max(0, (counts[cell.state] ?? 0) - 1);
    counts[state] = (counts[state] ?? 0) + 1;
  }
  return { ...payload, cells, counts };
}

export class StudyLive {
  readonly id: string;
  study = $state.raw<StudyOut | null>(null);
  cells = $state.raw<StudyCellsPayload | null>(null);
  budget = $state.raw<StudyBudgetOut | null>(null);
  tick = $state.raw<StudyTickEvent | null>(null);
  activity = $state.raw<ActivityItem[]>([]);
  /** Highest budget alert fraction crossed since load (from `budget_alert`). */
  alertFraction = $state<number | null>(null);
  connection = $state<LiveConnection>("idle");
  polling = $state(false);
  loading = $state(false);
  error = $state("");
  lastEventAt = $state(0);

  private deps: LiveDeps;
  private callbacks: LiveCallbacks;
  private unsubscribe: (() => void) | null = null;
  private ticker: ReturnType<typeof setInterval> | null = null;
  private lastStatusPoll = 0;
  private lastCellsPoll = 0;
  private statusInFlight = false;
  private cellsInFlight = false;
  private destroyed = false;
  private stopped = false;
  private seq = 0;
  private spendSamples: { at: number; spent: number }[] = [];
  private onVisibility = () => {
    if (!this.deps.hidden()) void this.resync();
  };

  constructor(
    id: string,
    callbacks: LiveCallbacks = {},
    deps: Partial<LiveDeps> = {},
  ) {
    this.id = id;
    this.callbacks = callbacks;
    this.deps = { ...defaultDeps, ...deps };
  }

  get inFlight(): boolean {
    return (this.cells?.cells ?? []).some(cellInFlight);
  }

  /** USD per hour over the last 10 minutes of `spend` events (null before two samples). */
  get liveBurnRate(): number | null {
    const samples = this.spendSamples;
    if (samples.length < 2) return null;
    const first = samples[0];
    const last = samples[samples.length - 1];
    const hours = (last.at - first.at) / 3_600_000;
    return hours > 0 ? Math.max(0, (last.spent - first.spent) / hours) : null;
  }

  async start(): Promise<void> {
    this.loading = true;
    await Promise.all([this.loadStudy(), this.loadCells(), this.loadBudget()]);
    this.loading = false;
    if (this.destroyed) return;
    this.connect();
    if (typeof document !== "undefined")
      document.addEventListener("visibilitychange", this.onVisibility);
    this.ticker = setInterval(() => this.onTicker(), TICKER_MS);
  }

  /** Resume after a top-up, resume or expand (re-opens the stream if it was closed). */
  restart(): void {
    this.stopped = false;
    void this.resync();
    if (!this.unsubscribe) this.connect();
  }

  destroy(): void {
    this.destroyed = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    if (this.ticker !== null) clearInterval(this.ticker);
    this.ticker = null;
    if (typeof document !== "undefined")
      document.removeEventListener("visibilitychange", this.onVisibility);
    this.connection = "closed";
  }

  async resync(): Promise<void> {
    await Promise.all([this.loadStudy(), this.loadCells(), this.loadBudget()]);
  }

  // ── event handling (public for tests) ────────────────────────────────

  handle(event: SseEvent): void {
    this.lastEventAt = this.deps.now();
    const data = {
      ...event.data,
      type: (event.data.type as string) ?? event.type,
    } as unknown as StudyEvent;
    switch (data.type) {
      case "run":
        this.onRun(data);
        break;
      case "tick":
        this.tick = data;
        this.callbacks.onTick?.();
        break;
      case "state":
        this.patchStudy({
          status: data.status,
          pause_reason: data.pause_reason,
        });
        this.callbacks.onState?.(data.status);
        void this.loadStudy();
        break;
      case "paused":
        if (data.condition_id == null)
          this.patchStudy({ status: "paused", pause_reason: data.reason });
        void this.loadStudy();
        void this.loadBudget();
        break;
      case "stopped":
        this.patchStudy({ status: "stopped" });
        void this.loadStudy();
        void this.loadBudget();
        break;
      case "spend":
        this.onSpend(data.spent, data.spent_list, data.reserved);
        break;
      case "budget_alert":
        this.alertFraction = Math.max(this.alertFraction ?? 0, data.fraction);
        break;
      case "post_run":
        this.callbacks.onPostRun?.(data);
        break;
    }
  }

  private onRun(event: StudyRunEvent): void {
    if (this.cells && event.cell_id != null) {
      const next = applyRunEvent(this.cells, event);
      if (next) this.cells = next;
      else void this.loadCells();
    }
    const item: ActivityItem = {
      key: `${event.run_id}:${event.status}:${event.phase}:${event.attempt}:${++this.seq}`,
      ts: event.ts ?? new Date(this.deps.now()).toISOString(),
      runId: event.run_id,
      cellId: event.cell_id,
      conditionId: event.condition_id,
      unitKey: event.unit_key,
      repeat: event.repeat_index,
      status: event.status,
      outcome: event.outcome,
      attempt: event.attempt,
    };
    this.activity = [item, ...this.activity].slice(0, ACTIVITY_LIMIT);
  }

  private onSpend(spent: number, spentList: number, reserved: number): void {
    const now = this.deps.now();
    this.spendSamples = [
      ...this.spendSamples.filter((s) => now - s.at <= BURN_WINDOW_MS),
      { at: now, spent },
    ];
    const budget = this.budget;
    if (budget) {
      const cap = budget.cap;
      this.budget = {
        ...budget,
        spent,
        spent_list: spentList,
        reserved,
        remaining: cap !== null ? cap - spent - reserved : budget.remaining,
      };
    }
    if (this.study)
      this.study = {
        ...this.study,
        spent_usd: spent,
        spent_list_usd: spentList,
        reserved_usd: reserved,
      };
  }

  private patchStudy(patch: Partial<StudyOut>): void {
    if (this.study) this.study = { ...this.study, ...patch };
  }

  // ── loading and fallback ─────────────────────────────────────────────

  private connect(): void {
    if (this.destroyed || this.stopped) return;
    this.connection = "connecting";
    this.unsubscribe = this.deps.subscribe(
      [`study:${this.id}`],
      (e) => this.handle(e),
      {
        onState: (s) => {
          if (this.destroyed) return;
          this.connection =
            s === "open"
              ? "open"
              : s === "reconnecting"
                ? "reconnecting"
                : "closed";
        },
      },
    );
  }

  private quietTooLong(): boolean {
    const now = this.deps.now();
    return (
      this.inFlight &&
      this.study?.status === "running" &&
      now - this.lastEventAt > SILENCE_MS
    );
  }

  /** Once a second: decide whether to poll, and close the stream when nothing can change any more. */
  onTicker(): void {
    if (this.destroyed || this.deps.hidden()) return;
    const status = this.study?.status;
    if (status && isTerminal(status) && !this.inFlight) {
      this.stopped = true;
      this.unsubscribe?.();
      this.unsubscribe = null;
      this.polling = false;
      this.connection = "closed";
      return;
    }
    const fallback =
      this.connection === "reconnecting" ||
      this.quietTooLong() ||
      this.study === null;
    this.polling = fallback;
    if (!fallback) return;
    const now = this.deps.now();
    if (now - this.lastStatusPoll >= STATUS_POLL_MS) {
      this.lastStatusPoll = now;
      void this.loadStudy();
      void this.loadBudget();
    }
    if (now - this.lastCellsPoll >= CELLS_POLL_MS) {
      this.lastCellsPoll = now;
      void this.loadCells();
    }
  }

  private async loadStudy(): Promise<void> {
    if (this.statusInFlight) return;
    this.statusInFlight = true;
    try {
      const study = await this.deps.getStudy(this.id);
      if (this.destroyed) return;
      const before = this.study?.status;
      this.study = study;
      this.error = "";
      if (before && before !== study.status)
        this.callbacks.onState?.(study.status);
    } catch (e) {
      if (!this.destroyed)
        this.error = formatApiError(e, "Could not load the experiment.");
    } finally {
      this.statusInFlight = false;
    }
  }

  private async loadCells(): Promise<void> {
    if (this.cellsInFlight) return;
    this.cellsInFlight = true;
    try {
      const cells = await this.deps.getCells(this.id);
      if (!this.destroyed) this.cells = cells;
    } catch {
      // The grid keeps its last state; the study error banner covers outages.
    } finally {
      this.cellsInFlight = false;
    }
  }

  private async loadBudget(): Promise<void> {
    try {
      const budget = await this.deps.getBudget(this.id);
      if (!this.destroyed) this.budget = budget;
    } catch {
      // The spend strip shows "—" for what it does not know.
    }
  }
}
