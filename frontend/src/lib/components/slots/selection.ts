/**
 * Pure selection logic of the slot picker (spec 7.4, 10.5): line/byte spans, snapping a drag or a click to
 * whole lines and symbol ranges, and serialising a selection plus the target form into `SlotTargetIn`.
 *
 * Spans follow the server (`slot/selection.py:_lines` / `_line_span`): 1-based inclusive lines; `start_byte`
 * is the first byte of the start line and `end_byte` the end of the end line **without** its newline (and
 * without a `\r` before it), in UTF-8 bytes. Region spans are snapped to whole lines.
 *
 * Fields the researcher did not touch are sent as omitted (`undefined`), so the server fills the kind
 * defaults and reports them in `preview.defaults`; the form labels those "default (can be varied in the
 * study)".
 */
import type {
  ContextPolicy,
  EditPolicy,
  EndpointEntry,
  HoleMode,
  ParentSelection,
  SlotKind,
  SlotTargetCreateIn,
  SlotTargetIn,
  Span,
  SymbolEntry,
} from "$lib/api/v3/types";

const encoder = new TextEncoder();

/** `[start, endWithoutNewline]` byte ranges of every line (one empty line for an empty file). */
export function lineRanges(text: string): [number, number][] {
  const bytes = encoder.encode(text);
  const ranges: [number, number][] = [];
  let start = 0;
  while (start <= bytes.length) {
    const newline = bytes.indexOf(0x0a, start);
    if (newline < 0) {
      if (start < bytes.length || ranges.length === 0)
        ranges.push([start, bytes.length]);
      break;
    }
    const end =
      newline > start && bytes[newline - 1] === 0x0d ? newline - 1 : newline;
    ranges.push([start, end]);
    start = newline + 1;
  }
  return ranges;
}

export function lineCount(text: string): number {
  return lineRanges(text).length;
}

/** Order and clamp two line numbers (a drag can go upwards) into `[start, end]` within `1..total`. */
export function normalizeLines(
  a: number,
  b: number,
  total: number,
): [number, number] {
  const max = Math.max(1, total);
  const clamp = (n: number) => Math.min(Math.max(1, Math.round(n)), max);
  const [x, y] = [clamp(a), clamp(b)];
  return x <= y ? [x, y] : [y, x];
}

/** The whole-line span of lines `start..end` (as `_line_span` computes it). */
export function lineSpan(
  text: string,
  startLine: number,
  endLine: number,
): Span {
  const ranges = lineRanges(text);
  const [start, end] = normalizeLines(startLine, endLine, ranges.length);
  const startByte = ranges[start - 1][0];
  const endByte = ranges[end - 1][1];
  return {
    start_line: start,
    end_line: end,
    start_byte: startByte,
    end_byte: Math.max(startByte, endByte),
  };
}

/** The text of a span (whole file when `span` is null). */
export function sliceSpan(
  text: string,
  span: Pick<Span, "start_byte" | "end_byte"> | null,
): string {
  if (!span) return text;
  const bytes = encoder.encode(text);
  return new TextDecoder().decode(
    bytes.subarray(span.start_byte, span.end_byte),
  );
}

function spanSize(symbol: SymbolEntry): number {
  return symbol.span.end_line - symbol.span.start_line;
}

/** Symbols a user can pick as a part: everything but the `<module>` pseudo-symbol. */
export function selectableSymbols(symbols: SymbolEntry[]): SymbolEntry[] {
  return symbols.filter((s) => s.kind !== "module");
}

/** The innermost selectable symbol whose span contains `line`, or null. */
export function symbolAtLine(
  symbols: SymbolEntry[],
  line: number,
): SymbolEntry | null {
  let best: SymbolEntry | null = null;
  for (const symbol of selectableSymbols(symbols)) {
    if (symbol.span.start_line <= line && line <= symbol.span.end_line) {
      if (!best || spanSize(symbol) < spanSize(best)) best = symbol;
    }
  }
  return best;
}

/** Symbols whose span starts on `line` (the gutter shows an outline marker there), outermost first. */
export function symbolsStartingAt(
  symbols: SymbolEntry[],
  line: number,
): SymbolEntry[] {
  return selectableSymbols(symbols)
    .filter((s) => s.span.start_line === line)
    .sort((a, b) => spanSize(b) - spanSize(a));
}

/**
 * Snap a drag selection to symbol ranges: each edge of `[start, end]` that falls inside a symbol which the
 * region only partly covers is widened to that symbol's boundary (the outermost such symbol), so a region
 * never cuts a function in half. A region fully inside one symbol snaps to that symbol's lines.
 */
