import { apiFetch } from "./core";

export interface LLMModelSummary {
  id: number;
  model_id: string;
  canonical_slug: string;
  provider: string;
  model_name: string;
  description: string;
  is_free: boolean;
  context_window: number;
  max_output_tokens: number;
  context_window_display: string;
  input_price_per_million: number;
  output_price_per_million: number;
  capabilities: string[];
  cost_efficiency: number;
  supports_vision: boolean;
  supports_function_calling: boolean;
  supports_streaming: boolean;
  supports_json_mode: boolean;
  /** OpenRouter lists `reasoning`/`include_reasoning` among the model's supported params. */
  supports_reasoning?: boolean;
  // Endpoint fields (spec §F.3). Optional until the models API serializes
  // them; `endpoint: null` / `is_local: false` mean the OpenRouter catalog.
  endpoint?: LLMModelEndpointRef | null;
  /** Exact name on the endpoint (Ollama tag, colon kept); "" for OpenRouter. */
  served_model_name?: string;
  digest?: string;
  quantization?: string;
  parameter_size?: string;
  is_local?: boolean;
  /** False once the endpoint stopped listing the model (only with `include_unavailable`). */
  is_available?: boolean;
}

/** `?endpoint=` filter of the models list: an endpoint slug, or every local / OpenRouter row. */
export type ModelEndpointFilter = "local" | "openrouter" | (string & {});

export interface LLMModelEndpointRef {
  id: number;
  slug: string;
  name: string;
  kind: "openrouter" | "ollama" | "openai_compatible";
  online: boolean;
}

