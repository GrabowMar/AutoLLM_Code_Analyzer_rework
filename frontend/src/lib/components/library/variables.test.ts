import { describe, expect, it } from "vitest";
import type { PromptVariable } from "$lib/api/v3/types";
import appVars from "$lib/api/v3/__fixtures__/variables-app.json";
import slotVars from "$lib/api/v3/__fixtures__/variables-slot.json";
import {
  TEMPLATE_FILTERS,
  applyCompletion,
  completionList,
  completionsAt,
  documentedFields,
  usedVariables,
  variableIssues,
} from "./variables";

const APP = appVars as unknown as PromptVariable[];
const SLOT = slotVars as unknown as PromptVariable[];

describe("prompt variable autocomplete list", () => {
  it("lists every documented variable, its fields and the filter whitelist", () => {
    const items = completionList(APP, "app");
    const labels = items.map((i) => i.label);
    for (const v of APP) expect(labels).toContain(v.name);
    expect(labels).toContain("task.title");
    expect(labels).toContain("task.endpoints");
    expect(labels).toContain("stack.fragment_text");
    expect(labels).toContain("guidance.prescriptions");
    for (const f of TEMPLATE_FILTERS) expect(labels).toContain(f);
    // Slot-only variables are not offered in app mode.
    expect(labels).not.toContain("slot");
    expect(completionList(SLOT, "slot").map((i) => i.label)).toContain(
      "slot.hole_marker",
    );
  });

  it("reads sub-fields from the description, not from nested braces", () => {
    const guidance = APP.find((v) => v.name === "guidance")!;
    expect(documentedFields(guidance)).toEqual(
      expect.arrayContaining([
        "security_hint",
        "cwes",
        "prescriptions",
        "fragment_text",
      ]),
    );
    expect(documentedFields(guidance)).not.toContain("purpose");
  });

  it("completes inside tags only, by prefix, with fields after a dot and filters after a pipe", () => {
    const items = completionList(APP, "app");
    expect(completionsAt(items, "Build ta", 8)).toBeNull();
    const top = completionsAt(items, "{{ ta", 5)!;
    expect(top.context).toEqual({ prefix: "ta", from: 3, filter: false });
    expect(top.items.map((i) => i.label)).toEqual(["task"]);
    const fields = completionsAt(items, "{{ task.ti", 10)!;
    expect(fields.items.map((i) => i.label)).toEqual(["task.title"]);
    const filters = completionsAt(items, "{{ task.title | up", 18)!;
    expect(filters.context.filter).toBe(true);
    expect(filters.items.map((i) => i.label)).toEqual(["upper"]);
    expect(
      completionsAt(items, "{% for e in ta", 14)!.items.map((i) => i.label),
    ).toEqual(["task"]);
    expect(completionsAt(items, "{{ task }} and ta", 17)).toBeNull();
  });

  it("inserts a completion in place of the typed prefix", () => {
    const items = completionList(APP, "app");
    const text = "Hello {{ task.ti }}!";
    const at = completionsAt(items, text, 16)!;
    expect(applyCompletion(text, 16, at.context, at.items[0])).toEqual({
      text: "Hello {{ task.title }}!",
      cursor: 19,
    });
  });

  it("finds the top-level variables a template reads", () => {
    const template = [
      "# {{ task.title | upper }}",
      "{% for e in task.endpoints %}- {{ e.method }} {{ e.path }}{% endfor %}",
      "{% set note = 'x' %}{{ note }} {{ stack.fragment_text | indent(width=2) }}",
      "{{ \"auth_contract\" }} {{ guidance.cwes | join(', ') }}{% if budget_neutral %}{{ budget_neutral }}{% endif %}",
      "{{ range(3) | length }}",
    ].join("\n");
    expect(usedVariables(template)).toEqual([
      "budget_neutral",
      "guidance",
      "stack",
      "task",
    ]);
  });

  it("reports undeclared and unknown variables against the documented context", () => {
    const issues = variableIssues(
      "{{ task.title }} {{ style.fragment_text }}",
      ["task", "slot"],
      APP,
      ["app"],
    );
    expect(issues.undeclared).toEqual(["style"]);
    expect(issues.unknown).toEqual(["slot"]);
  });
});
