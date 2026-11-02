// Immutable list edits used by every list in the design editor (blocks,
// criteria, questions, options, scenarios, checklist items).

/** A copy with the item at `index` moved by `delta` (clamped; no-op at the ends). */
export function moved<T>(
  list: readonly T[],
  index: number,
  delta: number,
): T[] {
  const next = [...list];
  const target = index + delta;
  if (index < 0 || index >= list.length || target < 0 || target >= list.length)
    return next;
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  return next;
}

/** A copy without the item at `index`. */
export function removedAt<T>(list: readonly T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

/** A copy with `item` inserted right after `index` (at the end when out of range). */
export function insertedAfter<T>(
  list: readonly T[],
  index: number,
  item: T,
): T[] {
  const next = [...list];
  const at = index >= 0 && index < list.length ? index + 1 : list.length;
  next.splice(at, 0, item);
  return next;
}
