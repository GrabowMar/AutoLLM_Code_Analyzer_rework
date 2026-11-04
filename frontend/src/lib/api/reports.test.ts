import { describe, expect, it } from "vitest";
import {
  excludeTruncatedConfig,
  reportDocument,
  summaryItems,
  type ReportTablesResponse,
} from "./reports";

// Shape of GET /api/reports/{id}/tables/ (a study conditions report, stored type `experiment_report`).
const RESPONSE: ReportTablesResponse = {
  report_id: "rpt_272a51a8cb1db9da",
  report_type: "experiment_report",
  title: "Pipeline audit",
  tables: [
    {
      title: "Leaderboard (conformance pass rate)",
      columns: [
        { key: "rank", header: "Rank" },
        { key: "label", header: "Condition" },
        { key: "pass_rate_mean", header: "Mean pass rate" },
        { key: "pass_rate_ci95", header: "95% CI (app bootstrap)" },
      ],
      rows: [
        {
          rank: "1",
          label: "DeepSeek V4.1 Flash",
          pass_rate_mean: "1",
          pass_rate_ci95: "-",
        },
        { rank: "1", label: "GLM | 5.3 Flash", pass_rate_mean: "1" },
      ],
      note: "Graded and Missing count runs (app x repeat). Equal means share a rank.",
      total_rows: 2,
    },
    {
      title: "Planned comparisons",
      columns: [],
      rows: [],
      note: "",
      total_rows: 0,
    },
    {
      title: "Issues by app",
      columns: [{ key: "app", header: "App" }],
      rows: [{ app: "a" }],
      note: "First 500 of 612 rows; the JSON export has all of them.",
      total_rows: 612,
    },
  ],
};

describe("reportDocument", () => {
  const doc = reportDocument(RESPONSE);

  it("orders cells by column; '-' stays the unmeasured marker and a missing cell is empty", () => {
    const [board] = doc.tables;
    expect(board.title).toBe("Leaderboard (conformance pass rate)");
    expect(board.columns).toEqual([
      "Rank",
      "Condition",
      "Mean pass rate",
      "95% CI (app bootstrap)",
    ]);
    expect(board.rows).toEqual([
      ["1", "DeepSeek V4.1 Flash", "1", "-"],
      ["1", "GLM | 5.3 Flash", "1", ""],
    ]);
    expect(board.note).toBe(
      "Graded and Missing count runs (app x repeat). Equal means share a rank.",
    );
  });

  it("keeps empty tables empty and the row cap's count", () => {
    expect(doc.tables[1]).toEqual({
      title: "Planned comparisons",
      columns: [],
      rows: [],
      note: "",
      totalRows: 0,
    });
    expect(doc.tables[2].totalRows).toBe(612);
    expect(doc.tables[2].note).toContain("First 500 of 612 rows");
  });
});

describe("summaryItems", () => {
  it("names no leader on a tie and shows unmeasured as a dash", () => {
    // f170be08: all three conditions at 100%.
    const items = summaryItems({
      study_id: "f170be08-a3e8-4adf-9ea8-7b2badaada44",
      condition_count: 3,
      leader: null,
      leader_pass_rate: 1,
      tied_leaders: 3,
      interim: false,
    });
    expect(items).toEqual([
      {
        label: "Experiment",
        value: "f170be08",
        href: "/experiments/f170be08-a3e8-4adf-9ea8-7b2badaada44",
      },
      { label: "Condition count", value: "3" },
      { label: "Leader", value: "Tie: 3 conditions share the top" },
      { label: "Leader pass rate", value: "100.0%" },
      { label: "Interim", value: "no" },
    ]);
    // A summary stored before ties were detected: re-read from the report's leaderboard.
    const stale = { leader: "DeepSeek", leader_pass_rate: 1 };
    const board = (means: (number | null)[]) => ({
      results: {
        leaderboard: means.map((m, i) => ({
          label: `c${i}`,
          pass_rate_mean: m,
        })),
      },
    });
    expect(summaryItems(stale, board([1, 1, 1]))[0]).toEqual({
      label: "Leader",
      value: "Tie: 3 conditions share the top",
    });
    expect(summaryItems(stale, board([0.5, 0.9, null]))[0].value).toBe("c1");
    expect(summaryItems(stale, board([null, null]))[0].value).toBe("—");
    expect(summaryItems({ total_findings: null, success_rate: null })).toEqual([
      { label: "Total findings", value: "—" },
      { label: "Success rate", value: "—" },
    ]);
  });

  it("re-reads a study report's leader from its TOPSIS ranks", () => {
    const ranking = (ranks: number[], grouped_by = "model") => ({
      protocol: { grouped_by },
      ranking: {
        ranking: ranks.map((r, i) => ({ model: `m${i}`, topsis_rank: r })),
      },
    });
    // A summary stored before ties were detected names ranking[0].
    const stale = { runs: 12, leader: "m0", unique_issues: null };
    expect(summaryItems(stale, ranking([1, 1, 3]))).toEqual([
      { label: "Runs", value: "12" },
      { label: "Leader", value: "Tie: 2 models share the top" },
      { label: "Unique issues", value: "—" },
    ]);
    expect(summaryItems(stale, ranking([1, 1], "condition"))[1].value).toBe(
      "Tie: 2 conditions share the top",
    );
    expect(summaryItems(stale, ranking([2, 1]))[1].value).toBe("m1");
    // The server's own tie contract, and a ranking without TOPSIS ranks keeps the stored leader.
    expect(
      summaryItems(
        { leader: null, tied_leaders: 3 },
        { ranking: { ranking: [] } },
      )[0].value,
    ).toBe("Tie: 3 conditions share the top");
    expect(
      summaryItems(stale, { ranking: { ranking: [{ model: "x" }] } })[1].value,
    ).toBe("m0");
  });
});

describe("excludeTruncatedConfig", () => {
  it("omits the key when the option is not ticked (no false override of a locked plan)", () => {
    expect(excludeTruncatedConfig(false)).toEqual({});
    expect("exclude_truncated" in excludeTruncatedConfig(false)).toBe(false);
  });
  it("sends true when ticked", () => {
    expect(excludeTruncatedConfig(true)).toEqual({ exclude_truncated: true });
  });
});
