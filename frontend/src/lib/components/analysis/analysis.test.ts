import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RunFinding, RunToolResult } from "$lib/api/runs";
import type { SseEvent } from "$lib/api/sse";
import { RunWatcher } from "./live.svelte";
import {
  formatClock,
  levelClasses,
  levelRank,
  reportedLevels,
  resultState,
  toCsv,
} from "./format";
import {
  advisoryUrl,
  dependencyInfo,
  httpInfo,
  otherData,
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
  it("counts a not-applicable skip as measured, other skips and failures as not", () => {
    expect(
      resultState(
        result({
          status: "skipped",
          summary: { skip_reason: "not_applicable" },
        }),
      ).measured,
    ).toBe(true);
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
    expect(reportedLevels(summary).map(([label]) => label)).toEqual([
      "critical",
      "warning",
      "convention",
      "8.6",
    ]);
    expect(reportedLevels({})).toEqual([]);
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
