/**
 * Source spans of a rendered stimulus (section 9.2, D10): which library entity owns each part of the text.
 * `segmentStimulus` splits the text into contiguous segments, each owned by the innermost span covering
 * it (nested fragments win over the template around them); text no span covers is `owner: null`.
 * Shared by the prompt playground and WP-15's ResolvedCellDrawer.
 */
import type {
  LibraryApiKind,
  SourceOwnerKind,
  SourceSpanOut,
  StimulusOut,
} from "$lib/api/v3/types";
import { parseRef } from "$lib/api/v3/types";

export interface StimulusSegment {
  text: string;
  start: number;
  end: number;
  source: SourceSpanOut | null;
}

/** Innermost span covering `[start, end)`: the shortest (ties: the later one, i.e. the nested one). */
function ownerOf(
  spans: SourceSpanOut[],
  start: number,
  end: number,
): SourceSpanOut | null {
  let best: SourceSpanOut | null = null;
  for (const span of spans) {
    if (
      span.char_start <= start &&
      span.char_end >= end &&
      span.char_end > span.char_start
    ) {
      if (
        !best ||
        span.char_end - span.char_start <= best.char_end - best.char_start
      )
        best = span;
    }
  }
  return best;
}

export function segmentStimulus(
  stimulus: Pick<StimulusOut, "text" | "sources">,
): StimulusSegment[] {
  const { text } = stimulus;
  const spans = stimulus.sources.filter((s) => s.char_end > s.char_start);
  const cuts = new Set<number>([0, text.length]);
  for (const s of spans) {
    cuts.add(Math.max(0, Math.min(text.length, s.char_start)));
    cuts.add(Math.max(0, Math.min(text.length, s.char_end)));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const segments: StimulusSegment[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const start = points[i];
    const end = points[i + 1];
    if (end <= start) continue;
    const source = ownerOf(spans, start, end);
    const prev = segments[segments.length - 1];
    if (prev && prev.source === source) {
      prev.text += text.slice(start, end);
      prev.end = end;
    } else {
      segments.push({ text: text.slice(start, end), start, end, source });
    }
  }
  return segments;
}

/** Distinct owners of a stimulus, in first-appearance order (the legend). */
export function stimulusOwners(
  stimulus: Pick<StimulusOut, "sources">,
): { owner_kind: SourceOwnerKind; owner_ref: string; chars: number }[] {
  const out = new Map<
    string,
    { owner_kind: SourceOwnerKind; owner_ref: string; chars: number }
  >();
  for (const s of [...stimulus.sources].sort(
    (a, b) => a.char_start - b.char_start,
  )) {
    const key = `${s.owner_kind}:${s.owner_ref}`;
    const entry = out.get(key) ?? {
      owner_kind: s.owner_kind,
      owner_ref: s.owner_ref,
      chars: 0,
    };
    entry.chars += s.char_end - s.char_start;
    out.set(key, entry);
  }
  return [...out.values()];
}

/** Owner kinds → the Library list that holds the owning entity. */
const OWNER_LIBRARY: Record<SourceOwnerKind, LibraryApiKind> = {
  prompt: "prompts",
  prompt_set: "prompt-sets",
  stack: "stacks",
  style: "styles",
  guidance: "guidance",
  auth_fragment: "prompts",
  task: "tasks",
  starter: "starters",
};

export const OWNER_LABEL: Record<SourceOwnerKind, string> = {
  prompt: "Prompt template",
  prompt_set: "Prompt set",
  stack: "Stack",
  style: "Style",
  guidance: "Guidance",
  auth_fragment: "Auth contract",
  task: "Task",
  starter: "Starter",
};

/** Library page of a span's owner (`/library/prompts/brief-app-default?v=1`), or null for draft text. */
export function ownerHref(
  source: Pick<SourceSpanOut, "owner_kind" | "owner_ref" | "template_ref">,
): string | null {
  const ref =
    source.owner_kind === "auth_fragment"
      ? source.template_ref
      : source.owner_ref;
  const parsed = parseRef(ref);
  if (!parsed) return null;
  return `/library/${OWNER_LIBRARY[source.owner_kind]}/${encodeURIComponent(parsed.slug)}?v=${parsed.version}`;
}

/** A stable tint slot (0–4) per owner kind, so a legend and the highlighted text agree. */
export function ownerSlot(kind: SourceOwnerKind): number {
  return (
    {
      prompt: 0,
      prompt_set: 0,
      auth_fragment: 1,
      stack: 2,
      style: 3,
      guidance: 4,
      task: 1,
      starter: 2,
    } as const
  )[kind];
}
