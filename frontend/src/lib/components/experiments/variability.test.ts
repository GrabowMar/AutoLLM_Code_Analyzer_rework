import { describe, expect, it } from "vitest";
import type { VariabilityCell } from "$lib/api/variability";
import {
  cellValues,
  componentColor,
  domainPos,
  formatAgreement,
  formatCompact,
  formatMetricValue,
  formatRatio,
  formatShare,
  plannerGrid,
  repeatsLabel,
  stripDomain,
} from "./variability";

describe("formatMetricValue", () => {
  it("prints each unit and a dash for unmeasured values (never 0)", () => {
    expect(formatMetricValue(null, "USD")).toBe("—");
    expect(formatMetricValue(undefined, "")).toBe("—");
    expect(formatMetricValue(0.03211787, "USD")).toBe("$0.0321");
    expect(formatMetricValue(12.5, "USD")).toBe("$12.50");
    expect(formatMetricValue(1, "share")).toBe("100%");
    expect(formatMetricValue(0.2667, "share")).toBe("27%");
    expect(formatMetricValue(0.57, "%")).toBe("0.6%");
    expect(formatMetricValue(45, "s")).toBe("45 s");
    expect(formatMetricValue(1190.64, "s")).toBe("19.8 min");
    expect(formatMetricValue(1849425, "tokens")).toBe("1.8M");
    expect(formatMetricValue(1141, "lines")).toBe("1,141");
  });

  it("compacts plain numbers", () => {
    expect(formatCompact(0)).toBe("0");
    expect(formatCompact(2.5)).toBe("2.5");
    expect(formatCompact(2)).toBe("2");
    expect(formatCompact(12.34)).toBe("12.3");
    expect(formatCompact(0.03456)).toBe("0.035");
    expect(formatCompact(25000)).toBe("25K");
    expect(formatCompact(Number.NaN)).toBe("—");
  });
});

describe("shares and ratios", () => {
  it("formats shares, ratios and agreement counts", () => {
    expect(formatShare(0.4247)).toBe("42%");
    expect(formatShare(null)).toBe("—");
    expect(formatRatio(0.27467)).toBe("0.27");
    expect(formatRatio(undefined)).toBe("—");
    expect(formatAgreement(5, 6)).toBe("5/6 (83%)");
    expect(formatAgreement(0, 0)).toBe("—");
    expect(repeatsLabel(2, 2)).toBe("r = 2");
    expect(repeatsLabel(2, 3)).toBe("r = 2–3");
    expect(repeatsLabel(null, null)).toBe("—");
  });

  it("gives the variance sources the first four categorical slots", () => {
    expect(componentColor("condition")).toBe("var(--chart-1)");
    expect(componentColor("repeat")).toBe("var(--chart-4)");
  });
});

describe("strip plot axis", () => {
  it("pads the domain, keeps non-negative data at or above 0 and shares on 0–1", () => {
    expect(stripDomain([], "")).toEqual([0, 1]);
    expect(stripDomain([0.2, 0.9], "share")).toEqual([0, 1]);
    const [lo, hi] = stripDomain([10, 20]);
    expect(lo).toBeCloseTo(9.4);
    expect(hi).toBeCloseTo(20.6);
    expect(stripDomain([0, 5])[0]).toBe(0);
    expect(stripDomain([4, 4])).toEqual([3.6, 4.4]);
    expect(stripDomain([0, 0])).toEqual([-1, 1]);
  });

  it("positions values and clamps them", () => {
    expect(domainPos(5, [0, 10])).toBe("50.00%");
    expect(domainPos(20, [0, 10])).toBe("100.00%");
    expect(domainPos(3, [3, 3])).toBe("50.00%");
  });

  it("collects measured values of a metric across cells", () => {
    const cell = (values: (number | null)[]) =>
      ({ values: { cost: values } }) as unknown as VariabilityCell;
    expect(cellValues([cell([1, null]), cell([2])], "cost")).toEqual([1, 2]);
    expect(cellValues([cell([1])], "missing")).toEqual([]);
  });
});

describe("plannerGrid", () => {
  it("indexes rows by apps and repeats", () => {
    const rows = [
      {
        apps: 10,
        repeats: 1,
        runs_per_condition: 10,
        mean_half_width: 0.2,
        delta_half_width: 0.3,
      },
      {
        apps: 5,
        repeats: 2,
        runs_per_condition: 10,
        mean_half_width: 0.25,
        delta_half_width: null,
      },
      {
        apps: 5,
        repeats: 1,
        runs_per_condition: 5,
        mean_half_width: 0.3,
        delta_half_width: 0.4,
      },
    ];
    const grid = plannerGrid(rows);
    expect(grid.apps).toEqual([5, 10]);
    expect(grid.repeats).toEqual([1, 2]);
    expect(grid.at(5, 2)?.mean_half_width).toBe(0.25);
    expect(grid.at(10, 2)).toBeUndefined();
  });
});
