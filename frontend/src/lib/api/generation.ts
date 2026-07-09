import { apiFetch } from "./core";

export interface AppRequirementTemplate {
  id: number;
  name: string;
  slug: string;
  category: string;
  description: string;
  backend_requirements: string[];
  frontend_requirements: string[];
  admin_requirements: string[];
  api_endpoints: any[];
  admin_api_endpoints: any[];
  data_model: Record<string, any>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface BatchCreateResponse {
  batch_id: string;
  job_count: number;
  status: string;
}

export interface ContentBlock {
  id: number;
  block_type: string;
  slug: string;
  version: number;
  name: string;
  description: string;
  content: string;
  metadata: Record<string, unknown>;
  is_system: boolean;
  created_at: string;
  updated_at: string;
}

export interface CopilotIteration {
  id: number;
  iteration_number: number;
  action: string;
  llm_request: Record<string, any>;
  llm_response: Record<string, any>;
  build_output: string;
  build_success: boolean;
  errors_detected: string[];
  fix_applied: string;
  created_at: string;
}

export interface GenerationArtifact {
  id: number;
  stage: string;
  request_payload: Record<string, any>;
  response_payload: Record<string, any>;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost: number;
  created_at: string;
}

export interface GenerationBatch {
  id: string;
  name: string;
  mode: string;
  status: string;
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  created_at: string;
  updated_at: string;
}

export interface GenerationJob {
  id: string;
  mode: string;
  status: string;
  model_name: string | null;
  model_id_str: string | null;
  batch_id: string | null;
  batch_name: string | null;
  template_name: string | null;
  scaffolding_name: string | null;
  created_by_email: string | null;
  temperature: number;
  max_tokens: number;
  custom_system_prompt: string;
  custom_user_prompt: string;
  copilot_description: string;
  copilot_max_iterations: number;
  copilot_current_iteration: number;
  copilot_use_open_source: boolean;
  app_directory: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string;
  result_data: Record<string, any>;
  metrics: Record<string, any>;
  experiment_seed: number | null;
  resolved_bundle: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface GenerationJobList {
  id: string;
  mode: string;
  status: string;
  model_name: string | null;
  model_id_str: string | null;
  template_name: string | null;
  scaffolding_name: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string;
  created_at: string;
}

export interface PaginatedJobs {
  items: GenerationJobList[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface PromptTemplate {
  id: number;
  name: string;
  slug: string;
  stage: string;
  role: string;
  content: string;
  description: string;
  is_default: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TemplateBundle {
  id: number;
  name: string;
  slug: string;
  description: string;
  scaffolding_slug: string;
  block_refs: { type: string; slug: string; version: number }[];
  llm_config: Record<string, unknown>;
  is_system: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface ScaffoldingTemplate {
  id: number;
  name: string;
  slug: string;
  description: string;
  tech_stack: Record<string, string>;
  substitution_vars: string[];
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface StarterTemplatePackage {
  slug: string;
  name: string;
  description: string;
  scaffolding_count: number;
  app_template_count: number;
  prompt_template_count: number;
  block_count: number;
  bundle_count: number;
}

export async function cancelGenerationJob(
  id: string,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`/generation/jobs/${id}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

export async function createAppTemplate(data: {
  name: string;
  slug: string;
  description?: string;
  backend_requirements?: string[];
  frontend_requirements?: string[];
  admin_requirements?: string[];
  api_endpoints?: Record<string, unknown>;
  admin_api_endpoints?: any[];
  data_model?: Record<string, unknown>;
}): Promise<AppRequirementTemplate> {
  const res = await apiFetch("/generation/app-templates/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createCopilotJob(data: {
  description: string;
  model_id?: number;
  max_iterations?: number;
  use_open_source?: boolean;
}): Promise<GenerationJob> {
  const res = await apiFetch("/generation/jobs/copilot/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createCustomJob(data: {
  model_id: number;
  system_prompt: string;
  user_prompt: string;
  temperature?: number;
  max_tokens?: number;
}): Promise<GenerationJob> {
  const res = await apiFetch("/generation/jobs/custom/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createPromptTemplate(data: {
  name: string;
  slug: string;
  stage: string;
  role: string;
  content: string;
}): Promise<PromptTemplate> {
  const res = await apiFetch("/generation/prompt-templates/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function getContentBlocks(): Promise<ContentBlock[]> {
  const res = await apiFetch("/generation/blocks/");
  return res.json();
}

export async function getTemplateBundles(): Promise<TemplateBundle[]> {
  const res = await apiFetch("/generation/bundles/");
  return res.json();
}

export interface BundlePreview {
  slug: string;
  scaffolding_slug: string;
  block_count: number;
  prompt_templates: Record<string, { system?: string; user?: string }>;
}

export async function getBundlePreview(slug: string): Promise<BundlePreview> {
  const res = await apiFetch(`/generation/bundles/${slug}/preview/`);
  return res.json();
}

export async function exportTemplatePackage(
  data: {
    scaffolding_slugs?: string[];
    app_template_slugs?: string[];
    prompt_template_slugs?: string[];
    bundle_slugs?: string[];
    block_refs?: { type: string; slug: string; version: number }[];
  },
  format: "json" | "yaml" = "json",
): Promise<{ content: string; contentType: string }> {
  const res = await apiFetch(`/generation/packages/export/?format=${format}`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  return {
    content: await res.text(),
    contentType: res.headers.get("content-type") || "application/octet-stream",
  };
}

export async function importTemplatePackage(data: {
  package_text: string;
  conflict_strategy?: "rename" | "overwrite" | "error";
}): Promise<Record<string, string[]>> {
  const res = await apiFetch("/generation/packages/import/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function getStarterTemplatePackages(): Promise<
  StarterTemplatePackage[]
> {
  const res = await apiFetch("/generation/packages/starters/");
  return res.json();
}

export async function importStarterTemplatePackage(
  slug: string,
  data: {
    conflict_strategy?: "rename" | "overwrite" | "error";
  } = {},
): Promise<Record<string, string[]>> {
  const res = await apiFetch(`/generation/packages/starters/${slug}/import/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createScaffoldingBatch(data: {
  scaffolding_template_id: number;
  app_requirement_ids: number[];
  model_ids: number[];
  temperature?: number;
  max_tokens?: number;
}): Promise<BatchCreateResponse> {
  const res = await apiFetch("/generation/jobs/scaffolding/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createScaffoldingTemplate(data: {
  name: string;
  slug: string;
  description?: string;
  tech_stack?: Record<string, string>;
  substitution_vars?: string[];
}): Promise<ScaffoldingTemplate> {
  const res = await apiFetch("/generation/scaffolding-templates/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteAppTemplate(slug: string): Promise<void> {
  await apiFetch(`/generation/app-templates/${slug}/`, { method: "DELETE" });
}

export async function deleteGenerationJob(
  id: string,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`/generation/jobs/${id}/`, { method: "DELETE" });
  return res.json();
}

export async function deletePromptTemplate(slug: string): Promise<void> {
  await apiFetch(`/generation/prompt-templates/${slug}/`, { method: "DELETE" });
}

export async function exportGenerationJob(
  id: string,
): Promise<Record<string, any>> {
  const res = await apiFetch(`/generation/jobs/${id}/export/`);
  return res.json();
}

export async function getAppTemplates(): Promise<AppRequirementTemplate[]> {
  const res = await apiFetch("/generation/app-templates/");
  return res.json();
}

export async function getCopilotIterations(
  id: string,
): Promise<CopilotIteration[]> {
  const res = await apiFetch(`/generation/jobs/${id}/copilot-iterations/`);
  return res.json();
}

export async function getGenerationBatch(id: string): Promise<GenerationBatch> {
  const res = await apiFetch(`/generation/batches/${id}/`);
  return res.json();
}

export async function getGenerationBatches(): Promise<GenerationBatch[]> {
  const res = await apiFetch("/generation/batches/");
  return res.json();
}

export async function getGenerationJob(id: string): Promise<GenerationJob> {
  const res = await apiFetch(`/generation/jobs/${id}/`);
  return res.json();
}

export async function getGenerationJobs(params?: {
  page?: number;
  per_page?: number;
  mode?: string;
  status?: string;
  model_id?: string;
  search?: string;
  sort_by?: string;
  container_status?: string;
}): Promise<PaginatedJobs> {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.per_page) q.set("per_page", String(params.per_page));
  if (params?.mode) q.set("mode", params.mode);
  if (params?.status) q.set("status", params.status);
  if (params?.model_id) q.set("model_id", params.model_id);
  if (params?.search) q.set("search", params.search);
  if (params?.sort_by) q.set("sort_by", params.sort_by);
  if (params?.container_status)
    q.set("container_status", params.container_status);
  const qs = q.toString();
  const res = await apiFetch(`/generation/jobs/${qs ? "?" + qs : ""}`);
  return res.json();
}

export async function getGenerationJobsStats(): Promise<{
  total: number;
  completed: number;
  running: number;
  failed: number;
  pending: number;
  cancelled: number;
}> {
  const res = await apiFetch("/generation/jobs/stats/");
  return res.json();
}

export async function getJobArtifacts(
  id: string,
): Promise<GenerationArtifact[]> {
  const res = await apiFetch(`/generation/jobs/${id}/artifacts/`);
  return res.json();
}

export async function getPromptTemplates(
  stage?: string,
  role?: string,
): Promise<PromptTemplate[]> {
  const params = new URLSearchParams();
  if (stage) params.set("stage", stage);
  if (role) params.set("role", role);
  const qs = params.toString();
  const res = await apiFetch(
    `/generation/prompt-templates/${qs ? "?" + qs : ""}`,
  );
  return res.json();
}

export async function getScaffoldingTemplates(): Promise<
  ScaffoldingTemplate[]
> {
  const res = await apiFetch("/generation/scaffolding-templates/");
  return res.json();
}

export async function retryGenerationJob(id: string): Promise<GenerationJob> {
  const res = await apiFetch(`/generation/jobs/${id}/retry/`, {
    method: "POST",
  });
  return res.json();
}

export async function updateAppTemplate(
  slug: string,
  data: Partial<{
    name: string;
    description: string;
    backend_requirements: string[];
    frontend_requirements: string[];
    admin_requirements: string[];
    api_endpoints: Record<string, unknown>;
    admin_api_endpoints: any[];
    data_model: Record<string, unknown>;
  }>,
): Promise<AppRequirementTemplate> {
  const res = await apiFetch(`/generation/app-templates/${slug}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updatePromptTemplate(
  slug: string,
  data: Partial<{
    name: string;
    stage: string;
    role: string;
    content: string;
  }>,
): Promise<PromptTemplate> {
  const res = await apiFetch(`/generation/prompt-templates/${slug}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateScaffoldingTemplate(
  slug: string,
  data: Partial<{
    name: string;
    description: string;
    tech_stack: Record<string, string>;
    substitution_vars: string[];
  }>,
): Promise<ScaffoldingTemplate> {
  const res = await apiFetch(`/generation/scaffolding-templates/${slug}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteScaffoldingTemplate(slug: string): Promise<void> {
  await apiFetch(`/generation/scaffolding-templates/${slug}/`, {
    method: "DELETE",
  });
}
