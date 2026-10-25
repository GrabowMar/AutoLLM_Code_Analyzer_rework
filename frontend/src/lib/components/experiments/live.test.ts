import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";
import type { SseEvent } from "$lib/api/sse";
import type {
  CellsPayload,
  ExperimentStatusV2Payload,
} from "$lib/api/types/experiments";
import {
  CELLS_POLL_MS,
  ExperimentLive,
  SILENCE_MS,
  STATUS_POLL_MS,
  type LiveDeps,
} from "./live.svelte";

const ID = "6f1c0c4e-0000-4000-8000-000000000001";

function status(
  over: Partial<ExperimentStatusV2Payload> = {},
): ExperimentStatusV2Payload {
  return {
    status: "running",
    pause_reason: "",
    policy_effective: "interleaved",
    launched_at: null,
    updated_at: "",
    counts: {
      pending: 1,
      running: 1,
      eval_queued: 0,
      evaluating: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    },
    cells: { total: 2, done: 0, failed_final: 0, in_flight: 1, retried: 0 },
    attempts_total: 2,
    throughput: {
      completed_last_15m: 0,
      completed_last_60m: 0,
      per_hour_ewma: 0,
    },
    eta_seconds: null,
    bottleneck: null,
    conditions: [],
    endpoints: [
      {
        id: 7,
        slug: "gpu-1",
        name: "gpu-1",
        kind: "ollama",
        connection: "relay",
        online: true,
        sessions: 1,
        health: "ok",
        slots: { used: 1, total: 2 },
        jobs: { active: 1, cap: 2, held_by_others: 0 },
        resident: { models: [], others: 0, limit: 1 },
        loaded: [],
        loaded_others: 0,
        queued_requests: 0,
        queued_jobs_this_experiment: 1,
        tokens_per_s_5m: null,
        p50_request_s_15m: null,
        circuit: "closed",
        blocked_reason: null,
      },
    ],
    lanes: {
      sandbox: { used: 0, total: 1 },
      eval: { used: 0, total: 1 },
      llm: { used: 1, total: 4 },
      post: { used: 0, total: 1 },
    },
    executor: { online: true, draining: false, code_stale: false },
    latest_post_run: null,
    ...over,
  };
}

const cells: CellsPayload = {
  conditions: [{ id: 1, label: "a", model_id: 1, endpoint_slug: "gpu-1" }],
  apps: [{ id: 2, slug: "todo", name: "Todo" }],
  repeats: 2,
  cells: [
    [0, 0, 0, "r", "j1", null, 1],
    [0, 0, 1, "p", "j2", null, 1],
  ],
};

let now = 0;
let onEvent: ((e: SseEvent) => void) | null = null;
let onState: ((s: "open" | "reconnecting" | "closed") => void) | undefined;
let deps: LiveDeps & {
  getStatus: Mock<LiveDeps["getStatus"]>;
  getCells: Mock<LiveDeps["getCells"]>;
  subscribe: Mock<LiveDeps["subscribe"]>;
};
let unsubscribe: Mock<() => void>;

beforeEach(() => {
  vi.useFakeTimers();
  now = 1_000_000;
  unsubscribe = vi.fn<() => void>();
  deps = {
    getStatus: vi.fn<LiveDeps["getStatus"]>(async () => status()),
    getCells: vi.fn<LiveDeps["getCells"]>(async () => structuredClone(cells)),
    subscribe: vi.fn<LiveDeps["subscribe"]>((_channels, handler, options) => {
      onEvent = handler;
      onState = options.onState;
      return unsubscribe;
    }),
    now: () => now,
    hidden: () => false,
  };
});

afterEach(() => {
  vi.useRealTimers();
});

async function started(): Promise<ExperimentLive> {
  const live = new ExperimentLive(ID, {}, deps);
  await live.start();
  return live;
}

function advance(ms: number) {
  now += ms;
  vi.advanceTimersByTime(ms);
}

