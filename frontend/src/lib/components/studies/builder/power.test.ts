import { describe, expect, it } from "vitest";
import {
  nForHalfWidth,
  normalQuantile,
  pairedDetectableDifference,
  pooledHalfWidth,
  precisionStopSaving,
  wilsonHalfWidth,
  wilsonInterval,
  Z95,
} from "./power";

describe("normalQuantile", () => {
  it("matches published quantiles", () => {
    expect(Z95).toBeCloseTo(1.959964, 6);
    expect(normalQuantile(0.8)).toBeCloseTo(0.841621, 6);
    expect(normalQuantile(0.5)).toBeCloseTo(0, 9);
    expect(normalQuantile(0.01)).toBeCloseTo(-2.326348, 6);
    expect(normalQuantile(0.999)).toBeCloseTo(3.090232, 5);
  });

  it("handles the ends", () => {
    expect(normalQuantile(0)).toBe(-Infinity);
    expect(normalQuantile(1)).toBe(Infinity);
    expect(Number.isNaN(normalQuantile(2))).toBe(true);
  });
});

describe("Wilson interval", () => {
  // Published values (Newcombe 1998, method 3; Brown, Cai & DasGupta 2001).
  it("15/30 → (0.3315, 0.6685)", () => {
    const ci = wilsonInterval(15, 30)!;
    expect(ci.low).toBeCloseTo(0.3315, 4);
    expect(ci.high).toBeCloseTo(0.6685, 4);
  });

  it("81/263 → (0.2553, 0.3662) (Newcombe example)", () => {
    const ci = wilsonInterval(81, 263)!;
    expect(ci.low).toBeCloseTo(0.2553, 4);
    expect(ci.high).toBeCloseTo(0.3662, 4);
  });

  it("0/10 → (0, 0.2775)", () => {
    const ci = wilsonInterval(0, 10)!;
    expect(ci.low).toBe(0);
    expect(ci.high).toBeCloseTo(0.2775, 4);
  });

  it("refuses impossible counts", () => {
    expect(wilsonInterval(3, 0)).toBeNull();
    expect(wilsonInterval(5, 4)).toBeNull();
  });

  it("half-width at p=.5 shrinks with n and matches the interval", () => {
    expect(wilsonHalfWidth(0.5, 30)).toBeCloseTo(0.1685, 4);
    expect(wilsonHalfWidth(0.5, 100)).toBeLessThan(wilsonHalfWidth(0.5, 30));
    expect(wilsonHalfWidth(0.5, 0)).toBeNaN();
  });
});

describe("nForHalfWidth", () => {
  it("is the smallest n reaching the target", () => {
    const n = nForHalfWidth(0.5, 0.1)!;
    expect(wilsonHalfWidth(0.5, n)).toBeLessThanOrEqual(0.1);
    expect(wilsonHalfWidth(0.5, n - 1)).toBeGreaterThan(0.1);
    expect(n).toBe(93);
  });

  it("returns null when the cap cannot reach it", () => {
    expect(nForHalfWidth(0.5, 0.01, 100)).toBeNull();
    expect(nForHalfWidth(0.5, 0)).toBeNull();
  });
});

describe("pairedDetectableDifference", () => {
  it("uses (z_α/2 + z_power)·√(Var/units)", () => {
    // Var = 2·0.2²·0.5 + 2·0.25/5 = 0.04 + 0.1 = 0.14; z = 1.959964 + 0.841621 = 2.801585.
    const mde = pairedDetectableDifference({ units: 10, repeats: 5 })!;
    expect(mde).toBeCloseTo(2.801585 * Math.sqrt(0.14 / 10), 5);
  });

  it("more units or repeats detect smaller differences; one unit gives none", () => {
    const base = pairedDetectableDifference({ units: 10, repeats: 5 })!;
    expect(pairedDetectableDifference({ units: 40, repeats: 5 })!).toBeLessThan(
      base,
    );
    expect(
      pairedDetectableDifference({ units: 10, repeats: 20 })!,
    ).toBeLessThan(base);
    expect(pairedDetectableDifference({ units: 1, repeats: 5 })).toBeNull();
  });

  it("stronger pairing (higher ρ) lowers the detectable difference", () => {
    expect(
      pairedDetectableDifference({ units: 10, repeats: 5, rho: 0.9 })!,
    ).toBeLessThan(
      pairedDetectableDifference({ units: 10, repeats: 5, rho: 0.1 })!,
    );
  });
});

describe("pooledHalfWidth (between-target variance)", () => {
  it("is z·√((τ² + p(1−p)/n)/T)", () => {
    expect(pooledHalfWidth(0.5, 30, 4, 0.1)!).toBeCloseTo(
      Z95 * Math.sqrt((0.01 + 0.25 / 30) / 4),
      8,
    );
  });

  it("between-target variance widens the pooled interval", () => {
    expect(pooledHalfWidth(0.5, 30, 4, 0.2)!).toBeGreaterThan(
      pooledHalfWidth(0.5, 30, 4, 0)!,
    );
    expect(pooledHalfWidth(0.5, 30, 0, 0)).toBeNull();
  });
});

describe("precisionStopSaving", () => {
  it("stops where the half-width reaches the threshold, never before n_min", () => {
    const out = precisionStopSaving(0.5, 0.15, 10, 200)!;
    expect(out.stopAt).toBe(nForHalfWidth(0.5, 0.15));
    expect(out.saving).toBeCloseTo((200 - out.stopAt) / 200, 9);
    expect(precisionStopSaving(0.5, 0.45, 25, 200)!.stopAt).toBe(25);
  });

  it("saves nothing when the threshold is out of reach", () => {
    expect(precisionStopSaving(0.5, 0.01, 10, 30)).toEqual({
      stopAt: 30,
      saving: 0,
    });
  });
});
