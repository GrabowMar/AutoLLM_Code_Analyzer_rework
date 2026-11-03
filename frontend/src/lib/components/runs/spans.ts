/**
 * Split a stimulus text into segments by its source spans (spec D10: every span of rendered text is traceable
 * to the library entity that owns it). Overlapping spans keep the innermost (latest-starting) owner; gaps are
 * returned unowned.
 */
import type { SourceSpanOut } from "$lib/api/v3/types";

export interface TextSegment {
  text: string;
  source: SourceSpanOut | null;
}

export function segmentBySources(
  text: string,
  sources: SourceSpanOut[] | null | undefined,
): TextSegment[] {
  const spans = (sources ?? [])
    .filter((s) => s.char_end > s.char_start && s.char_start < text.length)
    .sort((a, b) => a.char_start - b.char_start || b.char_end - a.char_end);
  if (spans.length === 0) return text ? [{ text, source: null }] : [];
  // owner per character boundary: walk the boundaries and pick the innermost covering span
  const cuts = new Set<number>([0, text.length]);
  for (const s of spans) {
    cuts.add(Math.max(0, s.char_start));
    cuts.add(Math.min(text.length, s.char_end));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const segments: TextSegment[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const [from, to] = [points[i], points[i + 1]];
    if (from === to) continue;
    let owner: SourceSpanOut | null = null;
    for (const s of spans) {
      if (s.char_start <= from && to <= s.char_end) {
        if (!owner || s.char_start >= owner.char_start) owner = s;
      }
    }
    const last = segments[segments.length - 1];
    if (last && last.source === owner) last.text += text.slice(from, to);
    else segments.push({ text: text.slice(from, to), source: owner });
  }
  return segments;
}
