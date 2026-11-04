import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SseEvent } from "$lib/api/sse";
import type {
  StudyBudgetOut,
  StudyCellOut,
  StudyCellsPayload,
  StudyOut,
} from "$lib/api/v3/types";
import {
  applyRunEvent,
  CELLS_POLL_MS,
  cellStateForRun,
  SILENCE_MS,
  STATUS_POLL_MS,
  StudyLive,
  type LiveDeps,
} from "./live.svelte";

const ID = "1f1c0c4e-0000-4000-8000-000000000001";

function cell(id: number, over: Partial<StudyCellOut> = {}): StudyCellOut {
  return {
    id,
    condition_id: 1,
    condition_index: 0,
    unit_id: 1,
    unit_key: "task:todo@1",
    repeat_index: id,
    stage: "main",
    block_index: id,
    sequence: id,
    seed: 7,
    state: "queued",
    exclusion_reason: "",
    current_run: null,
    outcome: "",
    attempts: 0,
    beyond_plan_runs: 0,
    beyond_plan: false,
    estimate_usd: 0.1,
    cap_usd: 0.3,
    cell_cap_usd: 1,
    spent_usd: 0,
    spent_list_usd: 0,
    adaptive_n: false,
    ...over,
  };
}

function cells(list: StudyCellOut[]): StudyCellsPayload {
  const counts: Record<string, number> = {};
  for (const c of list) counts[c.state] = (counts[c.state] ?? 0) + 1;
  return {
    cells: list,
    counts,
    states: [
      "planned",
      "queued",
      "running",
      "done",
      "infra_pending",
      "excluded",
    ],
  };
}

function studyOut(over: Partial<StudyOut> = {}): StudyOut {
  return {
    id: ID,
    status: "running",
    pause_reason: "",
    spent_usd: 0,
    spent_list_usd: 0,
    reserved_usd: 0,
    ...over,
  } as StudyOut;
}

function budget(over: Partial<StudyBudgetOut> = {}): StudyBudgetOut {
  return {
    study_id: ID,
    cap: 10,
    spent: 1,
    spent_list: 1.5,
    reserved: 2,
    remaining: 7,
    ...over,
  } as StudyBudgetOut;
}

function setup(initialCells = cells([cell(1), cell(2, { state: "running" })])) {
  let now = 1_000_000;
  let emit: ((e: SseEvent) => void) | null = null;
  let onState: ((s: "open" | "reconnecting" | "closed") => void) | undefined;
  const unsubscribe = vi.fn();
  const deps: LiveDeps = {
    getStudy: vi.fn(async () => studyOut()),
    getCells: vi.fn(async () => initialCells),
    getBudget: vi.fn(async () => budget()),
    subscribe: vi.fn((channels, cb, options) => {
      emit = cb;
      onState = options.onState;
      return unsubscribe;
    }),
    now: () => now,
    hidden: () => false,
  };
  const callbacks = { onState: vi.fn(), onPostRun: vi.fn(), onTick: vi.fn() };
  const live = new StudyLive(ID, callbacks, deps);
  return {
    live,
    deps,
    callbacks,
    unsubscribe,
    emit: (type: string, data: Record<string, unknown>) =>
      emit!({ type, data: { type, ...data } }),
    state: (s: "open" | "reconnecting" | "closed") => onState?.(s),
    advance: (ms: number) => (now += ms),
  };
}

describe("cellStateForRun / applyRunEvent", () => {
  it("maps run statuses and outcomes to cell states", () => {
    expect(cellStateForRun({ status: "pending", outcome: "" }, "planned")).toBe(
      "queued",
    );
    expect(cellStateForRun({ status: "running", outcome: "" }, "queued")).toBe(
      "running",
    );
    expect(
      cellStateForRun({ status: "completed", outcome: "tested" }, "running"),
    ).toBe("done");
    expect(
      cellStateForRun(
        { status: "completed", outcome: "build_fail" },
        "running",
      ),
    ).toBe("done");
    expect(
      cellStateForRun(
        { status: "completed", outcome: "infra_error" },
        "running",
      ),
    ).toBe("infra_pending");
    expect(cellStateForRun({ status: "failed", outcome: "" }, "running")).toBe(
      "infra_pending",
    );
    expect(
      cellStateForRun({ status: "completed", outcome: "excluded" }, "running"),
    ).toBe("excluded");
  });

  it("patches one cell and moves it between count buckets", () => {
    const payload = cells([cell(1), cell(2, { state: "running" })]);
    const next = applyRunEvent(payload, {
      type: "run",
      run_id: "r1",
      cell_id: 2,
      condition_id: 1,
      unit_key: "task:todo@1",
      repeat_index: 2,
      status: "completed",
      phase: "done",
      outcome: "tested",
      attempt: 1,
      endpoint_id: null,
      agent_pass_rate: 0.8,
      ts: null,
    })!;
    expect(next.cells[1]).toMatchObject({
      state: "done",
      outcome: "tested",
      current_run: { id: "r1", status: "completed" },
    });
    expect(next.counts).toMatchObject({ queued: 1, running: 0, done: 1 });
    expect(payload.cells[1].state).toBe("running"); // pure
    expect(
      applyRunEvent(payload, {
        type: "run",
        run_id: "r1",
        cell_id: 99,
        status: "running",
        phase: "",
        outcome: "",
        attempt: 1,
      } as never),
    ).toBeNull();
  });
});

