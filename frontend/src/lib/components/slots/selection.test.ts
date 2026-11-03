import { describe, expect, it } from "vitest";
import type { EndpointEntry, SymbolEntry } from "$lib/api/v3/types";
import { DEFAULT_CONTEXT_POLICY } from "$lib/api/v3/types";
import {
  anchorsFor,
  emptyParentDrawForm,
  guardSpan,
  staleGuard,
  textSha,
  emptyTargetForm,
  lineRanges,
  lineSpan,
  manualSelection,
  normalizeLines,
  previewKey,
  selectionForSymbol,
  selectionLines,
  sliceSpan,
  snapLinesToSymbols,
  symbolAtLine,
  symbolsStartingAt,
  toParentSelection,
  toSlotTargetCreateIn,
  toSlotTargetIn,
  validateParentDraw,
} from "./selection";

const PY = [
  "from flask import Flask", // 1
  "app = Flask(__name__)", // 2
  "", // 3
  "class TodoService:", // 4
  "    def update(self, id):", // 5
  "        return id", // 6
  "", // 7
  "    def delete(self, id):", // 8
  "        pass", // 9
  "", // 10
  "@app.get('/todos')", // 11
  "def list_todos():", // 12
  "    return []", // 13
].join("\n");

function sym(
  id: string,
  kind: SymbolEntry["kind"],
  start: number,
  end: number,
): SymbolEntry {
  return {
    id: `app.py::${id}`,
    path: "app.py",
    language: "python",
    kind,
    qualname: id,
    span: { start_line: start, end_line: end, start_byte: 0, end_byte: 0 },
    signature: "",
    decorators: [],
    content_sha: "0".repeat(64),
    parent: null,
  };
}

const SYMBOLS: SymbolEntry[] = [
  sym("<module>", "module", 1, 13),
  sym("TodoService", "class", 4, 9),
  sym("TodoService.update", "method", 5, 6),
  sym("TodoService.delete", "method", 8, 9),
  sym("list_todos", "route_handler", 11, 13),
];

describe("line spans (mirror of slot/selection.py:_line_span)", () => {
  it("computes byte ranges without the newline", () => {
    expect(lineRanges("ab\ncd\n")).toEqual([
      [0, 2],
      [3, 5],
    ]);
    expect(lineRanges("")).toEqual([[0, 0]]);
    expect(lineRanges("a\r\nb")).toEqual([
      [0, 1],
      [3, 4],
    ]);
  });

  it("counts UTF-8 bytes, not UTF-16 units", () => {
    const text = "é = 1\nx = 2";
    const span = lineSpan(text, 2, 2);
    expect(span.start_byte).toBe(7); // "é" is 2 bytes, then " = 1\n"
    expect(sliceSpan(text, span)).toBe("x = 2");
  });

  it("snaps a whole-line region and orders reversed drags", () => {
    const span = lineSpan(PY, 6, 5);
    expect(span.start_line).toBe(5);
    expect(span.end_line).toBe(6);
    expect(sliceSpan(PY, span)).toBe(
      "    def update(self, id):\n        return id",
    );
  });

  it("clamps lines into the file", () => {
    expect(normalizeLines(0, 99, 13)).toEqual([1, 13]);
    expect(normalizeLines(5, 5, 0)).toEqual([1, 1]);
  });
});

