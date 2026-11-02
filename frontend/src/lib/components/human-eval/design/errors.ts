// Map `validate-design` / create / update errors ({path, message}) onto the
// editor's fields. Paths are dotted (`blocks.0.criteria.1.label`); the helpers
// also accept bracket indices (`blocks[0]`), a leading `design.` / `settings.`
// / `body.` scope, id segments instead of indices (`blocks.rating_1.title`) and
// pydantic's discriminator tag after a block index (`blocks.0.rating.title`).

import type { TaskDesign } from "$lib/api/types/human-eval";

export interface PathError {
  path: string;
  message: string;
}

/** An error with its path split into segments (relative to the current scope). */
export interface FieldError {
  path: string[];
  message: string;
}

export type ErrorArea = "design" | "settings" | "general";

const BLOCK_TYPE_TAGS = new Set([
  "rating",
  "requirements",
  "security",
  "scenarios",
  "questionnaire",
  "bugs",
  "pairwise",
]);

const DESIGN_KEYS = new Set([
  "version",
  "blocks",
  "analysis",
  "consent",
  "pre_questions",
  "post_questions",
]);

const SETTINGS_KEYS = new Set([
  "blinding",
  "allocation",
  "pairing",
  "access",
  "hosting",
  "quality",
]);

/** "design.blocks[0].criteria[1].label" → ["design", "blocks", "0", "criteria", "1", "label"] */
export function splitPath(path: string): string[] {
  return path
    .replace(/\[(\w+)\]/g, ".$1")
    .split(/[./]/)
    .map((s) => s.trim())
    .filter((s) => s !== "" && s !== "__root__");
}

/** Which form an error belongs to, and its path inside that form. */
export function classifyPath(path: string): {
  area: ErrorArea;
  segments: string[];
} {
  let segs = splitPath(path);
  if (segs[0] === "body") segs = segs.slice(1);
  let area: ErrorArea = "general";
  if (segs[0] === "design") {
    area = "design";
    segs = segs.slice(1);
  } else if (segs[0] === "settings") {
    area = "settings";
    segs = segs.slice(1);
  } else if (segs.length > 0 && DESIGN_KEYS.has(segs[0])) {
    area = "design";
  } else if (segs.length > 0 && SETTINGS_KEYS.has(segs[0])) {
    area = "settings";
  }
  if (
    area === "design" &&
    segs[0] === "blocks" &&
    segs.length > 2 &&
    BLOCK_TYPE_TAGS.has(segs[2])
  ) {
    segs = [segs[0], segs[1], ...segs.slice(3)];
  }
  return { area, segments: segs };
}

/** Errors of one area, with paths relative to it. */
export function areaErrors(
  errors: readonly PathError[] | null | undefined,
  area: ErrorArea,
): FieldError[] {
  const out: FieldError[] = [];
  for (const e of errors ?? []) {
    const c = classifyPath(e.path ?? "");
    if (c.area === area) out.push({ path: c.segments, message: e.message });
  }
  return out;
}

/** Split errors by the form that should show them. */
export function splitErrors(errors: readonly PathError[] | null | undefined): {
  design: FieldError[];
  settings: FieldError[];
  general: FieldError[];
} {
  return {
    design: areaErrors(errors, "design"),
    settings: areaErrors(errors, "settings"),
    general: areaErrors(errors, "general"),
  };
}

/** One path segment, or any of several (an index and an id for the same entry). */
export type Segment = string | number | readonly (string | number)[];

function matches(seg: string, m: Segment): boolean {
  if (Array.isArray(m)) return m.some((x) => String(x) === seg);
  return String(m) === seg;
}

/** Errors under the given leading segments, with those segments removed. */
export function under(
  errors: readonly FieldError[],
  ...segments: Segment[]
): FieldError[] {
  const out: FieldError[] = [];
  outer: for (const e of errors) {
    if (e.path.length < segments.length) continue;
    for (let i = 0; i < segments.length; i++) {
      if (!matches(e.path[i], segments[i])) continue outer;
    }
    out.push({ path: e.path.slice(segments.length), message: e.message });
  }
  return out;
}

/** Messages that sit exactly at the current scope (or at the given field). */
export function messagesAt(
  errors: readonly FieldError[],
  ...segments: Segment[]
): string[] {
  return under(errors, ...segments)
    .filter((e) => e.path.length === 0)
    .map((e) => e.message);
}

/** The first message at a field, or "" (for `error` props). */
export function messageAt(
  errors: readonly FieldError[],
  ...segments: Segment[]
): string {
  return messagesAt(errors, ...segments)[0] ?? "";
}

/** Every message under a scope, prefixed by its remaining path ("max: must be > min"). */
export function messagesUnder(
  errors: readonly FieldError[],
  ...segments: Segment[]
): string[] {
  return under(errors, ...segments).map((e) =>
    e.path.length ? `${e.path.join(".")}: ${e.message}` : e.message,
  );
}

const LIST_LABELS: Record<string, string> = {
  criteria: "criterion",
  questions: "question",
  pre_questions: "pre-question",
  post_questions: "post-question",
  scenarios: "scenario",
  checklist: "checklist item",
  custom: "custom requirement",
  options: "option",
  sources: "source",
};

type Labelled = {
  id?: string;
  label?: string;
  title?: string;
  text?: string;
  value?: string;
};

function entryName(entry: Labelled | undefined): string {
  if (!entry) return "";
  return (
    entry.title || entry.label || entry.text || entry.id || entry.value || ""
  );
}

function findEntry(
  list: unknown,
  seg: string,
): { index: number; entry: Labelled | undefined } | null {
  if (!Array.isArray(list)) return null;
  if (/^\d+$/.test(seg)) {
    const index = Number(seg);
    return { index, entry: list[index] as Labelled | undefined };
  }
  const index = list.findIndex(
    (x) => (x as Labelled)?.id === seg || (x as Labelled)?.value === seg,
  );
  return index >= 0 ? { index, entry: list[index] as Labelled } : null;
}

/**
 * A readable location for a design error path, e.g.
 * ["blocks", "0", "criteria", "1", "scale", "max"] →
 * "Block 1 “Ratings” › criterion 2 “Ease of use” › scale › max".
 */
export function describeDesignPath(
  segments: readonly string[],
  design: TaskDesign | null | undefined,
): string {
  if (segments.length === 0) return "Design";
  const parts: string[] = [];
  let node: unknown = design ?? null;
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const next = segments[i + 1];
    if (seg === "blocks" && next !== undefined) {
      const found = findEntry((node as TaskDesign | null)?.blocks, next);
      const name = entryName(found?.entry);
      const n = found ? found.index + 1 : next;
      parts.push(`Block ${n}${name ? ` “${name}”` : ""}`);
      node = found?.entry ?? null;
      i++;
      continue;
    }
    if (LIST_LABELS[seg] && next !== undefined) {
      const found = findEntry(
        (node as Record<string, unknown> | null)?.[seg],
        next,
      );
      const name = entryName(found?.entry);
      const n = found ? found.index + 1 : next;
      parts.push(`${LIST_LABELS[seg]} ${n}${name ? ` “${name}”` : ""}`);
      node = found?.entry ?? null;
      i++;
      continue;
    }
    parts.push(seg.replace(/_/g, " "));
    node = (node as Record<string, unknown> | null)?.[seg] ?? null;
  }
  const text = parts.join(" › ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
