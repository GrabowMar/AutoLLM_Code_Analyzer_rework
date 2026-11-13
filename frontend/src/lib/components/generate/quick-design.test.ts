import { describe, expect, it } from "vitest";
import {
  defaultDesign,
  setLevels,
} from "$lib/components/studies/builder/design";
import {
  buildQuickDesign,
  emptyQuickAppForm,
  quickStudyName,
  validateQuickAppForm,
} from "./quick-design";

const FILLED = emptyQuickAppForm({
  task: "crud_todo_list@1",
  stack: "flask-react",
  model: "qwen/qwen3-coder",
});

describe("quick app design", () => {
  it("pre-selects the resolver defaults, frugal and security", () => {
    const form = emptyQuickAppForm();
    expect(form.harness_preset).toBe("opencode-native");
    expect(form.style).toBe("model-choice");
    expect(form.auth_mode).toBe("contract_bearer");
    expect(form.prose_variant).toBe("standard");
    expect(form.resource_policy).toBe("frugal");
    expect(form.eval_level).toBe("security");
    expect(form.prompt_set).toBe("");
  });

  it("requires task, stack, model, harness and Budget preset, and a valid seed", () => {
    expect(
      Object.keys(validateQuickAppForm(emptyQuickAppForm())).sort(),
    ).toEqual(["model", "stack", "task"]);
    expect(validateQuickAppForm(FILLED)).toEqual({});
    expect(
      validateQuickAppForm({ ...FILLED, base_seed: -1 }).base_seed,
    ).toBeTruthy();
    expect(
      validateQuickAppForm({ ...FILLED, base_seed: 1.5 }).base_seed,
    ).toBeTruthy();
    expect(
      validateQuickAppForm({ ...FILLED, harness_preset: "" }).harness_preset,
    ).toBeTruthy();
  });

  it("builds what the experiment builder produces for the same choices", () => {
    const design = buildQuickDesign(FILLED, 42);
    let expected = defaultDesign("app", "exploratory", { base: 42, order: 42 });
    expected = setLevels(expected, "model", ["qwen/qwen3-coder"]);
    expected = setLevels(expected, "harness_preset", ["opencode-native"]);
    expected = setLevels(expected, "stack_profile", ["flask-react"]);
    expected = setLevels(expected, "style", ["model-choice"]);
    expected = setLevels(expected, "auth_mode", ["contract_bearer"]);
    expected = setLevels(expected, "prose_variant", ["standard"]);
    expected = {
      ...expected,
      units: { tasks: ["crud_todo_list@1"], slot_sets: [], slot_targets: [] },
      repeats: 1,
    };
    expect(design).toEqual(expected);
    expect(
      design.factors.every((f) => f.role === "fixed" && f.levels.length === 1),
    ).toBe(true);
    expect(design.grid.type).toBe("full");
    expect(design.kind).toBe("exploratory");
    expect(design.resource_policy).toBe("frugal");
    expect(design.factors.some((f) => f.key === "resource_policy")).toBe(false);
  });

  it("adds the advanced choices: prompt set, evaluation level and seed", () => {
    const design = buildQuickDesign(
      {
        ...FILLED,
        prompt_set: "app-terse",
        eval_level: "build",
        base_seed: 7,
        resource_policy: "standard",
      },
      42,
    );
    expect(design.factors.find((f) => f.key === "prompt_set")?.levels).toEqual([
      "app-terse",
    ]);
    expect(design.evaluation.eval_level).toBe("build");
    expect(design.base_seed).toBe(7);
    expect(design.order_seed).toBe(7);
    expect(design.resource_policy).toBe("standard");
  });

  it("drops a factor left empty (the resolver default applies)", () => {
    const design = buildQuickDesign({ ...FILLED, style: "" }, 1);
    expect(design.factors.some((f) => f.key === "style")).toBe(false);
  });

  it("names the study after task, stack and model", () => {
    expect(quickStudyName(FILLED)).toBe(
      "Quick · crud_todo_list · flask-react · qwen3-coder",
    );
  });
});
