import { describe, expect, it } from "vitest";
import type { LibraryItemOut } from "$lib/api/v3/types";
import {
  estimateBasisTotals,
  findPinnedPolicy,
  launchStudyCap,
} from "./policy";

function row(
  slug: string,
  version: number,
  cap: number | "auto",
): LibraryItemOut<"resource_policy"> {
  return {
    slug,
    version,
    pinned_ref: `${slug}@${version}#${"a".repeat(12)}`,
    content: { study: { max_cost_usd: cap } },
  } as unknown as LibraryItemOut<"resource_policy">;
}

describe("findPinnedPolicy", () => {
  const latest = row("frugal", 2, 20);
  const old = row("frugal", 1, 5);

  it("never substitutes the slug's latest version for a pinned older one", () => {
    expect(findPinnedPolicy("frugal@1#aaaaaaaaaaaa", [latest])).toBeUndefined();
    expect(findPinnedPolicy("frugal@1#aaaaaaaaaaaa", [latest, old])).toBe(old);
    expect(findPinnedPolicy("frugal@1", [latest, old])).toBe(old);
    expect(findPinnedPolicy("frugal@2", [latest, old])).toBe(latest);
  });

  it("falls back to the latest row only for an unpinned bare slug", () => {
    expect(findPinnedPolicy("frugal", [latest])).toBe(latest);
    expect(findPinnedPolicy("other", [latest])).toBeUndefined();
  });
});

describe("launchStudyCap", () => {
  it("sends nothing (the pinned preset's own cap applies) when the cap is unchanged", () => {
    expect(launchStudyCap(10, "fixed", 10)).toBeNull();
    expect(launchStudyCap("auto", "auto", 3.2)).toBeNull();
    expect(launchStudyCap(undefined, "auto", null)).toBeNull();
  });

  it("sends the researcher's change", () => {
    expect(launchStudyCap(10, "fixed", 12.5)).toBe(12.5);
    expect(launchStudyCap("auto", "fixed", 4)).toBe(4);
    expect(launchStudyCap(10, "auto", 10)).toBe("auto");
  });

  it("never sends an empty fixed cap", () => {
    expect(launchStudyCap(10, "fixed", null)).toBeNull();
  });
});

describe("estimateBasisTotals", () => {
  const base = {
    expected_total: 0,
    p90_total: 0,
    worst_case_total: 4,
    expected_list_total: 1.88,
    p90_list_total: 3.1,
    expected_tokens_total: 0,
    cap_usd: 3.88,
    by_condition: [],
    by_level: {},
    fit: {
      max_repeats_under_cap: null,
      max_units_under_cap: null,
      blocks_under_cap: null,
    },
    time: null,
    method: "heuristic" as const,
    inputs: {},
    issues: [],
  };

  it("uses the list totals for a list-basis cap (subscription endpoints bill $0)", () => {
    expect(estimateBasisTotals({ ...base, cap_basis: "list" })).toEqual({
      basis: "list",
      expected: 1.88,
      p90: 3.1,
    });
  });

  it("uses the billed totals for a billed cap and for plans estimated before cap_basis existed", () => {
    const billed = { ...base, expected_total: 1, p90_total: 2 };
    expect(estimateBasisTotals({ ...billed, cap_basis: "billed" })).toEqual({
      basis: "billed",
      expected: 1,
      p90: 2,
    });
    expect(estimateBasisTotals(billed)).toEqual({
      basis: "billed",
      expected: 1,
      p90: 2,
    });
  });
});
