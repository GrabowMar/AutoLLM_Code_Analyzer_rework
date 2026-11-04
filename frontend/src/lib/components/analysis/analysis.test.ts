import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  RulePrevalence,
  RunFinding,
  RunTarget,
  RunToolResult,
} from "$lib/api/runs";
import type { SseEvent } from "$lib/api/sse";
import { RunWatcher } from "./live.svelte";
import {
  RUN_STATUS_HELP,
  excludedDirsText,
  excludedPathsNote,
  formatClock,
  issueClassLabel,
  levelClasses,
  levelRank,
  nonCodeText,
  prevalenceIndex,
  prevalenceKey,
  prevalenceText,
  reportedLevels,
  resultState,
  rulePrevalenceLines,
  runScopeCounts,
  runToolCounts,
  sortLevels,
  targetLines,
  toCsv,
  toolsMeasuredNote,
  truncationNote,
} from "./format";
import {
  advisoryUrl,
  complexityInfo,
  dependencyInfo,
  httpInfo,
  locationText,
  otherData,
  ruleInfo,
  safeUrl,
} from "./finding-data";

function result(over: Partial<RunToolResult> = {}): RunToolResult {
  return {
    id: "r1",
    tool_slug: "bandit",
    category: "security",
    status: "completed",
    summary: {},
    metrics: {},
    error_message: "",
    started_at: null,
    duration_seconds: 1,
    ...over,
  };
}

function finding(
  data: Record<string, unknown>,
  over: Partial<RunFinding> = {},
): RunFinding {
  return {
    id: "f1",
    severity: "high",
    tool_severity: "",
    category: "dependency",
    confidence: "medium",
    title: "t",
    description: "",
    suggestion: "",
    file_path: "requirements.txt",
    line_number: null,
    column_number: null,
    code_snippet: "",
    rule_id: "",
    tool_slug: "pip-audit",
    tool_specific_data: data,
    ...over,
  };
}

describe("resultState", () => {
  it("counts only a completed tool as measured: a not-applicable skip, other skips and failures are not", () => {
    expect(
      resultState(
        result({
          status: "skipped",
          summary: { skip_reason: "not_applicable" },
        }),
      ).measured,
    ).toBe(false);
    expect(
      resultState(
        result({
          status: "skipped",
          summary: { skip_reason: "app_not_running" },
        }),
      ).measured,
    ).toBe(false);
    expect(resultState(result({ status: "failed" })).measured).toBe(false);
    expect(resultState(result()).measured).toBe(true);
  });

  it("treats a tool without a result as queued and a running one as live", () => {
    expect(resultState(undefined).label).toBe("Queued");
    expect(resultState(result({ status: "running" })).live).toBe(true);
  });
});

describe("tool-reported levels", () => {
  it("orders the tools' own words most severe first and ignores unrated", () => {
    const summary = {
      reported_severity_counts: {
        convention: 9,
        warning: 2,
        critical: 1,
        "8.6": 1,
      },
    };
    // A CVSS score printed as the level ranks by its band (8.6: high), as the server orders facets.
    expect(reportedLevels(summary).map(([label]) => label)).toEqual([
      "critical",
      "8.6",
      "warning",
      "convention",
    ]);
    expect(reportedLevels({})).toEqual([]);
  });

  it("ranks by the word, not by count or the normalized level", () => {
    // Run fcd4bbe5's facets as the server once sent them (ordered by normalized severity).
    const facets = [
      { value: "critical", count: 2 },
      { value: "moderate", count: 29 },
      { value: "high", count: 16 },
      { value: "7.0", count: 1 },
      { value: "medium", count: 22 },
      { value: "low", count: 5 },
      { value: "warning", count: 3 },
    ];
    expect(sortLevels(facets).map((l) => l.value)).toEqual([
      "critical",
      "high",
      "7.0",
      "moderate",
      "medium",
      "warning",
      "low",
    ]);
    expect(levelRank("9.8")).toBe(0);
    expect(levelRank("5")).toBe(2);
    expect(levelRank("0.0")).toBe(levelRank("info"));
    expect(levelRank("v1.2")).toBe(7);
  });

  it("tints words case-insensitively and leaves unknown labels neutral", () => {
    expect(levelRank("HIGH")).toBe(levelRank("high"));
    expect(levelClasses("CRITICAL")).toContain("bg-destructive");
    expect(levelClasses("3")).toContain("text-muted-foreground");
  });
});

