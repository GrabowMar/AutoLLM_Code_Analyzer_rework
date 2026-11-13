/**
 * Models list filters: one object for every filter, its URL form (so a filtered view is a link)
 * and the `getModels` params it turns into. Comma-list fields mean "all must hold" on the API.
 */
import type { getModels } from "$lib/api/models";

export interface ModelFilters {
  search: string;
  /** "" | "openrouter" | "local" */
  source: string;
  provider: string;
  /** Comma list: function_calling, vision, json_mode, streaming, reasoning, structured_outputs, seed, logprobs. */
  capability: string;
  /** "" | free | low | medium | high (input price per 1M tokens). */
  price: string;
  /** "" | small | medium | large | xlarge (context window). */
  context: string;
  /** Same buckets as `context`, for max output tokens. */
  maxOutput: string;
  /** Comma list: image, file, audio, video. */
  modality: string;
  /** "" | open | closed (models.dev). */
  weights: string;
  /** "" | 30d | 90d | 180d | 365d | older. */
  released: string;
  tokenizer: string;
  /** Comma list: epoch, lmarena, huggingface. */
  coverage: string;
  usedInApps: boolean;
  hideVariants: boolean;
}

export const EMPTY_FILTERS: ModelFilters = {
  search: "",
  source: "",
  provider: "",
  capability: "",
  price: "",
  context: "",
  maxOutput: "",
  modality: "",
  weights: "",
  released: "",
  tokenizer: "",
  coverage: "",
  usedInApps: false,
  hideVariants: false,
};

/** URL query key for each field (kept equal to the API's names where they exist). */
const URL_KEYS: Record<keyof ModelFilters, string> = {
  search: "q",
  source: "source",
  provider: "provider",
  capability: "capability",
  price: "price",
  context: "context",
  maxOutput: "max_output",
  modality: "modality",
  weights: "weights",
  released: "released",
  tokenizer: "tokenizer",
  coverage: "coverage",
  usedInApps: "used_in_apps",
  hideVariants: "hide_variants",
};

/** Filters that live behind "More filters". */
export const SECONDARY: (keyof ModelFilters)[] = [
  "released",
  "weights",
  "modality",
  "tokenizer",
  "maxOutput",
  "coverage",
  "usedInApps",
  "hideVariants",
];

export function countActive(
  f: ModelFilters,
  keys?: (keyof ModelFilters)[],
): number {
  return (keys ?? (Object.keys(f) as (keyof ModelFilters)[]))
    .filter((k) => k !== "search")
    .filter((k) => Boolean(f[k])).length;
}

export function filtersFromUrl(params: URLSearchParams): ModelFilters {
  const f: ModelFilters = { ...EMPTY_FILTERS };
  for (const [field, key] of Object.entries(URL_KEYS) as [
    keyof ModelFilters,
    string,
  ][]) {
    const v = params.get(key);
    if (v == null) continue;
    if (typeof EMPTY_FILTERS[field] === "boolean")
      (f[field] as boolean) = v === "1" || v === "true";
    else (f[field] as string) = v;
  }
  return f;
}

/** Writes the filters into `params` (removing empty ones), leaving unrelated keys alone. */
export function filtersToUrl(
  f: ModelFilters,
  params: URLSearchParams,
): URLSearchParams {
  const out = new URLSearchParams(params);
  for (const [field, key] of Object.entries(URL_KEYS) as [
    keyof ModelFilters,
    string,
  ][]) {
    const v = f[field];
    if (v === "" || v === false) out.delete(key);
    else out.set(key, v === true ? "1" : String(v));
  }
  return out;
}

type ModelQuery = NonNullable<Parameters<typeof getModels>[0]>;

export function filtersToQuery(f: ModelFilters): ModelQuery {
  return {
    search: f.search,
    endpoint: (f.source || undefined) as ModelQuery["endpoint"],
    provider: f.provider,
    capability: f.capability,
    price_range: f.price,
    context_range: f.context,
    max_output_range: f.maxOutput,
    modality: f.modality,
    weights: f.weights,
    released: f.released,
    tokenizer: f.tokenizer,
    coverage: f.coverage,
    used_in_apps: f.usedInApps,
    hide_variants: f.hideVariants,
  };
}