export function snapLinesToSymbols(
  symbols: SymbolEntry[],
  startLine: number,
  endLine: number,
): { start_line: number; end_line: number; symbol: SymbolEntry | null } {
  let [start, end] =
    startLine <= endLine ? [startLine, endLine] : [endLine, startLine];
  const candidates = selectableSymbols(symbols);
  // Iterate to a fixed point: widening one edge can make it cut another enclosing symbol.
  for (let guard = 0; guard < candidates.length + 1; guard++) {
    let changed = false;
    for (const symbol of candidates) {
      const { start_line: s, end_line: e } = symbol.span;
      const overlaps = s <= end && start <= e;
      const covers = start <= s && e <= end;
      if (!overlaps || covers) continue;
      if (s < start) {
        start = s;
        changed = true;
      }
      if (e > end) {
        end = e;
        changed = true;
      }
    }
    if (!changed) break;
  }
  const exact = candidates
    .filter((s) => s.span.start_line === start && s.span.end_line === end)
    .sort((a, b) => spanSize(a) - spanSize(b))[0];
  return { start_line: start, end_line: end, symbol: exact ?? null };
}

// ── Selection model ────────────────────────────────────────────────────────

/** What the researcher picked in the code pane / lists (before the form adds its fields). */
export type PartSelection =
  | { kind: "file"; path: string }
  | { kind: "region"; path: string; start_line: number; end_line: number }
  | { kind: "symbol"; path: string; symbol_id: string }
  | {
      kind: "endpoint";
      path: string;
      symbol_id: string;
      method: string;
      route: string;
      spec_endpoint_id: string | null;
    }
  | {
      kind: "component";
      path: string;
      component: string;
      symbol_id: string | null;
    };

/** A selection for a clicked symbol: route handlers that the index matched to an endpoint become endpoints. */
export function selectionForSymbol(
  symbol: SymbolEntry,
  endpoints: EndpointEntry[] = [],
): PartSelection {
  const endpoint = endpoints.find((e) => e.handler === symbol.id);
  if (endpoint) return selectionForEndpoint(endpoint, symbol.path);
  return { kind: "symbol", path: symbol.path, symbol_id: symbol.id };
}

export function selectionForEndpoint(
  endpoint: EndpointEntry,
  path: string,
): PartSelection {
  return {
    kind: "endpoint",
    path,
    symbol_id: endpoint.handler,
    method: endpoint.method,
    route: endpoint.path,
    spec_endpoint_id: endpoint.spec_endpoint_id,
  };
}

/** The path part of a symbol id (`"<path>::<qualname>"`). */
export function symbolPath(symbolId: string): string {
  const i = symbolId.lastIndexOf("::");
  return i >= 0 ? symbolId.slice(0, i) : symbolId;
}

/** Line range of a selection in its file, or null for a whole file. */
export function selectionLines(
  selection: PartSelection,
  symbols: SymbolEntry[],
): { start_line: number; end_line: number } | null {
  if (selection.kind === "file") return null;
  if (selection.kind === "region")
    return { start_line: selection.start_line, end_line: selection.end_line };
  const id = selection.symbol_id;
  const symbol = id ? symbols.find((s) => s.id === id) : null;
  return symbol
    ? { start_line: symbol.span.start_line, end_line: symbol.span.end_line }
    : null;
}

export function selectionTitle(selection: PartSelection): string {
  switch (selection.kind) {
    case "file":
      return selection.path;
    case "region":
      return `${selection.path}:${selection.start_line}-${selection.end_line}`;
    case "symbol":
      return (
        selection.symbol_id.slice(symbolPath(selection.symbol_id).length + 2) ||
        selection.symbol_id
      );
    case "endpoint":
      return `${selection.method} ${selection.route}`;
    case "component":
      return selection.component;
  }
}

// ── Target form → SlotTargetIn ─────────────────────────────────────────────

/** The right-pane form. `null` = not touched: the server fills the kind default. */
export interface TargetFormState {
  label: string;
  hole_mode: HoleMode | null;
  keep_docstring: boolean | null;
  context_policy: ContextPolicy | null;
  edit_policy: EditPolicy | null;
  allowlist: string[] | null;
  linked_checks: string[] | null;
  linked_probes: string[] | null;
  cost_model: string | null;
}

export function emptyTargetForm(): TargetFormState {
  return {
    label: "",
    hole_mode: null,
    keep_docstring: null,
    context_policy: null,
    edit_policy: null,
    allowlist: null,
    linked_checks: null,
    linked_probes: null,
    cost_model: null,
  };
}

