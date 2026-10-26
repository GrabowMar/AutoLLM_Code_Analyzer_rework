/**
 * ExperimentLive — the live monitor's data for one experiment (spec §G.5).
 *
 * - On start: `getStatus` + `getCells`, then one SSE connection to
 *   `experiment:<id>` plus the endpoints it uses (≤ 20 channels).
 * - `job` events patch the cell grid in O(1) and move the cell between count
 *   buckets; `tick` replaces the rollups; `state` refetches (and tells the
 *   page); `post_run` updates the post-run progress.
 * - Fallback: while SSE is reconnecting, or no event arrived for 20 s while
 *   cells are in flight (status `running` with running / evaluating cells),
 *   `status` is polled every 5 s and `cells` every 15 s. A paused experiment,
 *   or one only waiting on offline endpoints, is legitimately quiet (ticks are
 *   sent only on change; the heartbeat is an SSE comment), so silence there
 *   does not count. Until the first status load succeeds, status is retried
 *   every 5 s. A hidden tab does not poll; becoming visible resyncs.
 * - Stops (closes SSE, no polling) once the experiment is terminal and no
 *   cell is in flight; `restart()` resumes after a top-up or resume.
 *
 * Every dependency is injectable so the fallback logic is unit-tested.
 */
import {
  getCells as apiGetCells,
  getStatus as apiGetStatus,
} from "$lib/api/experiments";
import { subscribe as sseSubscribe, type SseEvent } from "$lib/api/sse";
import type {
  EndpointChannelEvent,
  JobEvent,
  PostRunEvent,
  StateEvent,
  TickEvent,
} from "$lib/api/types/events";
import type {
  CellCode,
  CellsPayload,
  ExperimentStatusV2Payload,
  StatusEndpoint,
} from "$lib/api/types/experiments";
import { formatApiError } from "$lib/api/core";
import {
  adjustCounts,
  applyJobEvent,
  buildGrid,
  codeForJobEvent,
  type CellGrid,
} from "./cells";
import { activeCells, isTerminal } from "./format";

export type LiveConnection =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

export interface ActivityItem {
  /** Unique per event (list key). */
  key: string;
  ts: string;
  jobId: string;
  conditionId: number | null;
  appId: number | null;
  repeat: number | null;
  code: CellCode;
  /** 0-based like `GenerationJob.attempt` (0 = first try). */
  attempt: number;
  /** A retry (attempt > 0) re-entering the queue or running again. */
  retry: boolean;
}

export interface LiveDeps {
  getStatus: (
    id: string,
    options?: { signal?: AbortSignal },
  ) => Promise<ExperimentStatusV2Payload>;
  getCells: (
    id: string,
    options?: { signal?: AbortSignal },
  ) => Promise<CellsPayload>;
  subscribe: (
    channels: string[],
    onEvent: (e: SseEvent) => void,
    options: { onState?: (s: "open" | "reconnecting" | "closed") => void },
  ) => () => void;
  now: () => number;
  /** Whether the page is hidden (no polling then). */
  hidden: () => boolean;
}

const defaultDeps: LiveDeps = {
  getStatus: apiGetStatus,
  getCells: apiGetCells,
  subscribe: sseSubscribe,
  now: () => Date.now(),
  hidden: () => typeof document !== "undefined" && document.hidden,
};

export const SILENCE_MS = 20_000;
export const STATUS_POLL_MS = 5_000;
export const CELLS_POLL_MS = 15_000;
export const TICKER_MS = 1_000;
export const MAX_CHANNELS = 20;
export const ACTIVITY_LIMIT = 50;

export interface LiveCallbacks {
  /** A `state` event (status / pause reason changed): refetch the detail. */
  onState?: (event: StateEvent) => void;
  onPostRun?: (event: PostRunEvent) => void;
  /** A `tick` arrived (Results may refetch, throttled by the page). */
  onTick?: () => void;
}

export class ExperimentLive {
  readonly id: string;
  status = $state.raw<ExperimentStatusV2Payload | null>(null);
  grid = $state<CellGrid | null>(null);
  activity = $state.raw<ActivityItem[]>([]);
  connection = $state<LiveConnection>("idle");
  /** Polling instead of (or in addition to) SSE right now. */
  polling = $state(false);
  loading = $state(false);
  error = $state("");
  lastEventAt = $state(0);

  private deps: LiveDeps;
  private callbacks: LiveCallbacks;
  private unsubscribe: (() => void) | null = null;
  private channelsKey = "";
  private ticker: ReturnType<typeof setInterval> | null = null;
  private lastStatusPoll = 0;
  private lastCellsPoll = 0;
  private statusInFlight = false;
  private cellsInFlight = false;
  private destroyed = false;
  private stopped = false;
  private seq = 0;
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

  /** Terminal and nothing in flight: nothing will change without a user action. */
  get settled(): boolean {
    const s = this.status;
    return !!s && isTerminal(s.status) && activeCells(s.counts) === 0;
  }

