import { describe, expect, it } from "vitest";
import type { AnalysisRunListItem, RunSummary } from "$lib/api/analysis";
import { NA, fmtInt, fmtPct, fmtUsd, shortHash } from "./format";
import {
  aiConsensusCell,
  staticFindingsCell,
  summaryToolProblems,
} from "./run-utils";
import * as insights from "$lib/components/insights/format";

describe("shared analysis formatters", () => {
  it("render missing values as the NA marker, never 0", () => {
    expect(fmtInt(null)).toBe(NA);
    expect(fmtInt(undefined)).toBe(NA);
    expect(fmtInt(Number.NaN)).toBe(NA);
    expect(fmtPct(null)).toBe(NA);
    expect(fmtUsd(undefined)).toBe(NA);
    expect(shortHash("")).toBe(NA);
  });

  it("keeps a known zero distinct from a missing value", () => {
    expect(fmtInt(0)).toBe("0");
    expect(fmtUsd(0)).toBe("$0.00");
    expect(fmtPct(0)).toBe("0%");
  });

  it("insights wrappers return null for missing data and delegate otherwise", () => {
    expect(insights.fmtInt(null)).toBeNull();
    expect(insights.fmtShare(undefined)).toBeNull();
    expect(insights.fmtInt(1234)).toBe(fmtInt(1234));
    expect(insights.fmtUsd(0.5)).toBe(fmtUsd(0.5));
    // Inline labels: no marker for a missing hash.
    expect(insights.shortHash(null)).toBe("");
    expect(insights.shortHash("sha256:abcdef0123456789", 6)).toBe("abcdef");
  });

  it("derives outcome display meta from the one outcome vocabulary", () => {
    expect(insights.outcomeLabel("functional_pass_rate").share).toBe(true);
    expect(insights.outcomeLabel("ai_consensus_per_kloc").ai).toBe(true);
    expect(insights.outcomeLabel("measured_sloc").digits).toBe(0);
    expect(insights.outcomeLabel("some_new_key").label).toBe("some new key");
  });
});

function summary(patch: Partial<RunSummary> = {}): RunSummary {
  return {
    schema_version: 2,
    tools: {
      total: 3,
      pending: 0,
      running: 0,
      succeeded: 3,
      failed: 0,
      timed_out: 0,
      skipped: 0,
      cancelled: 0,
      not_applicable: 0,
    },
    complete: true,
    total_findings: 7,
    severity_counts: { critical: 0, high: 1, medium: 2, low: 4, info: 0 },
    static_measured: true,
    ai_findings: 2,
    ai_severity_counts: { critical: 0, high: 0, medium: 2, low: 0, info: 0 },
    issues: 5,
    sloc: { measured: 900, total: 1000 },
    cost_usd: 0.01,
    cost_complete: true,
    warnings: [],
    problems: [],
    tools_run: 3,
    tools_completed: 3,
    tools_failed: 0,
    ...patch,
  };
}

function run(patch: Partial<AnalysisRunListItem> = {}): AnalysisRunListItem {
  const s = (patch.summary as RunSummary | undefined) ?? summary();
  return {
    id: "r1",
    name: "run",
    purpose: "adhoc",
    status: "completed",
    phase: "",
    is_complete: true,
    tool_slugs: ["ruff", "bandit", "ai_review"],
    tools: s.tools,
    summary: s,
    ...patch,
  } as AnalysisRunListItem;
}

describe("run list cells", () => {
  it("read tool problems from the structured summary.problems", () => {
    const r = run({
      status: "partial",
      summary: summary({
        problems: [
          {
            tool_slug: "bandit",
            status: "failed",
            failure_reason: "timeout",
            required: false,
            message: "took too long",
          },
        ],
        tools: { ...summary().tools, succeeded: 2, failed: 1 },
      }),
    });
    const problems = summaryToolProblems(r);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatchObject({
      slug: "bandit",
      reason: "timeout",
      isAi: false,
    });
    const cell = staticFindingsCell(r);
    expect(cell.value).toBe(7);
    expect(cell.lowerBound).toBe(true);
  });

  it("show no static count (not 0) when no static tool produced data", () => {
    const r = run({
      summary: summary({
        total_findings: null,
        severity_counts: null,
        static_measured: false,
        issues: null,
      }),
    });
    expect(staticFindingsCell(r).value).toBeNull();
  });

  it("keep AI consensus separate from static findings", () => {
    const r = run();
    expect(aiConsensusCell(r).value).toBe(2);
    expect(staticFindingsCell(r).value).toBe(7);
  });
});
