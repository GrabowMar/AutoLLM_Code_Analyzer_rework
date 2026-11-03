/**
 * SampleLive — one run's state for `/samples/[id]`: the `RunOut`, the SSE channel `run:<id>` and a polling
 * fallback (fast while the stream is down, slow as a safety net while it is up; nothing once the run and its
 * evaluation have settled). Live trajectory steps and usage arrive through the stream and are kept here so
 * every tab reads one source.
 *
 * Usage: `const live = new SampleLive(); live.start(id); … live.stop();` (the page restarts it when the id
 * changes). Never hand-roll polling on the sample page.
 */
import { formatApiError, isAbortError } from "$lib/api/core";
import type { SseState } from "$lib/api/sse";
import { getSample, subscribeRun } from "$lib/api/v3/samples";
import type { RunEvent, RunOut, TrajectoryStepOut } from "$lib/api/v3/types";
import { isActiveStatus } from "./format";

const FAST_POLL_MS = 5_000;
const SLOW_POLL_MS = 30_000;
const MAX_LOG_LINES = 400;

type UsageEvent = Extract<RunEvent, { type: "usage" }>;

export class SampleLive {
  run = $state<RunOut | null>(null);
  loading = $state(true);
  error = $state("");
  notFound = $state(false);
  stream = $state<SseState | "idle">("idle");
  /** live trajectory steps of the running attempt (the trajectory endpoint is authoritative afterwards) */
  liveSteps = $state<TrajectoryStepOut[]>([]);
  liveStepCount = $state(0);
  usage = $state<UsageEvent | null>(null);
  logs = $state<{ stream: string; level: string; line: string }[]>([]);
  /** bumps whenever something changed that tabs may want to reload (evaluation, completion) */
  version = $state(0);

  #id: string | null = null;
  #unsubscribe: (() => void) | null = null;
  #timer: ReturnType<typeof setTimeout> | null = null;
  #abort: AbortController | null = null;

  get active(): boolean {
    const run = this.run;
    if (!run) return true;
    const evaluation = run.latest_evaluation?.status;
    return (
      isActiveStatus(run.status) ||
      evaluation === "pending" ||
      evaluation === "running"
    );
  }

  start(id: string): void {
    this.stop();
    this.#id = id;
    this.run = null;
    this.loading = true;
    this.error = "";
    this.notFound = false;
    this.liveSteps = [];
    this.liveStepCount = 0;
    this.usage = null;
    this.logs = [];
    this.#unsubscribe = subscribeRun(
      id,
      (event) => this.#onEvent(event),
      (state) => {
        this.stream = state;
      },
    );
    void this.refresh();
  }

  stop(): void {
    this.#unsubscribe?.();
    this.#unsubscribe = null;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
    this.#abort?.abort();
    this.#abort = null;
    this.#id = null;
    this.stream = "idle";
  }

  async refresh(): Promise<void> {
    const id = this.#id;
    if (!id) return;
    this.#abort?.abort();
    const controller = new AbortController();
    this.#abort = controller;
    try {
      const run = await getSample(id, { signal: controller.signal });
      if (this.#id !== id) return;
      const settledNow =
        this.run &&
        this.active &&
        !(isActiveStatus(run.status) || isEvalActive(run));
      this.run = run;
      this.error = "";
      if (settledNow) this.version += 1;
    } catch (e) {
      if (isAbortError(e)) return;
      const status = (e as { httpStatus?: number })?.httpStatus;
      if (status === 404) this.notFound = true;
      this.error = formatApiError(e, "Could not load the sample.");
    } finally {
      if (this.#id === id) {
        this.loading = false;
        this.#schedule();
      }
    }
  }

  #schedule(): void {
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
    if (!this.#id || this.notFound || !this.active) return;
    const delay = this.stream === "open" ? SLOW_POLL_MS : FAST_POLL_MS;
    this.#timer = setTimeout(() => void this.refresh(), delay);
  }

  #onEvent(event: RunEvent): void {
    switch (event.type) {
      case "status":
        if (this.run) {
          this.run = {
            ...this.run,
            status: event.status,
            phase: event.phase,
            outcome: (event.outcome || this.run.outcome) as RunOut["outcome"],
            usable: event.usable ?? this.run.usable,
          };
        }
        if (!isActiveStatus(event.status)) void this.refresh();
        break;
      case "trajectory_step":
        this.liveStepCount = event.n_steps;
        if (event.step) {
          const steps = this.liveSteps.filter(
            (s) => s.index !== event.step!.index,
          );
          steps.push(event.step);
          steps.sort((a, b) => a.index - b.index);
          this.liveSteps = steps;
        }
        break;
      case "usage":
        this.usage = event;
        break;
      case "log":
        this.logs = [
          ...this.logs.slice(-(MAX_LOG_LINES - 1)),
          { stream: event.stream, level: event.level, line: event.line },
        ];
        break;
      case "evaluation":
        if (event.status !== "pending" && event.status !== "running") {
          this.version += 1;
          void this.refresh();
        }
        break;
      case "step":
        if (event.status !== "started") void this.refresh();
        break;
    }
  }
}

function isEvalActive(run: RunOut): boolean {
  const status = run.latest_evaluation?.status;
  return status === "pending" || status === "running";
}
