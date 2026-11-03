import { describe, expect, it } from "vitest";
import libraryDiff from "$lib/api/v3/__fixtures__/library-diff-prompt.json";
import promptDiff from "$lib/api/v3/__fixtures__/prompt-diff.json";
import {
  collapseContext,
  diffStats,
  formatDiffValue,
  lineDiff,
  parseUnified,
  structuredRows,
} from "./diff";

describe("version-diff rendering", () => {
  it("numbers the rows of the server's unified diff", () => {
    const rows = parseUnified(libraryDiff.unified);
    expect(rows.slice(0, 2).map((r) => r.type)).toEqual(["meta", "meta"]);
    expect(rows[2].type).toBe("hunk");
    expect(diffStats(rows)).toEqual({ added: 2, removed: 1 });
    const removed = rows.find((r) => r.type === "del")!;
    expect(removed.text).toBe("Build {{ task.title }}.");
    expect(removed.newNo).toBeNull();
    const added = rows.filter((r) => r.type === "add");
    expect(added.map((r) => r.text)).toEqual([
      "Build {{ task.title }} with care.",
      "Add tests.",
    ]);
    // Line numbers continue from the hunk header on both sides.
    const hunk = /@@ -(\d+)/.exec(rows[2].text)!;
    expect(rows[3].oldNo).toBe(Number(hunk[1]));
    const context = rows.filter((r) => r.type === "context");
    expect(context.every((r) => r.oldNo !== null && r.newNo !== null)).toBe(
      true,
    );
  });

  it("formats the structured changes of the server diff", () => {
    const [row] = structuredRows(libraryDiff.structured);
    expect(row.path).toBe("template");
    expect(row.change).toBe("changed");
    expect(row.a).toBe("Build {{ task.title }}. Keep it small. ");
    expect(structuredRows([{ path: "x", a: null, b: 1 }])[0].change).toBe(
      "added",
    );
    expect(structuredRows([{ path: "x", a: [1], b: null }])[0].change).toBe(
      "removed",
    );
    expect(formatDiffValue("a".repeat(300), 10)).toHaveLength(10);
  });

  it("computes the same change in the browser as the server", () => {
    const [entry] = libraryDiff.structured;
    const rows = lineDiff(String(entry.a), String(entry.b));
    expect(diffStats(rows)).toEqual(
      diffStats(parseUnified(libraryDiff.unified)),
    );
    expect(rows.filter((r) => r.type === "context").map((r) => r.text)).toEqual(
      ["Keep it small."],
    );
  });

  it("parses the per-role prompt diff of two renders", () => {
    const brief = promptDiff.per_role.find((r) => r.role === "brief")!;
    const stats = diffStats(parseUnified(brief.unified));
    expect(stats.added + stats.removed).toBeGreaterThan(0);
  });

  it("collapses long unchanged runs but keeps context around changes", () => {
    const a = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");
    const b = a.replace("line 15", "line fifteen");
    const rows = collapseContext(lineDiff(a, b), 2);
    const meta = rows.filter((r) => r.type === "meta");
    expect(meta).toHaveLength(2);
    expect(meta[0].text).toMatch(/13 unchanged lines/);
    expect(rows.filter((r) => r.type !== "meta").map((r) => r.text)).toEqual([
      "line 13",
      "line 14",
      "line 15",
      "line fifteen",
      "line 16",
      "line 17",
    ]);
    expect(collapseContext(lineDiff(a, a))).toHaveLength(30);
    expect(parseUnified("")).toEqual([]);
  });
});
