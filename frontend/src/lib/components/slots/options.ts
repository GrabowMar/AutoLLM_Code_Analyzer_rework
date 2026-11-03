/**
 * Option lists for the slot picker's forms, loaded once per page load (browser-only caches): library rows of
 * one kind (latest version per slug) and the model catalog. Values are what the APIs expect: `slug@v` for
 * tasks (TaskSpecVersion refs), the bare slug for factor levels (pinned at plan time), `model_id` for models.
 */
import { getModels, type LLMModelSummary } from "$lib/api/models";
import { listAllLibrary } from "$lib/api/v3/library";
import type { LibraryItemOut, LibraryKind } from "$lib/api/v3/types";

export interface OptionItem {
  value: string;
  label: string;
  description?: string;
}

const libraryCache = new Map<string, Promise<LibraryItemOut[]>>();

export function loadLibrary(kind: LibraryKind): Promise<LibraryItemOut[]> {
  let cached = libraryCache.get(kind);
  if (!cached) {
    cached = listAllLibrary(kind, { archived: false });
    cached.catch(() => libraryCache.delete(kind));
    libraryCache.set(kind, cached);
  }
  return cached;
}

/** Library rows as select options; `versioned` uses `slug@v` (tasks), otherwise the slug. */
export async function libraryOptions(
  kind: LibraryKind,
  versioned = false,
): Promise<OptionItem[]> {
  const rows = await loadLibrary(kind);
  return rows
    .map((row) => ({
      value: versioned ? `${row.slug}@${row.version}` : row.slug,
      label: row.name || row.slug,
      description: `${row.slug}@${row.version}${row.is_system ? " · system" : ""}`,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

let modelsCache: Promise<LLMModelSummary[]> | null = null;

/** Models that can drive an agentic harness (tool calling), cheapest first. */
export function loadModels(): Promise<LLMModelSummary[]> {
  if (!modelsCache) {
    modelsCache = getModels({
      per_page: 200,
      sort_by: "input_price",
      sort_dir: "asc",
    }).then((page) => page.items);
    modelsCache.catch(() => {
      modelsCache = null;
    });
  }
  return modelsCache;
}

export function modelLabel(model: LLMModelSummary): string {
  return model.model_name || model.model_id;
}

/** "$0.15 / $0.60 per Mtok" (input / output), "free" for free models. */
export function modelPrice(model: LLMModelSummary): string {
  if (model.is_free) return "free (rate limited)";
  const f = (v: number) => (v >= 1 ? v.toFixed(2) : v.toFixed(3));
  return `$${f(model.input_price_per_million)} / $${f(model.output_price_per_million)} per Mtok`;
}

export async function modelOptions(requireTools = true): Promise<OptionItem[]> {
  const models = await loadModels();
  return models
    .filter((m) => !requireTools || m.supports_function_calling)
    .map((m) => ({
      value: m.model_id,
      label: modelLabel(m),
      description: `${m.model_id} · ${modelPrice(m)}`,
    }));
}