describe("formatting", () => {
  it("formats clocks", () => {
    expect(formatClock(5)).toBe("0:05");
    expect(formatClock(3725)).toBe("1:02:05");
  });

  it("quotes CSV cells that need it", () => {
    expect(toCsv([["a", 'say "hi"', "x,y", null]])).toBe(
      'a,"say ""hi""","x,y",',
    );
  });

  it("neutralizes text a spreadsheet would run as a formula, not numbers", () => {
    expect(
      toCsv([
        [
          "@remix-run/router <=1.23.1",
          '=HYPERLINK("http://x","y")',
          "+1 day",
          "-1.5",
          -2,
          "1e-3",
          "\tx",
        ],
      ]),
    ).toBe(
      `'@remix-run/router <=1.23.1,"'=HYPERLINK(""http://x"",""y"")",'+1 day,-1.5,-2,1e-3,'\tx`,
    );
  });
});

describe("run tool counts", () => {
  const run = (summary: Record<string, unknown>, status = "completed") => ({
    status,
    tool_slugs: ["a", "b", "c", "d"],
    summary,
  });

  it("never counts not-applicable tools as measured", () => {
    expect(
      runToolCounts(
        run({
          tools_run: 4,
          tools_completed: 1,
          tools_failed: 1,
          tools_not_applicable: 1,
          tools_unmeasured: 1,
        }),
      ),
    ).toEqual({
      total: 4,
      measured: 1,
      notApplicable: 1,
      failed: 1,
      unmeasured: 1,
      nothingMeasured: false,
    });
  });

  it("flags a finished run in which no tool measured anything", () => {
    const counts = runToolCounts(
      run({
        tools_run: 4,
        tools_completed: 0,
        tools_failed: 0,
        tools_not_applicable: 0,
        tools_unmeasured: 4,
      }),
    )!;
    expect(counts.nothingMeasured).toBe(true);
    expect(counts.unmeasured).toBe(4);
    expect(RUN_STATUS_HELP.completed).toMatch(/measured nothing/);
  });

  it("flags a run whose only skips were not applicable as having measured nothing", () => {
    const counts = runToolCounts(
      run({ tools_run: 4, tools_completed: 0, tools_not_applicable: 4 }),
    )!;
    expect(counts.measured).toBe(0);
    expect(counts.unmeasured).toBe(0);
    expect(counts.nothingMeasured).toBe(true);
  });

  it("derives the unmeasured count for older summaries and skips active runs", () => {
    expect(
      runToolCounts(run({ tools_run: 4, tools_completed: 2, tools_failed: 1 }))
        ?.unmeasured,
    ).toBe(1);
    expect(runToolCounts(run({ tools_run: 4 }, "running"))).toBeNull();
    expect(runToolCounts(run({}))).toBeNull();
  });

  it("counts tools without a result as not measured in the Overview subtitle", () => {
    // A run that failed while preparing has no result rows: never "all measured".
    expect(
      toolsMeasuredNote({
        total: 13,
        measured: 0,
        failed: 0,
        notApplicable: 0,
      }),
    ).toBe("13 not measured");
    expect(
      toolsMeasuredNote({ total: 4, measured: 1, failed: 1, notApplicable: 1 }),
    ).toBe("1 failed · 1 not measured · 1 had nothing to read (not measured)");
    expect(
      toolsMeasuredNote({ total: 3, measured: 3, failed: 0, notApplicable: 0 }),
    ).toBe("all measured");
  });
});

describe("run findings by scope", () => {
  it("reads code, advisories and runtime, keeping an unmeasured scope null", () => {
    // fcd4bbe5: 120 findings, 18 code, 102 advisories, no dynamic tool.
    const sc = runScopeCounts({
      total_findings: 120,
      code_findings: 18,
      dependency_findings: 102,
      runtime_findings: null,
    });
    expect(sc).toEqual({ code: 18, dependencies: 102, runtime: null });
    expect(nonCodeText(sc)).toBe("102 advisories");
    expect(nonCodeText({ code: null, dependencies: 18, runtime: 19 })).toBe(
      "18 advisories · 19 runtime",
    );
    expect(nonCodeText({ code: 5, dependencies: 0, runtime: null })).toBe("");
    // A summary without the split (older, or active) falls back to the total.
    expect(runScopeCounts({ total_findings: 3 })).toBeNull();
    expect(runScopeCounts(null)).toBeNull();
  });

  it("says what a truncated input means for the findings", () => {
    expect(truncationNote([])).toBe("");
    expect(truncationNote(["backend", "frontend"])).toBe(
      "The generated backend and frontend code was cut off at the model's token limit: these findings describe an incomplete app.",
    );
    expect(truncationNote(["agent"])).toBe(
      "The agent's final response was cut off at the model's token limit: the app may be unfinished.",
    );
    expect(truncationNote(["backend", "agent"])).toContain(
      "backend code was cut off at the model's token limit, and so was the agent's final response",
    );
  });
});

