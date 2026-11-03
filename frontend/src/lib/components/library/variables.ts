/**
 * Prompt-template variable autocomplete (PromptTemplateEditor; CodeMirror-free).
 *
 * The list comes from `GET /prompts/variables/` (the documented render context, section 9.2): each
 * top-level variable plus the sub-keys its description documents (`{title, summary, …}`) or its example
 * shows. Inside `{{ … }}` / `{% … %}` the editor offers the variables that match the identifier under the
 * cursor; after a `|` it offers the engine's filter whitelist. `usedVariables` finds the top-level names a
 * template reads, so the editor can flag ones missing from the template's declared `variables` (the
 * library lint treats an undeclared variable as an error).
 */
import type { PromptMode, PromptVariable } from "$lib/api/v3/types";

/** The sandboxed engine's filter whitelist (`prompts/engine.py`). */
export const TEMPLATE_FILTERS = [
  "bullet_list",
  "default",
  "endpoint_table",
  "indent",
  "join",
  "length",
  "lower",
  "replace",
  "sort",
  "title",
  "tojson",
  "trim",
  "upper",
  "wordwrap",
] as const;

/** Names the engine defines besides the context (globals and Jinja keywords). */
const RESERVED = new Set([
  "range",
  "loop",
  "true",
  "false",
  "none",
  "True",
  "False",
  "None",
  "and",
  "or",
  "not",
  "in",
  "is",
  "if",
  "else",
  "elif",
  "endif",
  "for",
  "endfor",
  "set",
  "endset",
  "with",
  "endwith",
  "macro",
  "endmacro",
  "call",
  "endcall",
  "filter",
  "endfilter",
  "raw",
  "endraw",
  "block",
  "endblock",
  "defined",
  "undefined",
  "recursive",
  "without",
  "context",
  "ignore",
  "missing",
  "import",
  "include",
  "from",
  "as",
]);

export interface Completion {
  /** Text inserted for this item ("task.title", "upper"). */
  label: string;
  kind: "variable" | "field" | "filter";
  detail: string;
  /** The top-level variable it belongs to. */
  root?: string;
}

/** Sub-keys of a documented variable: `{a, b: [...], c}` in the description, else the example's keys. */
export function documentedFields(variable: PromptVariable): string[] {
  const match = /\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/.exec(variable.description);
  const fields = new Set<string>();
  if (match && variable.type === "object") {
    let depth = 0;
    let token = "";
    const flush = () => {
      const name = /^\s*([A-Za-z_][A-Za-z0-9_]*)/.exec(token)?.[1];
      if (name) fields.add(name);
      token = "";
    };
    for (const ch of match[1]) {
      if (ch === "[" || ch === "{" || ch === "(") depth++;
      else if (ch === "]" || ch === "}" || ch === ")") depth--;
      if (ch === "," && depth === 0) flush();
      else token += ch;
    }
    flush();
  }
  if (
    variable.example &&
    typeof variable.example === "object" &&
    !Array.isArray(variable.example)
  ) {
    for (const key of Object.keys(variable.example)) fields.add(key);
  }
  return [...fields];
}

/** Every completion item for a mode, variables first, then their fields, then the filters. */
export function completionList(
  variables: PromptVariable[],
  mode?: PromptMode,
): Completion[] {
  const visible = variables.filter((v) => !mode || v.modes.includes(mode));
  const items: Completion[] = [];
  for (const v of visible) {
    items.push({
      label: v.name,
      kind: "variable",
      detail: `${v.type} — ${v.description}`,
      root: v.name,
    });
  }
  for (const v of visible) {
    for (const field of documentedFields(v)) {
      items.push({
        label: `${v.name}.${field}`,
        kind: "field",
        detail: `field of ${v.name}`,
        root: v.name,
      });
    }
  }
  for (const f of TEMPLATE_FILTERS)
    items.push({ label: f, kind: "filter", detail: "filter" });
  return items;
}

export interface CompletionContext {
  /** The identifier (possibly dotted) being typed. */
  prefix: string;
  /** Offset where `prefix` starts. */
  from: number;
  /** Whether the cursor follows a `|` (a filter position). */
  filter: boolean;
}

