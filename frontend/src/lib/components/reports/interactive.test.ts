import { describe, expect, it } from "vitest";
import type { ReportDetail, ReportTable } from "$lib/api/reports";
import { chartDomain, chartRows, chartSpec, parseInterval } from "./chart";
import { cellDelta, matchTable, rowsOnlyInOther } from "./compare";
import { planTable, sortRows, toCsv, toMarkdown } from "./format";
import { buildNotebook, notebookPaths } from "./notebook";
import { competitionRanks, rankingData, topsis } from "./ranking";

const table = (
  title: string,
  columns: string[],
  rows: string[][],
): ReportTable => ({
  title,
  columns,
  rows,
  note: "",
  totalRows: rows.length,
});

// The study report on dev1 (rpt_7df6…): the backend's TOPSIS gave 0.7639 / 0.2361.
const RANKING = {
  ranking: [
    {
      model: "a/pro",
      deploy_rate: 1.0,
      pass_rate: 0.9872,
      issues_per_kloc: 12.8978,
      security_per_app: 0.5,
      cost_per_app: 0.0,
      topsis_rank: 1,
      topsis_score: 0.7639,
    },
    {
      model: "a/flash",
      deploy_rate: 1.0,
      pass_rate: 0.9761,
      issues_per_kloc: 9.2238,
      security_per_app: 2.1667,
      cost_per_app: 0.0,
      topsis_rank: 2,
      topsis_score: 0.2361,
    },
  ],
  criteria: [
    {
      key: "deploy_rate",
      label: "Deploy rate",
      weight: 0.3,
      direction: "benefit" as const,
    },
    {
      key: "pass_rate",
      label: "Functional pass rate",
      weight: 0.3,
      direction: "benefit" as const,
    },
    {
      key: "issues_per_kloc",
      label: "Code issues / kLOC",
      weight: 0.15,
      direction: "cost" as const,
    },
    {
      key: "security_per_app",
      label: "Security issues / app",
      weight: 0.15,
      direction: "cost" as const,
    },
    {
      key: "cost_per_app",
      label: "Cost / app (USD)",
      weight: 0.1,
      direction: "cost" as const,
    },
  ],
};

describe("what-if ranking", () => {
  it("reproduces the backend's TOPSIS scores with the report's weights", () => {
    const data = rankingData({ ranking: RANKING })!;
    const { scores, dropped } = topsis(
      data.ranking,
      data.criteria,
      data.criteria.map((c) => c.weight),
    );
    expect(dropped).toEqual([]);
    expect(scores.get("a/pro")).toBeCloseTo(0.7639, 4);
    expect(scores.get("a/flash")).toBeCloseTo(0.2361, 4);
  });

  it("re-ranks when only code issues count, and ranks ties 1-1-3", () => {
    const { scores } = topsis(
      RANKING.ranking,
      RANKING.criteria,
      [0, 0, 1, 0, 0],
    );
    expect(competitionRanks(scores).get("a/flash")).toBe(1);
    expect([
      ...competitionRanks(
        new Map([
          ["x", 0.5],
          ["y", 0.5],
          ["z", 0.1],
        ]),
      ).values(),
    ]).toEqual([1, 1, 3]);
  });

  it("leaves out models missing a criterion and has no data without criteria", () => {
    const rows = [...RANKING.ranking, { model: "c", deploy_rate: null }];
    expect(
      topsis(rows as never, RANKING.criteria, [1, 1, 1, 1, 1]).dropped,
    ).toEqual(["c"]);
    expect(rankingData({ ranking: { ranking: [], criteria: [] } })).toBeNull();
  });
});

describe("compare", () => {
  it("deltas only between numbers of the same unit", () => {
    expect(cellDelta("0.9872", "0.9761")).toEqual({ text: "+0.011", sign: 1 });
    expect(cellDelta("75.0% (9/12)", "80.0% (8/10)")).toEqual({
      text: "−5 pp",
      sign: -1,
    });
    expect(cellDelta("12", "12")?.sign).toBe(0);
    expect(cellDelta("471", "-")).toBeNull();
    expect(cellDelta("0.5", "50%")).toBeNull();
    expect(cellDelta("m1", "m2")).toBeNull();
  });

  it("matches tables by title, rows by their first cell", () => {
    const a = table(
      "Issues by model",
      ["Model", "Raw"],
      [
        ["m1", "1"],
        ["m2", "2"],
      ],
    );
    const b = table(
      "Issues by model",
      ["Model", "Raw"],
      [
        ["m1", "3"],
        ["m3", "4"],
      ],
    );
    expect(matchTable(a, 0, { tables: [table("Other", ["X"], []), b] })).toBe(
      b,
    );
    expect(matchTable(a, 0, { tables: [] })).toBeNull();
    expect(rowsOnlyInOther(a, b)).toEqual(["m3"]);
    const c = table("Issues by condition", ["Condition", "Raw"], []);
    expect(matchTable(a, 3, { tables: [c] })).toBe(c);
  });
});

