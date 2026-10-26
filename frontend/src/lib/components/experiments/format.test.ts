import { describe, expect, it } from "vitest";
import {
  bottleneckLabel,
  effectivePolicyLabel,
  formatDeltaPp,
  formatEta,
  priorityBucket,
  toBatchCounts,
} from "./format";

describe("priorityBucket", () => {
  it("maps stored priorities onto the three UI levels", () => {
    expect(priorityBucket(0)).toBe(0);
    expect(priorityBucket(null)).toBe(0);
    expect(priorityBucket(3)).toBe(5);
    expect(priorityBucket(10)).toBe(5);
    expect(priorityBucket(-1)).toBe(-5);
    expect(priorityBucket(-10)).toBe(-5);
  });
});

describe("toBatchCounts", () => {
  it("counts eval_queued as queued and evaluating as running", () => {
    expect(
      toBatchCounts({
        pending: 1,
        running: 2,
        eval_queued: 3,
        evaluating: 4,
        completed: 5,
      }),
    ).toEqual({
      pending: 1,
      queued: 3,
      running: 6,
      completed: 5,
      failed: 0,
      cancelled: 0,
    });
  });
});

describe("formatting", () => {
  it("formats ETA, deltas and per-endpoint policies", () => {
    expect(formatEta(null)).toBe("—");
    expect(formatEta(30)).toBe("<1m");
    expect(formatEta(3_900)).toBe("1h 05m");
    expect(formatDeltaPp(0.123)).toBe("+12.3 pp");
    expect(formatDeltaPp(-0.05)).toBe("−5.0 pp");
    expect(effectivePolicyLabel({ a: "blocked", b: "blocked" })).toBe(
      "Per repeat",
    );
    expect(
      effectivePolicyLabel({ "gpu-1": "blocked", openrouter: "interleaved" }),
    ).toBe("Per repeat (gpu-1) · Interleaved (openrouter)");
    expect(effectivePolicyLabel(null, "grouped")).toBe("Grouped");
  });
});

describe("bottleneckLabel", () => {
  it("tells an offline endpoint apart from one at capacity", () => {
    const offline = bottleneckLabel("endpoint_offline:gpu-1");
    expect(offline?.label).toBe("Endpoint gpu-1 offline");
    expect(offline?.title).toMatch(/relay/);
    expect(offline?.title).not.toMatch(/raise its slots/);
    expect(bottleneckLabel("endpoint:gpu-1")?.title).toMatch(/raise its slots/);
    // Raw scheduler reasons from live ticks.
    expect(bottleneckLabel("endpoint_offline")?.title).toMatch(/relay/);
    expect(bottleneckLabel(null)).toBeNull();
  });
});
