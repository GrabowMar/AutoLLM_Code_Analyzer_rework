import { describe, expect, it } from "vitest";
import { defaultDesign, setKind, setLevels } from "./design";
import {
  STEP_IDS,
  guardConditions,
  isStepId,
  issuesByStep,
  missingChoices,
  nextStep,
  prevStep,
  requiredSteps,
  stepOfIssue,
  stepsFor,
} from "./steps";

const seeds = { base: 1, order: 2 };

describe("steps", () => {
  it("names the unit step after the mode", () => {
    expect(stepsFor("app").find((s) => s.id === "units")?.label).toBe("Tasks");
    expect(stepsFor("slot").find((s) => s.id === "units")?.label).toBe(
      "Targets",
    );
    expect(stepsFor("app").map((s) => s.id)).toEqual([...STEP_IDS]);
  });

  it("walks forward and back and stops at the ends", () => {
    expect(nextStep("setup")).toBe("models");
    expect(nextStep("review")).toBeNull();
    expect(prevStep("setup")).toBeNull();
    expect(prevStep("review")).toBe("measurement");
    expect(isStepId("models")).toBe(true);
    expect(isStepId("nope")).toBe(false);
    expect(isStepId(null)).toBe(false);
  });

  it("lists the open required choices of an app design", () => {
    const d = defaultDesign("app", "exploratory", seeds);
    expect(missingChoices(d).map((m) => m.step)).toEqual([
      "models",
      "units",
      "treatments",
    ]);
    let done = setLevels(d, "model", ["openai/gpt-5"]);
    done = setLevels(done, "stack_profile", ["django-react@1"]);
    done = { ...done, units: { ...done.units, tasks: ["todo@1"] } };
    expect(missingChoices(done)).toEqual([]);
    expect(missingChoices(setKind(done, "confirmatory"))).toEqual([]);
    expect(
      missingChoices({ ...setKind(done, "confirmatory"), analysis_plan: null }),
    ).toEqual([{ step: "measurement", label: "Write the analysis plan" }]);
  });

  it("needs targets but no stack in slot mode", () => {
    const d = setLevels(defaultDesign("slot", "exploratory", seeds), "model", [
      "m",
    ]);
    expect(missingChoices(d)).toEqual([
      { step: "units", label: "Choose at least one slot target" },
    ]);
    expect(requiredSteps(d).has("treatments")).toBe(false);
    expect(
      requiredSteps(defaultDesign("app", "exploratory", seeds)).has(
        "treatments",
      ),
    ).toBe(true);
  });

  it("maps issues to steps by path, then by code", () => {
    expect(stepOfIssue({ code: "x", path: "factors.model" })).toBe("models");
    expect(stepOfIssue({ code: "x", path: "factors.prompt_set" })).toBe(
      "treatments",
    );
    expect(stepOfIssue({ code: "x", path: "units.tasks" })).toBe("units");
    expect(
      stepOfIssue({ code: "x", path: "resource_policy.study.stop_rules" }),
    ).toBe("replication");
    expect(stepOfIssue({ code: "x", path: "evaluation.eval_suite" })).toBe(
      "measurement",
    );
    expect(stepOfIssue({ code: "estimate_exceeds_cap", path: null })).toBe(
      "replication",
    );
    expect(stepOfIssue({ code: "something_new", path: null })).toBe("review");
  });

  it("counts errors and warnings per step, skipping to-dos and notes", () => {
    const counts = issuesByStep([
      { code: "no_models", severity: "error", detail: "" },
      { code: "aliasing", severity: "error", detail: "" },
      { code: "few_clusters", severity: "warn", detail: "" },
      { code: "unbalanced_grid", severity: "info", detail: "" },
    ]);
    expect(counts.models).toEqual({ errors: 0, warnings: 0 });
    expect(counts.treatments).toEqual({ errors: 1, warnings: 0 });
    expect(counts.units).toEqual({ errors: 0, warnings: 1 });
  });

  it("never counts conditions while a required factor is missing", () => {
    const d = defaultDesign("app", "exploratory", seeds);
    expect(guardConditions(setLevels(d, "stack_profile", ["s"]), 1)).toBe(0);
    expect(guardConditions(setLevels(d, "model", ["m"]), 1)).toBe(0);
    const both = setLevels(setLevels(d, "model", ["m"]), "stack_profile", [
      "s",
    ]);
    expect(guardConditions(both, 3)).toBe(3);
    expect(guardConditions(both, null)).toBeNull();
  });
});
