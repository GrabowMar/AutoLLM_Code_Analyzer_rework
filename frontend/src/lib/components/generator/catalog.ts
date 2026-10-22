/**
 * Shared caches for the catalogs the generator pickers use, so a composer
 * with several pickers (and its run summary) issues one request each.
 * Browser-only: on the server nothing is cached (module state would be shared
 * between users).
 *
 * The lists change behind the cache: the Library creates, edits and deletes
 * app specs and imports packages, and a catalog sync adds or retires models,
 * often in this same tab. So a cached list is served at once, but a picker
 * that mounts once it is older than its revalidation age refetches it in the
 * background and swaps the fresh list in; regaining window focus refreshes
 * the lists mounted pickers show (and marks the rest stale, so the next load
 * refetches them); and after a TTL a list is never served again.
 * `refreshCatalog()` forces a refetch after a change made elsewhere.
 */
import { browser } from "$app/environment";
import { getModels, type LLMModelSummary } from "$lib/api/models";
import {
  getAppTemplates,
  type AppRequirementTemplate,
} from "$lib/api/generation";

const MODEL_PAGE_SIZE = 200; // backend maximum

export type CatalogName = "models" | "app-specs";

/** A cached list older than this is refetched instead of served. */
const MAX_AGE_MS = 10 * 60_000;
/** Focus refreshes skip a list younger than this (rapid window switching). */
const FOCUS_FLOOR_MS = 10_000;

interface Entry<T> {
  request: Promise<T>;
  at: number;
  stale: boolean;
}

class Catalog<T> {
  private entry: Entry<T> | null = null;
  private readonly listeners = new Set<(value: T) => void>();

  constructor(
    private readonly fetcher: () => Promise<T>,
    /** A mounting consumer refetches (in the background) a list older than this. */
    private readonly revalidateAfterMs: number,
  ) {}

  load(force = false): Promise<T> {
    if (!browser) return this.fetcher();
    const entry = this.entry;
    // A stale list (marked on window focus) is as untrustworthy as an expired one.
    if (force || !entry || entry.stale || Date.now() - entry.at > MAX_AGE_MS) {
      return this.start();
    }
    return entry.request;
  }

  /** A fresh fetch when the cached list is stale or old enough; null when it is current. */
  revalidate(): Promise<T> | null {
    if (!browser) return null;
    const entry = this.entry;
    if (entry && !entry.stale && Date.now() - entry.at < this.revalidateAfterMs)
      return null;
    return this.start();
  }

  /** Called with every list fetched from now on (until the returned function unsubscribes). */
  subscribe(listener: (value: T) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** The window regained focus: refresh what is on screen, mark the rest stale. */
  onFocus(): void {
    const entry = this.entry;
    if (!entry) return;
    entry.stale = true;
    if (this.listeners.size > 0 && Date.now() - entry.at >= FOCUS_FLOOR_MS) {
      this.start().catch(() => {});
    }
  }

  private start(): Promise<T> {
    const request = this.fetcher();
    const entry: Entry<T> = { request, at: Date.now(), stale: false };
    this.entry = entry;
    request.then(
      (value) => {
        if (this.entry !== entry) return;
        for (const listener of this.listeners) listener(value);
      },
      () => {
        // A failed load must not poison the cache for the next caller.
        if (this.entry === entry) this.entry = null;
      },
    );
    return request;
  }
}

async function fetchAllModels(): Promise<LLMModelSummary[]> {
  const first = await getModels({ per_page: MODEL_PAGE_SIZE, page: 1 });
  const rest = await Promise.all(
    Array.from({ length: Math.max(0, first.pages - 1) }, (_, i) =>
      getModels({ per_page: MODEL_PAGE_SIZE, page: i + 2 }),
    ),
  );
  // A catalog sync between page requests can shift rows; keep the first copy.
  const byId = new Map<number, LLMModelSummary>();
  for (const page of [first, ...rest]) {
    for (const model of page.items) {
      if (!byId.has(model.id)) byId.set(model.id, model);
    }
  }
  return [...byId.values()];
}

// Models change only on a catalog sync (and the list is several pages);
// app specs change whenever the Library is used.
const models = new Catalog<LLMModelSummary[]>(fetchAllModels, 2 * 60_000);
const appSpecs = new Catalog<AppRequirementTemplate[]>(getAppTemplates, 15_000);

if (browser) {
  window.addEventListener("focus", () => {
    models.onFocus();
    appSpecs.onFocus();
  });
}

/** Every model in the catalog (all pages), cached (see the module comment). */
export function loadModelCatalog(
  options: { force?: boolean } = {},
): Promise<LLMModelSummary[]> {
  return models.load(options.force);
}

/** Every visible app spec, cached (see the module comment). */
export function loadAppSpecCatalog(
  options: { force?: boolean } = {},
): Promise<AppRequirementTemplate[]> {
  return appSpecs.load(options.force);
}

/**
 * Keep a mounted consumer current: refetch now if the cached list is old or
 * stale, and call `onList` with every list fetched while subscribed (window
 * focus, `refreshCatalog`, another picker's revalidation). Returns the
 * unsubscribe function (an `$effect` / `onMount` cleanup).
 */
export function watchCatalog(
  name: "models",
  onList: (list: LLMModelSummary[]) => void,
): () => void;
export function watchCatalog(
  name: "app-specs",
  onList: (list: AppRequirementTemplate[]) => void,
): () => void;
export function watchCatalog(
  name: CatalogName,
  onList: (list: never) => void,
): () => void {
  const catalog = (name === "models" ? models : appSpecs) as Catalog<unknown>;
  const unsubscribe = catalog.subscribe(onList as (value: unknown) => void);
  // Errors are left to the next load; the cached list stays on screen.
  catalog.revalidate()?.catch(() => {});
  return unsubscribe;
}

/**
 * Refetch a catalog (both by default) after a change made elsewhere, e.g. an
 * app spec created in the Library or a model sync. Mounted pickers get the
 * new list; later loads reuse it.
 */
export function refreshCatalog(name?: CatalogName): Promise<void> {
  const targets = name
    ? [name === "models" ? models : appSpecs]
    : [models, appSpecs];
  return Promise.all(
    targets.map((c) => (c as Catalog<unknown>).load(true)),
  ).then(() => undefined);
}

/** Match `?model=` links: canonical slug, OpenRouter id, or numeric pk. */
export function findModel(
  models: LLMModelSummary[],
  ref: string | number | null | undefined,
): LLMModelSummary | undefined {
  if (ref === null || ref === undefined || ref === "") return undefined;
  if (typeof ref === "number") return models.find((m) => m.id === ref);
  const needle = ref.toLowerCase();
  return (
    models.find((m) => m.canonical_slug.toLowerCase() === needle) ??
    models.find((m) => m.model_id.toLowerCase() === needle) ??
    (/^\d+$/.test(ref) ? models.find((m) => m.id === Number(ref)) : undefined)
  );
}

export interface ModelEligibilityRules {
  /** A selected harness needs native tool calling. */
  requireTools?: boolean;
}

/** Why a model cannot run under the given rules, or null when it can. */
export function modelIneligibility(
  model: LLMModelSummary | undefined,
  rules: ModelEligibilityRules,
): string | null {
  if (!model) return "No longer in the model catalog";
  if (rules.requireTools && !model.supports_function_calling) {
    return "No tool calling (a selected harness needs it)";
  }
  return null;
}
