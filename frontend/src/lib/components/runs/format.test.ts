import { describe, expect, it } from "vitest";
import type { RunEvaluationOut, RunFileOut } from "$lib/api/v3/types";
import {
  buildFileTree,
  canBeParent,
  defaultFileFilter,
  evaluationSummary,
  filterFiles,
  fmtRate,
  fmtSeconds,
  fmtUsd,
  groupChecks,
  groupTurns,
  turnTools,
  originCounts,
  presentOrigins,
  scopesFor,
} from "./format";

function file(
  path: string,
  origin: string,
  extra: Partial<RunFileOut> = {},
): RunFileOut {
  return {
    path,
    origin,
    size: 10,
    loc: 3,
    language: "python",
    is_test: false,
    excluded: "",
    blob: "b",
    ...extra,
  };
}

// App scope of a slot variant: the regenerated slot file, the parent's other files overlaid as `parent`.
const VARIANT_APP = [
  file("backend/app.py", "slot"),
  file("backend/models.py", "parent"),
  file("backend/tests/test_api.py", "parent", { is_test: true }),
  file("frontend/src/App.tsx", "parent", { language: "tsx" }),
  file("frontend/dist/bundle.js", "parent", { excluded: "build_output" }),
];

const APP_RUN = [
  file("backend/app.py", "authored"),
  file("backend/requirements.txt", "modified"),
  file("backend/Dockerfile", "starter"),
  file("backend/old.py", "deleted", { blob: "" }),
];

describe("file-origin filter", () => {
  it("shows every included origin by default, parent overlay included", () => {
    const rows = filterFiles(VARIANT_APP, defaultFileFilter());
    expect(rows.map((r) => r.path)).toEqual([
      "backend/app.py",
      "backend/models.py",
      "backend/tests/test_api.py",
      "frontend/src/App.tsx",
    ]);
  });

  it("hides the parent overlay, leaving the variant's own rows", () => {
    const rows = filterFiles(VARIANT_APP, {
      ...defaultFileFilter(),
      hideParent: true,
    });
    expect(rows.map((r) => r.origin)).toEqual(["slot"]);
  });

  it("filters by origin and keeps excluded rows of an explicitly chosen origin", () => {
    expect(
      filterFiles(VARIANT_APP, {
        ...defaultFileFilter(),
        origins: ["slot"],
      }).map((r) => r.path),
    ).toEqual(["backend/app.py"]);
    const parents = filterFiles(VARIANT_APP, {
      ...defaultFileFilter(),
      origins: ["parent"],
    });
    expect(parents).toHaveLength(4);
    expect(parents.some((r) => r.excluded === "build_output")).toBe(true);
  });

  it("lists deleted rows only when asked for", () => {
    expect(
      filterFiles(APP_RUN, defaultFileFilter()).some(
        (r) => r.origin === "deleted",
      ),
    ).toBe(false);
    expect(
      filterFiles(APP_RUN, {
        ...defaultFileFilter(),
        origins: ["deleted"],
      }).map((r) => r.path),
    ).toEqual(["backend/old.py"]);
  });

  it("hides tests and matches the path query", () => {
    const rows = filterFiles(VARIANT_APP, {
      ...defaultFileFilter(),
      showTests: false,
      query: "BACKEND",
    });
    expect(rows.map((r) => r.path)).toEqual([
      "backend/app.py",
      "backend/models.py",
    ]);
  });

  it("counts origins in display order", () => {
    expect(originCounts(VARIANT_APP)).toEqual({ slot: 1, parent: 4 });
    expect(presentOrigins(APP_RUN)).toEqual([
      "authored",
      "modified",
      "starter",
      "deleted",
    ]);
  });

  it("offers the slot scope only for variants", () => {
    expect(scopesFor("slot_single_shot")).toContain("slot");
    expect(scopesFor("app_agentic")).toEqual(["model", "app"]);
  });
});

describe("file tree", () => {
  it("puts directories first and collapses single-child directories", () => {
    const tree = buildFileTree([
      file("a/b/c.py", "authored"),
      file("a/b/d.py", "authored"),
      file("z.py", "authored"),
    ]);
    expect(tree.map((n) => n.name)).toEqual(["a/b", "z.py"]);
    expect(tree[0].children.map((n) => n.name)).toEqual(["c.py", "d.py"]);
    expect(tree[0].children[0].file?.path).toBe("a/b/c.py");
  });
});

