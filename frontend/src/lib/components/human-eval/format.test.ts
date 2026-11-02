import { describe, expect, it } from "vitest";
import {
  absoluteUrl,
  anyTransitional,
  axisTicks,
  codeText,
  codesCsv,
  codesPrintHtml,
  codesText,
  csvCell,
  deviationText,
  formatCi,
  formatCoef,
  formatContrastDiff,
  formatDiff,
  formatP,
  formatScore,
  formatSeconds,
  formatShare,
  hostingActions,
  hostingIsRunning,
  humanize,
  isTransitional,
  metricIsShare,
  niceCeil,
  OPEN_WARNING_TEXT,
  orderedActions,
  pairwiseValueText,
  parseEmails,
  plotPercent,
  primaryAction,
  qualityFlagLabel,
  questionAnswerText,
  ratingValueText,
  repeatSuffix,
  resolveTaskTab,
  safeHttpUrl,
  shortHash,
  skipReasonLabel,
  slugify,
  symmetricDomain,
  taskActionConfirm,
  taskActionLabel,
  testerSeverityLabel,
  tickText,
  toCsv,
  toolLevelLabel,
  zeroBasedDomain,
} from "./format";

describe("resolveTaskTab", () => {
  it("accepts every tab and aliases, case-insensitively", () => {
    expect(resolveTaskTab("results")).toBe("results");
    expect(resolveTaskTab("Participants")).toBe("participants");
    expect(resolveTaskTab("hosting")).toBe("apps");
    expect(resolveTaskTab("assignments")).toBe("responses");
  });

  it("returns null for unknown or empty values", () => {
    expect(resolveTaskTab(null)).toBeNull();
    expect(resolveTaskTab("")).toBeNull();
    expect(resolveTaskTab("nope")).toBeNull();
  });
});

describe("small formats", () => {
  it("formats shares, scores, diffs and coefficients", () => {
    expect(formatShare(0.8765)).toBe("88%");
    expect(formatShare(0.876, 1)).toBe("87.6%");
    expect(formatShare(null)).toBe("—");
    expect(formatScore(4.256)).toBe("4.26");
    expect(formatScore(undefined)).toBe("—");
    expect(formatDiff(0.5)).toBe("+0.50");
    expect(formatDiff(-0.25)).toBe("−0.25");
    expect(formatDiff(0)).toBe("±0.00");
    expect(formatCoef(-0.1)).toBe("−0.10");
    expect(formatCoef(0.625)).toBe("0.63");
  });

  it("formats CIs only when both bounds exist", () => {
    expect(formatCi({ low: 3.1, high: 4.2 })).toBe("[3.10, 4.20]");
    expect(formatCi({ low: 0.1, high: 0.4 }, (v) => formatShare(v))).toBe(
      "[10%, 40%]",
    );
    expect(formatCi({ low: null, high: 4 })).toBe("");
    expect(formatCi(null)).toBe("");
  });

  it("formats p-values without false precision", () => {
    expect(formatP(0.0004)).toBe("<0.001");
    expect(formatP(0.0312)).toBe("0.031");
    expect(formatP(null)).toBe("—");
  });

  it("formats seconds", () => {
    expect(formatSeconds(42.4)).toBe("42 s");
    expect(formatSeconds(192)).toBe("3 min 12 s");
    expect(formatSeconds(180)).toBe("3 min");
    expect(formatSeconds(3900)).toBe("1 h 05 min");
    expect(formatSeconds(null)).toBe("—");
  });

  it("humanizes codes and shortens hashes", () => {
    expect(humanize("not_met")).toBe("Not met");
    expect(humanize("")).toBe("");
    expect(shortHash("abcdef0123456789")).toBe("abcdef01");
  });
});

