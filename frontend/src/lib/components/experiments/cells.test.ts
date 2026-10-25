import { describe, expect, it } from "vitest";
import type { JobEvent } from "$lib/api/types/events";
import type { CellsPayload } from "$lib/api/types/experiments";
import {
  adjustCounts,
  applyJobEvent,
  buildGrid,
  cellIndex,
  codeForJobEvent,
  countsFromGrid,
  groupConditions,
  rowSummary,
} from "./cells";

function payload(): CellsPayload {
  return {
    conditions: [
      { id: 10, label: "qwen · aider", model_id: 1, endpoint_slug: "gpu-1" },
      { id: 11, label: "gpt · aider", model_id: 2, endpoint_slug: null },
      { id: 12, label: "llama · aider", model_id: 3, endpoint_slug: "gpu-1" },
    ],
    apps: [
      { id: 100, slug: "todo", name: "Todo" },
      { id: 101, slug: "blog", name: "Blog" },
    ],
    repeats: 2,
    cells: [
      [0, 0, 0, "c", "j1", 1, 1],
      [0, 0, 1, "f", "j2", 0.5, 1],
      [0, 1, 0, "r", "j3", null, 1],
      [1, 0, 0, "x", "j4", null, 2],
      [2, 1, 1, "p", "j5", null, 1],
    ],
  };
}

function event(over: Partial<JobEvent>): JobEvent {
  return {
    type: "job",
    job_id: "j3",
    condition_id: 10,
    app_id: 101,
    repeat_index: 0,
    status: "running",
    phase: "running",
    attempt: 1,
    endpoint_id: 1,
    ts: "2026-10-25T10:00:00Z",
    ...over,
  };
}

describe("buildGrid", () => {
  it("decodes tuples into flat arrays", () => {
    const grid = buildGrid(payload());
    expect(grid.codes).toHaveLength(3 * 2 * 2);
    expect(grid.codes[cellIndex(grid, 0, 0, 1)]).toBe("f");
    expect(grid.passRates[cellIndex(grid, 0, 0, 1)]).toBe(0.5);
    expect(grid.jobIds[cellIndex(grid, 1, 0, 0)]).toBe("j4");
    expect(grid.attempts[cellIndex(grid, 1, 0, 0)]).toBe(2);
    expect(grid.codes[cellIndex(grid, 2, 0, 0)]).toBeNull();
  });

  it("ignores out-of-range tuples", () => {
    const p = payload();
    p.cells.push([9, 0, 0, "c", "bad", 1, 1], [0, 0, 5, "c", "bad", 1, 1]);
    const grid = buildGrid(p);
    expect(grid.jobIds).not.toContain("bad");
  });

  it("counts buckets", () => {
    const counts = countsFromGrid(buildGrid(payload()));
    expect(counts).toMatchObject({
      completed: 2,
      running: 1,
      failed: 1,
      pending: 1,
    });
  });
});

describe("codeForJobEvent", () => {
  it("maps status, phase and pass rate", () => {
    expect(codeForJobEvent({ status: "pending", phase: "" })).toBe("p");
    expect(codeForJobEvent({ status: "running", phase: "eval_queued" })).toBe(
      "q",
    );
    expect(codeForJobEvent({ status: "running", phase: "evaluating" })).toBe(
      "e",
    );
    expect(codeForJobEvent({ status: "running", phase: "running" })).toBe("r");
    expect(codeForJobEvent({ status: "running", phase: "queued" })).toBe("p");
    expect(
      codeForJobEvent({ status: "completed", phase: "", pass_rate: 1 }),
    ).toBe("c");
    expect(
      codeForJobEvent({ status: "completed", phase: "", pass_rate: 0.3 }),
    ).toBe("f");
    expect(
      codeForJobEvent({ status: "completed", phase: "", pass_rate: null }),
    ).toBe("u");
    expect(codeForJobEvent({ status: "failed", phase: "" })).toBe("x");
    expect(codeForJobEvent({ status: "cancelled", phase: "" })).toBe("k");
  });
});

describe("applyJobEvent", () => {
  it("patches one cell and reports the bucket move", () => {
    const grid = buildGrid(payload());
    const patch = applyJobEvent(grid, event({ phase: "eval_queued" }));
    expect(patch).toEqual({
      index: cellIndex(grid, 0, 1, 0),
      previous: "r",
      next: "q",
    });
    const counts = adjustCounts(countsFromGrid(buildGrid(payload())), patch!);
    expect(counts.running).toBe(0);
    expect(counts.eval_queued).toBe(1);
  });

  it("fills an empty cell (a new job)", () => {
    const grid = buildGrid(payload());
    const patch = applyJobEvent(
      grid,
      event({
        job_id: "new",
        condition_id: 12,
        app_id: 100,
        status: "pending",
        phase: "",
      }),
    );
    expect(patch?.previous).toBeNull();
    expect(grid.jobIds[cellIndex(grid, 2, 0, 0)]).toBe("new");
  });

  it("drops stale attempts of the same job", () => {
    const grid = buildGrid(payload());
    const i = cellIndex(grid, 1, 0, 0);
    expect(
      applyJobEvent(
        grid,
        event({
          job_id: "j4",
          condition_id: 11,
          app_id: 100,
          attempt: 1,
          status: "running",
        }),
      ),
    ).toBeNull();
    expect(grid.codes[i]).toBe("x");
    expect(
      applyJobEvent(
        grid,
        event({
          job_id: "j4",
          condition_id: 11,
          app_id: 100,
          attempt: 3,
          status: "pending",
          phase: "",
        }),
      )?.next,
    ).toBe("p");
  });

  it("does not let a replaced job's late terminal event overwrite a live job", () => {
    const grid = buildGrid(payload());
    expect(
      applyJobEvent(
        grid,
        event({ job_id: "old", status: "failed", phase: "" }),
      ),
    ).toBeNull();
    expect(grid.codes[cellIndex(grid, 0, 1, 0)]).toBe("r");
  });

  it("ignores unknown cells", () => {
    const grid = buildGrid(payload());
    expect(applyJobEvent(grid, event({ condition_id: 999 }))).toBeNull();
    expect(applyJobEvent(grid, event({ repeat_index: 7 }))).toBeNull();
    expect(applyJobEvent(grid, event({ app_id: null }))).toBeNull();
  });

  it("returns null when nothing changed", () => {
    const grid = buildGrid(payload());
    expect(applyJobEvent(grid, event({}))).toBeNull();
  });
});

describe("rowSummary and grouping", () => {
  it("summarises a row", () => {
    const grid = buildGrid(payload());
    expect(rowSummary(grid, 0)).toEqual({
      total: 4,
      started: 3,
      done: 2,
      active: 1,
      failed: 0,
      graded: 2,
      passed: 1,
      passRate: 0.75,
    });
  });

  it("groups by endpoint with OpenRouter last and sorts within groups", () => {
    const grid = buildGrid(payload());
    const summaries = grid.conditions.map((_, i) => rowSummary(grid, i));
    const groups = groupConditions(grid, "endpoint", "label", summaries);
    expect(groups.map((g) => g.label)).toEqual(["gpu-1", "OpenRouter"]);
    expect(groups[0].rows).toEqual([2, 0]);
    const flat = groupConditions(grid, "none", "pass", summaries);
    expect(flat).toHaveLength(1);
    expect(flat[0].rows[0]).toBe(0);
    const byProgress = groupConditions(grid, "none", "progress", summaries);
    expect(byProgress[0].rows).toEqual([0, 1, 2]);
  });
});