function uniqueSorted(values: string[]): string[] {
  return [...new Set(values.map((v) => v.trim()).filter(Boolean))].sort();
}

/**
 * Serialise a selection and the form into the `SlotTargetIn` body of `preview-target` (and, with `label`,
 * `targets`). Only the address fields of the selected kind are sent; untouched form fields are omitted.
 * Regions carry whole-line byte offsets when the file text is known (the server re-snaps them anyway).
 */
export function toSlotTargetIn(
  selection: PartSelection,
  form: TargetFormState,
  fileText?: string,
): SlotTargetIn {
  const body: SlotTargetIn = { kind: selection.kind };
  switch (selection.kind) {
    case "file":
      body.path = selection.path;
      break;
    case "region": {
      body.path = selection.path;
      const [start, end] =
        selection.start_line <= selection.end_line
          ? [selection.start_line, selection.end_line]
          : [selection.end_line, selection.start_line];
      body.span =
        fileText !== undefined
          ? lineSpan(fileText, start, end)
          : { start_line: start, end_line: end };
      break;
    }
    case "symbol":
      body.path = selection.path;
      body.symbol_id = selection.symbol_id;
      break;
    case "endpoint":
      body.symbol_id = selection.symbol_id;
      body.endpoint = {
        method: selection.method,
        path: selection.route,
        spec_endpoint_id: selection.spec_endpoint_id,
      };
      break;
    case "component":
      body.path = selection.path;
      body.component = selection.component;
      if (selection.symbol_id) body.symbol_id = selection.symbol_id;
      break;
  }
  if (form.hole_mode !== null) body.default_hole_mode = form.hole_mode;
  if (form.keep_docstring !== null)
    body.default_keep_docstring = form.keep_docstring;
  if (form.context_policy !== null)
    body.default_context_policy = structuredClone(form.context_policy);
  if (form.edit_policy !== null) body.edit_policy = form.edit_policy;
  if (form.allowlist !== null) {
    const list = uniqueSorted([...form.allowlist, selection.path]);
    body.allowlist = list;
  }
  if (form.linked_checks !== null)
    body.linked_checks = uniqueSorted(form.linked_checks);
  if (form.linked_probes !== null)
    body.linked_probes = uniqueSorted(form.linked_probes);
  if (form.cost_model) body.cost_model = form.cost_model;
  return body;
}

/** `toSlotTargetIn` plus the label, without the preview-only `cost_model`. */
export function toSlotTargetCreateIn(
  selection: PartSelection,
  form: TargetFormState,
  fileText?: string,
): SlotTargetCreateIn {
  const body: SlotTargetCreateIn = toSlotTargetIn(selection, form, fileText);
  delete body.cost_model;
  const label = form.label.trim();
  if (label) body.label = label.slice(0, 200);
  return body;
}

/** Stable cache key of a preview request (same body → same preview). */
export function previewKey(setId: string, body: SlotTargetIn): string {
  return `${setId}|${stableStringify(body)}`;
}

export function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stableStringify(v)}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

/** Which form fields the server reported as filled with defaults (`preview.defaults`). */
export const DEFAULT_LABELLED_FIELDS = [
  "default_hole_mode",
  "default_keep_docstring",
  "default_context_policy",
] as const;

// ── Parent draw form → ParentSelection ─────────────────────────────────────

export interface ParentDrawForm {
  task: string;
  stack: string;
  models: string[];
  harnesses: string[];
  studies: string[];
  min_pass_rate: number | null;
  k: number | null;
  stratify_by: "none" | "parent_model";
  seed: number | null;
}

export function emptyParentDrawForm(task = ""): ParentDrawForm {
  return {
    task,
    stack: "",
    models: [],
    harnesses: [],
    studies: [],
    min_pass_rate: null,
    k: 3,
    stratify_by: "parent_model",
    seed: null,
  };
}

/** Field → message; empty when the draw can be sent. */
export function validateParentDraw(
  form: ParentDrawForm,
  poolSize?: number | null,
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.task.trim())
    errors.task = "Pick a task: parents are drawn per task.";
  if (form.k === null || !Number.isInteger(form.k) || form.k < 1)
    errors.k = "Draw at least one parent.";
  else if (poolSize != null && form.k > poolSize)
    errors.k = `The pool has ${poolSize} parent${poolSize === 1 ? "" : "s"}; draw at most that many.`;
  if (form.seed !== null && (!Number.isInteger(form.seed) || form.seed < 0))
    errors.seed =
      "The seed is a non-negative integer (leave it empty to derive one).";
  if (
    form.min_pass_rate !== null &&
    (form.min_pass_rate < 0 || form.min_pass_rate > 1)
  )
    errors.min_pass_rate = "A pass rate is between 0 and 1.";
  return errors;
}

