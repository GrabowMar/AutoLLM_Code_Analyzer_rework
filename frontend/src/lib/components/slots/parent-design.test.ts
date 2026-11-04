import { describe, expect, it } from "vitest";
import {
  buildParentDesign,
  emptyParentAppForm,
  launchQuote,
  parentStudyName,
  validateParentAppForm,
} from "./parent-design";

describe("generate-a-parent form", () => {
  it("requires task, stack, model, budget preset and eval suite", () => {
    const errors = validateParentAppForm({
      ...emptyParentAppForm(),
      resource_policy: "",
    });
    expect(Object.keys(errors).sort()).toEqual([
      "eval_suite",
      "model",
      "resource_policy",
      "stack",
      "task",
    ]);
    const ok = {
      ...emptyParentAppForm("crud_todo_list@1"),
      stack: "flask-react",
      model: "openai/gpt-5-mini",
      eval_suite: "eval-v2@1",
    };
    expect(validateParentAppForm(ok)).toEqual({});
    expect(validateParentAppForm({ ...ok, base_seed: -3 })).toHaveProperty(
      "base_seed",
    );
  });

  it("builds a one-cell exploratory app design with fixed factors and frugal budget", () => {
    const form = {
      ...emptyParentAppForm("crud_todo_list@1"),
      stack: "flask-react",
      model: "openai/gpt-5-mini",
      harness_preset: "claude-code-native",
      eval_suite: "eval-v2@1",
    };
    const design = buildParentDesign(form, 1234);
    expect(design).toMatchObject({
      mode: "app",
      kind: "exploratory",
      repeats: 1,
      base_seed: 1234,
      order_seed: 1234,
      resource_policy: "frugal",
      units: { tasks: ["crud_todo_list@1"], slot_sets: [], slot_targets: [] },
      evaluation: { eval_suite: "eval-v2@1", eval_level: "security" },
    });
    expect(design.factors).toEqual([
      { key: "model", role: "fixed", levels: ["openai/gpt-5-mini"] },
      { key: "stack_profile", role: "fixed", levels: ["flask-react"] },
      { key: "harness_preset", role: "fixed", levels: ["claude-code-native"] },
      { key: "resource_policy", role: "fixed", levels: ["frugal"] },
    ]);
    // a chosen seed wins over the random one
    expect(buildParentDesign({ ...form, base_seed: 7 }, 1234).base_seed).toBe(
      7,
    );
    expect(parentStudyName(form)).toBe(
      "Parent · crud_todo_list@1 · flask-react · gpt-5-mini",
    );
  });

  it("reads the cost quote of a launch that needs confirmation and ignores other errors", () => {
    const err = {
      detail:
        "p90 estimate 3.20 USD exceeds 1.00 USD; confirm with confirm_usd",
      code: "confirmation_required",
      data: { p90_total: 3.2, expected_total: 1.9, threshold: 1 },
    };
    expect(launchQuote(err)).toEqual({
      p90_total: 3.2,
      expected_total: 1.9,
      threshold: 1,
    });
    expect(
      launchQuote({
        detail: "x",
        code: "plan_has_errors",
        data: { p90_total: 3 },
      }),
    ).toBeNull();
    expect(launchQuote({ code: "confirmation_required" })).toBeNull();
    expect(launchQuote(new Error("network"))).toBeNull();
  });
});
