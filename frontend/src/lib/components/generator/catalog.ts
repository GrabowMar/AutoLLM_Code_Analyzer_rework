/**
 * Page-lifetime caches for the catalogs the generator pickers share, so a
 * composer with several pickers (and its run summary) issues one request each.
 * Browser-only: on the server nothing is cached (module state would be shared
 * between users).
 */
import { browser } from "$app/environment";
import { getModels, type LLMModelSummary } from "$lib/api/models";
import {
  getAppTemplates,
  type AppRequirementTemplate,
} from "$lib/api/generation";

const MODEL_PAGE_SIZE = 200; // backend maximum

let modelCatalog: Promise<LLMModelSummary[]> | null = null;
let appSpecCatalog: Promise<AppRequirementTemplate[]> | null = null;

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

/** Every model in the catalog (all pages), fetched once per page load. */
export function loadModelCatalog(
  options: { force?: boolean } = {},
): Promise<LLMModelSummary[]> {
  if (!browser) return fetchAllModels();
  if (!modelCatalog || options.force) {
    const request = fetchAllModels();
    modelCatalog = request;
    // A failed load must not poison the cache for the next caller.
    request.catch(() => {
      if (modelCatalog === request) modelCatalog = null;
    });
  }
  return modelCatalog;
}

/** Every visible app spec, fetched once per page load. */
export function loadAppSpecCatalog(
  options: { force?: boolean } = {},
): Promise<AppRequirementTemplate[]> {
  if (!browser) return getAppTemplates();
  if (!appSpecCatalog || options.force) {
    const request = getAppTemplates();
    appSpecCatalog = request;
    request.catch(() => {
      if (appSpecCatalog === request) appSpecCatalog = null;
    });
  }
  return appSpecCatalog;
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
