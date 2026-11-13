/**
 * Source-span segmentation for the ResolvedCellDrawer (spec 10.2, D10): a rendered stimulus plus its
 * `sources` (`{owner_kind, owner_ref, template_ref, char_start, char_end}`) → contiguous segments, each
 * owned by at most one span. Nested spans (a stack fragment inside the prompt template) resolve to the
 * **innermost** (shortest) span covering a character, so every highlighted run names the entity whose
 * text it is and whose "Edit" opens the right override dialog. Out-of-range spans are clipped; text no
 * span covers is a plain segment (`source: null`).
 */
import type {
  PromptOverrideIn,
  SourceOwnerKind,
  SourceSpanOut,
} from "$lib/api/v3/types";

export interface TextSegment {
  start: number;
  end: number;
  text: string;
  source: SourceSpanOut | null;
}

export function segmentText(
  text: string,
  sources: readonly SourceSpanOut[],
): TextSegment[] {
  const length = text.length;
  if (length === 0) return [];
  const spans = sources
    .map((s) => ({
      ...s,
      char_start: Math.max(0, s.char_start),
      char_end: Math.min(length, s.char_end),
    }))
    .filter((s) => s.char_end > s.char_start);
  if (spans.length === 0)
    return [{ start: 0, end: length, text, source: null }];
  // Boundaries where ownership may change.
  const cuts = new Set<number>([0, length]);
  for (const s of spans) {
    cuts.add(s.char_start);
    cuts.add(s.char_end);
  }
  const points = [...cuts].sort((a, b) => a - b);
  const segments: TextSegment[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const start = points[i];
    const end = points[i + 1];
    let owner: SourceSpanOut | null = null;
    for (const s of spans) {
      if (s.char_start <= start && s.char_end >= end) {
        const width = s.char_end - s.char_start;
        if (owner === null || width < owner.char_end - owner.char_start)
          owner = s;
      }
    }
    const last = segments[segments.length - 1];
    if (last && sameSource(last.source, owner)) {
      last.end = end;
      last.text = text.slice(last.start, end);
    } else {
      segments.push({
        start,
        end,
        text: text.slice(start, end),
        source: owner,
      });
    }
  }
  return segments;
}

function sameSource(a: SourceSpanOut | null, b: SourceSpanOut | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    a.owner_ref === b.owner_ref &&
    a.template_ref === b.template_ref &&
    a.char_start === b.char_start &&
    a.char_end === b.char_end
  );
}

export const OWNER_LABELS: Record<SourceOwnerKind, string> = {
  prompt: "Prompt template",
  prompt_set: "Prompt set",
  stack: "Stack fragment",
  style: "Style fragment",
  guidance: "Guidance",
  auth_fragment: "Auth contract",
  task: "Task prose",
  starter: "Starter",
};

/** Owners whose text the researcher can override from the drawer (`POST /studies/{id}/prompt-overrides/`). */
export const EDITABLE_OWNERS: ReadonlySet<SourceOwnerKind> =
  new Set<SourceOwnerKind>([
    "prompt",
    "prompt_set",
    "stack",
    "style",
    "guidance",
    "auth_fragment",
  ]);

/** Distinct owners of a stimulus in order of first appearance (the drawer's legend). */
export function owners(
  sources: readonly SourceSpanOut[],
): { kind: SourceOwnerKind; ref: string; templateRef: string }[] {
  const out: { kind: SourceOwnerKind; ref: string; templateRef: string }[] = [];
  const seen = new Set<string>();
  for (const s of [...sources].sort((a, b) => a.char_start - b.char_start)) {
    const key = `${s.owner_kind}|${s.owner_ref}|${s.template_ref}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      kind: s.owner_kind,
      ref: s.owner_ref,
      templateRef: s.template_ref,
    });
  }
  return out;
}

/** The text of one span (what the override dialog pre-fills when the owner's template is not loaded). */
export function spanText(text: string, span: SourceSpanOut): string {
  return text.slice(
    Math.max(0, span.char_start),
    Math.min(text.length, span.char_end),
  );
}

/**
 * The override request for an edited span (`POST /studies/{id}/prompt-overrides/`): the edited text is
 * the **whole template** of the PromptTemplate that rendered the span (`template_ref`), and the server
 * forks the owning entity (`owner_kind` / `owner_ref`) and swaps the study's factor level to the fork.
 * Returns null for owners that cannot be overridden (task prose, starters) or an unchanged template.
 */
export function buildOverrideRequest(
  span: Pick<SourceSpanOut, "owner_kind" | "owner_ref">,
  role: string | null,
  baseTemplate: string,
  editedTemplate: string,
): PromptOverrideIn | null {
  if (!EDITABLE_OWNERS.has(span.owner_kind)) return null;
  if (editedTemplate === baseTemplate || editedTemplate.trim() === "")
    return null;
  return {
    owner_kind: span.owner_kind,
    owner_ref: span.owner_ref,
    role: role || null,
    template: editedTemplate,
  };
}