describe("task lifecycle", () => {
  it("labels actions by the current status", () => {
    expect(taskActionLabel("open", "draft")).toBe("Open task");
    expect(taskActionLabel("open", "paused")).toBe("Resume");
    expect(taskActionLabel("reopen", "closed")).toBe("Reopen");
    expect(taskActionLabel("reopen", "archived")).toBe("Unarchive");
  });

  it("asks before freezing, closing, reopening and archiving, not before pausing or resuming", () => {
    const draft = { status: "draft" as const, frozen_at: null };
    const first = taskActionConfirm("open", draft);
    expect(first?.label).toBe("Open and freeze");
    expect(first?.description).toMatch(/freezes the design/);
    expect(
      taskActionConfirm("open", { status: "paused", frozen_at: "x" }),
    ).toBeNull();
    expect(
      taskActionConfirm("pause", { status: "open", frozen_at: "x" }),
    ).toBeNull();
    expect(
      taskActionConfirm("close", { status: "open", frozen_at: "x" })?.label,
    ).toBe("Close task");
    expect(
      taskActionConfirm("reopen", { status: "closed", frozen_at: "x" })
        ?.description,
    ).toMatch(/not confirmatory/);
    expect(
      taskActionConfirm("reopen", { status: "archived", frozen_at: "x" }),
    ).toBeNull();
    expect(
      taskActionConfirm("archive", { status: "closed", frozen_at: "x" }),
    ).not.toBeNull();
  });

  it("orders actions with the primary one last", () => {
    expect(orderedActions(["open", "archive", "close"])).toEqual([
      "archive",
      "close",
      "open",
    ]);
    expect(orderedActions(["pause", "close"], "open")).toEqual([
      "pause",
      "close",
    ]);
    expect(orderedActions(["open", "close"], "paused")).toEqual([
      "close",
      "open",
    ]);
    expect(orderedActions(["reopen", "archive"], "closed")).toEqual([
      "reopen",
      "archive",
    ]);
    expect(primaryAction(["pause", "close"], "open")).toBe("close");
    expect(primaryAction(["open", "close"], "paused")).toBe("open");
    expect(primaryAction(["reopen", "archive"], "closed")).toBe("archive");
    expect(primaryAction([], "draft")).toBeNull();
  });

  it("explains known warning codes and keeps server sentences", () => {
    expect(codeText("shared_app_state", OPEN_WARNING_TEXT)).toMatch(/reset/);
    expect(codeText("no_active_apps")).toBe("No active apps");
    expect(codeText("Add at least one app.")).toBe("Add at least one app.");
    expect(deviationText("stopped_early")).toMatch(/planned sample/);
  });
});

describe("hosting", () => {
  const info = (over: Record<string, unknown> = {}) => ({
    state: "idle" as const,
    container_status: null,
    pinned: false,
    ...over,
  });

  it("knows which states change on their own", () => {
    expect(isTransitional("building")).toBe(true);
    expect(isTransitional("queued")).toBe(true);
    expect(isTransitional("running")).toBe(false);
    expect(isTransitional(undefined)).toBe(false);
    expect(anyTransitional([{ state: "running" }, { state: "starting" }])).toBe(
      true,
    );
    expect(anyTransitional([{ state: "idle" }, { state: "failed" }])).toBe(
      false,
    );
  });

  it("offers start for stopped apps and stop for running or queued ones", () => {
    expect(hostingActions(info())).toEqual(["start", "pin", "rebuild"]);
    expect(hostingActions(info({ state: "running", pinned: true }))).toEqual([
      "stop",
      "unpin",
      "rebuild",
    ]);
    expect(hostingActions(info({ state: "queued" }))).toEqual(["stop", "pin"]);
    expect(hostingActions(info({ state: "building" }))).toEqual(["pin"]);
    expect(hostingActions(info({ state: "failed" }))).toEqual([
      "start",
      "pin",
      "rebuild",
    ]);
  });

  it("offers stop for a starting app only when its container is already running (a reset)", () => {
    // The backend stops running containers only: a pending container or a start in flight
    // would get a 200 "Stop requested" that does nothing.
    expect(
      hostingActions(info({ state: "starting", container_status: "running" })),
    ).toEqual(["stop", "pin"]);
    expect(
      hostingActions(info({ state: "starting", container_status: "pending" })),
    ).toEqual(["pin"]);
    expect(
      hostingActions(info({ state: "starting", container_status: "stopped" })),
    ).toEqual(["pin"]);
    expect(
      hostingActions(info({ state: "starting", container_status: null })),
    ).toEqual(["pin"]);
  });

  it("treats an unreachable but running container as running", () => {
    const unreachable = info({
      state: "unavailable",
      container_status: "running",
    });
    expect(hostingIsRunning(unreachable)).toBe(true);
    expect(hostingActions(unreachable)[0]).toBe("stop");
    expect(
      hostingActions(
        info({ state: "unavailable", container_status: "stopped" }),
      )[0],
    ).toBe("start");
  });
});

describe("repeatSuffix", () => {
  it("shows the 0-based repeat_index 1-based, like the pool picker and the generator", () => {
    expect(repeatSuffix(0)).toBe(" · repeat 1");
    expect(repeatSuffix(2)).toBe(" · repeat 3");
    expect(repeatSuffix(null)).toBe("");
    expect(repeatSuffix(undefined)).toBe("");
  });
});

