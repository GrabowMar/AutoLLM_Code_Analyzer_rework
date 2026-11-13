import { describe, expect, it } from "vitest";
import type { ReportTable } from "$lib/api/reports";
import {
  chipParts,
  expiryLabel,
  generationTime,
  headline,
  humanize,
  keyFigures,
  planTable,
  reportAccession,
  shareOf,
  specItems,
  titleParts,
} from "./format";

const table = (
  columns: string[],
  rows: string[][],
  note = "",
): ReportTable => ({
  title: "T",
  columns,
  rows,
  note,
  totalRows: rows.length,
});

describe("labels", () => {
  it("accession ids and humanized keys", () => {
    expect(reportAccession("rpt_7df60bd5d38755b6")).toBe("RPT-7DF60BD5");
    expect(humanize("success_rate")).toBe("Success rate");
    expect(humanize("Mean pass rate")).toBe("Mean pass rate");
    expect(titleParts("generation_analytics / summary / by_status")).toEqual([
      "Generation analytics",
      "Summary",
      "By status",
    ]);
  });
});

describe("shareOf", () => {
  it("reads fractions and leading percentages, nothing else", () => {
    expect(shareOf("0.9872")).toBe(0.9872);
    expect(shareOf("1")).toBe(1);
    expect(shareOf("75.0% (9/12) [60.1, 88.0]")).toBe(0.75);
    expect(shareOf("17.5")).toBeNull();
    expect(shareOf("[0.96, 1.0]")).toBeNull();
    expect(shareOf("-")).toBeNull();
  });
});

describe("planTable", () => {
  it("sets Item/Value tables as pairs and level/finding tables as threats", () => {
    expect(planTable(table(["Item", "Value"], [["Runs", "3"]])).layout).toBe(
      "pairs",
    );
    expect(planTable(table(["Term", "Meaning"], [["app", "x"]])).layout).toBe(
      "pairs",
    );
    expect(
      planTable(
        table(["Level", "Affects", "Finding"], [["warning", "all", "x"]]),
      ).layout,
    ).toBe("threats");
    expect(planTable(table(["Note"], [["x"]])).layout).toBe("list");
  });

  it("classifies columns and leaves out the ones blank on every row", () => {
    const plan = planTable(
      table(
        [
          "Model",
          "Apps",
          "Pass rate",
          "p (Holm)",
          "Levels the tool printed",
          "Runtime (DAST)",
        ],
        [
          ["m1", "6", "0.98", "0.5", "LOW: 4, MEDIUM: 5", "-"],
          ["m2", "6", "0.97", "1.0", "unrated: 3", ""],
        ],
      ),
    );
    expect(plan.layout).toBe("grid");
    expect(plan.columns.map((c) => c.kind)).toEqual([
      "text",
      "num",
      "share",
      "num",
      "chips",
    ]);
    expect(plan.columns[2].best).toBe(0.98);
    expect(plan.hidden).toEqual(["Runtime (DAST)"]);
  });

  it("keeps the columns of an empty table", () => {
    const plan = planTable(table(["Model", "Apps"], []));
    expect(plan.columns).toHaveLength(2);
    expect(plan.hidden).toEqual([]);
  });

  it("splits level chips", () => {
    expect(chipParts("LOW: 4, MEDIUM: 5")).toEqual([
      ["LOW", "4"],
      ["MEDIUM", "5"],
    ]);
  });
});

describe("specItems", () => {
  it("reads scope, analysis and randomisation from a study report config", () => {
    const items = specItems("study_report", {
      study_id: "d9ed43ed-4c0b",
      seed: 1,
      exclude_truncated: true,
    });
    expect(items.map((i) => i.label)).toEqual([
      "Scope",
      "Analysis",
      "Randomisation",
    ]);
    expect(items[0]).toMatchObject({
      value: "Experiment d9ed43ed",
      href: "/experiments/d9ed43ed-4c0b",
    });
    expect(items[1].value).toBe("Truncated runs excluded");
    expect(items[2].value).toBe("seed 1");
  });

  it("names the window and the whole-platform scope; lists unknown keys", () => {
    const items = specItems("generation_analytics", {
      days: 30,
      extra_flag: "on",
    });
    expect(items).toEqual([
      { label: "Scope", value: "Whole platform", sub: "your data" },
      { label: "Window", value: "Last 30 days" },
      { label: "Extra flag", value: "on" },
    ]);
    expect(specItems("study_report", {})[0].value).toBe("All my apps");
  });
});

describe("keyFigures", () => {
  it("puts the leader first, rates with a share, warnings marked, by_* maps as breakdowns", () => {
    const figs = keyFigures({
      jobs: 12,
      leader: "m1",
      warnings: 4,
      deploy_rate: 1,
      by_status: { failed: 2, completed: 8 },
      platform: { total_runs: 499 },
    });
    expect(figs[0]).toMatchObject({ kind: "leader", value: "m1" });
    expect(figs[1]).toMatchObject({ kind: "rate", value: "100.0%", share: 1 });
    expect(figs.find((f) => f.label === "Warnings")?.attention).toBe(true);
    expect(figs.find((f) => f.label === "Platform · total runs")?.value).toBe(
      "499",
    );
    const b = figs.at(-1)!;
    expect(b.kind).toBe("breakdown");
    expect(b.total).toBe(10);
    expect(b.parts?.[0]).toEqual({ label: "Completed", n: 8 });
  });

  it("keeps unmeasured values as a dash", () => {
    expect(keyFigures({ total_findings: null })[0].value).toBe("—");
  });
});

describe("list helpers", () => {
  it("headline picks the leader, else a count", () => {
    expect(
      headline({ leader: "local-x/m1", leader_pass_rate: 0.9872 }),
    ).toEqual({
      label: "leader",
      value: "m1 · 98.7%",
    });
    expect(headline({ unique_issues: 325, jobs: 12 })).toEqual({
      label: "unique issues",
      value: "325",
    });
    expect(headline({ tool: null })).toBeNull();
  });

  it("expiry and generation time", () => {
    const now = Date.parse("2026-11-05T12:00:00Z");
    expect(expiryLabel("2026-11-10T12:00:00Z", now)).toBe("expires in 5 d");
    expect(expiryLabel("2026-11-05T15:00:00Z", now)).toBe("expires in 3 h");
    expect(expiryLabel("2026-11-01T00:00:00Z", now)).toBe("expired");
    expect(expiryLabel(null, now)).toBeNull();
    expect(
      generationTime("2026-11-05T12:00:00Z", "2026-11-05T12:00:01.2Z"),
    ).toBe("1.2 s");
    expect(generationTime("2026-11-05T12:00:00Z", "2026-11-05T12:03:00Z")).toBe(
      "3 min",
    );
    expect(generationTime("2026-11-05T12:00:00Z", null)).toBeNull();
  });
});
