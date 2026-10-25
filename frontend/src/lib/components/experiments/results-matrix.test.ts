import { describe, expect, it } from "vitest";
import type { ExperimentResults } from "$lib/api/types/experiments";
import {
  cellKey,
  fromResultsMatrix,
} from "$lib/components/generator/batches/matrix";

function results(): Pick<ExperimentResults, "matrix" | "leaderboard"> {
  const lb = (
    condition_id: number,
    rank: number,
    name: string,
    k: number,
    n: number,
    mean: number,
  ) => ({
    rank,
    condition_id,
    label: `${name} · aider`,
    model: { id: condition_id, name, endpoint_slug: null },
    harness: "aider@default",
    n_apps: 1,
    repeats_per_app: 2,
    n_cells: 2,
    n_graded: n,
    k_passed: k,
    n_missing: {
      failed: 0,
      ungraded: 0,
      excluded_invalid: 0,
      excluded_truncated: 0,
      not_common: 0,
    },
    pass_rate_mean: mean,
    pass_rate_ci95: null,
    cost_usd: 0,
    tokens_total: 0,
    median_seconds: null,
    timing_endpoint_slug: null,
    terminations: {},
    validity: { ok: 2, warn: 0, invalid: 0 },
    mixed_fingerprints: null,
  });
  return {
    leaderboard: [lb(2, 1, "B", 2, 2, 1), lb(1, 2, "A", 0, 1, 0.25)],
    matrix: {
      apps: [{ id: 9, slug: "todo", name: "Todo" }],
      cells: [
        {
          condition_id: 1,
          app_id: 9,
          k: 0,
          n: 1,
          mean: 0.25,
          runs: [
            {
              job_id: "a2",
              repeat_index: 1,
              pass_rate: null,
              termination: "timeout",
              valid: false,
              code: "x",
            },
            {
              job_id: "a1",
              repeat_index: 0,
              pass_rate: 0.25,
              termination: "completed",
              valid: true,
              code: "f",
            },
          ],
        },
        {
          condition_id: 2,
          app_id: 9,
          k: 2,
          n: 2,
          mean: 1,
          runs: [
            {
              job_id: "b1",
              repeat_index: 0,
              pass_rate: 1,
              termination: "completed",
              valid: true,
              code: "c",
            },
            {
              job_id: "b2",
              repeat_index: 1,
              pass_rate: 1,
              termination: "completed",
              valid: true,
              code: "c",
            },
          ],
        },
      ],
    },
  };
}

describe("fromResultsMatrix", () => {
  it("orders conditions by rank and uses the server's k/n/mean", () => {
    const m = fromResultsMatrix(results());
    expect(m.columns.map((c) => c.conditionId)).toEqual([2, 1]);
    expect(m.columns[0]).toMatchObject({
      model: "B",
      variant: "aider@default",
    });
    expect(m.rows).toEqual([{ key: "a:9", label: "Todo", slug: "todo" }]);
    const cell = m.cells.get(cellKey("a:9", "c:1"))!;
    expect(cell.stats).toMatchObject({
      graded: 1,
      passed: 0,
      meanPassRate: 0.25,
    });
    expect(cell.trials.map((t) => [t.job.id, t.outcome])).toEqual([
      ["a1", "not_passed"],
      ["a2", "failed"],
    ]);
    expect(cell.trials[1].job.validity).toBe("exclude");
    expect(m.columnStats.get("c:2")).toMatchObject({
      graded: 2,
      passed: 2,
      meanPassRate: 1,
    });
  });

  it("prefers explicit condition info", () => {
    const m = fromResultsMatrix(results(), [
      { id: 1, label: "x", model: "Alpha", variant: "opencode" },
    ]);
    expect(m.columns.find((c) => c.conditionId === 1)).toMatchObject({
      model: "Alpha",
      variant: "opencode",
    });
  });
});