describe("ExperimentLive", () => {
  it("loads, then subscribes to the experiment and its endpoints", async () => {
    const live = await started();
    expect(deps.getStatus).toHaveBeenCalledTimes(1);
    expect(deps.getCells).toHaveBeenCalledTimes(1);
    expect(deps.subscribe.mock.calls[0][0]).toEqual([
      `experiment:${ID}`,
      "endpoint:7",
    ]);
    expect(live.grid?.codes).toEqual(["r", "p"]);
    live.destroy();
    expect(unsubscribe).toHaveBeenCalled();
  });

  it("patches cells and counts from job events", async () => {
    const live = await started();
    onEvent!({
      type: "job",
      data: {
        type: "job",
        job_id: "j1",
        condition_id: 1,
        app_id: 2,
        repeat_index: 0,
        status: "running",
        phase: "eval_queued",
        attempt: 1,
        endpoint_id: 7,
        ts: "t",
      },
    });
    expect(live.grid?.codes[0]).toBe("q");
    expect(live.status?.counts.running).toBe(0);
    expect(live.status?.counts.eval_queued).toBe(1);
    expect(live.activity[0]).toMatchObject({
      jobId: "j1",
      code: "q",
      retry: false,
    });
    live.destroy();
  });

  it("replaces rollups on tick", async () => {
    const live = await started();
    const tick = {
      type: "tick",
      counts: { ...status().counts, completed: 1, pending: 0 },
      cells: status().cells,
      throughput: {
        completed_last_15m: 1,
        completed_last_60m: 1,
        per_hour_ewma: 4,
      },
      eta_seconds: 600,
      bottleneck: "endpoint:gpu-1",
      endpoints: [
        {
          id: 7,
          slots_used: 2,
          active: 2,
          queued: 3,
          tokens_per_s: 40,
          loaded: 1,
          blocked_reason: "endpoint_full",
          circuit: "closed",
        },
      ],
    };
    onEvent!({ type: "tick", data: tick });
    expect(live.status?.eta_seconds).toBe(600);
    expect(live.status?.endpoints[0].slots.used).toBe(2);
    expect(live.status?.endpoints[0].blocked_reason).toBe("endpoint_full");
    live.destroy();
  });

  it("does not poll while SSE delivers events", async () => {
    const live = await started();
    onState!("open");
    for (let i = 0; i < 10; i++) {
      advance(STATUS_POLL_MS);
      onEvent!({
        type: "tick",
        data: {
          type: "tick",
          counts: status().counts,
          cells: status().cells,
          throughput: status().throughput,
          eta_seconds: null,
          bottleneck: null,
          endpoints: [],
        },
      });
    }
    expect(deps.getStatus).toHaveBeenCalledTimes(1);
    expect(live.polling).toBe(false);
    live.destroy();
  });

  it("polls status every 5 s and cells every 15 s while reconnecting", async () => {
    const live = await started();
    onState!("reconnecting");
    advance(STATUS_POLL_MS);
    await vi.runOnlyPendingTimersAsync();
    expect(live.polling).toBe(true);
    expect(deps.getStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
    const statusCalls = deps.getStatus.mock.calls.length;
    advance(CELLS_POLL_MS);
    expect(deps.getCells.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(deps.getStatus.mock.calls.length).toBeGreaterThan(statusCalls);
    live.destroy();
  });

  it("falls back to polling after 20 s of silence while active", async () => {
    const live = await started();
    onState!("open");
    advance(SILENCE_MS - 2_000);
    expect(live.polling).toBe(false);
    advance(3_000);
    expect(live.polling).toBe(true);
    expect(deps.getStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
    live.destroy();
  });

  it("does not poll a hidden tab", async () => {
    let hidden = true;
    deps.hidden = () => hidden;
    const live = await started();
    onState!("reconnecting");
    advance(CELLS_POLL_MS * 2);
    expect(deps.getStatus).toHaveBeenCalledTimes(1);
    hidden = false;
    advance(1_000);
    expect(deps.getStatus.mock.calls.length).toBe(2);
    live.destroy();
  });

  it("stops when terminal with nothing in flight, and restarts", async () => {
    deps.getStatus.mockImplementation(async () =>
      status({
        status: "completed",
        counts: {
          pending: 0,
          running: 0,
          eval_queued: 0,
          evaluating: 0,
          completed: 2,
          failed: 0,
          cancelled: 0,
        },
      }),
    );
    const live = await started();
    expect(deps.subscribe).not.toHaveBeenCalled();
    expect(live.connection).toBe("closed");
    advance(CELLS_POLL_MS * 2);
    expect(deps.getStatus).toHaveBeenCalledTimes(1);
    deps.getStatus.mockImplementation(async () => status());
    await live.restart();
    expect(deps.subscribe).toHaveBeenCalledTimes(1);
    live.destroy();
  });

  it("refetches and notifies on state events", async () => {
    const onStateCb = vi.fn();
    const live = new ExperimentLive(ID, { onState: onStateCb }, deps);
    await live.start();
    onEvent!({
      type: "state",
      data: { type: "state", status: "paused", pause_reason: "user" },
    });
    expect(live.status?.status).toBe("paused");
    expect(onStateCb).toHaveBeenCalledWith(
      expect.objectContaining({ status: "paused" }),
    );
    await vi.waitFor(() => expect(deps.getStatus.mock.calls.length).toBe(2));
    live.destroy();
  });

  it("applies endpoint channel events to its lane", async () => {
    const live = await started();
    onEvent!({
      type: "endpoint",
      data: {
        type: "endpoint",
        channel: "endpoint:7",
        status: "offline",
        session_count: 0,
        inflight: 0,
        queued: 4,
        loaded_count: 0,
      },
    });
    expect(live.status?.endpoints[0]).toMatchObject({
      online: false,
      health: "offline",
      queued_requests: 4,
    });
    live.destroy();
  });

  it("keeps lane fields a redacted endpoint event does not carry", async () => {
    const live = await started();
    onEvent!({
      type: "endpoint",
      data: { type: "endpoint", channel: "endpoint:7:public" },
    });
    expect(live.status?.endpoints[0]).toMatchObject({
      online: true,
      health: "ok",
      queued_requests: 0,
    });
    onEvent!({
      type: "endpoint",
      data: {
        type: "endpoint",
        channel: "endpoint:7:public",
        status: "degraded",
      },
    });
    expect(live.status?.endpoints[0]).toMatchObject({
      online: true,
      health: "degraded",
      queued_requests: 0,
    });
    live.destroy();
  });
});