describe("finding data", () => {
  it("merges standardized and legacy dependency keys", () => {
    const dep = dependencyInfo(
      finding(
        {
          package: "flask",
          version: "3.0.0",
          fix_versions: ["3.1.3"],
          aliases: ["CVE-2026-1", "GHSA-68rp-wp8r-4726"],
          cvss: 0,
        },
        { rule_id: "PYSEC-2026-2151" },
      ),
    )!;
    expect(dep.installed).toBe("3.0.0");
    expect(dep.fixedIn).toEqual(["3.1.3"]);
    expect(dep.ids).toEqual([
      "PYSEC-2026-2151",
      "CVE-2026-1",
      "GHSA-68rp-wp8r-4726",
    ]);
    // npm audit's 0 means "not scored".
    expect(dep.cvssScore).toBeNull();
  });

  it("links advisories by id family and only http(s) URLs", () => {
    expect(advisoryUrl("CVE-2026-1")).toBe(
      "https://nvd.nist.gov/vuln/detail/CVE-2026-1",
    );
    expect(advisoryUrl("GHSA-68rp-wp8r-4726")).toBe(
      "https://github.com/advisories/GHSA-68rp-wp8r-4726",
    );
    expect(advisoryUrl("PYSEC-2026-2151")).toBe(
      "https://osv.dev/vulnerability/PYSEC-2026-2151",
    );
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("https://example.org/a")).toBe("https://example.org/a");
  });

  it("shows evidence once when it repeats the code snippet", () => {
    const info = httpInfo(
      finding(
        { url: "http://172.17.0.3:8000/", evidence: "Werkzeug/3.0.1" },
        { code_snippet: "Werkzeug/3.0.1" },
      ),
    )!;
    expect(info.evidence).toBe("");
  });

  it("does not show a static advisory link as a flagged request", () => {
    // trivy stores its AVD page under the legacy `url` key.
    const trivy = finding(
      {
        package: "PyJWT",
        url: "https://avd.aquasec.com/nvd/cve-2026-102268",
        docs_url: "https://avd.aquasec.com/nvd/cve-2026-102268",
      },
      { tool_slug: "trivy" },
    );
    expect(httpInfo(trivy)).toBeNull();
    // Older trivy rows have no docs_url: the advisory page becomes the rule docs.
    const old = finding(
      { package: "PyJWT", url: "https://avd.aquasec.com/nvd/cve-2026-1" },
      { tool_slug: "trivy" },
    );
    expect(httpInfo(old)).toBeNull();
    expect(ruleInfo(old).docsUrl).toBe(
      "https://avd.aquasec.com/nvd/cve-2026-1",
    );
    // A trivy misconfiguration links its page under both keys, without a package.
    const misconfig = finding(
      {
        url: "https://avd.aquasec.com/misconfig/ds002",
        docs_url: "https://avd.aquasec.com/misconfig/ds002",
      },
      { tool_slug: "trivy", category: "configuration" },
    );
    expect(httpInfo(misconfig)).toBeNull();
    // nuclei detections can store an empty matcher: their request URL still shows.
    const nuclei = finding(
      { url: "http://172.17.0.6:8000/docs", matcher: "" },
      { tool_slug: "nuclei", category: "security" },
    );
    expect(httpInfo(nuclei)?.url).toBe("http://172.17.0.6:8000/docs");
  });

  it("reads the legacy lizard `ccn` and radon `endline` keys", () => {
    // Current parsers write both keys; older rows have only the legacy one.
    const lizard = finding(
      { ccn: 17, function: "handler", nloc: 40 },
      {
        tool_slug: "lizard",
        category: "complexity",
        file_path: "app.py",
        line_number: 3,
      },
    );
    expect(complexityInfo(lizard)?.complexity).toBe(17);
    expect(otherData(lizard)).toEqual([]);
    const radon = finding(
      { endline: 30, complexity: 12 },
      {
        tool_slug: "radon",
        category: "complexity",
        file_path: "app.py",
        line_number: 10,
      },
    );
    expect(locationText(radon)).toBe("app.py:10–30");
    expect(otherData(radon)).toEqual([]);
    // The standardized keys win when both are present.
    expect(
      complexityInfo(finding({ complexity: 9, ccn: 9, function: "f" }))
        ?.complexity,
    ).toBe(9);
    expect(
      locationText(
        finding(
          { end_line: 12, endline: 12 },
          { file_path: "a.py", line_number: 5 },
        ),
      ),
    ).toBe("a.py:5–12");
  });

  it("hides dedup keys and secret hashes from other data", () => {
    const rest = otherData(
      finding({
        issue_key: "x",
        hashed_secret: "abc",
        ecosystem: "PyPI",
        sbom: { a: 1 },
      }),
    );
    expect(rest).toEqual([["sbom", '{"a":1}']]);
  });
});

