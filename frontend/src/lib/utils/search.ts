/**
 * Client-side search for pick lists (model, task and library pickers): a query is split into words and
 * every word must appear somewhere ("qwen 235b" finds "Qwen3 235B A22B"); `highlightParts` splits a
 * label around the words so a picker can mark them.
 */

/** Lower-case search words. */
export function searchTokens(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

/** Every token occurs in one of `fields` (case-insensitive). */
export function matchesAll(
  fields: (string | null | undefined)[],
  tokens: string[],
): boolean {
  if (tokens.length === 0) return true;
  const text = fields.filter(Boolean).join(" ").toLowerCase();
  return tokens.every((t) => text.includes(t));
}

/** Split `text` around the tokens for highlighting (case-insensitive, longest token first). */
export function highlightParts(
  text: string,
  tokens: string[],
): { text: string; match: boolean }[] {
  const ts = [...new Set(tokens.filter(Boolean))].sort(
    (a, b) => b.length - a.length,
  );
  if (ts.length === 0) return [{ text, match: false }];
  const escaped = ts.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  return text
    .split(re)
    .filter((part) => part !== "")
    .map((part) => ({ text: part, match: ts.includes(part.toLowerCase()) }));
}