function listOrNull(values: string[]): string[] | null {
  const list = uniqueSorted(values);
  return list.length ? list : null;
}

/** The `ParentSelection` body of `POST /slots/parents/draw/` (rule `random`). */
export function toParentSelection(form: ParentDrawForm): ParentSelection {
  return {
    rule: "random",
    pool_query: {
      task: form.task.trim(),
      stack: form.stack.trim() || null,
      models: listOrNull(form.models),
      harnesses: listOrNull(form.harnesses),
      studies: listOrNull(form.studies),
      min_pass_rate: form.min_pass_rate,
    },
    k: form.k ?? 3,
    stratify_by: form.stratify_by,
    seed: form.seed,
  };
}

/** The selection recorded on a SlotSet frozen from a manual pick (`rule: manual`). */
export function manualSelection(task: string, k: number): ParentSelection {
  return {
    rule: "manual",
    pool_query: { task },
    k: Math.max(1, k),
    stratify_by: "none",
    seed: null,
  };
}

/** A fresh random seed for "randomise" (31-bit, like the design seeds). */
export function randomSeed(random: () => number = Math.random): number {
  return Math.floor(random() * 2 ** 31);
}

/** Every kind the picker supports, in tab order. */
export const PICKER_KINDS: SlotKind[] = [
  "symbol",
  "endpoint",
  "component",
  "region",
  "file",
];

// ── Stale-selection guard (WP-12: send content_sha + anchors) ──────────────

/** `identity.text_sha`: sha256 of the NFC / LF-normalised UTF-8 text, lowercase hex. */
export async function textSha(text: string): Promise<string> {
  const normalized = text
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .normalize("NFC");
  const digest = await crypto.subtle.digest(
    "SHA-256",
    encoder.encode(normalized),
  );
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

const ANCHOR_LINES = 3;

/** `slot/selection.py:anchors_for`: text_sha of the 3 lines before / after the span (`""` at a file edge). */
export async function anchorsFor(
  text: string,
  span: Pick<Span, "start_line" | "end_line">,
): Promise<{ before_sha: string; after_sha: string }> {
  const bytes = encoder.encode(text);
  const decoder = new TextDecoder();
  const ranges = lineRanges(text);
  const before = ranges.slice(
    Math.max(0, span.start_line - 1 - ANCHOR_LINES),
    span.start_line - 1,
  );
  const after = ranges.slice(span.end_line, span.end_line + ANCHOR_LINES);
  const sha = async (chunk: [number, number][]) =>
    chunk.length
      ? textSha(
          chunk
            .map(([a, b]) => decoder.decode(bytes.subarray(a, b)))
            .join("\n"),
        )
      : "";
  return { before_sha: await sha(before), after_sha: await sha(after) };
}

/**
 * The span the server will hash for a selection in `file` (null = the whole file), or `undefined` when it
 * cannot be known here (the part lives in another file, or the symbol is not in this file's index).
 */
export function guardSpan(
  selection: PartSelection,
  file: { path: string; text: string; symbols: SymbolEntry[] },
): Span | null | undefined {
  if (selection.path !== file.path) return undefined;
  switch (selection.kind) {
    case "file":
      return null;
    case "region":
      return lineSpan(file.text, selection.start_line, selection.end_line);
    case "component":
      if (!selection.symbol_id) return null;
      return file.symbols.find((s) => s.id === selection.symbol_id)?.span;
    default:
      return file.symbols.find((s) => s.id === selection.symbol_id)?.span;
  }
}

/** `content_sha` + `anchors` of what the picker shows, so the server relocates or refuses (409) a stale part. */
export async function staleGuard(
  selection: PartSelection,
  file:
    | { path: string; text: string; symbols: SymbolEntry[] }
    | null
    | undefined,
): Promise<Pick<SlotTargetIn, "content_sha" | "anchors">> {
  if (!file) return {};
  const span = guardSpan(selection, file);
  if (span === undefined) return {};
  if (span === null) return { content_sha: await textSha(file.text) };
  return {
    content_sha: await textSha(sliceSpan(file.text, span)),
    anchors: await anchorsFor(file.text, span),
  };
}
