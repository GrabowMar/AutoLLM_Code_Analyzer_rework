/**
 * Resolve the Budget preset a design PINS (spec 9.1: library refs are immutable `slug@version`). The shared
 * `libraryStore` only caches the latest row of each slug, so a design pinning `frugal@1` after `frugal@2`
 * exists (an exact clone, a resumed draft) would otherwise show, edit and enforce the wrong knobs. Versions
 * missing from the cache are fetched once (`getLibraryVersion`) and kept for the page's lifetime: a
 * (slug, version) row never changes.
 *
 * Usage: `const pinned = new PinnedPolicy(() => ref)` in a component; read `pinned.item` / `.loading` /
 * `.error`. The fetch runs from an `$effect`, never during a `$derived` read.
 */
import { SvelteMap } from "svelte/reactivity";
import { formatApiError } from "$lib/api/core";
import { getLibraryVersion } from "$lib/api/v3/library";
import type { LibraryItemOut } from "$lib/api/v3/types";
import { parseRef } from "$lib/api/v3/types";
import { libraryStore } from "$lib/components/library";
import { findPinnedPolicy } from "./policy";

type PolicyRow = LibraryItemOut<"resource_policy">;

/** `slug@version` → the fetched row, or the error text of a failed fetch. Module-wide: rows are immutable. */
const fetched = new SvelteMap<string, PolicyRow | { error: string }>();
const inflight = new Set<string>();

function keyOf(ref: string): string | null {
  const parsed = parseRef(ref);
  return parsed ? `${parsed.slug}@${parsed.version}` : null;
}

/** Remember a row the page just created (e.g. "Save as preset"), so pinning it needs no refetch. */
export function rememberPolicy(row: PolicyRow): void {
  fetched.set(`${row.slug}@${row.version}`, row);
}

export class PinnedPolicy {
  #ref: () => string;

  constructor(ref: () => string) {
    this.#ref = ref;
    $effect(() => {
      const ref = this.#ref();
      const list = libraryStore.list("resource_policy");
      const key = keyOf(ref);
      if (!key || !list.loaded || fetched.has(key) || inflight.has(key)) return;
      if (findPinnedPolicy(ref, list.items)) return;
      const parsed = parseRef(ref)!;
      inflight.add(key);
      getLibraryVersion("resource_policy", parsed.slug, parsed.version)
        .then((row) => fetched.set(key, row))
        .catch((e) =>
          fetched.set(key, {
            error: formatApiError(
              e,
              `Could not load the pinned budget preset ${key}.`,
            ),
          }),
        )
        .finally(() => inflight.delete(key));
    });
  }

  /** The pinned row (the exact version; latest only for an unpinned bare slug), or null while it loads. */
  get item(): PolicyRow | null {
    const ref = this.#ref();
    const list = libraryStore.list("resource_policy");
    const extra = [...fetched.values()].filter(
      (v): v is PolicyRow => !("error" in v),
    );
    return findPinnedPolicy(ref, [...list.items, ...extra]) ?? null;
  }

  /** The latest row of the pinned slug (to tell "v1 · latest is v3" and to version+1 the user's own). */
  get latest(): PolicyRow | null {
    const parsed = parseRef(this.#ref());
    const slug = parsed?.slug ?? this.#ref();
    return libraryStore.list("resource_policy").bySlug(slug) ?? null;
  }

  get error(): string {
    const key = keyOf(this.#ref());
    const v = key ? fetched.get(key) : undefined;
    return v && "error" in v ? v.error : "";
  }

  get loading(): boolean {
    return !!this.#ref() && !this.item && !this.error;
  }
}
