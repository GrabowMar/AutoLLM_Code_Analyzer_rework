import { describe, expect, it } from "vitest";
import type { RunSetup, RunTarget } from "$lib/api/runs";
import { repeatLabel, runSetupItems, runTargetLines } from "./setup";

const SETUP: RunSetup = {
  id: "7d0b9c1e-0000-4000-8000-000000000001",
  kind: "app_agentic",
  status: "completed",
  outcome: "tested",
  usable: true,
  model_pk: 4,
  model_id: "deepseek/deepseek-v4-flash",
  model_name: "DeepSeek: V4 Flash",
  model_slug: "deepseek_deepseek-v4-flash",
  task_ref: "todo-api@2",
  task_slug: "todo-api",
  task_title: "Todo API",
  prose_variant: "minimal",
  unit_key: "task:todo-api@2",
  stack_ref: "fastapi-react@1",
  study_id: "5a6b7c8d-0000-4000-8000-000000000002",
  study_name: "Harness study",
  condition_id: 11,
  condition_label: "opencode · deepseek",
  stage: "main",
  repeat_index: 1,
  planned_repeats: 5,
  harness: "opencode",
  harness_version: "1.4.2",
  config_fingerprint: "abcdef0123456789",
  run_fingerprint: "0123",
};

describe("repeatLabel", () => {
  it("counts from 1 and says when a repeat is beyond the plan", () => {
    expect(repeatLabel(null, 5)).toBeNull();
    expect(repeatLabel(0, null)).toBe("repeat 1");
    expect(repeatLabel(1, 5)).toBe("repeat 2/5");
    expect(repeatLabel(6, 5)).toBe("repeat 7 (beyond the 5 planned)");
  });
});

describe("runSetupItems", () => {
  it("links the experiment record and the sample, never an application page", () => {
    const { cell, method } = runSetupItems(SETUP);
    expect(cell.map((c) => c.label)).toEqual([
      "Harness study",
      "opencode · deepseek",
      "repeat 2/5",
    ]);
    expect(cell[0].href).toBe(`/experiments/${SETUP.study_id}`);
    expect(method.map((m) => m.label)).toEqual([
      "V4 Flash",
      "opencode 1.4.2",
      "Todo API (minimal)",
    ]);
    expect(method[0].href).toBe("/models/deepseek_deepseek-v4-flash");
    expect(method[2].href).toBe(`/samples/${SETUP.id}`);
    expect(method[2].title).toBe("todo-api@2 · fastapi-react@1");
    const hrefs = [...cell, ...method].map((i) => i.href ?? "");
    expect(hrefs.some((h) => h.includes("/applications/"))).toBe(false);
    expect(hrefs.filter((h) => h.includes("/experiments/"))).toEqual([
      `/experiments/${SETUP.study_id}`,
    ]);
  });

  it("marks a pilot run and leaves out the cell of an ad-hoc run", () => {
    expect(
      runSetupItems({ ...SETUP, stage: "pilot" }).cell.map((c) => c.label),
    ).toContain("pilot");
    const adhoc = runSetupItems({
      ...SETUP,
      study_id: null,
      study_name: "",
      condition_id: null,
      condition_label: "",
      stage: "",
      repeat_index: null,
      planned_repeats: null,
      harness: null,
      harness_version: "",
      task_title: "",
      task_slug: "",
    });
    expect(adhoc.cell).toEqual([]);
    expect(adhoc.method.map((m) => m.label)).toEqual([
      "V4 Flash",
      "fastapi-react@1",
    ]);
  });
});

describe("runTargetLines", () => {
  const target: RunTarget = {
    kind: "run",
    label: "Todo API · V4 Flash",
    job_id: SETUP.id,
    model_name: "DeepSeek: V4 Flash",
    task_title: "Todo API",
    stack_ref: "fastapi-react@1",
  };

  it("titles a run by its task and shows model and stack", () => {
    expect(runTargetLines(target)).toEqual({
      title: "Todo API",
      sub: "V4 Flash · fastapi-react@1",
    });
    expect(runTargetLines({ ...target, task_title: "" })).toEqual({
      title: "fastapi-react@1",
      sub: "V4 Flash",
    });
  });

  it("uses the label for pasted code and deleted apps", () => {
    expect(runTargetLines({ kind: "code", label: "3 pasted files" })).toEqual({
      title: "3 pasted files",
      sub: "",
    });
    expect(runTargetLines(null)).toEqual({ title: "—", sub: "" });
  });
});
