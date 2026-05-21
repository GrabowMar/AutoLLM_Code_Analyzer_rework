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

export async function deleteModel(slug: string): Promise<void> {
  await apiFetch(`/models/detail/${encodeURIComponent(slug)}/`, {
    method: "DELETE",
  });
}

export async function getModel(slug: string): Promise<LLMModelDetail> {
  const res = await apiFetch(`/models/detail/${encodeURIComponent(slug)}/`);
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
    `/models/detail/${encodeURIComponent(slug)}/related/?limit=${limit}`,
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
    `/models/detail/${encodeURIComponent(slug)}/refresh/`,
    { method: "POST" },
  );
  return res.json();
}

export async function syncModelsFromOpenRouter(): Promise<SyncResult> {
  const res = await apiFetch("/models/sync/", { method: "POST" });
  return res.json();
}
