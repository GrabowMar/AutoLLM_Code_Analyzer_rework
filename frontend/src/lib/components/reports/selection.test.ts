import { describe, expect, it } from "vitest";
import {
  cellKind,
  cellLabel,
  designScope,
  groupDisplay,
  inSample,
  modelShortLabels,
  parseSampleSelection,
  reportSelection,
  sampleSelectionConfig,
  sampleSelectionFromConfig,
  sampleSelectionLink,
  selectionMatrix,
  selectionTableLinks,
  sharedVersion,
  taskRange,
  unionWithCounts,
} from "./selection";

const A = "afed1882-2d75-41ad-982c-1d1ddfd043ff";
const B = "d6fc1cd2-3844-43fa-a4fe-90d44b9437e3";

describe("create page: config and deep link", () => {
  it("parses comma and repeated lists, trims, de-duplicates and reads the complete-cases flag", () => {
    const p = new URLSearchParams(
      `type=study_report&selection=latest_unique&study_ids=${A},%20${B},,${A}&study_ids=${B}&models=a/x&tasks=one@1,two&common_tasks=1`,
    );
    expect(parseSampleSelection(p)).toEqual({
      studyIds: [A, B],
      models: ["a/x"],
      tasks: ["one@1", "two"],
      commonTasksOnly: true,
    });
    expect(
      parseSampleSelection(new URLSearchParams("selection=latest_unique"))
        ?.commonTasksOnly,
    ).toBe(false);
    expect(
      parseSampleSelection(
        new URLSearchParams("selection=latest_unique&common_tasks=true"),
      )?.commonTasksOnly,
    ).toBe(true);
  });

  it("ignores links that do not ask for a cross-experiment selection", () => {
    expect(
      parseSampleSelection(new URLSearchParams(`study_id=${A}`)),
    ).toBeNull();
    expect(
      parseSampleSelection(new URLSearchParams("selection=other")),
    ).toBeNull();
  });

  it("builds the config without empty filters and never sends common_tasks_only=false", () => {
    expect(
      sampleSelectionConfig({
        studyIds: [],
        models: [],
        tasks: [" "],
        commonTasksOnly: false,
      }),
    ).toEqual({ selection: "latest_unique" });
    expect(
      sampleSelectionConfig({
        studyIds: [A, A, B],
        models: ["m/one"],
        tasks: ["t@1"],
        commonTasksOnly: true,
      }),
    ).toEqual({
      selection: "latest_unique",
      study_ids: [A, B],
      models: ["m/one"],
      tasks: ["t@1"],
      common_tasks_only: true,
    });
  });

  it("round-trips a selection through the deep link and a stored config", () => {
    const state = {
      studyIds: [A, B],
      models: ["local-anthropic/claude-opus-5-5"],
      tasks: ["account_password_reset@1", "media_audio_player"],
      commonTasksOnly: true,
    };
    const link = sampleSelectionLink(state, {
      eval_suite: "eval-v2@2",
      empty: "",
    });
    expect(link).toContain(`study_ids=${A},${B}`);
    expect(link).toContain("eval_suite=eval-v2%402");
    expect(link).not.toContain("empty=");
    const url = new URL(link, "https://lab.example");
    expect(url.pathname).toBe("/reports/create");
    expect(url.searchParams.get("type")).toBe("study_report");
    expect(parseSampleSelection(url.searchParams)).toEqual(state);
    expect(sampleSelectionFromConfig(sampleSelectionConfig(state))).toEqual(
      state,
    );
    expect(sampleSelectionFromConfig({ study_id: A })).toBeNull();
  });

  it("reads models and task units from experiment designs for the filter options", () => {
    const design = {
      factors: [
        { key: "harness_preset", levels: ["h@1"] },
        { key: "model", levels: ["a/one", "a/two", "a/one"] },
      ],
      units: { tasks: ["x@1", "y@1"] },
    };
    expect(designScope(design)).toEqual({
      models: ["a/one", "a/two"],
      tasks: ["x@1", "y@1"],
    });
    expect(designScope(null)).toEqual({ models: [], tasks: [] });
    expect(
      unionWithCounts([
        ["a", "b"],
        ["b", "c", "b"],
      ]),
    ).toEqual([
      ["a", 1],
      ["b", 2],
      ["c", 1],
    ]);
  });
});

