import { describe, expect, it } from "vitest";
import { slotStudyHref, validationLabel } from "./validation";

describe("slot study link", () => {
  it("passes only usable targets and their sets, in order", () => {
    const href = slotStudyHref(
      ["s1", "s2", "s3"],
      [
        { id: "t1", slot_set: "s1", usable: true },
        { id: "t2", slot_set: "s2", usable: false },
        { id: "t3", slot_set: "s3", usable: true },
      ],
    );
    expect(href).toBe(
      "/experiments/new?mode=slot&slot_sets=s1,s3&targets=t1,t3",
    );
  });

  it("still opens the builder in slot mode with nothing usable", () => {
    expect(
      slotStudyHref(["s1"], [{ id: "t1", slot_set: "s1", usable: false }]),
    ).toBe("/experiments/new?mode=slot");
  });

  it("labels validation states", () => {
    const v = (status: "pending" | "ok" | "uninformative" | "error") => ({
      validation: {
        status,
        original_pass: {},
        holed_fail: {},
        flaky_checks: [],
        dropped_checks: [],
        discriminating_checks: [],
        probe_stability: {},
        runs: [],
        at: null,
      },
    });
    expect(validationLabel(v("pending"))).toBe("validating");
    expect(validationLabel(v("ok"))).toBe("validated");
    expect(validationLabel(v("uninformative"))).toBe("uninformative");
  });
});
