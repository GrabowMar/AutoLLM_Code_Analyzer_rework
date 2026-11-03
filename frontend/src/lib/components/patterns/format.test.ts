import { describe, expect, it } from "vitest";
import type { ComparisonRow, PrevalenceRow } from "$lib/api/v3/types";
import {
  ciBar,
  fmtAudit,
  fmtCI,
  fmtComparisonN,
  fmtDecision,
  fmtDiff,
  fmtDiffCI,
  fmtN,
  fmtPrevalence,
  fmtProportion,
  fmtSignificance,
  pooledSignificance,
  fmtUnmeasured,
  prevalenceFlags,
  splitFeatureKey,
} from "./format";

function row(extra: Partial<PrevalenceRow> = {}): PrevalenceRow {
  return {
    condition: 1,
    target: "t1",
    feature: "sql_string_concat",
    scope: "span",
    k: 12,
    n_measured: 30,
    n_itt: 32,
    errors: 1,
    na: 0,
    missing: 1,
    p: 0.4,
    ci_low: 0.2459,
    ci_high: 0.5768,
    method: "wilson",
    adaptive_n: false,
    audit: null,
    ...extra,
  };
}

describe("prevalence CI formatting", () => {
  it("prints the proportion with its Wilson interval", () => {
    expect(fmtProportion(0.4)).toBe("40.0%");
    expect(fmtProportion(0)).toBe("0%");
    expect(fmtProportion(1)).toBe("100%");
    expect(fmtCI(0.2459, 0.5768)).toBe("[24.6%, 57.7%]");
    expect(fmtPrevalence(row())).toBe("40.0% [24.6%, 57.7%]");
  });

  it("never shows an unmeasured row as 0 %", () => {
    expect(
      fmtPrevalence(
        row({ k: 0, n_measured: 0, p: null, ci_low: null, ci_high: null }),
      ),
    ).toBe("not measured");
    expect(fmtCI(null, 0.3)).toBe("—");
  });

  it("computes CI-bar geometry on a 0–1 track", () => {
    expect(ciBar(0.25, 0.5, 0.4)).toEqual({ left: 25, width: 25, point: 40 });
    expect(ciBar(0, 0, 0)).toEqual({ left: 0, width: 0.5, point: 0 });
    expect(ciBar(null, 0.5, 0.4)).toBeNull();
    expect(ciBar(-0.1, 1.2, null)).toEqual({
      left: 0,
      width: 100,
      point: null,
    });
  });
});

describe("prevalence n formatting", () => {
  it("shows measured n and ITT n side by side", () => {
    expect(fmtN(row())).toBe("12/30 measured · ITT 32");
    expect(fmtN(row({ n_itt: null }))).toBe("12/30 measured");
  });

  it("names what the measured n leaves out", () => {
    expect(fmtUnmeasured(row())).toBe("1 detector error · 1 without output");
    expect(fmtUnmeasured(row({ errors: 2, na: 3, missing: 0 }))).toBe(
      "2 detector errors · 3 n/a",
    );
    expect(fmtUnmeasured(row({ errors: 0, missing: 0 }))).toBe("");
  });

  it("flags adaptive n, low effective n, bounds and provisional rows", () => {
    const flags = prevalenceFlags(
      row({
        adaptive_n: true,
        looks: 3,
        low_effective_n: true,
        bounds: { low: 0.3, high: 0.5 },
        provisional: true,
      }),
    );
    expect(flags.map((f) => f.code)).toEqual([
      "provisional",
      "adaptive_n",
      "low_effective_n",
      "bounds",
    ]);
    expect(flags[1].title).toMatch(/3 looks/);
    expect(prevalenceFlags(row())).toEqual([]);
  });

  it("formats the audit or its status", () => {
    expect(fmtAudit(row({ audit: { precision: 0.9, npv: 0.95 } }))).toBe(
      "precision 0.90 · NPV 0.95",
    );
    expect(fmtAudit(row({ audit_status: "not_audited" }))).toBe("not audited");
    expect(fmtAudit(row({ audit_status: "in_progress" }))).toBe(
      "audit in progress",
    );
  });
});

describe("comparisons", () => {
  const base: ComparisonRow = {
    feature: "f",
    a: 1,
    b: 2,
    diff: 0.125,
    ci_low: -0.05,
    ci_high: 0.3,
    method: "paired",
    n_pairs: 28,
    p: 0.0123,
    q: null,
    family_size: 1,
    planned: true,
  };

  it("prints differences in points with a sign", () => {
    expect(fmtDiff(0.125)).toBe("+12.5 pts");
    expect(fmtDiff(-0.03)).toBe("−3.0 pts");
    expect(fmtDiff(0)).toBe("±0.0 pts");
    expect(fmtDiffCI(-0.05, 0.3)).toBe("[−5.0, 30.0]");
  });

  it("shows p for planned rows, q for exploratory rows and withholds both while interim", () => {
    expect(fmtSignificance(base)).toBe("p = 0.012");
    expect(
      fmtSignificance({ ...base, planned: false, p: null, q: 0.0004 }),
    ).toBe("q < 0.001");
    expect(fmtSignificance({ ...base, interim: true })).toBe(
      "withheld (interim)",
    );
  });

  it("shows the plan-adjusted p and the decision for planned rows, withheld while interim", () => {
    const planned: ComparisonRow = {
      ...base,
      family_size: 4,
      p_adjusted: 0.0492,
      correction: "holm",
      alpha: 0.05,
      significant: true,
    };
    expect(fmtSignificance(planned)).toBe("p = 0.012 (adj. holm 0.049)");
    expect(fmtDecision(planned)).toBe("significant (α 0.05)");
    const notSig = {
      ...planned,
      p: 0.03,
      p_adjusted: 0.12,
      significant: false,
    };
    expect(fmtSignificance(notSig)).toBe("p = 0.030 (adj. holm 0.120)");
    expect(fmtDecision(notSig)).toBe("not significant (α 0.05)");
    const interim = {
      ...planned,
      interim: true,
      p: null,
      p_adjusted: null,
      significant: null,
    };
    expect(fmtSignificance(interim)).toBe("withheld (interim)");
    expect(fmtDecision(interim)).toBe("withheld (interim)");
    expect(
      fmtDecision({
        ...base,
        planned: false,
        p: null,
        q: 0.02,
        correction: "bh",
        alpha: null,
        significant: null,
      }),
    ).toBeNull();
    const pooled = pooledSignificance({
      planned: true,
      permutation_p: 0.004,
      p_adjusted: 0.016,
      correction: "bh",
      alpha: 0.01,
      significant: false,
    });
    expect(fmtSignificance(pooled)).toBe("p = 0.004 (adj. bh 0.016)");
    expect(fmtDecision(pooled)).toBe("not significant (α 0.01)");
  });

  it("states the n behind each method", () => {
    expect(fmtComparisonN(base)).toBe("28 pairs");
    expect(
      fmtComparisonN({
        ...base,
        method: "independent",
        k_a: 3,
        n_a: 30,
        k_b: 9,
        n_b: 29,
      }),
    ).toBe("3/30 vs 9/29");
  });

  it("splits variant feature keys", () => {
    expect(splitFeatureKey("sql_string_concat:span")).toEqual({
      feature: "sql_string_concat",
      scope: "span",
    });
    expect(splitFeatureKey("plain")).toEqual({ feature: "plain", scope: "" });
  });
});
