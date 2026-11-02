import { describe, expect, it } from "vitest";
import {
  formatPairwiseValue,
  isValidPairwiseValue,
  pairwiseKey,
  pairwiseOptions,
  pairwiseSide,
  parsePairwiseKey,
  slotLetter,
} from "./pairwise";

describe("pairwise options", () => {
  it("puts A on the left for both scales", () => {
    expect(pairwiseOptions("ab_tie").map((o) => o.key)).toEqual([
      "A",
      "tie",
      "B",
    ]);
    expect(pairwiseOptions("five_point").map((o) => o.value)).toEqual([
      -2, -1, 0, 1, 2,
    ]);
    expect(pairwiseOptions("five_point")[0].side).toBe("A");
    expect(pairwiseOptions("five_point")[4].side).toBe("B");
  });
});

describe("pairwise values", () => {
  it("validates per scale", () => {
    expect(isValidPairwiseValue("A", "ab_tie")).toBe(true);
    expect(isValidPairwiseValue("tie", "ab_tie")).toBe(true);
    expect(isValidPairwiseValue(0, "ab_tie")).toBe(false);
    expect(isValidPairwiseValue(-2, "five_point")).toBe(true);
    expect(isValidPairwiseValue(2, "five_point")).toBe(true);
    expect(isValidPairwiseValue(3, "five_point")).toBe(false);
    expect(isValidPairwiseValue(1.5, "five_point")).toBe(false);
    expect(isValidPairwiseValue("B", "five_point")).toBe(false);
    expect(isValidPairwiseValue(undefined, "ab_tie")).toBe(false);
  });

  it("round-trips through radio keys", () => {
    for (const scale of ["ab_tie", "five_point"] as const) {
      for (const option of pairwiseOptions(scale)) {
        const key = pairwiseKey(option.value, scale);
        expect(parsePairwiseKey(key, scale)).toBe(option.value);
      }
    }
    expect(pairwiseKey(undefined, "ab_tie")).toBe("");
    expect(pairwiseKey(5, "five_point")).toBe("");
    expect(parsePairwiseKey("", "five_point")).toBeUndefined();
    expect(parsePairwiseKey("A", "five_point")).toBeUndefined();
  });

  it("formats values as displayed sides, never as app identities", () => {
    expect(formatPairwiseValue("B", "ab_tie")).toBe("B is better");
    expect(formatPairwiseValue(-1, "five_point")).toBe("A is slightly better");
    expect(formatPairwiseValue(0, "five_point")).toBe("About the same");
    expect(formatPairwiseValue(undefined, "five_point")).toBe("—");
    expect(pairwiseSide(2, "five_point")).toBe("B");
    expect(pairwiseSide("tie", "ab_tie")).toBe("tie");
    expect(pairwiseSide(null, "ab_tie")).toBeNull();
  });

  it("letters slots", () => {
    expect(slotLetter("A")).toBe("A");
    expect(slotLetter("single")).toBe("");
  });
});