const cell = (
  group: string,
  task: string,
  status: string,
  extra: Record<string, unknown> = {},
) => ({
  group,
  task,
  status,
  run_id: "9f8e7d6c-1111-2222-3333-444455556666",
  study_id: A,
  study_name: "FINAL study",
  outcome: status === "selected" ? "tested" : null,
  created_at: "2026-11-10T10:00:00Z",
  attempts: 1,
  superseded_run_ids: [],
  ...extra,
});

const payload = () => ({
  selection: {
    mode: "latest_unique",
    rule: "Newest usable run per model and task.",
    filters: {
      study_ids: [A, B],
      models: null,
      tasks: null,
      common_tasks_only: false,
    },
    studies: [{ id: A, name: "FINAL study", status: "completed" }],
    counts: { candidates: 10, selected: 6 },
    groups: [
      {
        group: "local-anthropic/claude-opus-5-5",
        model: "local-anthropic/claude-opus-5-5",
        tasks_selected: 3,
        tasks_in_scope: 3,
        outcomes: { tested: 2, build_fail: 1 },
      },
      {
        group: "local-opencode-go/deepseek-v4-pro",
        tasks_selected: 2,
        tasks_in_scope: 3,
      },
    ],
    tasks: ["one@1", "two@1", "three@1"],
    matrix: [
      cell("local-anthropic/claude-opus-5-5", "one@1", "selected", {
        superseded_run_ids: ["old-1"],
        attempts: 2,
      }),
      cell("local-anthropic/claude-opus-5-5", "two@1", "selected", {
        outcome: "build_fail",
      }),
      cell("local-anthropic/claude-opus-5-5", "three@1", "selected"),
      cell("local-opencode-go/deepseek-v4-pro", "one@1", "selected"),
      cell("local-opencode-go/deepseek-v4-pro", "two@1", "attrition", {
        outcome: "infra_error",
        attempts: 3,
      }),
      // three@1 of deepseek: no row at all (missing)
      cell("extra/model (treatment ab12cd34)", "four@1", "in_flight"),
    ],
    superseded: [
      {
        run_id: "old-1",
        group: "local-anthropic/claude-opus-5-5",
        task: "one@1",
        reason: "newer_usable_run",
      },
    ],
    analyses: {
      by_launch: [{ launch_id: "l1", name: "FINAL static" }],
      by_label: [
        {
          label: "FINAL static",
          example_name: "FINAL static 1a2b3c4d",
          results_selected: 4,
        },
      ],
      tools_mixed_labels: [
        { tool: "zap", labels: ["FINAL dynamic", "SHALLOW dynamic"] },
      ],
    },
    common_tasks: ["one@1"],
    dropped_tasks: [] as string[],
  },
});