export interface LLMModelDetail extends LLMModelSummary {
  description: string;
  max_output_tokens: number;
  input_price_per_token: number;
  output_price_per_token: number;
  metadata: Record<string, unknown>;
  capabilities_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ModelComparisonResult {
  items: LLMModelDetail[];
  missing: string[];
}

export interface ModelImportResult {
  count: number;
  imported: number;
}

export interface ModelsStats {
  total: number;
  providers: number;
  free: number;
  avg_input_price: number;
  avg_output_price: number;
}

export interface PaginatedModels {
  items: LLMModelSummary[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface SyncResult {
  fetched: number;
  upserted: number;
}

/**
 * Path-safe model identifier. Proxies decode `%2F`, so an OpenRouter id like
 * `deepseek/deepseek-v4.1-flash` would split the route; the backend resolves
 * the `provider_model` form back to the model.
 */
export function modelPathSegment(id: string): string {
  return encodeURIComponent(id.replaceAll("/", "_"));
}

export async function deleteModel(slug: string): Promise<void> {
  await apiFetch(`/models/detail/${modelPathSegment(slug)}/`, {
    method: "DELETE",
  });
}

export async function getModel(slug: string): Promise<LLMModelDetail> {
  const res = await apiFetch(`/models/detail/${modelPathSegment(slug)}/`);
  return res.json();
}

export async function getModelComparison(
  slugs: string[],
): Promise<ModelComparisonResult> {
  const q = new URLSearchParams();
  if (slugs.length > 0) q.set("models", slugs.join(","));
  const qs = q.toString();
  const res = await apiFetch(`/models/comparison/${qs ? "?" + qs : ""}`);
  return res.json();
}

export async function getModels(
  params: {
    page?: number;
    per_page?: number;
    search?: string;
    provider?: string;
    capability?: string;
    free_only?: boolean;
    sort_by?: string;
    sort_dir?: "asc" | "desc";
    price_range?: string;
    context_range?: string;
    used_in_apps?: boolean;
    endpoint?: ModelEndpointFilter;
    /** Also list hidden and unavailable local models. */
    include_unavailable?: boolean;
  } = {},
): Promise<PaginatedModels> {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.per_page) q.set("per_page", String(params.per_page));
  if (params.search) q.set("search", params.search);
  if (params.provider) q.set("provider", params.provider);
  if (params.capability) q.set("capability", params.capability);
  if (params.free_only) q.set("free_only", "true");
  if (params.sort_by) q.set("sort_by", params.sort_by);
  if (params.sort_dir) q.set("sort_dir", params.sort_dir);
  if (params.price_range) q.set("price_range", params.price_range);
  if (params.context_range) q.set("context_range", params.context_range);
  if (params.used_in_apps) q.set("used_in_apps", "true");
  if (params.endpoint) q.set("endpoint", params.endpoint);
  if (params.include_unavailable) q.set("include_unavailable", "1");
  const qs = q.toString();
  const res = await apiFetch(`/models/${qs ? "?" + qs : ""}`);
  return res.json();
}

export function getModelsExportUrl(format: "csv" | "json" = "csv"): string {
  return `/api/models/export/?format=${format}`;
}

export async function getModelsStats(): Promise<ModelsStats> {
  const res = await apiFetch("/models/stats/");
  return res.json();
}

export async function getProviders(): Promise<string[]> {
  const res = await apiFetch("/models/providers/");
  return res.json();
}

export async function getRelatedModels(
  slug: string,
  limit = 10,
): Promise<LLMModelSummary[]> {
  const res = await apiFetch(
    `/models/detail/${modelPathSegment(slug)}/related/?limit=${limit}`,
  );
  return res.json();
}

export async function importModelsFromJson(
  payload: unknown,
): Promise<ModelImportResult> {
  const res = await apiFetch("/models/import/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function refreshModelFromOpenRouter(
  slug: string,
): Promise<LLMModelDetail> {
  const res = await apiFetch(
    `/models/detail/${modelPathSegment(slug)}/refresh/`,
    { method: "POST" },
  );
  return res.json();
}

export async function syncModelsFromOpenRouter(): Promise<SyncResult> {
  const res = await apiFetch("/models/sync/", { method: "POST" });
  return res.json();
}

// ── Model page: platform measurements, catalog context, providers ─────────

/** A proportion with its Wilson 95% interval (`rate`/`ci_*` null when n = 0). */
export interface RateWithCI {
  successes: number;
  n: number;
  rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
}

export type SeverityCounts = Record<
  "critical" | "high" | "medium" | "low" | "info",
  number
>;

export interface RankPosition {
  rank: number;
  of: number;
  score: number;
}

export interface CatalogPercentile {
  value: number | null;
  /** Share of the catalog below this value (ties count half), 0–1. */
  percentile: number | null;
  n: number;
  median: number | null;
}

export interface ModelInsights {
  generation: {
    jobs_total: number;
    by_status: Record<string, number>;
    by_mode: Record<string, number>;
    completed: number;
    success: RateWithCI;
    functional: RateWithCI & {
      mean_pass_rate: number | null;
      by_status: Record<string, number>;
    };
    resources: {
      total_cost: number;
      mean_cost: number | null;
      cost_per_kloc: number | null;
      mean_duration: number | null;
      median_duration: number | null;
      mean_prompt_tokens: number | null;
      mean_completion_tokens: number | null;
      mean_reasoning_tokens: number | null;
      mean_total_tokens: number | null;
      cache_hit_ratio: number | null;
      mean_llm_calls: number | null;
      mean_lines_of_code: number | null;
      median_output_tps: number | null;
    };
    repair_rate: number | null;
    continuations_used: number;
    engines: Record<string, number>;
    providers: Record<string, number>;
    terminations: Record<string, number>;
    first_job_at: string | null;
    last_job_at: string | null;
  };
  templates: {
    slug: string;
    name: string;
    jobs: number;
    completed: number;
    functional: RateWithCI;
  }[];
  experiments: {
    id: string;
    name: string;
    status: string;
    jobs: number;
    created_at: string;
  }[];
  analysis: {
    analyzed_jobs: number;
    findings_static: SeverityCounts;
    findings_ai: SeverityCounts;
    by_tool: ({ tool: string; ai: boolean; total: number } & SeverityCounts)[];
    top_rules: {
      tool: string;
      rule_id: string;
      title: string;
      severity: string;
      count: number;
    }[];
  };
  ranking: {
    scores: Record<
      | "composite_score"
      | "mss_score"
      | "empirical_quality_score"
      | "benchmark_score"
      | "cost_efficiency_score"
      | "accessibility_score"
      | "adoption_score",
      number | null
    >;
    positions: Record<
      "composite" | "mss" | "empirical" | "benchmark",
      RankPosition | null
    >;
    variance: {
      n_jobs: number;
      density_per_kloc_mean: number | null;
      density_per_kloc_stdev: number | null;
      smoke_pass_rate_stdev: number | null;
    } | null;
    local_loc: number;
    n_trials: number;
    models_exercised: number;
  } | null;
  catalog: {
    input_price: CatalogPercentile;
    output_price: CatalogPercentile;
    context_window: CatalogPercentile;
    max_output_tokens: CatalogPercentile;
    artificial_analysis: Record<
      "intelligence_index" | "coding_index" | "agentic_index",
      CatalogPercentile
    >;
    catalog_size: number;
    provider_models: number;
  };
  benchmarks: {
    benchmark: string;
    score: number;
    source: string;
    fetched_at: string;
    percentile: number | null;
    n: number;
  }[];
}

export interface ProviderEndpoint {
  provider_name: string;
  tag: string;
  quantization: string;
  context_length: number | null;
  max_completion_tokens: number | null;
  max_prompt_tokens: number | null;
  input_price_per_million: number;
  output_price_per_million: number;
  cache_read_per_million: number | null;
  discount: number;
  /** OpenRouter endpoint status; 0 = OK, negative = degraded / down. */
  status: number | null;
  uptime_last_5m: number | null;
  uptime_last_30m: number | null;
  uptime_last_1d: number | null;
  /** p50 latency (ms) — OpenRouter returns a number or a percentile object. */
  latency_last_30m: number | Record<string, number> | null;
  throughput_last_30m: number | Record<string, number> | null;
  supported_parameters: string[];
  supports_implicit_caching: boolean;
  native_tools: string[];
  service_tier: string | null;
}

export interface ProviderEndpoints {
  available: boolean;
  fetched_at: string | null;
  endpoints: ProviderEndpoint[];
}

/** Platform measurements (caller's jobs), ranking position and catalog percentiles for one model. */
export async function getModelInsights(slug: string): Promise<ModelInsights> {
  const res = await apiFetch(
    `/models/detail/${modelPathSegment(slug)}/insights/`,
  );
  return res.json();
}

/** Providers serving the model on OpenRouter (live; cached 10 min server-side). */
export async function getModelEndpoints(
  slug: string,
  refresh = false,
): Promise<ProviderEndpoints> {
  const q = refresh ? "?refresh=true" : "";
  const res = await apiFetch(
    `/models/detail/${modelPathSegment(slug)}/endpoints/${q}`,
  );
  return res.json();
}
