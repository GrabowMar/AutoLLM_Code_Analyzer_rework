import { describe, expect, it } from "vitest";
import type { LibraryItemOut } from "$lib/api/v3/types";
import { findPinnedPolicy, launchStudyCap } from "./policy";

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