describe("StudyLive", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("loads, subscribes to study:<id> and patches cells from run events", async () => {
    const t = setup();
    await t.live.start();
    expect(t.deps.subscribe).toHaveBeenCalledWith(
      [`study:${ID}`],
      expect.any(Function),
      expect.any(Object),
    );
    t.emit("run", {
      run_id: "r9",
      cell_id: 1,
      status: "running",
      phase: "sandbox",
      outcome: "",
      attempt: 1,
    });
    expect(t.live.cells!.cells[0].state).toBe("running");
    expect(t.live.activity[0]).toMatchObject({
      runId: "r9",
      status: "running",
    });
    t.live.destroy();
  });

  it("spend events update the strip without a refetch and feed the burn rate", async () => {
    const t = setup();
    await t.live.start();
    t.emit("spend", {
      spent: 2,
      spent_list: 2.5,
      reserved: 1,
      delta: 1,
      run_id: "r1",
    });
    expect(t.live.budget).toMatchObject({
      spent: 2,
      spent_list: 2.5,
      reserved: 1,
      remaining: 7,
    });
    expect(t.live.study).toMatchObject({ spent_usd: 2, reserved_usd: 1 });
    expect(t.live.liveBurnRate).toBeNull();
    t.advance((30 * 60_000) / 6); // 5 minutes
    t.emit("spend", {
      spent: 3,
      spent_list: 3.5,
      reserved: 1,
      delta: 1,
      run_id: "r1",
    });
    expect(t.live.liveBurnRate).toBeCloseTo(12, 6); // $1 per 5 min
    expect(t.deps.getBudget).toHaveBeenCalledTimes(1);
    t.live.destroy();
  });

  it("budget alerts, pauses and stops update the status", async () => {
    const t = setup();
    await t.live.start();
    t.emit("budget_alert", { fraction: 0.8 });
    t.emit("budget_alert", { fraction: 0.5 });
    expect(t.live.alertFraction).toBe(0.8);
    t.emit("paused", { reason: "budget", condition_id: null });
    expect(t.live.study).toMatchObject({
      status: "paused",
      pause_reason: "budget",
    });
    t.emit("stopped", { reason: "budget" });
    expect(t.live.study?.status).toBe("stopped");
    t.emit("post_run", { id: 3, status: "running", progress: {} });
    expect(t.callbacks.onPostRun).toHaveBeenCalledWith(
      expect.objectContaining({ id: 3 }),
    );
    t.emit("tick", {
      counts: { done: 1 },
      cells: {},
      endpoints: [],
      bottleneck: null,
    });
    expect(t.live.tick?.counts).toEqual({ done: 1 });
    expect(t.callbacks.onTick).toHaveBeenCalled();
    t.live.destroy();
  });

  it("polls while SSE is reconnecting and stops polling once it is open", async () => {
    const t = setup();
    await t.live.start();
    const calls = () =>
      (t.deps.getStudy as ReturnType<typeof vi.fn>).mock.calls.length;
    const before = calls();
    t.state("reconnecting");
    t.advance(STATUS_POLL_MS);
    t.live.onTicker();
    expect(t.live.polling).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls()).toBe(before + 1);
    t.advance(CELLS_POLL_MS);
    t.live.onTicker();
    expect(
      (t.deps.getCells as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThanOrEqual(2);
    t.state("open");
    t.emit("tick", { counts: {}, cells: {}, endpoints: [], bottleneck: null });
    t.live.onTicker();
    expect(t.live.polling).toBe(false);
    t.live.destroy();
  });

  it("polls when a running study is silent for too long with runs in flight", async () => {
    const t = setup();
    await t.live.start();
    t.state("open");
    t.emit("tick", { counts: {}, cells: {}, endpoints: [], bottleneck: null });
    t.advance(SILENCE_MS + 1);
    t.live.onTicker();
    expect(t.live.polling).toBe(true);
    t.live.destroy();
  });

  it("closes the stream once the study is terminal and nothing is in flight", async () => {
    const t = setup(cells([cell(1, { state: "done" })]));
    (t.deps.getStudy as ReturnType<typeof vi.fn>).mockResolvedValue(
      studyOut({ status: "completed" }),
    );
    await t.live.start();
    t.live.onTicker();
    expect(t.unsubscribe).toHaveBeenCalled();
    expect(t.live.connection).toBe("closed");
    t.live.destroy();
  });
});