describe("report page: selection payload", () => {
  it("is null for reports without a cross-experiment selection", () => {
    expect(reportSelection(null)).toBeNull();
    expect(reportSelection({})).toBeNull();
    expect(reportSelection({ selection: [] })).toBeNull();
    expect(reportSelection({ selection: { mode: "other" } })).toBeNull();
  });

  it("fills every list and number so partial payloads never throw", () => {
    const s = reportSelection({ selection: { mode: "latest_unique" } })!;
    expect(s.matrix).toEqual([]);
    expect(s.groups).toEqual([]);
    expect(s.filters).toEqual({
      study_ids: [],
      models: null,
      tasks: null,
      common_tasks_only: false,
      exclude_truncated: false,
    });
    expect(s.analyses).toBeNull();
    const m = selectionMatrix(s);
    expect(m.groups).toEqual([]);
    expect(m.tasks).toEqual([]);
    expect(m.unequalTasks).toBe(false);

    const full = reportSelection(payload())!;
    expect(full.groups[1].model).toBe("local-opencode-go/deepseek-v4-pro");
    expect(full.groups[1].outcomes).toEqual({});
    expect(full.studies[0].runs_selected).toBe(0);
    expect(full.analyses?.by_launch[0]).toMatchObject({
      launch_id: "l1",
      example_name: "FINAL static",
      analysis_runs: 0,
    });
    expect(full.analyses?.by_label[0]).toEqual({
      label: "FINAL static",
      example_name: "FINAL static 1a2b3c4d",
      analysis_runs: 0,
      results_selected: 4,
      results_superseded: 0,
    });
    expect(full.analyses?.tools_mixed_labels[0].labels).toHaveLength(2);
    expect(full.analyses?.tools_mixed_launches).toEqual([]);
    expect(full.superseded[0]).toMatchObject({
      usable: null,
      replaced_by: null,
      recorded_attempt: null,
    });
    // Payloads without in_sample: a selected run is in the sample unless its task was dropped.
    expect(full.matrix.map((c) => c.in_sample)).toEqual([
      true,
      true,
      true,
      true,
      false,
      false,
    ]);
  });

  it("draws selected outcomes, attrition, running and missing units as distinct kinds", () => {
    expect(cellKind(undefined)).toBe("missing");
    const s = reportSelection(payload())!;
    expect(s.matrix.map(cellKind)).toEqual([
      "tested",
      "failed",
      "tested",
      "tested",
      "attrition",
      "in_flight",
    ]);
    expect(cellKind({ ...s.matrix[0], outcome: null })).toBe("selected");
  });

  it("lays the matrix out in payload order, appends unknown units and counts per kind", () => {
    const m = selectionMatrix(reportSelection(payload())!);
    expect(m.groups.map((g) => g.label)).toEqual([
      "local-anthropic/claude-opus-5-5",
      "local-opencode-go/deepseek-v4-pro",
      "extra/model (treatment ab12cd34)",
    ]);
    expect(m.groups[0]).toMatchObject({
      name: "claude-opus-5-5",
      prefix: "local-anthropic",
      selected: 3,
      inScope: 3,
    });
    // A group the groups list lacks: counted from its cells, in scope = every task.
    expect(m.groups[2]).toMatchObject({
      name: "model",
      treatment: "ab12cd34",
      selected: 0,
      inScope: 4,
    });
    expect(m.tasks.map((t) => t.label)).toEqual([
      "one",
      "two",
      "three",
      "four",
    ]);
    expect(m.version).toBe("@1");
    expect(m.tasks.map((t) => t.covered)).toEqual([2, 1, 1, 0]);
    expect(m.cells[1][2]).toBeUndefined();
    expect(m.kindCounts).toEqual({
      tested: 3,
      failed: 1,
      selected: 0,
      excluded: 0,
      attrition: 1,
      in_flight: 1,
      missing: 6,
    });
    expect(m.supersededCells).toBe(1);
    expect(m.unequalTasks).toBe(true);
  });

  it("marks tasks dropped by complete cases and compares only the kept tasks", () => {
    const data = payload();
    data.selection.filters.common_tasks_only = true;
    data.selection.dropped_tasks = ["two@1", "three@1"];
    data.selection.tasks = ["one@1"];
    data.selection.matrix = data.selection.matrix.filter(
      (c) => !c.group.startsWith("extra/"),
    );
    const m = selectionMatrix(reportSelection(data)!);
    expect(m.tasks.map((t) => [t.ref, t.dropped])).toEqual([
      ["one@1", false],
      ["two@1", true],
      ["three@1", true],
    ]);
    // The payload's tasks_in_scope counts every task; complete cases keep one.
    expect(m.groups.map((g) => g.inScope)).toEqual([1, 1]);
    expect(m.unequalTasks).toBe(false);
    // Selected runs of dropped tasks are drawn and counted apart: the legend's selected kinds sum to the sample.
    const opus = m.cells[0];
    expect(opus.map((c) => cellKind(c))).toEqual([
      "tested",
      "excluded",
      "excluded",
    ]);
    expect(m.kindCounts.excluded).toBe(2);
    expect(
      m.kindCounts.tested + m.kindCounts.failed + m.kindCounts.selected,
    ).toBe(2);
    expect(m.tasks.map((t) => t.covered)).toEqual([2, 0, 0]);
    expect(cellLabel("opus", "two@1", opus[1])).toBe(
      "opus, two@1: not in the sample, task dropped for complete cases; newest usable run 9f8e7d6c build failed from FINAL study",
    );
  });

  it("draws truncated runs left out after selection apart and labels why", () => {
    const data = payload();
    data.selection.matrix[2] = cell(
      "local-anthropic/claude-opus-5-5",
      "three@1",
      "selected",
      { in_sample: false, excluded: "truncated" },
    );
    data.selection.groups[0].tasks_selected = 2;
    const s = reportSelection(data)!;
    expect(s.matrix[2].excluded).toBe("truncated");
    expect(inSample(s.matrix[2])).toBe(false);
    expect(inSample(s.matrix[0])).toBe(true);
    expect(cellKind(s.matrix[2])).toBe("excluded");
    expect(cellLabel("opus", "three@1", s.matrix[2])).toContain(
      "not in the sample, truncated run left out",
    );
    const m = selectionMatrix(s);
    expect(m.groups[0].selected).toBe(2);
    expect(m.kindCounts).toMatchObject({ tested: 2, failed: 1, excluded: 1 });
    expect(m.tasks.map((t) => t.covered)).toEqual([2, 1, 0, 0]);
  });

  it("keeps the endpoint in a model label only when two endpoints share the name", () => {
    const labels = modelShortLabels([
      "openrouter/x",
      "local-opencode-go/x",
      "local-anthropic/claude-opus-5-5",
      "openrouter/x",
    ]);
    expect([...labels.entries()]).toEqual([
      ["openrouter/x", "openrouter/x"],
      ["local-opencode-go/x", "local-opencode-go/x"],
      ["local-anthropic/claude-opus-5-5", "claude-opus-5-5"],
    ]);
  });

  it("links the server's Sample selection tables by index and short label", () => {
    expect(
      selectionTableLinks([
        { title: "Protocol" },
        { title: "Sample selection" },
        { title: "Sample selection: coverage by group" },
        { title: "Definitions" },
        { title: "Sample selection: superseded runs" },
      ]),
    ).toEqual([
      { index: 1, label: "Summary" },
      { index: 2, label: "Coverage by group" },
      { index: 4, label: "Superseded runs" },
    ]);
  });

  it("labels every cell with group, task, status, outcome, run, experiment and superseded attempts", () => {
    const s = reportSelection(payload())!;
    expect(cellLabel("opus", "one@1", s.matrix[0])).toBe(
      "opus, one@1: selected, tested; run 9f8e7d6c from FINAL study; 1 older attempt superseded",
    );
    expect(cellLabel("ds", "two@1", s.matrix[4])).toBe(
      "ds, two@1: attrition, no usable run in 3 attempts; newest 9f8e7d6c infra error from FINAL study",
    );
    expect(cellLabel("ds", "three@1", undefined)).toBe(
      "ds, three@1: no attempt",
    );
    expect(cellLabel("x", "four@1", s.matrix[5])).toContain("still running");
  });

  it("formats group names, shared versions and task ranges", () => {
    expect(groupDisplay("plain")).toEqual({
      name: "plain",
      prefix: "",
      treatment: "",
    });
    expect(groupDisplay("a/b/c (treatment 1234abcd)")).toEqual({
      name: "c",
      prefix: "a/b",
      treatment: "1234abcd",
    });
    expect(sharedVersion(["a@1", "b@1"])).toBe("@1");
    expect(sharedVersion(["a@1", "b@2"])).toBeNull();
    expect(sharedVersion(["a", "b"])).toBeNull();
    expect(taskRange([{ selected: 30 }, { selected: 24 }])).toBe("24–30");
    expect(taskRange([{ selected: 30 }])).toBe("30");
    expect(taskRange([])).toBe("—");
  });
});
