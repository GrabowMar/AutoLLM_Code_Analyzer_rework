/**
 * Version diffs (VersionDiff, PromptTemplateEditor "diff vs base", the playground's per-role diff).
 *
 * `parseUnified` turns the server's unified diff (`GET /library/{kind}/{slug}/diff/`, `POST /prompts/diff/`)
 * into numbered rows; `lineDiff` computes one in the browser (draft vs base) with the same row shape, so
 * both render through one component. `structuredRows` formats the structured `[{path, a, b}]` list.
 */
import type { DiffEntry } from "$lib/api/v3/types";

export type DiffRowType = "context" | "add" | "del" | "hunk" | "meta";

export interface DiffRow {
  type: DiffRowType;
  text: string;
  /** Line number in the old (a) side; null for added lines and headers. */
  oldNo: number | null;
  /** Line number in the new (b) side; null for deleted lines and headers. */
  newNo: number | null;
}

export interface DiffStats {
  added: number;
  removed: number;
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/;

/** Rows of a unified diff (`---`/`+++` headers become `meta`, `@@` lines `hunk`). */
export function parseUnified(unified: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldNo = 0;
  let newNo = 0;
  const lines = unified.replace(/\n$/, "").split("\n");
  if (lines.length === 1 && lines[0] === "") return rows;
  for (const line of lines) {
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      rows.push({ type: "meta", text: line, oldNo: null, newNo: null });
      continue;
    }
    const hunk = HUNK_RE.exec(line);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[3]);
      rows.push({ type: "hunk", text: line, oldNo: null, newNo: null });
      continue;
    }
    if (line.startsWith("\\")) {
      rows.push({ type: "meta", text: line, oldNo: null, newNo: null });
    } else if (line.startsWith("+")) {
      rows.push({
        type: "add",
        text: line.slice(1),
        oldNo: null,
        newNo: newNo++,
      });
    } else if (line.startsWith("-")) {
      rows.push({
        type: "del",
        text: line.slice(1),
        oldNo: oldNo++,
        newNo: null,
      });
    } else {
      rows.push({
        type: "context",
        text: line.startsWith(" ") ? line.slice(1) : line,
        oldNo: oldNo++,
        newNo: newNo++,
      });
    }
  }
  return rows;
}

export function diffStats(rows: DiffRow[]): DiffStats {
  return {
    added: rows.filter((r) => r.type === "add").length,
    removed: rows.filter((r) => r.type === "del").length,
  };
}

/** Longest-common-subsequence line diff of two texts, as rows (no hunks: every line is shown). */
export function lineDiff(a: string, b: string): DiffRow[] {
  const left = a === "" ? [] : a.replace(/\n$/, "").split("\n");
  const right = b === "" ? [] : b.replace(/\n$/, "").split("\n");
  const n = left.length;
  const m = right.length;
  // Long inputs: keep the LCS table bounded (prompts are small; fall back to a block replace).
  if (n * m > 4_000_000) {
    return [
      ...left.map((text, i) => ({
        type: "del" as const,
        text,
        oldNo: i + 1,
        newNo: null,
      })),
      ...right.map((text, j) => ({
        type: "add" as const,
        text,
        oldNo: null,
        newNo: j + 1,
      })),
    ];
  }
  const lcs: Uint32Array[] = Array.from(
    { length: n + 1 },
    () => new Uint32Array(m + 1),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        left[i] === right[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) {
      rows.push({ type: "context", text: left[i], oldNo: i + 1, newNo: j + 1 });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      rows.push({ type: "del", text: left[i], oldNo: i + 1, newNo: null });
      i++;
    } else {
      rows.push({ type: "add", text: right[j], oldNo: null, newNo: j + 1 });
      j++;
    }
  }
  for (; i < n; i++)
    rows.push({ type: "del", text: left[i], oldNo: i + 1, newNo: null });
  for (; j < m; j++)
    rows.push({ type: "add", text: right[j], oldNo: null, newNo: j + 1 });
  return rows;
}

/** Collapse runs of unchanged lines longer than `2 × context` into one "⋯ n unchanged lines" meta row. */
export function collapseContext(rows: DiffRow[], context = 3): DiffRow[] {
  const out: DiffRow[] = [];
  let run: DiffRow[] = [];
  const flush = (atEnd: boolean, atStart: boolean) => {
    const keepHead = atStart ? 0 : context;
    const keepTail = atEnd ? 0 : context;
    if (run.length > keepHead + keepTail + 1) {
      out.push(...run.slice(0, keepHead));
      out.push({
        type: "meta",
        text: `⋯ ${run.length - keepHead - keepTail} unchanged lines`,
        oldNo: null,
        newNo: null,
      });
      out.push(...run.slice(run.length - keepTail));
    } else {
      out.push(...run);
    }
    run = [];
  };
  let seenChange = false;
  for (const row of rows) {
    if (row.type === "context") {
      run.push(row);
    } else {
      flush(false, !seenChange);
      seenChange = true;
      out.push(row);
    }
  }
  flush(true, !seenChange);
  return seenChange ? out : rows;
}

export interface StructuredRow {
  path: string;
  change: "added" | "removed" | "changed";
  a: string;
  b: string;
}

/** A diff value as one line (strings verbatim and truncated, the rest as JSON). */
export function formatDiffValue(value: unknown, max = 160): string {
  if (value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  const flat = text.replace(/\s+/g, " ");
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** The server's structured diff as display rows (an absent side is `undefined` in the JSON → "added"/"removed"). */
export function structuredRows(entries: DiffEntry[]): StructuredRow[] {
  return entries.map((e) => ({
    path: e.path,
    change:
      e.a === null || e.a === undefined
        ? "added"
        : e.b === null || e.b === undefined
          ? "removed"
          : "changed",
    a: formatDiffValue(e.a),
    b: formatDiffValue(e.b),
  }));
}
