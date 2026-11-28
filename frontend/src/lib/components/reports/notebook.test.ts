import { describe, expect, it } from "vitest";
import type { ReportDetail } from "$lib/api/reports";
import {
  buildNotebook,
  notebookData,
  notebookNeedsUpdate,
  notebookPaths,
} from "./notebook";

const report = {
  report_id: "rpt_7df60bd5aaaa1111",
  report_type: "model_analysis",
  title: 'A "quoted" title: λ, 日本語, 🧪\nC:\\results',
  description: "Tabs\tand newlines\nremain data",
  config: { seed: 37, include_ai: false },
  summary: {},
  created_at: "2026-11-16T00:00:00Z",
  completed_at: null,
  report_data: { per_app: [{ total_loc: null, usable: true, cost: 0.00123 }] },
} as unknown as ReportDetail;

describe("portable report notebook", () => {
  it("identifies saved workflows needing an explicit reset without downgrading newer notebooks", () => {
    const notebook = buildNotebook(report, { tables: [] });
    expect(notebookNeedsUpdate(notebook)).toBe(false);
    expect(
      notebookNeedsUpdate({ metadata: { llmlab: { workflow_version: 2 } } }),
    ).toBe(true);
    expect(notebookNeedsUpdate({ metadata: {} })).toBe(true);
    expect(
      notebookNeedsUpdate({ metadata: { llmlab: { workflow_version: 4 } } }),
    ).toBe(true);
    expect(
      notebookNeedsUpdate({ metadata: { llmlab: { workflow_version: 5 } } }),
    ).toBe(false);
  });
  it("embeds an exact snapshot with Unicode, escaped text and unrounded values", () => {
    const doc = { tables: [] };
    const nb = buildNotebook(report, doc);
    const setup = nb.cells.find((cell) =>
      cell.source.includes("report = json.loads("),
    )!.source;
    const encoded = setup
      .split("\n")
      .find((line) => line.trimStart().startsWith("report = json.loads("))!;
    const pythonString = encoded
      .trim()
      .slice("report = json.loads(".length, -1);
    expect(JSON.parse(JSON.parse(pythonString))).toEqual(
      notebookData(report, doc),
    );
    expect(setup).toContain("Path('report.json').exists()");
    expect(setup).toContain("belongs to a different report");
  });

  it("says what the final-assessment criteria count in the what-if section, and nothing for other rankings", () => {
    const ranked = (keys: string[]) =>
      ({
        ...report,
        report_type: "study_report",
        report_data: {
          per_app: [],
          ranking: {
            ranking: [
              {
                model: "m",
                topsis_rank: 1,
                ...Object.fromEntries(keys.map((k) => [k, 1])),
              },
            ],
            criteria: keys.map((key) => ({
              key,
              label: key,
              weight: 1 / keys.length,
              direction: "benefit",
            })),
          },
        },
      }) as unknown as ReportDetail;
    const whatIf = (r: ReportDetail) =>
      buildNotebook(r, { tables: [] }).cells.find((cell) =>
        cell.source.startsWith("## What if"),
      )!.source;
    expect(whatIf(ranked(["deploy_rate_itt", "list_cost_per_app"]))).toContain(
      "failures as 0 over every usable app; cost is every run's tokens at the model's public list prices",
    );
    expect(whatIf(ranked(["deploy_rate", "cost_per_app"]))).not.toContain(
      "failures as 0",
    );
  });

  it("keeps full report identifiers distinct even when the displayed accession collides", () => {
    const first = notebookPaths(report);
    const second = notebookPaths({ report_id: "rpt_7df60bd5bbbb2222" });
    expect(first.dir).not.toEqual(second.dir);
    expect(first.notebook).toMatch(/^reports\/[^/]+\/report\.ipynb$/);
    expect(notebookPaths({ report_id: "rpt_a/b?c" }).dir).not.toContain("/b");
  });

  it("keeps duplicate headings as a table without generating ambiguous chart column lookups", () => {
    const nb = buildNotebook(report, {
      tables: [
        {
          title: "Duplicates",
          columns: ["Model", "Value", "Value", "Value [2]"],
          rows: [["m1", "0.123456", "-", "7"]],
          note: "",
          totalRows: 1,
        },
      ],
    });
    const index = nb.cells.findIndex(
      (cell) => cell.source === "## Table 1 · Duplicates",
    );
    expect(nb.cells[index + 1].source).toEqual("T[1]");
    const setup = nb.cells.find((cell) =>
      cell.source.includes("report = json.loads("),
    )!.source;
    const encoded = setup
      .split("\n")
      .find((line) => line.trimStart().startsWith("report = json.loads("))!;
    const snapshot = JSON.parse(
      JSON.parse(encoded.trim().slice("report = json.loads(".length, -1)),
    );
    expect(snapshot.tables[0].columns).toEqual([
      "Model",
      "Value",
      "Value",
      "Value [2]",
    ]);
    expect(snapshot.tables[0].rows[0][2]).toBe("-");
  });

  it("adds the sample-selection cells only to study reports that pool experiments", () => {
    const study = {
      ...report,
      report_type: "study_report",
      report_data: {
        per_app: [],
        selection: { mode: "latest_unique", matrix: [], groups: [] },
      },
    } as unknown as ReportDetail;
    const cells = buildNotebook(study, { tables: [] }).cells;
    const heading = cells.findIndex((cell) =>
      cell.source.startsWith("## Sample selection"),
    );
    expect(heading).toBeGreaterThan(0);
    expect(cells[heading + 1].source).toContain("S = pd.DataFrame(");
    // Selected runs outside the analysed sample are marked, never shown as plain "selected".
    expect(cells[heading + 1].source).toContain("'left out'");
    expect(cells[heading + 1].source).toContain("values='sample'");
    expect(cells[heading + 1].cell_type).toBe("code");
    const workflow = cells.findIndex((cell) =>
      cell.source.startsWith("## Reproducible analysis workflow"),
    );
    expect(workflow).toBeGreaterThan(heading);
    const plain = buildNotebook(
      { ...study, report_data: { per_app: [] } } as unknown as ReportDetail,
      { tables: [] },
    ).cells;
    expect(
      plain.some((cell) => cell.source.startsWith("## Sample selection")),
    ).toBe(false);
  });

  it("makes live access optional and keeps all cell IDs valid and stable", () => {
    const notebook = buildNotebook(report, { tables: [] });
    const live = notebook.cells.find((cell) =>
      cell.source.startsWith("async def fetch_live_tables():"),
    );
    expect(live).toBeDefined();
    expect(
      live!.source
        .split("\n")
        .slice(1)
        .every((line) => line.startsWith("    ")),
    ).toBe(true);
    expect(
      notebook.cells.every((cell) => /^[A-Za-z0-9_-]{1,64}$/.test(cell.id)),
    ).toBe(true);
    expect(new Set(notebook.cells.map((cell) => cell.id)).size).toBe(
      notebook.cells.length,
    );
    expect(
      buildNotebook(report, { tables: [] }).cells.map((cell) => cell.id),
    ).toEqual(notebook.cells.map((cell) => cell.id));
  });
});