/** Is `offset` inside an open `{{ … }}` or `{% … %}` tag? */
function insideTag(text: string, offset: number): boolean {
  const before = text.slice(0, offset);
  const open = Math.max(before.lastIndexOf("{{"), before.lastIndexOf("{%"));
  if (open < 0) return false;
  const close = Math.max(before.lastIndexOf("}}"), before.lastIndexOf("%}"));
  return close < open;
}

/** What is being completed at `cursor`, or `null` outside template tags. */
export function completionContext(
  text: string,
  cursor: number,
): CompletionContext | null {
  if (!insideTag(text, cursor)) return null;
  const before = text.slice(0, cursor);
  const m = /([A-Za-z_][A-Za-z0-9_.]*)?$/.exec(before);
  const prefix = m?.[1] ?? "";
  const from = cursor - prefix.length;
  const filter = /\|\s*$/.test(text.slice(0, from));
  return { prefix, from, filter };
}

/** Items matching the completion context (filters after `|`, variables and fields elsewhere). */
export function completionsAt(
  items: Completion[],
  text: string,
  cursor: number,
  limit = 12,
): { context: CompletionContext; items: Completion[] } | null {
  const context = completionContext(text, cursor);
  if (!context) return null;
  const wanted = context.filter
    ? items.filter((i) => i.kind === "filter")
    : items.filter((i) => i.kind !== "filter");
  const p = context.prefix.toLowerCase();
  const matched = wanted.filter((i) => {
    const label = i.label.toLowerCase();
    if (!p) return i.kind !== "field";
    if (!label.startsWith(p) || label === p) return false;
    // Without a dot in the prefix, offer top-level names only (fields once "task." is typed).
    return p.includes(".") || i.kind !== "field";
  });
  return { context, items: matched.slice(0, limit) };
}

/** `text` with `item` inserted at the completion context; returns the new text and cursor. */
export function applyCompletion(
  text: string,
  cursor: number,
  context: CompletionContext,
  item: Completion,
): { text: string; cursor: number } {
  const next = text.slice(0, context.from) + item.label + text.slice(cursor);
  return { text: next, cursor: context.from + item.label.length };
}

/**
 * Top-level names a template reads (expressions and statements), minus names it binds itself (`for x in`,
 * `set x =`), reserved words, filters, attribute names and string literals.
 */
export function usedVariables(template: string): string[] {
  const used = new Set<string>();
  const bound = new Set<string>();
  const tags = template.matchAll(/\{\{([\s\S]*?)\}\}|\{%-?([\s\S]*?)-?%\}/g);
  for (const tag of tags) {
    const body = (tag[1] ?? tag[2] ?? "").replace(
      /(["'])(?:\\.|(?!\1).)*\1/g,
      " ",
    );
    const forMatch =
      /^\s*for\s+([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*)\s+in\b/.exec(body);
    if (forMatch)
      for (const name of forMatch[1].split(",")) bound.add(name.trim());
    const setMatch = /^\s*set\s+([A-Za-z_][\w]*)/.exec(body);
    if (setMatch) bound.add(setMatch[1]);
    const statementBody = forMatch
      ? body.slice(forMatch[0].length)
      : setMatch
        ? body.slice(setMatch[0].length)
        : body;
    for (const m of statementBody.matchAll(
      /(\|\s*)?(\.)?\b([A-Za-z_][\w]*)\b/g,
    )) {
      if (m[1] || m[2]) continue; // a filter name or an attribute
      const name = m[3];
      if (
        RESERVED.has(name) ||
        (TEMPLATE_FILTERS as readonly string[]).includes(name)
      )
        continue;
      // keyword arguments of calls/filters: `indent(width=2)`
      const after = statementBody.slice((m.index ?? 0) + m[0].length);
      if (/^\s*=(?!=)/.test(after)) continue;
      used.add(name);
    }
  }
  for (const name of bound) used.delete(name);
  return [...used].sort();
}

/** Used names missing from the declared list, and declared names that are not documented for the modes. */
export function variableIssues(
  template: string,
  declared: string[],
  documented: PromptVariable[],
  modes: PromptMode[],
): { undeclared: string[]; unknown: string[] } {
  const known = new Set(
    documented
      .filter(
        (v) => modes.length === 0 || v.modes.some((m) => modes.includes(m)),
      )
      .map((v) => v.name),
  );
  const used = usedVariables(template);
  return {
    undeclared: used.filter((name) => !declared.includes(name)),
    unknown: declared.filter((name) => !known.has(name)),
  };
}