describe("RunWatcher", () => {
  let now = 0;
  let onState: ((s: "open" | "reconnecting" | "closed") => void) | undefined;
  const subscribed: string[][] = [];
  const unsubscribe = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    now = 0;
    subscribed.length = 0;
    unsubscribe.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  function make(active: { value: boolean }, channels: { value: string[] }) {
    const poll = vi.fn(async () => {});
    const events: SseEvent[] = [];
    const watcher = new RunWatcher({
      channels: () => channels.value,
      active: () => active.value,
      onEvent: (e) => events.push(e),
      poll,
      fastPollMs: 3_000,
      slowPollMs: 10_000,
      deps: {
        subscribe: (chs, _onEvent, opts) => {
          subscribed.push(chs);
          onState = opts.onState;
          return unsubscribe;
        },
        now: () => now,
        hidden: () => false,
      },
    });
    return { watcher, poll, events };
  }

  async function advance(ms: number) {
    for (let t = 0; t < ms; t += 1_000) {
      now += 1_000;
      await vi.advanceTimersByTimeAsync(1_000);
    }
  }

  it("polls fast while SSE is not open and slowly once it is", async () => {
    const active = { value: true };
    const { watcher, poll } = make(active, { value: ["analysis:a"] });
    watcher.start();
    expect(subscribed).toEqual([["analysis:a"]]);
    await advance(3_000);
    expect(poll).toHaveBeenCalledTimes(1);
    onState?.("open");
    poll.mockClear();
    await advance(9_000);
    expect(poll).not.toHaveBeenCalled();
    await advance(2_000);
    expect(poll).toHaveBeenCalledTimes(1);
    watcher.destroy();
  });

  it("stops polling and disconnects once nothing is active", async () => {
    const active = { value: true };
    const { watcher, poll } = make(active, { value: ["analysis:a"] });
    watcher.start();
    active.value = false;
    watcher.sync();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(watcher.connection).toBe("closed");
    await advance(30_000);
    expect(poll).not.toHaveBeenCalled();
    watcher.destroy();
  });

  it("reconnects only when the channel set changes", () => {
    const channels = { value: ["analysis:a"] };
    const { watcher } = make({ value: true }, channels);
    watcher.start();
    watcher.sync();
    expect(subscribed).toHaveLength(1);
    channels.value = ["analysis:a", "analysis:b"];
    watcher.sync();
    expect(subscribed).toHaveLength(2);
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    watcher.destroy();
  });
});

