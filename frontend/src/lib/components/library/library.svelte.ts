/**
 * `libraryStore` (spec 10.7): a per-kind cache of the latest-per-slug library rows, shared by every picker,
 * list and badge on a page.
 *
 * - `libraryStore.list(kind)` returns a `KindList` (reactive `items`, `loading`, `error`); the first read
 *   loads it, later reads reuse it. `revalidate()` reloads silently (the rows stay on screen).
 * - `invalidateLibrary(kind?)` marks a kind (or every kind) stale after a write anywhere on the page, and
 *   reloads the lists that are mounted. Editors call it after a save; pickers never cache on their own.
 * - `pin(ref)`/`latestOf(kind, slug)` resolve a pinned ref to its row and tell whether it is the latest
 *   version ("v1 · latest is v3").
 * - `me` is the signed-in user (`id`, `is_staff`), loaded once, for edit permissions.
 * - `knobs` is `GET /budget/knobs/` (KNOB_META + tier presets), loaded once on first use.
 *
 * Browser-only: the store is a module singleton, so on the server (SSR) nothing is fetched.
 */
import { browser } from "$app/environment";
import { formatApiError } from "$lib/api/core";
import { getMe } from "$lib/api/users";
import { getAuth } from "$lib/stores/auth.svelte";
import { getBudgetKnobs, listAllLibrary } from "$lib/api/v3/library";
import type { KnobsOut, LibraryItemOut, LibraryKind } from "$lib/api/v3/types";
import { parseRef } from "$lib/api/v3/types";
import { ALL_KINDS } from "./kinds";

export class KindList<K extends LibraryKind = LibraryKind> {
  readonly kind: K;
  items = $state<LibraryItemOut<K>[]>([]) as LibraryItemOut<K>[];
  loading = $state(false);
  loaded = $state(false);
  error = $state("");
  /** Archived slugs are included when true (the list pages' "Show archived"). */
  readonly archived: boolean;
  #seq = 0;
  #pending: Promise<void> | null = null;

  constructor(kind: K, archived = false) {
    this.kind = kind;
    this.archived = archived;
  }

