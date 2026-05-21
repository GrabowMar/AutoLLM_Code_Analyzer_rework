/** Reading time and tree helpers used by the docs UI. */

export function readingTime(raw: string): number {
  const words = raw.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / 200));
}

export interface TreeLike {
  slug: string;
  title: string;
  category?: string;
  children: TreeLike[];
}

export function flattenLeaves<T extends TreeLike>(nodes: T[]): T[] {
  const out: T[] = [];
  for (const n of nodes) {
    if (n.children && n.children.length > 0) {
      out.push(...flattenLeaves(n.children as T[]));
    } else {
      out.push(n);
    }
  }
  return out;
}

export function prevNext<T extends TreeLike>(
  nodes: T[],
  slug: string,
): [T | null, T | null] {
  const flat = flattenLeaves(nodes);
  const idx = flat.findIndex((n) => n.slug === slug);
  if (idx === -1) return [null, null];
  return [
    idx > 0 ? flat[idx - 1] : null,
    idx < flat.length - 1 ? flat[idx + 1] : null,
  ];
}

export function groupByCategory<T extends TreeLike>(
  nodes: T[],
  categoryOrder: string[],
): Array<{ category: string; items: T[] }> {
  const flat = flattenLeaves(nodes);
  const map = new Map<string, T[]>();
  for (const n of flat) {
    const key = n.category ?? "Other";
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(n);
  }
  const out: Array<{ category: string; items: T[] }> = [];
  for (const c of categoryOrder) {
    const items = map.get(c);
    if (items && items.length) out.push({ category: c, items });
  }
  for (const [c, items] of map.entries()) {
    if (!categoryOrder.includes(c)) out.push({ category: c, items });
  }
  return out;
}
