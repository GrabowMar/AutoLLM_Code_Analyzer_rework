import { describe, expect, it } from "vitest";
import type {
  ExperimentConditionV2,
  ExperimentResults,
  LeaderboardRow,
  PairedRow,
} from "$lib/api/types/experiments";
import {
  designShape,
  keyFindings,
  shortExperimentId,
  type Finding,
} from "./findings";
import { resolveExperimentTab } from "./tabs";

function condition(
  id: number,
  model: number,
  preset: number,
  overrides: Record<string, number> = {},
): ExperimentConditionV2 {
  return {
    id,
    label: `c${id}`,
    profile: null,
    model,
    harness: "opencode",
    harness_preset: preset,
    model_name: `m${model}`,
    bundle_slug: null,
    bundle_version: null,
    param_overrides: overrides,
    created_at: "",
  };
}

function row(
  id: number,
  rank: number,
  mean: number | null,
  extra: Partial<LeaderboardRow> = {},
): LeaderboardRow {
  return {
    rank,
    condition_id: id,
    label: `Condition ${id}`,
    model: { id, name: `m${id}`, endpoint_slug: null },
    harness: "opencode",
    n_apps: 8,
    repeats_per_app: 3,
    n_cells: 24,
    n_graded: mean == null ? 0 : 24,
    k_passed: 0,
    n_missing: {
      failed: 0,
      ungraded: 0,
      excluded_invalid: 0,
      excluded_truncated: 0,
      not_common: 0,
    },
    pass_rate_mean: mean,
    pass_rate_ci95: mean == null ? null : [mean - 0.1, mean + 0.1],
    cost_usd: 0,
    tokens_total: 0,
    median_seconds: null,
    timing_endpoint_slug: null,
    terminations: {},
    validity: { ok: 0, warn: 0, invalid: 0 },
    mixed_fingerprints: null,
    ...extra,
  };
}

function paired(id: number, delta: number, pHolm: number | null): PairedRow {
  return {
    condition_id: id,
    n_pairs: 8,
    mean_delta: delta,
    ci95: [delta - 0.05, delta + 0.05],
    wins: 4,
    losses: 2,
    ties: 2,
    sign_test_p: null,
    wilcoxon_p: null,
    p_holm: pHolm,
  };
}

function results(
  leaderboard: LeaderboardRow[],
  rows: PairedRow[],
  extra: Partial<ExperimentResults> = {},
): ExperimentResults {
  return {
    metric: "pass_rate",
    exclude_invalid: false,
    exclude_truncated: true,
    common_cells: true,
    fingerprint_filter: "all",
    baseline_condition_id: 1,
    baseline_source: "preregistered",
    interim: false,
    balanced: true,
    leaderboard,
    matrix: { apps: [], cells: [] },
    paired: {
      baseline_condition_id: 1,
      pairing: "app_blocked",
      n_apps: 8,
      rows,
    },
    notes: [],
    generated_at: "",
    ...extra,
  };
}

const text = (f: Finding) =>
  f.parts.map((p) => (typeof p === "string" ? p : p.strong)).join("");
const labelOf = (id: number) => `Condition ${id}`;

describe("designShape", () => {
  it("counts models × variants, ablations apart, and the cells", () => {
    const shape = designShape({
      conditions: [
        condition(1, 10, 3),
        condition(2, 10, 5),
        condition(3, 11, 3),
        condition(4, 11, 5),
        condition(5, 10, 3, { temperature: 0.2 }),
      ],
      app_requirement_ids: [1, 2, 3],
      repeats: 2,
      total_cells: 0,
    });
    expect(shape).toEqual({
      conditions: 5,
      models: 2,
      variants: 2,
      ablations: 1,
      apps: 3,
      repeats: 2,
      cells: 30,
    });
  });
});

describe("shortExperimentId", () => {
  it("uses the first UUID block as an accession number", () => {
    expect(shortExperimentId("955a5bd5-031f-4518-a6bc-afe09b0e98c3")).toBe(
      "EXP-955A5BD5",
    );
  });
});

describe("keyFindings", () => {
  it("is empty while nothing is graded", () => {
    expect(keyFindings(results([row(1, 1, null)], []), labelOf)).toEqual([]);
  });

  it("names the leader and the significant effect against the baseline", () => {
    const findings = keyFindings(
      results(
        [row(2, 1, 0.8), row(1, 2, 0.6), row(3, 3, 0.4)],
        [paired(2, 0.2, 0.01), paired(3, -0.2, 0.2)],
      ),
      labelOf,
    );
    expect(findings.map((f) => f.kind)).toEqual(["leader", "effect"]);
    expect(text(findings[0])).toContain(
      "Highest mean pass rate: Condition 2, 80%",
    );
    expect(text(findings[0])).toContain("Lowest: Condition 3, 40%");
    expect(text(findings[1])).toContain(
      "1 of 2 conditions differ from the baseline Condition 1",
    );
    expect(text(findings[1])).toContain("+20.0 pp");
  });

  it("reports a null result with the largest observed difference", () => {
    const findings = keyFindings(
      results([row(2, 1, 0.65), row(1, 2, 0.6)], [paired(2, 0.05, 0.4)]),
      labelOf,
    );
    expect(findings[1].kind).toBe("null");
    expect(text(findings[1])).toContain(
      "No condition differs from the baseline",
    );
    expect(text(findings[1])).toContain("p = 0.400");
  });

  it("withholds tests while interim", () => {
    const findings = keyFindings(
      results([row(2, 1, 0.65), row(1, 2, 0.6)], [paired(2, 0.05, null)], {
        interim: true,
      }),
      labelOf,
    );
    expect(findings[1].kind).toBe("interim");
    expect(text(findings[1])).not.toContain("p =");
  });

  it("calls out ties for first place", () => {
    const findings = keyFindings(
      results([row(1, 1, 0.5), row(2, 1, 0.5)], []),
      labelOf,
    );
    expect(text(findings[0])).toContain(
      "2 conditions share the highest mean pass rate",
    );
  });

  it("lists missing grades, mixed fingerprints and too few apps as limitations", () => {
    const findings = keyFindings(
      results(
        [
          row(1, 1, 1, {
            n_apps: 1,
            n_cells: 4,
            n_missing: {
              failed: 1,
              ungraded: 0,
              excluded_invalid: 0,
              excluded_truncated: 1,
              not_common: 0,
            },
            mixed_fingerprints: { count: 2, majority_share: 0.75 },
          }),
        ],
        [],
      ),
      labelOf,
    );
    const caveats = findings.filter((f) => f.kind === "caveat").map(text);
    expect(caveats).toHaveLength(3);
    expect(caveats[0]).toContain("2 of 4 cells");
    expect(caveats[1]).toContain(
      "1 condition mixes configuration fingerprints",
    );
    expect(caveats[2]).toContain("With 1 app per condition");
  });
});

describe("resolveExperimentTab", () => {
  it("accepts the sections and the names older links used", () => {
    expect(resolveExperimentTab("results")).toBe("results");
    expect(resolveExperimentTab("overview")).toBe("summary");
    expect(resolveExperimentTab("design")).toBe("method");
    expect(resolveExperimentTab("runs")).toBe("data");
    expect(resolveExperimentTab("automation")).toBe("operations");
    expect(resolveExperimentTab("bogus")).toBeNull();
    expect(resolveExperimentTab(null)).toBeNull();
  });
});
