import { describe, expect, it } from "vitest";
import {
  canCreate,
  clampRepeats,
  crossedCounts,
  defaultAnalysisPlan,
  defaultDesign,
  factorsForMode,
  getLevels,
  localChecks,
  moveProseVariants,
  setKind,
  setLevels,
  setRole,
  switchMode,
} from "./design";

const seeds = { base: 11, order: 22 };

describe("defaultDesign", () => {
  it("uses the mode's repeats default, the frugal preset and the security eval level", () => {
    const app = defaultDesign("app", "exploratory", seeds);
    expect(app).toMatchObject({
      mode: "app",
      repeats: 5,
      resource_policy: "frugal",
      base_seed: 11,
      order_seed: 22,
    });
    expect(app.evaluation).toMatchObject({
      eval_suite: "eval-v2",
      eval_level: "security",
      slot: null,
    });
    const slot = defaultDesign("slot", "exploratory", seeds);
    expect(slot.repeats).toBe(30);
    expect(slot.evaluation.slot).toEqual({ mode: "full", regression: false });
  });

  it("draws 31-bit seeds by default", () => {
    const d = defaultDesign("app");
    expect(d.base_seed).toBeGreaterThanOrEqual(0);
    expect(d.base_seed).toBeLessThan(2 ** 31);
  });
});

describe("setLevels / setRole", () => {
  it("adds factors in vocabulary order with roles from the level count", () => {
    let d = defaultDesign("app", "exploratory", seeds);
    d = setLevels(d, "prose_variant", ["minimal", "standard"]);
    d = setLevels(d, "model", ["m1"]);
    expect(d.factors.map((f) => [f.key, f.role])).toEqual([
      ["model", "fixed"],
      ["prose_variant", "crossed"],
    ]);
    d = setLevels(d, "model", ["m1", "m2", "m1", " "]);
    expect(getLevels(d, "model")).toEqual(["m1", "m2"]);
    expect(d.factors[0].role).toBe("crossed");
  });

  it("drops a factor without levels and the exclusions naming it", () => {
    let d = setLevels(defaultDesign("app", "exploratory", seeds), "model", [
      "m1",
      "m2",
    ]);
    d = setLevels(d, "auth_mode", ["contract_bearer", "cookie_session"]);
    d = {
      ...d,
      grid: {
        ...d.grid,
        exclude: [{ model: "m1", auth_mode: "cookie_session" }],
      },
    };
    d = setLevels(d, "auth_mode", []);
    expect(d.factors.map((f) => f.key)).toEqual(["model"]);
    expect(d.grid.exclude).toEqual([]);
  });

  it("a fraction role makes the grid fractional and keeps two levels", () => {
    let d = setLevels(defaultDesign("app", "exploratory", seeds), "model", [
      "m1",
      "m2",
    ]);
    d = setLevels(d, "style", ["a@1", "b@1"]);
    d = setRole(d, "style", "fraction");
    expect(d.grid.type).toBe("fractional");
    expect(d.grid.fraction).toEqual({ generator: "" });
    // a fraction with a third level falls back to crossed
    d = setLevels(d, "style", ["a@1", "b@1", "c@1"]);
    expect(d.factors.find((f) => f.key === "style")?.role).toBe("crossed");
    expect(d.grid.type).toBe("full");
  });

  it("refuses fixed with several levels", () => {
    const d = setLevels(defaultDesign("app", "exploratory", seeds), "model", [
      "m1",
      "m2",
    ]);
    expect(setRole(d, "model", "fixed").factors[0].role).toBe("crossed");
  });
});

describe("modes", () => {
  it("slot mode offers no parent-fixed factor", () => {
    const keys = factorsForMode("slot").map((d) => d.key);
    expect(keys).not.toContain("stack_profile");
    expect(keys).not.toContain("prose_variant");
    expect(keys).toContain("hole_mode");
    expect(factorsForMode("app").map((d) => d.key)).not.toContain(
      "context_policy",
    );
  });

  it("switching mode keeps allowed factors and resets units and repeats", () => {
    let d = defaultDesign("app", "exploratory", seeds);
    d = setLevels(d, "model", ["m1"]);
    d = setLevels(d, "stack_profile", ["s@1"]);
    d = { ...d, units: { ...d.units, tasks: ["t@1"] } };
    const slot = switchMode(d, "slot");
    expect(slot.factors.map((f) => f.key)).toEqual(["model"]);
    expect(slot.units.tasks).toEqual([]);
    expect(slot.repeats).toBe(30);
    expect(switchMode(slot, "slot")).toBe(slot);
  });

  it("confirmatory gets the ITT analysis plan; exploratory drops it", () => {
    const d = setKind(
      defaultDesign("app", "exploratory", seeds),
      "confirmatory",
    );
    expect(d.analysis_plan).toMatchObject({
      primary_metric: "pass_rate",
      itt: true,
      sensitivity: ["exclude_truncated"],
    });
    expect(setKind(d, "exploratory").analysis_plan).toBeNull();
    expect(defaultAnalysisPlan("slot")).toMatchObject({
      primary_metric: "pattern_prevalence",
      random_effects: ["target"],
    });
  });

  it("clamps repeats to the mode's limits", () => {
    expect(clampRepeats("app", 50)).toBe(20);
    expect(clampRepeats("slot", 150)).toBe(150);
    expect(clampRepeats("slot", 0)).toBe(1);
    expect(clampRepeats("app", null)).toBe(5);
    expect(clampRepeats("app", 2.6)).toBe(3);
  });
});

describe("moveProseVariants", () => {
  it("moves legacy per-task variants into the prose_variant factor", () => {
    let d = defaultDesign("app", "exploratory", seeds);
    d = {
      ...d,
      units: {
        ...d.units,
        tasks: ["todo@1:minimal", "todo@1/exhaustive", "blog@2"],
      },
    };
    const fixed = moveProseVariants(d);
    expect(fixed.units.tasks).toEqual(["todo@1", "blog@2"]);
    expect(getLevels(fixed, "prose_variant")).toEqual([
      "minimal",
      "exhaustive",
    ]);
  });
});

describe("localChecks / canCreate / crossedCounts", () => {
  it("requires models, tasks and a stack in app mode", () => {
    const d = defaultDesign("app", "exploratory", seeds);
    expect(localChecks(d).map((c) => c.code)).toEqual([
      "no_models",
      "no_tasks",
      "no_stack",
    ]);
    expect(canCreate(d)).toBe(false);
    let ok = setLevels(d, "model", ["m1", "m2"]);
    ok = setLevels(ok, "stack_profile", ["s@1"]);
    ok = { ...ok, units: { ...ok.units, tasks: ["t@1"] } };
    expect(localChecks(ok)).toEqual([]);
    expect(canCreate(ok)).toBe(true);
    expect(crossedCounts(ok)).toEqual([
      { key: "model", label: "Model", count: 2 },
    ]);
  });

  it("requires targets in slot mode", () => {
    const d = setLevels(defaultDesign("slot", "exploratory", seeds), "model", [
      "m1",
    ]);
    expect(localChecks(d).map((c) => c.code)).toEqual(["no_targets"]);
  });
});
