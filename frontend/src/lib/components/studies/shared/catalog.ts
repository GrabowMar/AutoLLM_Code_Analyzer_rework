/**
 * Shared cache of the model catalog the pickers use, so a builder with several
 * pickers (and its plan rail) issues one request.
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

const MODEL_PAGE_SIZE = 200; // backend maximum

export type CatalogName = "models";

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

  /** Whether this list was ever requested (so a forced refresh is worth it). */
  get loaded(): boolean {
    return this.entry !== null;
  }

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

async function fetchAllModels(
  includeUnavailable = false,
): Promise<LLMModelSummary[]> {
  const extra = includeUnavailable ? { include_unavailable: true } : {};
  const first = await getModels({
    per_page: MODEL_PAGE_SIZE,
    page: 1,
    ...extra,
  });
  const rest = await Promise.all(
    Array.from({ length: Math.max(0, first.pages - 1) }, (_, i) =>
      getModels({ per_page: MODEL_PAGE_SIZE, page: i + 2, ...extra }),
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

// Models change only on a catalog sync (and the list is several pages).
const models = new Catalog<LLMModelSummary[]>(
  () => fetchAllModels(),
  2 * 60_000,
);
// Hidden and unavailable local models too (pickers with `showUnavailable`).
// Endpoint inventories change while a relay is connected, so this list
// revalidates sooner.
const modelsWithUnavailable = new Catalog<LLMModelSummary[]>(
  () => fetchAllModels(true),
  30_000,
);

if (browser) {
  window.addEventListener("focus", () => {
    models.onFocus();
    modelsWithUnavailable.onFocus();
  });
}

/**
 * Every model in the catalog (all pages), cached (see the module comment).
 * `includeUnavailable` also returns hidden and no-longer-served local models
 * (a separate cached list).
 */
export function loadModelCatalog(
  options: { force?: boolean; includeUnavailable?: boolean } = {},
): Promise<LLMModelSummary[]> {
  return (options.includeUnavailable ? modelsWithUnavailable : models).load(
    options.force,
  );
}

/**
 * Keep a mounted consumer current: refetch now if the cached list is old or
 * stale, and call `onList` with every list fetched while subscribed (window
 * focus, `refreshCatalog`, another picker's revalidation). Returns the
 * unsubscribe function (an `$effect` / `onMount` cleanup).
 */
export function watchCatalog(
  name: CatalogName,
  onList: (list: LLMModelSummary[]) => void,
  options: { includeUnavailable?: boolean } = {},
): () => void {
  void name;
  const catalog = options.includeUnavailable ? modelsWithUnavailable : models;
  const unsubscribe = catalog.subscribe(onList);
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
  const all: Catalog<unknown>[] = [];
  if (!name || name === "models") {
    all.push(models as Catalog<unknown>);
    // The second model list is refetched only when something loaded it before.
    if (modelsWithUnavailable.loaded) {
      all.push(modelsWithUnavailable as Catalog<unknown>);
    }
  }
  return Promise.all(all.map((c) => c.load(true))).then(() => undefined);
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

/**
 * Model factor levels for `?model=` / `?models=a,b` links (endpoint page, model page): each ref
 * resolved with `findModel` to its `model_id`, in link order, duplicates dropped. A ref the
 * catalog does not know is kept verbatim only when it looks like a model id (contains `/`), so
 * the plan's lint names it; an unknown bare pk is dropped and returned in `unknown`.
 */
export function modelLevelsFromRefs(
  models: LLMModelSummary[],
  refs: string[],
): { levels: string[]; unknown: string[] } {
  const levels: string[] = [];
  const unknown: string[] = [];
  for (const ref of refs) {
    const found = findModel(models, ref);
    const level = found?.model_id ?? (ref.includes("/") ? ref : null);
    if (level === null) unknown.push(ref);
    else if (!levels.includes(level)) levels.push(level);
  }
  return { levels, unknown };
}

export interface ModelEligibilityRules {
  /** A selected harness needs native tool calling. */
  requireTools?: boolean;
}

/** True for a model served by a local endpoint (Ollama / OpenAI-compatible), not OpenRouter. */
export function isLocalModel(model: LLMModelSummary): boolean {
  if (model.is_local != null) return model.is_local;
  return model.endpoint != null && model.endpoint.kind !== "openrouter";
}

/**
 * Why a model cannot run under the given rules, or null when it can. For
 * local models, tool support is what the endpoint's discovery reported
 * (Ollama `capabilities`, or the endpoint's "assume tools" setting).
 * An offline endpoint does not make a model ineligible: its runs wait.
 */
export function modelIneligibility(
  model: LLMModelSummary | undefined,
  rules: ModelEligibilityRules,
): string | null {
  if (!model) return "No longer in the model catalog";
  if (model.is_available === false) {
    return model.endpoint
      ? `No longer served by ${model.endpoint.name}`
      : "No longer available";
  }
  if (rules.requireTools && !model.supports_function_calling) {
    return isLocalModel(model)
      ? "Endpoint reports no tool calling (a selected harness needs it)"
      : "No tool calling (a selected harness needs it)";
  }
  return null;
}

// ── grouping (ModelPicker, selection summaries) ──────────────────────

export type ModelGroupBy = "provider" | "endpoint";
export type ModelEndpointScope = "all" | "local" | "openrouter";

export interface ModelGroup {
  /** Stable key: `endpoint:<id>` or `provider:<name>`. */
  key: string;
  /** Header text: the endpoint name, or the OpenRouter vendor. */
  label: string;
  /** `endpoint` groups hold one local endpoint's models; `provider` groups an OpenRouter vendor's. */
  type: "endpoint" | "provider";
  endpoint: LLMModelSummary["endpoint"] | null;
  /** Vendor for `provider` groups (ProviderIcon), the endpoint slug otherwise. */
  provider: string;
  models: LLMModelSummary[];
  selectedCount: number;
}

/** Keep only local or only OpenRouter models (`all` keeps every one). */
export function filterByEndpointScope(
  models: LLMModelSummary[],
  scope: ModelEndpointScope,
): LLMModelSummary[] {
  if (scope === "all") return models;
  const wantLocal = scope === "local";
  return models.filter((m) => isLocalModel(m) === wantLocal);
}

/**
 * Group models for display. `provider` groups by `model.provider` (today's
 * picker). `endpoint` puts each local endpoint's models under one group,
 * first and sorted by endpoint name, then the OpenRouter catalog grouped by
 * vendor as before. Models inside a group are sorted by name. Inputs are not
 * mutated.
 */
/** Group of OpenRouter "~vendor/model-latest" aliases (they point at whatever version is newest). */
export const ALIAS_GROUP_KEY = "provider:~aliases";
export const ALIAS_GROUP_LABEL = "Latest aliases (version can change)";

export function groupModels(
  models: LLMModelSummary[],
  groupBy: ModelGroupBy,
  selected: ReadonlySet<number> = new Set(),
): ModelGroup[] {
  const map = new Map<string, ModelGroup>();
  for (const m of models) {
    const local = groupBy === "endpoint" && isLocalModel(m) && m.endpoint;
    // OpenRouter's floating "~vendor/…-latest" aliases share one group.
    const alias = !local && m.provider.startsWith("~");
    const key = local
      ? `endpoint:${m.endpoint!.id}`
      : alias
        ? ALIAS_GROUP_KEY
        : `provider:${m.provider}`;
    let group = map.get(key);
    if (!group) {
      group = local
        ? {
            key,
            label: m.endpoint!.name || m.endpoint!.slug,
            type: "endpoint",
            endpoint: m.endpoint!,
            provider: m.endpoint!.slug,
            models: [],
            selectedCount: 0,
          }
        : {
            key,
            label: alias ? ALIAS_GROUP_LABEL : m.provider,
            type: "provider",
            endpoint: null,
            provider: alias ? "openrouter" : m.provider,
            models: [],
            selectedCount: 0,
          };
      map.set(key, group);
    }
    group.models.push(m);
    if (selected.has(m.id)) group.selectedCount += 1;
  }
  const groups = [...map.values()];
  for (const g of groups) {
    g.models.sort((a, b) => a.model_name.localeCompare(b.model_name));
  }
  return groups.sort((a, b) => {
    if (a.type !== b.type) return a.type === "endpoint" ? -1 : 1;
    // Aliases last: their weights can change, so they are a deliberate pick.
    if ((a.key === ALIAS_GROUP_KEY) !== (b.key === ALIAS_GROUP_KEY))
      return a.key === ALIAS_GROUP_KEY ? 1 : -1;
    return a.label.localeCompare(b.label);
  });
}

/** Selected model counts per endpoint (local) or "OpenRouter", in display order. */
export function selectionByEndpoint(
  models: LLMModelSummary[],
  selectedIds: Iterable<number>,
): { key: string; label: string; count: number }[] {
  const byId = new Map(models.map((m) => [m.id, m]));
  const counts = new Map<
    string,
    { key: string; label: string; count: number }
  >();
  for (const id of selectedIds) {
    const m = byId.get(id);
    if (!m) continue;
    const local = isLocalModel(m) && m.endpoint;
    const key = local ? `endpoint:${m.endpoint!.id}` : "openrouter";
    const label = local ? m.endpoint!.name || m.endpoint!.slug : "OpenRouter";
    const entry = counts.get(key) ?? { key, label, count: 0 };
    entry.count += 1;
    counts.set(key, entry);
  }
  return [...counts.values()].sort((a, b) => {
    if (a.key === "openrouter" || b.key === "openrouter") {
      return a.key === "openrouter" ? 1 : -1;
    }
    return a.label.localeCompare(b.label);
  });
}

// ── search, facets, sorting (ModelPicker) ─────────────────────────────

export { searchTokens, highlightParts } from "$lib/utils/search";

function haystack(m: LLMModelSummary): string {
  return [
    m.model_name,
    m.provider,
    m.model_id,
    m.canonical_slug,
    m.served_model_name ?? "",
    m.endpoint?.slug ?? "",
    m.endpoint?.name ?? "",
  ]
    .join(" ")
    .toLowerCase();
}

export function matchesTokens(m: LLMModelSummary, tokens: string[]): boolean {
  if (tokens.length === 0) return true;
  const text = haystack(m);
  return tokens.every((t) => text.includes(t));
}

/** Capability filters (chips). Each one narrows; models without the data never match. */
export type ModelFacet =
  | "reasoning"
  | "vision"
  | "open_weights"
  | "free"
  | "new";
export const MODEL_FACETS: { key: ModelFacet; label: string; title: string }[] =
  [
    {
      key: "reasoning",
      label: "Reasoning",
      title: "Accepts reasoning parameters",
    },
    { key: "vision", label: "Vision", title: "Accepts image input" },
    {
      key: "open_weights",
      label: "Open weights",
      title: "Weights are published",
    },
    { key: "free", label: "Free", title: "Free tier (rate-limited)" },
    { key: "new", label: "New", title: "Released in the last 120 days" },
  ];
export const NEW_MODEL_DAYS = 120;

export function isNewModel(
  m: LLMModelSummary,
  now: number = Date.now(),
): boolean {
  if (!m.released_at) return false;
  const t = Date.parse(m.released_at);
  return (
    Number.isFinite(t) && now - t <= NEW_MODEL_DAYS * 86_400_000 && t <= now
  );
}

export function hasFacet(
  m: LLMModelSummary,
  facet: ModelFacet,
  now: number = Date.now(),
): boolean {
  switch (facet) {
    case "reasoning":
      return !!m.supports_reasoning;
    case "vision":
      return (
        !!m.supports_vision || (m.input_modalities ?? []).includes("image")
      );
    case "open_weights":
      return m.open_weights === true || isLocalModel(m);
    case "free":
      return m.is_free;
    case "new":
      return isNewModel(m, now);
  }
}

export function applyFacets(
  models: LLMModelSummary[],
  facets: ReadonlySet<ModelFacet>,
  now: number = Date.now(),
): LLMModelSummary[] {
  if (facets.size === 0) return models;
  return models.filter((m) => [...facets].every((f) => hasFacet(m, f, now)));
}

/** How many of `models` each facet would keep on top of the facets already on (chip counts). */
export function facetCounts(
  models: LLMModelSummary[],
  active: ReadonlySet<ModelFacet>,
  now: number = Date.now(),
): Record<ModelFacet, number> {
  const base = applyFacets(models, active, now);
  const out = {} as Record<ModelFacet, number>;
  for (const { key } of MODEL_FACETS) {
    out[key] = active.has(key)
      ? base.length
      : base.filter((m) => hasFacet(m, key, now)).length;
  }
  return out;
}

export type ModelSort = "vendor" | "price" | "context" | "newest";
export const MODEL_SORTS: { value: ModelSort; label: string }[] = [
  { value: "vendor", label: "Grouped by vendor" },
  { value: "price", label: "Cheapest first" },
  { value: "context", label: "Longest context" },
  { value: "newest", label: "Newest first" },
];

/** Blended $/Mtok (1 part output to 3 parts input, a typical agent mix); local models cost 0. */
export function blendedPrice(m: LLMModelSummary): number {
  if (isLocalModel(m) || m.is_free) return 0;
  if (hasVariablePrice(m)) return Number.POSITIVE_INFINITY; // routers: sorted last, never "cheapest"
  return (3 * m.input_price_per_million + m.output_price_per_million) / 4;
}

/** OpenRouter routers list a negative price: what they cost depends on the model they route to. */
export function hasVariablePrice(m: LLMModelSummary): boolean {
  return (
    !isLocalModel(m) &&
    (m.input_price_per_million < 0 || m.output_price_per_million < 0)
  );
}

/** A flat sort (every sort but `vendor`); ties break by name. Does not mutate. */
export function sortModels(
  models: LLMModelSummary[],
  sort: ModelSort,
): LLMModelSummary[] {
  const byName = (a: LLMModelSummary, b: LLMModelSummary) =>
    a.model_name.localeCompare(b.model_name);
  const time = (m: LLMModelSummary) =>
    m.released_at ? Date.parse(m.released_at) || 0 : 0;
  const cmp: Record<
    ModelSort,
    (a: LLMModelSummary, b: LLMModelSummary) => number
  > = {
    vendor: byName,
    price: (a, b) => {
      const pa = blendedPrice(a);
      const pb = blendedPrice(b);
      return pa === pb ? byName(a, b) : pa < pb ? -1 : 1;
    },
    context: (a, b) =>
      (b.context_window ?? 0) - (a.context_window ?? 0) || byName(a, b),
    newest: (a, b) => time(b) - time(a) || byName(a, b),
  };
  return [...models].sort(cmp[sort]);
}

/**
 * The first `limit` rows of grouped models, cut across group boundaries (a group is shown with as many
 * of its rows as still fit; later groups are left out). `remaining` counts the rows not shown, for a
 * "Show more" row: the picker never mounts hundreds of rows at once.
 */
export function paginateGroups(
  groups: ModelGroup[],
  limit: number,
): {
  groups: (ModelGroup & { shown: LLMModelSummary[] })[];
  remaining: number;
} {
  const out: (ModelGroup & { shown: LLMModelSummary[] })[] = [];
  let left = limit;
  let total = 0;
  for (const g of groups) {
    total += g.models.length;
    if (left <= 0) continue;
    const shown = g.models.slice(0, left);
    left -= shown.length;
    out.push({ ...g, shown });
  }
  return {
    groups: out,
    remaining: total - out.reduce((n, g) => n + g.shown.length, 0),
  };
}