describe("validity-preserving formats", () => {
  it("never renders a missing rate as 0", () => {
    expect(fmtRate(null)).toBe("not measured");
    expect(fmtRate(0)).toBe("0%");
    expect(fmtRate(0.8333)).toBe("83.3%");
  });

  it("formats money and time", () => {
    expect(fmtUsd(null)).toBe("—");
    expect(fmtUsd(0.0042)).toBe("$0.0042");
    expect(fmtUsd(1.5)).toBe("$1.50");
    expect(fmtSeconds(0.25)).toBe("250 ms");
    expect(fmtSeconds(125)).toBe("2m 05s");
  });

  it("summarises an evaluation without turning errors into pass rates", () => {
    const base = {
      id: 1,
      eval_suite: "eval-v2@1#abc",
      eval_level: "security",
      mode: "full",
      stages: {},
      adherence: null,
      build_key: "",
      image_id: "",
      reused_from: null,
      retest_of: null,
      baseline: null,
      created_at: "",
    } satisfies Omit<RunEvaluationOut, "status" | "result">;
    expect(evaluationSummary(null)).toBe("Not evaluated yet");
    expect(
      evaluationSummary({ ...base, status: "error", result: null }),
    ).toMatch(/not a 0 pass rate/);
    expect(
      evaluationSummary({
        ...base,
        status: "not_measured",
        result: {
          tester_version: 1,
          compiler_version: 1,
          auth_mode: "contract_bearer",
          checks: [],
          probes: [],
          metrics: {
            infra_ok: false,
            agent_checks: 0,
            agent_passed: 0,
            agent_pass_rate: null,
            by_category: {},
            baseline_pass_rate: null,
            over_baseline: null,
            probes_tested: 0,
            probes_exploited: 0,
            probes_skipped: 0,
            sec_pass: null,
          },
          measured: false,
          not_measured_reason: "start_fail",
          flaky_checks: [],
        },
      }),
    ).toBe("Not measured · start_fail");
  });

  it("groups checks with infra apart and flaky checks uncounted", () => {
    const { agent, infra } = groupChecks(
      [
        { id: "health", category: "infra", endpoint: null, status: "pass" },
        { id: "c1", category: "functional", endpoint: "list", status: "pass" },
        { id: "c2", category: "functional", endpoint: "list", status: "fail" },
        { id: "c3", category: "functional", endpoint: "list", status: "pass" },
        { id: "a1", category: "auth", endpoint: null, status: "skipped" },
      ],
      ["c3"],
    );
    expect(infra.map((c) => c.id)).toEqual(["health"]);
    expect(agent.map((g) => [g.category, g.passed, g.counted])).toEqual([
      ["auth", 0, 0],
      ["functional", 1, 2],
    ]);
  });

  it("accepts only tested, usable agentic apps as parents", () => {
    const ok = {
      kind: "app_agentic",
      status: "completed",
      usable: true,
      outcome: "tested" as const,
    };
    expect(canBeParent(ok)).toBe(true);
    expect(canBeParent({ ...ok, outcome: "build_fail" })).toBe(false);
    expect(canBeParent({ ...ok, kind: "slot_agent" })).toBe(false);
  });
});

describe("groupTurns", () => {
  const step = (
    index: number,
    kind: string,
    extra: Record<string, unknown> = {},
  ) =>
    ({
      index,
      kind,
      tool: kind === "llm" ? null : "Bash",
      ok: true,
      started: null,
      duration_s: null,
      tokens_in: 0,
      tokens_out: 0,
      files: [],
      command_sha: null,
      ...extra,
    }) as never;

  it("pairs each LLM call with the actions until the next call", () => {
    const turns = groupTurns([
      step(0, "llm", { tokens_in: 100, tokens_out: 5 }),
      step(1, "shell", { ok: false }),
      step(2, "shell", { files: ["a.py"] }),
      step(3, "llm", { tokens_in: 180, tokens_out: 9 }),
      step(4, "edit", { tool: "Edit", files: ["a.py", "b.py"] }),
    ]);
    expect(
      turns.map((t) => [t.n, t.actions.length, t.tokensIn, t.failed]),
    ).toEqual([
      [1, 2, 100, 1],
      [2, 1, 180, 0],
    ]);
    expect(turns[1].files).toEqual(["a.py", "b.py"]);
    expect(turnTools(turns[0])).toBe("Bash ×2");
  });

  it("keeps steps before the first LLM call in turn 0", () => {
    const turns = groupTurns([step(0, "shell"), step(1, "llm")]);
    expect(turns.map((t) => [t.n, t.llm === null, t.actions.length])).toEqual([
      [0, true, 1],
      [1, false, 0],
    ]);
  });

  it("sums durations only when the parser records them", () => {
    expect(
      groupTurns([step(0, "llm"), step(1, "shell")])[0].seconds,
    ).toBeNull();
    expect(
      groupTurns([
        step(0, "llm", { duration_s: 2 }),
        step(1, "shell", { duration_s: 1.5 }),
      ])[0].seconds,
    ).toBe(3.5);
  });
});
