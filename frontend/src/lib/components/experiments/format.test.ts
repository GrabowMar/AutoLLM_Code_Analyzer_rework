import { describe, expect, it } from "vitest";
import type {
  AnalysisPlan,
  ExperimentResults,
} from "$lib/api/types/experiments";
import {
  analysisStatus,
  bottleneckLabel,
  effectivePolicyLabel,
  formatDeltaPp,
  formatEta,
  frozenPlan,
  planTruncationRule,
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

describe("analysis plan and status", () => {
  const plan: AnalysisPlan = {
    primary_metric: "pass_rate",
    correction: "holm",
    alpha: 0.05,
    exclusions: { truncated: false },
  };
  const view = (
    status: "confirmatory" | "exploratory",
    deviations: ExperimentResults["paired"]["deviations"] = [],
    baseline_source: "preregistered" | "post_hoc" = "preregistered",
  ) =>
    ({
      baseline_source,
      paired: { status, deviations },
    }) as unknown as Pick<ExperimentResults, "baseline_source" | "paired">;

  it("takes the plan only once frozen, and its truncation rule even as a draft", () => {
    expect(
      frozenPlan({ analysis_plan: plan, analysis_plan_frozen_at: null }),
    ).toBeNull();
    expect(
      frozenPlan({ analysis_plan: {}, analysis_plan_frozen_at: "2026-10-26" }),
    ).toBeNull();
    expect(
      frozenPlan({
        analysis_plan: plan,
        analysis_plan_frozen_at: "2026-10-26",
      }),
    ).toBe(plan);
    expect(planTruncationRule({ analysis_plan: plan })).toBe(false);
    expect(
      planTruncationRule({ analysis_plan: { primary_metric: "pass_rate" } }),
    ).toBe(true);
    expect(planTruncationRule({ analysis_plan: {} })).toBeNull();
  });

  it("follows the server's verdict, not the URL", () => {
    expect(analysisStatus(view("confirmatory"), plan)).toEqual({
      kind: "confirmatory",
      label: "Pre-registered",
      reasons: [],
    });
    // Excluding invalid runs is a deviation the server reports.
    const invalid = analysisStatus(
      view("exploratory", ["invalid_excluded"]),
      plan,
    );
    expect(invalid.kind).toBe("exploratory");
    expect(invalid.reasons).toEqual([
      "invalid runs excluded (not one of the plan's exclusions)",
    ]);
  });

  it("says why the default view is exploratory", () => {
    // No plan: never "Pre-registered" (955a5bd5).
    expect(analysisStatus(view("exploratory"), null).reasons).toEqual([
      "no frozen analysis plan",
    ]);
    expect(
      analysisStatus(view("exploratory", [], "post_hoc"), plan).reasons,
    ).toEqual(["baseline chosen post hoc"]);
    expect(
      analysisStatus(view("exploratory"), {
        ...plan,
        primary_metric: "issues_per_kloc",
      }).reasons,
    ).toEqual(["the plan does not declare pass rate"]);
    // Only the multi-metric correction family: the plan's own analysis, p-values exploratory here.
    const family = analysisStatus(
      view("exploratory", ["multiplicity_family"]),
      plan,
    );
    expect(family.kind).toBe("plan");
    expect(family.reasons[0]).toContain("spans several metrics");
    expect(
      analysisStatus(
        view("exploratory", ["multiplicity_family", "common_cells_off"]),
        plan,
      ).kind,
    ).toBe("exploratory");
  });

  it("never calls a view the plan's analysis when the plan doesn't test pass rate", () => {
    // multiplicity "all" over two declared metrics: the server adds multiplicity_family regardless.
    const noPassRate = {
      primary_metric: "issues_per_kloc",
      secondary_metrics: ["deploy_rate"],
      multiplicity: "all",
    } as unknown as typeof plan;
    expect(
      analysisStatus(view("exploratory", ["multiplicity_family"]), noPassRate),
    ).toEqual({
      kind: "exploratory",
      label: "Exploratory",
      reasons: [
        "the plan corrects across several metrics (see the study report)",
        "the plan does not declare pass rate",
      ],
    });
    // A resettable deviation doesn't hide the reason that would remain after a reset.
    expect(
      analysisStatus(view("exploratory", ["invalid_excluded"]), noPassRate)
        .reasons,
    ).toContain("the plan does not declare pass rate");
  });
});