describe("selection snapping from symbol ranges", () => {
  it("picks the innermost symbol on a line, never <module>", () => {
    expect(symbolAtLine(SYMBOLS, 6)?.qualname).toBe("TodoService.update");
    expect(symbolAtLine(SYMBOLS, 4)?.qualname).toBe("TodoService");
    expect(symbolAtLine(SYMBOLS, 2)).toBeNull();
  });

  it("lists outline markers outermost first", () => {
    expect(symbolsStartingAt(SYMBOLS, 5).map((s) => s.qualname)).toEqual([
      "TodoService.update",
    ]);
    expect(symbolsStartingAt(SYMBOLS, 1)).toEqual([]);
  });

  it("widens a region that cuts a symbol to the symbol's boundaries", () => {
    // 6..8 cuts update (5-6) and delete (8-9) and sits inside TodoService (4-9).
    const snapped = snapLinesToSymbols(SYMBOLS, 6, 8);
    expect([snapped.start_line, snapped.end_line]).toEqual([4, 9]);
    expect(snapped.symbol?.qualname).toBe("TodoService");
  });

  it("snaps a region inside one function to that function", () => {
    const snapped = snapLinesToSymbols(SYMBOLS, 13, 12);
    expect([snapped.start_line, snapped.end_line]).toEqual([11, 13]);
    expect(snapped.symbol?.qualname).toBe("list_todos");
  });

  it("leaves a region between symbols alone", () => {
    const snapped = snapLinesToSymbols(SYMBOLS, 2, 3);
    expect([snapped.start_line, snapped.end_line]).toEqual([2, 3]);
    expect(snapped.symbol).toBeNull();
  });

  it("keeps a region that already covers whole symbols", () => {
    const snapped = snapLinesToSymbols(SYMBOLS, 5, 9);
    expect([snapped.start_line, snapped.end_line]).toEqual([4, 9]);
  });

  it("resolves the lines of symbol and endpoint selections", () => {
    const endpoints: EndpointEntry[] = [
      {
        method: "GET",
        path: "/todos",
        framework: "flask",
        handler: "app.py::list_todos",
        spec_endpoint_id: "list_todos",
        confidence: "exact",
        linked_checks: 2,
        linked_probes: 0,
      },
    ];
    const selection = selectionForSymbol(SYMBOLS[4], endpoints);
    expect(selection.kind).toBe("endpoint");
    expect(selectionLines(selection, SYMBOLS)).toEqual({
      start_line: 11,
      end_line: 13,
    });
    expect(
      selectionLines({ kind: "file", path: "app.py" }, SYMBOLS),
    ).toBeNull();
    expect(selectionForSymbol(SYMBOLS[2], endpoints)).toEqual({
      kind: "symbol",
      path: "app.py",
      symbol_id: "app.py::TodoService.update",
    });
  });
});

describe("SlotTargetIn serialization", () => {
  it("omits untouched form fields so the server fills kind defaults", () => {
    const body = toSlotTargetIn(
      {
        kind: "symbol",
        path: "app.py",
        symbol_id: "app.py::TodoService.update",
      },
      emptyTargetForm(),
    );
    expect(body).toEqual({
      kind: "symbol",
      path: "app.py",
      symbol_id: "app.py::TodoService.update",
    });
    expect(JSON.parse(JSON.stringify(body))).toEqual(body);
  });

  it("serialises an endpoint by method, route and spec id", () => {
    const body = toSlotTargetIn(
      {
        kind: "endpoint",
        path: "app.py",
        symbol_id: "app.py::list_todos",
        method: "GET",
        route: "/todos",
        spec_endpoint_id: "list_todos",
      },
      emptyTargetForm(),
    );
    expect(body).toEqual({
      kind: "endpoint",
      symbol_id: "app.py::list_todos",
      endpoint: {
        method: "GET",
        path: "/todos",
        spec_endpoint_id: "list_todos",
      },
    });
  });

  it("sends whole-line byte offsets for a region when the text is known", () => {
    const body = toSlotTargetIn(
      { kind: "region", path: "app.py", start_line: 9, end_line: 8 },
      emptyTargetForm(),
      PY,
    );
    expect(body.span?.start_line).toBe(8);
    expect(body.span?.end_line).toBe(9);
    expect(
      sliceSpan(PY, {
        start_byte: body.span!.start_byte!,
        end_byte: body.span!.end_byte!,
      }),
    ).toBe("    def delete(self, id):\n        pass");
    const linesOnly = toSlotTargetIn(
      { kind: "region", path: "app.py", start_line: 2, end_line: 3 },
      emptyTargetForm(),
    );
    expect(linesOnly.span).toEqual({ start_line: 2, end_line: 3 });
  });

  it("serialises edited fields, keeping the slot file in the allowlist and lists sorted", () => {
    const form = {
      ...emptyTargetForm(),
      label: "  update handler ",
      hole_mode: "rewrite" as const,
      keep_docstring: true,
      context_policy: { ...DEFAULT_CONTEXT_POLICY, mode: "full_app" as const },
      edit_policy: "fail" as const,
      allowlist: ["models.py", "models.py", " "],
      linked_checks: ["c2", "c1", "c2"],
      linked_probes: [],
      cost_model: "openai/gpt-5-mini",
    };
    const selection = { kind: "file" as const, path: "app.py" };
    const preview = toSlotTargetIn(selection, form);
    expect(preview).toMatchObject({
      kind: "file",
      path: "app.py",
      default_hole_mode: "rewrite",
      default_keep_docstring: true,
      edit_policy: "fail",
      allowlist: ["app.py", "models.py"],
      linked_checks: ["c1", "c2"],
      linked_probes: [],
      cost_model: "openai/gpt-5-mini",
    });
    expect(preview.default_context_policy?.mode).toBe("full_app");
    // the form's policy object is not shared with the body
    expect(preview.default_context_policy).not.toBe(form.context_policy);

    const create = toSlotTargetCreateIn(selection, form);
    expect(create.label).toBe("update handler");
    expect("cost_model" in create).toBe(false);
  });

  it("sends a component by name and path", () => {
    const body = toSlotTargetIn(
      {
        kind: "component",
        path: "src/TodoList.tsx",
        component: "TodoList",
        symbol_id: null,
      },
      emptyTargetForm(),
    );
    expect(body).toEqual({
      kind: "component",
      path: "src/TodoList.tsx",
      component: "TodoList",
    });
  });

  it("keys previews independently of key order", () => {
    expect(previewKey("s", { kind: "file", path: "a" })).toBe(
      previewKey("s", { path: "a", kind: "file" }),
    );
    expect(previewKey("s", { kind: "file", path: "a" })).not.toBe(
      previewKey("t", { kind: "file", path: "a" }),
    );
  });
});

