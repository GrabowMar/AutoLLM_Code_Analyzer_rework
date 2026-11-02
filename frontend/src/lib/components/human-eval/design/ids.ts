// Stable ids for design entities (blocks, criteria, questions, scenarios,
// checklist items, options). The backend accepts ^[a-z0-9_]{1,40}$, unique
// within the container. An id is derived from the label once, when the entity
// is created, and never follows later renames: answers, exports and the
// analysis plan reference it.

export const ID_PATTERN = /^[a-z0-9_]{1,40}$/;
export const ID_MAX = 40;

/** "Ease of use (overall)" → "ease_of_use_overall"; "" when nothing usable remains. */
export function slugifyId(label: string): string {
  const ascii = label.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const slug = ascii
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
  return trimToMax(slug, ID_MAX);
}

function trimToMax(id: string, max: number): string {
  if (id.length <= max) return id;
  return id.slice(0, max).replace(/_+$/g, "");
}

/**
 * `base` if free, else `base_2`, `base_3`, … (the base is shortened so the
 * suffix still fits the 40-character limit).
 */
export function uniqueId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const root = trimToMax(base || "item", ID_MAX) || "item";
  if (!used.has(root)) return root;
  for (let n = 2; n < 10_000; n++) {
    const suffix = `_${n}`;
    const candidate = `${trimToMax(root, ID_MAX - suffix.length)}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
  // Unreachable in practice (≤ 50 entities per container).
  return `${trimToMax(root, ID_MAX - 9)}_${Date.now().toString(36).slice(-8)}`;
}

/** A new unique id from a label, falling back to `fallback` when the label has no usable characters. */
export function idFromLabel(
  label: string,
  taken: Iterable<string>,
  fallback = "item",
): string {
  return uniqueId(slugifyId(label) || slugifyId(fallback) || "item", taken);
}

/** True when `id` matches the backend id rule. */
export function isValidId(id: string): boolean {
  return ID_PATTERN.test(id);
}

/** Ids that occur more than once (in order of first repetition). */
export function duplicateIds(ids: Iterable<string>): string[] {
  const seen = new Set<string>();
  const dups: string[] = [];
  for (const id of ids) {
    if (seen.has(id) && !dups.includes(id)) dups.push(id);
    seen.add(id);
  }
  return dups;
}