describe("charts", () => {
  it("pairs a metric with its interval column and skips ranks", () => {
    const t = table(
      "Leaderboard",
      ["Rank", "Condition", "Apps", "Mean pass rate", "95% CI (app bootstrap)"],
      [
        ["1", "c1", "6", "0.9872", "[0.9615, 1.0]"],
        ["2", "c2", "6", "0.9761", "[0.9504, 1.0]"],
      ],
    );
    const spec = chartSpec(t, planTable(t))!;
    expect(t.columns[spec.labelIndex]).toBe("Condition");
    expect(spec.metrics.map((m) => m.header)).toEqual([
      "Mean pass rate",
      "Apps",
    ]);
    const rate = spec.metrics[0];
    expect(rate.ciIndex).toBe(4);
    expect(chartRows(t, spec, rate)[0]).toMatchObject({
      label: "c1",
      value: 0.9872,
      lo: 0.9615,
      hi: 1,
    });
    expect(chartDomain(chartRows(t, spec, rate), "share")).toEqual([0, 1]);
  });

  it("adds the second identity column when labels repeat", () => {
    const t = table(
      "Issue types",
      ["Tool", "Rule", "Findings"],
      [
        ["osv", "A", "5"],
        ["osv", "B", "3"],
      ],
    );
    const spec = chartSpec(t, planTable(t))!;
    expect(chartRows(t, spec, spec.metrics[0]).map((r) => r.label)).toEqual([
      "osv · A",
      "osv · B",
    ]);
  });

  it("reads inline percentage intervals; pairs tables are not charts", () => {
    expect(parseInterval("75.0% (9/12) [60.1, 88.0]")).toEqual([0.601, 0.88]);
    expect(parseInterval("12")).toBeNull();
    const pairs = table(
      "Protocol",
      ["Item", "Value"],
      [
        ["Runs", "3"],
        ["Apps", "4"],
      ],
    );
    expect(chartSpec(pairs, planTable(pairs))).toBeNull();
    expect(chartDomain([{ value: -2 }, { value: 3 }], "num")).toEqual([-2, 3]);
  });
});

describe("table interaction", () => {
  it("sorts numbers numerically with blanks last", () => {
    const rows = [
      ["a", "10"],
      ["b", "-"],
      ["c", "9"],
      ["d", "1,200"],
    ];
    expect(sortRows(rows, 1, "num", "desc")).toEqual([3, 0, 2, 1]);
    expect(sortRows(rows, 1, "num", "asc")).toEqual([2, 0, 3, 1]);
    expect(sortRows(rows, 0, "text", "desc")).toEqual([3, 2, 1, 0]);
  });

  it("writes CSV and Markdown", () => {
    expect(toCsv(["A", "B"], [["x, y", 'say "hi"']])).toBe(
      'A,B\r\n"x, y","say ""hi"""\r\n',
    );
    expect(toMarkdown(["A"], [["a|b"]])).toBe("| A |\n| --- |\n| a\\|b |\n");
  });
});

describe("notebook", () => {
  it("loads report.json, one section per table, and a what-if cell for study reports", () => {
    const report = {
      report_id: "rpt_7df60bd5d38755b6",
      report_type: "study_report",
      title: "Study",
      description: "",
      config: {},
      summary: {},
      created_at: "2026-11-02T11:52:24Z",
      completed_at: "2026-11-02T11:52:25Z",
      report_data: { ranking: RANKING },
    } as unknown as ReportDetail;
    const doc = {
      tables: [
        table(
          "Plan metrics by model",
          ["Model", "Pass rate"],
          [
            ["m1", "0.9"],
            ["m2", "0.8"],
          ],
        ),
        table("Empty", ["Model"], []),
      ],
    };
    const nb = buildNotebook(report, doc, "https://lab.example");
    expect(notebookPaths(report)).toEqual({
      dir: "reports/RPT-7DF60BD5",
      notebook: "reports/RPT-7DF60BD5/report.ipynb",
      data: "reports/RPT-7DF60BD5/report.json",
    });
    const src = nb.cells.map((c) => c.source).join("\n");
    expect(src).toContain("open('report.json')");
    expect(src).toContain("## Table 1 · Plan metrics by model");
    expect(src).toContain('col = "Pass rate"');
    expect(src).toContain("## Table 2 · Empty");
    expect(src).toContain("What if: re-rank");
    expect(src).toContain("/api/reports/rpt_7df60bd5d38755b6/tables/");
    expect(nb.metadata.kernelspec).toMatchObject({ name: "python" });
  });
});