describe("parent draw form", () => {
  it("requires a task and a positive k within the pool", () => {
    const form = emptyParentDrawForm();
    expect(validateParentDraw(form)).toHaveProperty("task");
    form.task = "crud_todo_list@1";
    expect(validateParentDraw(form)).toEqual({});
    form.k = 0;
    expect(validateParentDraw(form)).toHaveProperty("k");
    form.k = 5;
    expect(validateParentDraw(form, 4).k).toMatch(/at most/);
    form.k = 2;
    form.seed = -1;
    expect(validateParentDraw(form)).toHaveProperty("seed");
    form.seed = 7;
    form.min_pass_rate = 1.5;
    expect(validateParentDraw(form)).toHaveProperty("min_pass_rate");
  });

  it("serialises the draw with empty filters as null and stratification by parent model", () => {
    const form = {
      ...emptyParentDrawForm("crud_todo_list@1"),
      models: ["b", "a", "a"],
      seed: 42,
    };
    expect(toParentSelection(form)).toEqual({
      rule: "random",
      pool_query: {
        task: "crud_todo_list@1",
        stack: null,
        models: ["a", "b"],
        harnesses: null,
        studies: null,
        min_pass_rate: null,
      },
      k: 3,
      stratify_by: "parent_model",
      seed: 42,
    });
  });

  it("records manual picks", () => {
    expect(manualSelection("t@1", 2)).toEqual({
      rule: "manual",
      pool_query: { task: "t@1" },
      k: 2,
      stratify_by: "none",
      seed: null,
    });
  });
});

describe("stale-selection guard (identity.text_sha / anchors_for)", () => {
  it("hashes like identity.text_sha (NFC, LF)", async () => {
    // sha256("abc")
    expect(await textSha("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(await textSha("a\r\nb")).toBe(await textSha("a\nb"));
    expect(await textSha("é")).toBe(await textSha("é"));
  });

  it("anchors the 3 lines around a span and leaves file edges empty", async () => {
    const anchors = await anchorsFor(PY, { start_line: 1, end_line: 2 });
    expect(anchors.before_sha).toBe("");
    expect(anchors.after_sha).toBe(
      await textSha(
        ["", "class TodoService:", "    def update(self, id):"].join("\n"),
      ),
    );
    const end = await anchorsFor(PY, { start_line: 12, end_line: 13 });
    expect(end.after_sha).toBe("");
  });

  it("guards regions and whole files, and skips parts of other files", async () => {
    const file = { path: "app.py", text: PY, symbols: SYMBOLS };
    const region = await staleGuard(
      { kind: "region", path: "app.py", start_line: 5, end_line: 6 },
      file,
    );
    expect(region.content_sha).toBe(
      await textSha("    def update(self, id):\n        return id"),
    );
    expect(region.anchors?.before_sha).toBe(
      await textSha(
        ["app = Flask(__name__)", "", "class TodoService:"].join("\n"),
      ),
    );
    expect(
      (await staleGuard({ kind: "file", path: "app.py" }, file)).content_sha,
    ).toBe(await textSha(PY));
    expect(await staleGuard({ kind: "file", path: "other.py" }, file)).toEqual(
      {},
    );
    expect(
      guardSpan(
        { kind: "symbol", path: "app.py", symbol_id: "app.py::missing" },
        file,
      ),
    ).toBeUndefined();
  });
});
