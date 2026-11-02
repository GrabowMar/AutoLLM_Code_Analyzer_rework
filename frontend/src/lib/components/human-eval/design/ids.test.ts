import { describe, expect, it } from "vitest";
import {
  ID_PATTERN,
  duplicateIds,
  idFromLabel,
  isValidId,
  slugifyId,
  uniqueId,
} from "./ids";

describe("slugifyId", () => {
  it("lowercases, strips accents and joins words with underscores", () => {
    expect(slugifyId("Ease of use (overall)")).toBe("ease_of_use_overall");
    expect(slugifyId("Café Crème")).toBe("cafe_creme");
    expect(slugifyId("  --Login / Logout--  ")).toBe("login_logout");
  });

  it("returns empty for labels without usable characters", () => {
    expect(slugifyId("!!! ???")).toBe("");
    expect(slugifyId("")).toBe("");
  });

  it("caps the id at 40 characters without a trailing underscore", () => {
    const id = slugifyId(
      "a very long criterion label that keeps going and going",
    );
    expect(id.length).toBeLessThanOrEqual(40);
    expect(id.endsWith("_")).toBe(false);
    expect(ID_PATTERN.test(id)).toBe(true);
  });
});

describe("uniqueId", () => {
  it("keeps a free base and suffixes taken ones", () => {
    expect(uniqueId("usability", [])).toBe("usability");
    expect(uniqueId("usability", ["usability"])).toBe("usability_2");
    expect(uniqueId("usability", ["usability", "usability_2"])).toBe(
      "usability_3",
    );
  });

  it("shortens the base so the suffix still fits", () => {
    const base = "x".repeat(40);
    const id = uniqueId(base, [base]);
    expect(id).toBe(`${"x".repeat(38)}_2`);
    expect(isValidId(id)).toBe(true);
  });

  it("falls back to 'item' for an empty base", () => {
    expect(uniqueId("", [])).toBe("item");
  });
});

describe("idFromLabel", () => {
  it("derives a unique id from the label", () => {
    expect(idFromLabel("Overall quality", ["overall_quality"])).toBe(
      "overall_quality_2",
    );
  });

  it("uses the fallback when the label has no usable characters", () => {
    expect(idFromLabel("???", [], "criterion")).toBe("criterion");
    expect(idFromLabel("???", ["criterion"], "criterion")).toBe("criterion_2");
  });

  it("always produces ids the backend accepts", () => {
    for (const label of ["Ünïcödé ✓", "123 go", "a".repeat(80), "x-y-z"]) {
      expect(isValidId(idFromLabel(label, []))).toBe(true);
    }
  });
});

describe("duplicateIds", () => {
  it("lists each repeated id once", () => {
    expect(duplicateIds(["a", "b", "a", "c", "b", "a"])).toEqual(["a", "b"]);
    expect(duplicateIds(["a", "b"])).toEqual([]);
  });
});
