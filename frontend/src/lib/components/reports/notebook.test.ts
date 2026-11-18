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
