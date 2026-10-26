/**
 * RunWatcher — live updates for analysis pages: one SSE connection to the
 * `analysis:<id>` channels of the runs on screen, plus polling.
 *
 * - `channels()` is re-read on `sync()`; the stream reconnects only when the
 *   set changes (at most 20 channels, the server's limit).
 * - While `active()`: `poll()` runs every `slowPollMs` as a safety net even with
 *   SSE open (a long tool is legitimately quiet: the server heartbeat is an SSE
 *   comment), and every `fastPollMs` while SSE is connecting/reconnecting.
 *   A hidden tab doesn't poll; becoming visible polls at once.
 * - `now` ticks every second while active (elapsed clocks).
 *
 * Dependencies are injectable for tests.
 */
import {
  subscribe as sseSubscribe,
  type SseEvent,
  type SseState,
} from "$lib/api/sse";

export type WatchConnection =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

export interface RunWatcherOptions {
  channels: () => string[];
  active: () => boolean;
  onEvent: (event: SseEvent) => void;
  poll: () => Promise<void> | void;
  fastPollMs?: number;
  slowPollMs?: number;
  deps?: Partial<WatcherDeps>;
}

export interface WatcherDeps {
  subscribe: (
    channels: string[],
    onEvent: (e: SseEvent) => void,
    options: { onState?: (s: SseState) => void },
  ) => () => void;
  now: () => number;
  hidden: () => boolean;
}

const MAX_CHANNELS = 20;

export class RunWatcher {
  connection = $state<WatchConnection>("idle");
  now = $state(Date.now());

  private opts: Required<Omit<RunWatcherOptions, "deps">>;
  private deps: WatcherDeps;
  private unsubscribe: (() => void) | null = null;
  private channelsKey = "";
  private ticker: ReturnType<typeof setInterval> | null = null;
  private lastPoll = 0;
  private polling = false;
  private destroyed = false;
  private onVisibility = () => {
    if (!this.deps.hidden()) void this.pollNow();
  };

  constructor(options: RunWatcherOptions) {
    this.opts = {
      fastPollMs: 3_000,
      slowPollMs: 10_000,
      ...options,
    } as Required<Omit<RunWatcherOptions, "deps">>;
    this.deps = {
      subscribe: sseSubscribe,
      now: () => Date.now(),
      hidden: () => typeof document !== "undefined" && document.hidden,
      ...options.deps,
    };
  }

  start(): void {
    this.destroyed = false;
    this.lastPoll = this.deps.now();
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this.onVisibility);
    }
    this.sync();
  }

  /** Re-read `channels()` / `active()`: (re)connect, start or stop the ticker. */
  sync(): void {
    if (this.destroyed) return;
    const active = this.opts.active();
    const channels = active ? this.opts.channels().slice(0, MAX_CHANNELS) : [];
    const key = channels.join(",");
    if (key !== this.channelsKey) {
      this.unsubscribe?.();
      this.unsubscribe = null;
      this.channelsKey = key;
      if (channels.length > 0) {
        this.connection = "connecting";
        this.unsubscribe = this.deps.subscribe(
          channels,
          (e) => this.opts.onEvent(e),
          {
            onState: (s) => {
              this.connection = s;
            },
          },
        );
      } else {
        this.connection = "closed";
      }
    }
    if (active && this.ticker === null) {
      this.ticker = setInterval(() => this.tick(), 1_000);
    } else if (!active && this.ticker !== null) {
      clearInterval(this.ticker);
      this.ticker = null;
    }
  }

  /** Poll now (manual refresh, visibility change); skipped while one is in flight. */
  async pollNow(): Promise<void> {
    if (this.polling || this.destroyed) return;
    this.polling = true;
    this.lastPoll = this.deps.now();
    try {
      await this.opts.poll();
    } finally {
      this.polling = false;
      this.sync();
    }
  }

  destroy(): void {
    this.destroyed = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.channelsKey = "";
    if (this.ticker !== null) clearInterval(this.ticker);
    this.ticker = null;
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.onVisibility);
    }
    this.connection = "closed";
  }

  private tick(): void {
    const now = this.deps.now();
    this.now = now;
    if (!this.opts.active() || this.deps.hidden()) return;
    const every =
      this.connection === "open" ? this.opts.slowPollMs : this.opts.fastPollMs;
    if (now - this.lastPoll >= every) void this.pollNow();
  }
}