  /**
   * Load once; concurrent callers share the request. Safe to call while rendering: the request starts in
   * a microtask, so no state is written during a `$derived`/template read. A failed load is not retried
   * automatically (call `revalidate`, e.g. from an ErrorAlert's retry).
   */
  ensure(): Promise<void> {
    if (this.loaded || this.error || !browser) return Promise.resolve();
    this.#pending ??= new Promise<void>((resolve) =>
      queueMicrotask(() => void this.revalidate().then(resolve)),
    );
    return this.#pending;
  }

  /** Reload; only the latest request may land, and the current rows stay visible meanwhile. */
  revalidate(): Promise<void> {
    if (!browser) return Promise.resolve();
    const mine = ++this.#seq;
    this.loading = this.items.length === 0;
    this.error = "";
    return (async () => {
      try {
        const items = await listAllLibrary<K>(this.kind, {
          archived: this.archived,
        });
        if (mine !== this.#seq) return;
        this.items = items;
        this.loaded = true;
      } catch (e) {
        if (mine !== this.#seq) return;
        this.error = formatApiError(e, "Could not load the library.");
      } finally {
        if (mine === this.#seq) {
          this.loading = false;
          this.#pending = null;
        }
      }
    })();
  }

  bySlug(slug: string): LibraryItemOut<K> | undefined {
    return this.items.find((i) => i.slug === slug);
  }
}

export interface LibraryMe {
  id: number;
  is_staff: boolean;
}

class LibraryStore {
  #lists = new Map<string, KindList>();
  /** Listeners notified by `invalidate` (e.g. pickers in other components that keep derived state). */
  #listeners = new Set<(kind: LibraryKind | null) => void>();

  me = $state<LibraryMe | null>(null);
  #meLoading: Promise<void> | null = null;

  knobs = $state<KnobsOut | null>(null);
  knobsError = $state("");
  #knobsLoading: Promise<KnobsOut | null> | null = null;

  /** The cached list of `kind` (loads on first use). */
  list<K extends LibraryKind>(
    kind: K,
    opts: { archived?: boolean } = {},
  ): KindList<K> {
    const key = `${kind}:${opts.archived ? "all" : "active"}`;
    let entry = this.#lists.get(key) as KindList<K> | undefined;
    if (!entry) {
      entry = new KindList<K>(kind, opts.archived ?? false);
      this.#lists.set(key, entry as unknown as KindList);
    }
    void entry.ensure();
    return entry;
  }

  /** Mark `kind` (or every kind) stale and reload the lists already loaded. */
  invalidate(kind?: LibraryKind | null): void {
    for (const entry of this.#lists.values()) {
      if (!kind || entry.kind === kind) {
        if (entry.loaded || entry.loading) void entry.revalidate();
        else entry.loaded = false;
      }
    }
    for (const listener of this.#listeners) listener(kind ?? null);
  }

  /** Subscribe to invalidations; returns the unsubscribe function (use it as an `$effect` teardown). */
  onInvalidate(listener: (kind: LibraryKind | null) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  /** The latest row of a slug (from the cached list), or undefined while it loads. */
  latestOf<K extends LibraryKind>(
    kind: K,
    slug: string,
  ): LibraryItemOut<K> | undefined {
    return this.list(kind).bySlug(slug);
  }

  /** `ref` against the cache: its parts, the latest row of its slug and whether the ref is that version. */
  pin<K extends LibraryKind>(
    kind: K,
    ref: string,
  ): {
    slug: string;
    version: number;
    latest?: LibraryItemOut<K>;
    isLatest: boolean | null;
  } | null {
    const parsed = parseRef(ref);
    if (!parsed) return null;
    const latest = this.latestOf(kind, parsed.slug);
    return {
      slug: parsed.slug,
      version: parsed.version,
      latest,
      isLatest: latest ? latest.version === parsed.version : null,
    };
  }

  /** Signed-in user's id and staff flag (null until loaded or when signed out). */
  ensureMe(): Promise<void> {
    if (!browser || this.me) return Promise.resolve();
    this.#meLoading ??= getMe()
      .then((me) => {
        const id = getAuth().user?.id;
        this.me = {
          id: typeof id === "number" ? id : -1,
          is_staff: me.is_staff ?? false,
        };
      })
      .catch(() => {
        this.me = null;
      })
      .finally(() => {
        this.#meLoading = null;
      });
    return this.#meLoading;
  }

  /** `GET /budget/knobs/`, once per page load. */
  ensureKnobs(): Promise<KnobsOut | null> {
    if (!browser) return Promise.resolve(null);
    if (this.knobs) return Promise.resolve(this.knobs);
    this.#knobsLoading ??= getBudgetKnobs()
      .then((k) => {
        this.knobs = k;
        this.knobsError = "";
        return k;
      })
      .catch((e) => {
        this.knobsError = formatApiError(e, "Could not load the budget knobs.");
        return null;
      })
      .finally(() => {
        this.#knobsLoading = null;
      });
    return this.#knobsLoading;
  }

  /** Counts of non-archived latest rows per kind (null while that kind loads). */
  counts(): Record<LibraryKind, number | null> {
    return Object.fromEntries(
      ALL_KINDS.map((kind) => {
        const entry = this.list(kind);
        return [kind, entry.loaded ? entry.items.length : null];
      }),
    ) as Record<LibraryKind, number | null>;
  }
}

export const libraryStore = new LibraryStore();

/** After creating or versioning a library entity anywhere: reload mounted lists and pickers. */
export function invalidateLibrary(kind?: LibraryKind | null): void {
  libraryStore.invalidate(kind);
}

/** A destructive action awaiting confirmation; the page renders the dialog. */
export interface ConfirmRequest {
  title: string;
  description: string;
  confirmLabel: string;
  action: () => Promise<void>;
}
export type AskConfirm = (request: ConfirmRequest) => void;