describe("links", () => {
  it("joins origin and path", () => {
    expect(absoluteUrl("https://lab.example/", "/eval/join/abc")).toBe(
      "https://lab.example/eval/join/abc",
    );
    expect(absoluteUrl("https://lab.example", "eval")).toBe(
      "https://lab.example/eval",
    );
  });

  it("links only absolute http(s) URLs", () => {
    expect(safeHttpUrl("https://app.example/x?y=1")).toBe(
      "https://app.example/x?y=1",
    );
    expect(safeHttpUrl(" http://a.b/ ")).toBe("http://a.b/");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("JAVASCRIPT:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,x")).toBeNull();
    expect(safeHttpUrl("/admin/users")).toBeNull();
    expect(safeHttpUrl("//evil.example")).toBeNull();
    expect(safeHttpUrl("")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
});

describe("codes and CSV", () => {
  const codes = [
    { participant_id: "1", label: "P001", code: "K7QM-4XT9-BW2R" },
    { participant_id: "2", label: "P002", code: "AB12-CD34-EF56" },
  ];

  it("escapes formula-like strings but keeps numbers numeric", () => {
    expect(csvCell("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvCell("@cmd")).toBe("'@cmd");
    expect(csvCell("-5")).toBe("-5");
    expect(csvCell("+1.5e3")).toBe("+1.5e3");
    expect(csvCell(-5)).toBe("-5");
    expect(csvCell('a "b", c')).toBe('"a ""b"", c"');
    expect(csvCell("-x,y")).toBe('"\'-x,y"');
    expect(csvCell(null)).toBe("");
    expect(csvCell(true)).toBe("true");
  });

  it("writes a header and one row per code", () => {
    const csv = codesCsv(codes, "https://lab.example/eval");
    expect(csv).toBe(
      "participant,access_code,portal_url\nP001,K7QM-4XT9-BW2R,https://lab.example/eval\nP002,AB12-CD34-EF56,https://lab.example/eval\n",
    );
    expect(toCsv(["a"], [[1], ["x"]])).toBe("a\n1\nx\n");
  });

  it("copies codes as tab-separated lines", () => {
    expect(codesText(codes)).toBe("P001\tK7QM-4XT9-BW2R\nP002\tAB12-CD34-EF56");
  });

  it("escapes everything in the printable list", () => {
    const html = codesPrintHtml(codes, {
      title: '<script>alert("x")</script>',
      portalUrl: "https://lab.example/eval",
    });
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("K7QM-4XT9-BW2R");
    expect(html.match(/class="slip"/g)).toHaveLength(2);
  });
});

describe("contrasts and file names", () => {
  it("prints share metrics in percentage points", () => {
    expect(metricIsShare("compliance")).toBe(true);
    expect(metricIsShare("composite")).toBe(false);
    expect(formatContrastDiff(0.123, "success_rate")).toBe("+12.3 pp");
    expect(formatContrastDiff(-0.25, "composite")).toBe("−0.25");
    expect(formatContrastDiff(null, "score")).toBe("—");
  });

  it("slugifies titles for download names", () => {
    expect(slugify("My Study (2)")).toBe("my-study-2");
    expect(slugify("Ünïcode — tést")).toBe("unicode-test");
    expect(slugify("***")).toBe("task");
    expect(slugify("a".repeat(80))).toHaveLength(60);
  });
});

describe("parseEmails", () => {
  it("splits, lowercases, dedupes and reports invalid entries", () => {
    const { emails, invalid } = parseEmails(
      "A@Example.com, b@example.org;\nnot-an-email  a@example.com\nc@x.io",
    );
    expect(emails).toEqual(["a@example.com", "b@example.org", "c@x.io"]);
    expect(invalid).toEqual(["not-an-email"]);
  });

  it("accepts display-name forms", () => {
    expect(
      parseEmails("Ada Lovelace <ada@example.com>, Bob <bob@example.com>")
        .emails,
    ).toEqual(["ada@example.com", "bob@example.com"]);
  });

  it("returns nothing for blank input", () => {
    expect(parseEmails("  \n ")).toEqual({ emails: [], invalid: [] });
  });
});

describe("answers", () => {
  const scale = {
    min: 1,
    max: 7,
    min_label: "Poor",
    max_label: "Excellent",
    labels: { "4": "OK" },
  };

  it("shows rating values with their anchors", () => {
    expect(ratingValueText(7, scale)).toBe("7 · Excellent");
    expect(ratingValueText(4, scale)).toBe("4 · OK");
    expect(ratingValueText(5, scale)).toBe("5");
    expect(ratingValueText("na", scale)).toBe("N/A (can't judge)");
    expect(ratingValueText(undefined, scale)).toBe("—");
  });

  it("names the app on the side the participant picked", () => {
    const apps = [
      { slot: "A", label: "App 03" },
      { slot: "B", label: "App 11" },
    ];
    expect(pairwiseValueText("A", "ab_tie", apps)).toBe("A (App 03) better");
    expect(pairwiseValueText("tie", "ab_tie", apps)).toBe("Tie");
    expect(pairwiseValueText(-2, "five_point", apps)).toBe(
      "A (App 03) much better",
    );
    expect(pairwiseValueText(1, "five_point", apps)).toBe("B (App 11) better");
    expect(pairwiseValueText(0, "five_point", apps)).toBe("No difference");
    expect(pairwiseValueText(null, "ab_tie", apps)).toBe("—");
    expect(pairwiseValueText("B", "ab_tie")).toBe("B better");
  });

  it("maps question option values to labels", () => {
    const q = {
      kind: "multi" as const,
      options: [
        { value: "py", label: "Python" },
        { value: "js", label: "JavaScript" },
      ],
    };
    expect(questionAnswerText(q, ["py", "js"])).toBe("Python, JavaScript");
    expect(questionAnswerText({ ...q, kind: "single" }, "js")).toBe(
      "JavaScript",
    );
    expect(questionAnswerText({ ...q, kind: "single" }, "go")).toBe("go");
    expect(questionAnswerText({ kind: "scale", scale }, 7)).toBe(
      "7 · Excellent",
    );
    expect(questionAnswerText({ kind: "number" }, 0)).toBe("0");
    expect(questionAnswerText({ kind: "text" }, "")).toBe("—");
    expect(questionAnswerText({ kind: "multi" }, [])).toBe("—");
    expect(questionAnswerText({ kind: "text" }, undefined)).toBe("—");
  });

  it("labels tool levels and tester severities apart", () => {
    expect(toolLevelLabel("")).toBe("unrated");
    expect(toolLevelLabel("  ")).toBe("unrated");
    expect(toolLevelLabel("HIGH")).toBe("HIGH");
    expect(toolLevelLabel("convention")).toBe("convention");
    expect(testerSeverityLabel("high")).toBe("High");
    expect(testerSeverityLabel("")).toBe("Not given");
  });

  it("labels skip reasons and quality flags", () => {
    expect(skipReasonLabel("app_broken")).toBe("App broken");
    expect(skipReasonLabel("weird_reason")).toBe("Weird reason");
    expect(skipReasonLabel("")).toBe("");
    expect(qualityFlagLabel("possible_duplicate")).toBe("Possible duplicate");
    expect(qualityFlagLabel("new_flag")).toBe("New flag");
  });
});

describe("figures", () => {
  it("positions values on a domain, clamped", () => {
    expect(plotPercent(4, [1, 7])).toBe(50);
    expect(plotPercent(-1, [0, 1])).toBe(0);
    expect(plotPercent(2, [0, 1])).toBe(100);
    expect(plotPercent(3, [3, 3])).toBe(0);
  });

  it("rounds maxima up to nice numbers", () => {
    expect(niceCeil(0.7)).toBe(1);
    expect(niceCeil(1.3)).toBe(2);
    expect(niceCeil(2.2)).toBe(2.5);
    expect(niceCeil(3.4)).toBe(5);
    expect(niceCeil(7)).toBe(10);
    expect(niceCeil(0)).toBe(1);
  });

  it("ticks every integer of a short scale, else five steps", () => {
    expect(axisTicks([1, 7])).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(axisTicks([0, 1])).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(axisTicks([0, 100])).toEqual([0, 25, 50, 75, 100]);
    expect(axisTicks([-2, 2])).toEqual([-2, -1, 0, 1, 2]);
  });

  it("builds zero-based and symmetric domains", () => {
    expect(zeroBasedDomain([1.2, null, 3.4])).toEqual([0, 5]);
    expect(zeroBasedDomain([])).toEqual([0, 1]);
    expect(symmetricDomain([-0.3, 1.7])).toEqual([-2, 2]);
    expect(symmetricDomain([0.1])).toEqual([-0.5, 0.5]);
  });

  it("writes tick text per kind", () => {
    expect(tickText(0.25, "share")).toBe("25%");
    expect(tickText(3)).toBe("3");
    expect(tickText(0.125)).toBe("0.13");
    expect(tickText(-1, "log")).toBe("−1");
    expect(tickText(0, "log")).toBe("0");
  });
});