describe("run context", () => {
  const target: RunTarget = {
    kind: "run",
    label: "Todo",
    job_id: "run-1",
    model_name: "vendor/model-x",
    task_title: "Todo list",
    task_slug: "todo",
    task_ref: "todo@1",
    stack_ref: "flask-react@2",
  };

  it("titles a generated run by its task, with model and stack below", () => {
    const t = targetLines(target);
    expect(t.title).toBe("Todo list");
    expect(t.sub).toContain("flask-react@2");
    expect(targetLines({ ...target, task_title: undefined }).title).toBe(
      "flask-react@2",
    );
    expect(targetLines({ kind: "code", label: "pasted.py" })).toEqual({
      title: "pasted.py",
      sub: "",
    });
    expect(targetLines(null).title).toBe("—");
  });

  const row = (over: Partial<RulePrevalence> = {}): RulePrevalence => ({
    tool: "bandit",
    rule_id: "B105",
    rule_name: "",
    title: "",
    issue_class: "hardcoded-secret",
    scope: "code",
    count_in_run: 1,
    apps_with_rule: 15,
    apps_measured: 30,
    model_apps_with_rule: 4,
    model_apps_measured: 6,
    ...over,
  });

  it("states prevalence over the apps the tool measured, never zero for none", () => {
    expect(prevalenceText(row())).toBe("15/30 apps · 4/6 of this model");
    expect(
      prevalenceText(
        row({ model_apps_with_rule: null, model_apps_measured: null }),
      ),
    ).toBe("15/30 apps");
    expect(
      prevalenceText(row({ apps_measured: 0, model_apps_measured: 0 })),
    ).toBe("— apps · — of this model");
  });

  it("indexes prevalence by tool + rule and by rule (most frequent in the run)", () => {
    const a = row({ tool: "ruff", rule_id: "S105", count_in_run: 1 });
    const b = row({ tool: "bandit", rule_id: "S105", count_in_run: 3 });
    const { byToolRule, byRule } = prevalenceIndex([a, b]);
    expect(byToolRule.get(prevalenceKey("ruff", "S105"))).toBe(a);
    expect(byRule.get("S105")).toBe(b);
  });

  it("describes excluded directories and drops uninformative issue classes", () => {
    expect(excludedDirsText({ node_modules: 3, ".tmp": 81 })).toBe(
      "81 in .tmp/ · 3 in node_modules/",
    );
    expect(issueClassLabel("hardcoded-secret", "bandit", "B105")).toBe(
      "hardcoded-secret",
    );
    expect(
      issueClassLabel("pylint:line-too-long", "pylint", "line-too-long"),
    ).toBe("");
    expect(issueClassLabel("unused-import", "pylint", "unused-import")).toBe(
      "",
    );
    expect(issueClassLabel(undefined, "x", "y")).toBe("");
  });

  it("names each tool when several report one rule id, so one tool's figures never stand for another's", () => {
    const flake8 = row({
      tool: "flake8",
      rule_id: "F401",
      count_in_run: 2,
      apps_with_rule: 3,
      apps_measured: 18,
    });
    const ruff = row({
      tool: "ruff",
      rule_id: "F401",
      count_in_run: 2,
      apps_with_rule: 7,
      apps_measured: 30,
    });
    const { byRuleAll } = prevalenceIndex([ruff, flake8]);
    const lines = rulePrevalenceLines(byRuleAll.get("F401") ?? []);
    expect(lines.map((l) => l.text)).toEqual([
      "flake8: 3/18 apps · 4/6 of this model",
      "ruff: 7/30 apps · 4/6 of this model",
    ]);
    // One tool: the plain figure.
    expect(rulePrevalenceLines([row()]).map((l) => l.text)).toEqual([
      "15/30 apps · 4/6 of this model",
    ]);
    expect(rulePrevalenceLines([])).toEqual([]);
  });

  it("says whether this run left stored scratch files out or read them", () => {
    const dirs = { ".tmp": 81 };
    expect(
      excludedPathsNote({
        count: 81,
        dirs,
        left_out: true,
        findings_in_excluded: 0,
      }),
    ).toBe(
      "81 stored files were left out of the analysis and of the LOC (81 in .tmp/): dependency, cache and " +
        "scratch directories are not the app's source.",
    );
    const old = excludedPathsNote({
      count: 81,
      dirs,
      left_out: false,
      findings_in_excluded: 74,
    });
    expect(old).toContain(
      "This run predates leaving them out of the analysis: its tools read them, and 74 of its findings are in them.",
    );
    expect(old).not.toContain("were left out of the analysis");
    const unknown = excludedPathsNote({
      count: 1,
      dirs: { node_modules: 1 },
      left_out: null,
    });
    expect(unknown).toBe(
      "1 stored file (1 in node_modules/) is not counted in the LOC: dependency, cache and scratch " +
        "directories are not the app's source; today's analyses leave it out.",
    );
  });
});