  async start(): Promise<void> {
    this.destroyed = false;
    this.stopped = false;
    this.loading = true;
    this.lastEventAt = this.deps.now();
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this.onVisibility);
    }
    await Promise.all([this.refreshStatus(), this.refreshCells()]);
    this.loading = false;
    if (this.destroyed) return;
    this.ensureLive();
  }

  /** Resume live updates after an action that may reopen the experiment. */
  async restart(): Promise<void> {
    this.stopped = false;
    await this.resync();
    this.ensureLive();
  }

  /** Refetch status and cells now (visibility change, manual refresh). */
  async resync(): Promise<void> {
    await Promise.all([this.refreshStatus(), this.refreshCells()]);
    this.ensureLive();
  }

  destroy(): void {
    this.destroyed = true;
    this.teardown();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.onVisibility);
    }
    this.connection = "closed";
  }

  // ── fetching ──────────────────────────────────────────────────────

  async refreshStatus(): Promise<void> {
    if (this.statusInFlight) return;
    this.statusInFlight = true;
    this.lastStatusPoll = this.deps.now();
    try {
      const next = await this.deps.getStatus(this.id);
      if (this.destroyed) return;
      this.status = next;
      this.error = "";
    } catch (e) {
      // A failed refresh keeps the last render.
      this.error = errorText(e);
    } finally {
      this.statusInFlight = false;
    }
  }

  async refreshCells(): Promise<void> {
    if (this.cellsInFlight) return;
    this.cellsInFlight = true;
    this.lastCellsPoll = this.deps.now();
    try {
      const payload = await this.deps.getCells(this.id);
      if (this.destroyed) return;
      this.grid = buildGrid(payload);
    } catch (e) {
      this.error = errorText(e);
    } finally {
      this.cellsInFlight = false;
    }
  }

  // ── live wiring ───────────────────────────────────────────────────

  private channels(): string[] {
    const ids = (this.status?.endpoints ?? []).map((e) => e.id);
    return [
      `experiment:${this.id}`,
      ...ids.slice(0, MAX_CHANNELS - 1).map((id) => `endpoint:${id}`),
    ];
  }

  private ensureLive(): void {
    if (this.destroyed) return;
    if (this.settled) {
      this.stopped = true;
      this.teardown();
      this.connection = "closed";
      return;
    }
    const channels = this.channels();
    const key = channels.join(",");
    if (!this.unsubscribe || key !== this.channelsKey) {
      this.unsubscribe?.();
      this.channelsKey = key;
      this.connection = "connecting";
      this.unsubscribe = this.deps.subscribe(channels, (e) => this.handle(e), {
        onState: (s) => {
          if (this.unsubscribe === null && s === "closed") return;
          this.connection = s;
          if (s === "open") this.lastEventAt = this.deps.now();
        },
      });
    }
    if (!this.ticker) {
      this.ticker = setInterval(() => this.onTicker(), TICKER_MS);
    }
  }

  private teardown(): void {
    const unsubscribe = this.unsubscribe;
    this.unsubscribe = null;
    this.channelsKey = "";
    unsubscribe?.();
    if (this.ticker) clearInterval(this.ticker);
    this.ticker = null;
    this.polling = false;
  }

  /** Every second: decide whether the polling fallback runs. */
  onTicker(): void {
    if (this.destroyed || this.stopped) return;
    const now = this.deps.now();
    if (!this.status) {
      // The first status load failed: keep retrying it (events need a status).
      this.polling = false;
      if (this.deps.hidden()) return;
      if (now - this.lastStatusPoll >= STATUS_POLL_MS) {
        void this.refreshStatus().then(() => this.ensureLive());
      }
      if (!this.grid && now - this.lastCellsPoll >= CELLS_POLL_MS) {
        void this.refreshCells();
      }
      return;
    }
    const silent = this.expectsEvents() && now - this.lastEventAt > SILENCE_MS;
    const fallback = this.connection === "reconnecting" || silent;
    this.polling = fallback;
    if (!fallback || this.deps.hidden()) return;
    if (now - this.lastStatusPoll >= STATUS_POLL_MS) {
      void this.refreshStatus().then(() => this.ensureLive());
    }
    if (now - this.lastCellsPoll >= CELLS_POLL_MS) void this.refreshCells();
  }

  /** Running with cells in flight: job / tick events are due, silence is suspect. */
  private expectsEvents(): boolean {
    const s = this.status;
    if (!s || this.settled || s.status !== "running") return false;
    const c = s.counts;
    return (c.running ?? 0) + (c.eval_queued ?? 0) + (c.evaluating ?? 0) > 0;
  }

  // ── events ────────────────────────────────────────────────────────

  handle(e: SseEvent): void {
    if (this.destroyed) return;
    this.lastEventAt = this.deps.now();
    const data = e.data as Record<string, unknown>;
    const type = (typeof data.type === "string" ? data.type : e.type) as string;
    const channel = typeof data.channel === "string" ? data.channel : "";
    if (channel.startsWith("endpoint:")) {
      this.onEndpointEvent(channel, data as unknown as EndpointChannelEvent);
      return;
    }
    switch (type) {
      case "job":
        this.onJob(data as unknown as JobEvent);
        break;
      case "tick":
        this.onTick(data as unknown as TickEvent);
        break;
      case "state":
        this.onState(data as unknown as StateEvent);
        break;
      case "post_run":
        this.onPostRun(data as unknown as PostRunEvent);
        break;
      case "endpoint":
      case "session":
        this.onEndpointEvent(channel, data as unknown as EndpointChannelEvent);
        break;
    }
  }

  private onJob(event: JobEvent): void {
    const grid = this.grid;
    if (grid) {
      const patch = applyJobEvent(grid, event);
      if (patch && this.status) {
        this.status = {
          ...this.status,
          counts: adjustCounts(this.status.counts, patch),
        };
      }
    }
    const item: ActivityItem = {
      key: `${event.job_id}:${event.attempt}:${++this.seq}`,
      ts: event.ts ?? new Date(this.deps.now()).toISOString(),
      jobId: event.job_id,
      conditionId: event.condition_id,
      appId: event.app_id,
      repeat: event.repeat_index,
      code: codeForJobEvent(event),
      attempt: event.attempt ?? 0,
      retry:
        (event.attempt ?? 0) > 0 &&
        (event.status === "pending" || event.status === "running"),
    };
    this.activity = [item, ...this.activity].slice(0, ACTIVITY_LIMIT);
  }

  private onTick(event: TickEvent): void {
    const status = this.status;
    if (!status) return;
    // The executor's tick carries only what it computes every tick: merge the
    // keys present and keep the last REST values for the rest.
    const byId = new Map((event.endpoints ?? []).map((e) => [e.id, e]));
    const endpoints: StatusEndpoint[] = status.endpoints.map((ep) => {
      const t = byId.get(ep.id);
      if (!t) return ep;
      const next: StatusEndpoint = { ...ep };
      if (t.slots_used !== undefined)
        next.slots = { ...ep.slots, used: t.slots_used };
      if (t.active !== undefined) next.jobs = { ...ep.jobs, active: t.active };
      if (t.queued !== undefined) next.queued_requests = t.queued;
      if (t.tokens_per_s !== undefined) next.tokens_per_s_5m = t.tokens_per_s;
      if (t.blocked_reason !== undefined)
        next.blocked_reason = t.blocked_reason;
      if (t.circuit !== undefined) next.circuit = t.circuit;
      return next;
    });
    this.status = {
      ...status,
      counts: event.counts
        ? { ...status.counts, ...event.counts }
        : status.counts,
      cells: event.cells ? { ...status.cells, ...event.cells } : status.cells,
      throughput: event.throughput ?? status.throughput,
      eta_seconds:
        event.eta_seconds !== undefined
          ? event.eta_seconds
          : status.eta_seconds,
      bottleneck:
        event.bottleneck !== undefined ? event.bottleneck : status.bottleneck,
      endpoints,
    };
    this.callbacks.onTick?.();
  }

  private onState(event: StateEvent): void {
    if (this.status) {
      this.status = {
        ...this.status,
        status: event.status,
        pause_reason: event.pause_reason,
      };
    }
    this.callbacks.onState?.(event);
    // Counts and cells may have moved in bulk (cancel, resume, top-up).
    void this.resync();
  }

  private onPostRun(event: PostRunEvent): void {
    const status = this.status;
    if (status?.latest_post_run && status.latest_post_run.id === event.id) {
      this.status = {
        ...status,
        latest_post_run: {
          ...status.latest_post_run,
          status: event.status,
          progress: event.progress,
        },
      };
    }
    this.callbacks.onPostRun?.(event);
  }

  private onEndpointEvent(channel: string, event: EndpointChannelEvent): void {
    const status = this.status;
    if (!status) return;
    const id = Number(channel.split(":")[1]);
    if (!Number.isFinite(id)) return;
    if (event.type === "session") {
      // A relay (dis)connected: the rollups change; fetch them.
      void this.refreshStatus();
      return;
    }
    const endpoints = status.endpoints.map((ep) => {
      if (ep.id !== id) return ep;
      // The redacted `:public` default variant may carry only `{type, status}`:
      // patch what the event has and keep the rest of the lane.
      const next: StatusEndpoint = { ...ep };
      if (typeof event.status === "string") {
        next.online = event.status === "online" || event.status === "degraded";
        next.health =
          event.status === "online"
            ? "ok"
            : event.status === "degraded"
              ? "degraded"
              : "offline";
      }
      if (typeof event.queued === "number") next.queued_requests = event.queued;
      if ("last_error" in event && event.last_error !== undefined) {
        next.last_error = event.last_error;
      }
      return next;
    });
    this.status = { ...status, endpoints };
  }
}

function errorText(e: unknown): string {
  return formatApiError(e, "Could not refresh the live status.");
}
