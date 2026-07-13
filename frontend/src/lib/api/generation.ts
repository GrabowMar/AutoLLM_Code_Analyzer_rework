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
  llm_params: LLMParams;
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

export interface LLMParams {
  temperature?: number | null;
  top_p?: number | null;
  top_k?: number | null;
  min_p?: number | null;
  max_tokens?: number | null;
  frequency_penalty?: number | null;
  presence_penalty?: number | null;
  repetition_penalty?: number | null;
  stop?: string[] | null;
  response_format?: Record<string, unknown> | null;
  provider?: Record<string, unknown> | null;
  reasoning?: Record<string, unknown> | null;
}

export interface BlockRef {
  type: string;
  slug: string;
  version: number;
}

export interface GenerationProfile {
  id: number;
  name: string;
  slug: string;
  version: number;
  is_archived: boolean;
  description: string;
  scaffolding_slug: string;
  block_refs: BlockRef[];
  llm_config: LLMParams;
  is_system: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface Stack {
  slug: string;
  has_frontend: boolean;
  aliases: string[];
  version: number;
  name: string;
  description: string;
  is_builtin: boolean;
  default_port: number;
  patch_profile: string;
  file_count: number;
  is_approved: boolean;
  dockerfile_mode: "bundled" | "generated";
  backend_base_image: string;
  frontend_base_image: string;
  server_kind: string;
  backend_filename: string;
  frontend_component: string;
}

export interface StackDetail extends Stack {
  files: Record<string, string>;
  generated_dockerfile?: string;
}

export interface StackWritePayload {
  slug?: string;
  name: string;
  description?: string;
  has_frontend: boolean;
  default_port: number;
  patch_profile: string;
  frontend_component?: string;
  backend_filename: string;
  backend_base_image: string;
  frontend_base_image?: string;
  server_kind: string;
  files: Record<string, string>;
}

export interface StarterTemplatePackage {
  slug: string;
  name: string;
  description: string;
  app_template_count: number;
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
  const res = await apiFetch("/generation/app-specs/", {
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
  llm_params?: LLMParams;
  seed?: number | null;
}): Promise<GenerationJob> {
  const res = await apiFetch("/generation/jobs/custom/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function getContentBlocks(): Promise<ContentBlock[]> {
  const res = await apiFetch("/generation/blocks/");
  return res.json();
}

export async function getGenerationProfiles(): Promise<GenerationProfile[]> {
  const res = await apiFetch("/generation/profiles/");
  return res.json();
}

export interface ProfilePreview {
  slug?: string;
  version?: number;
  scaffolding_slug?: string;
  block_count: number;
  prompt_templates: Record<string, { system?: string; user?: string }>;
  effective_llm: LLMParams;
  rendered?: Record<string, { system: string; user: string }>;
  app_slug?: string;
}

export async function getProfilePreview(
  slug: string,
  appSlug?: string,
): Promise<ProfilePreview> {
  const qs = appSlug ? `?app_slug=${encodeURIComponent(appSlug)}` : "";
  const res = await apiFetch(`/generation/profiles/${slug}/preview/${qs}`);
  return res.json();
}

export async function previewDraftProfile(data: {
  block_refs: BlockRef[];
  app_slug?: string;
  llm_config?: LLMParams;
}): Promise<ProfilePreview> {
  const res = await apiFetch("/generation/profiles/preview-draft/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export interface ProfileSuggestion {
  app_id: number;
  app_slug: string;
  profile_id: number | null;
  slug: string | null;
  version: number | null;
  name: string | null;
  provenance: "app-pilot" | "stack-default" | "system-default" | "catalog";
}

export async function suggestProfiles(
  appIds: number[],
  stack?: string,
): Promise<ProfileSuggestion[]> {
  const params = new URLSearchParams({ app_ids: appIds.join(",") });
  if (stack) params.set("stack", stack);
  const res = await apiFetch(`/generation/profiles/suggest/?${params}`);
  return res.json();
}

export async function getProfileVersions(
  slug: string,
): Promise<GenerationProfile[]> {
  const res = await apiFetch(`/generation/profiles/${slug}/versions/`);
  return res.json();
}

export interface ProfileWritePayload {
  name: string;
  slug: string;
  description?: string;
  scaffolding_slug: string;
  block_refs: BlockRef[];
  llm_config?: LLMParams;
  is_default?: boolean;
}

export async function createGenerationProfile(
  data: ProfileWritePayload,
): Promise<GenerationProfile> {
  const res = await apiFetch("/generation/profiles/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateGenerationProfile(
  slug: string,
  data: ProfileWritePayload,
): Promise<GenerationProfile> {
  const res = await apiFetch(`/generation/profiles/${slug}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function archiveGenerationProfile(slug: string): Promise<void> {
  await apiFetch(`/generation/profiles/${slug}/`, { method: "DELETE" });
}

export async function createContentBlock(data: {
  block_type: string;
  slug: string;
  version?: number;
  name: string;
  description?: string;
  content: string;
  metadata?: Record<string, unknown>;
}): Promise<ContentBlock> {
  const res = await apiFetch("/generation/blocks/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createBlockVersion(
  slug: string,
  data: {
    content: string;
    name?: string;
    description?: string;
    metadata?: Record<string, unknown>;
  },
): Promise<ContentBlock> {
  const res = await apiFetch(`/generation/blocks/${slug}/new-version/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function exportTemplatePackage(
  data: {
    app_template_slugs?: string[];
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
  stack_slug: string;
  app_requirement_ids: number[];
  model_ids: number[];
  temperature?: number;
  max_tokens?: number;
  llm_params?: LLMParams;
  profile_id?: number | null;
  trials?: number;
}): Promise<BatchCreateResponse> {
  const res = await apiFetch("/generation/jobs/scaffolding/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteAppTemplate(slug: string): Promise<void> {
  await apiFetch(`/generation/app-specs/${slug}/`, { method: "DELETE" });
}

export async function deleteGenerationJob(
  id: string,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`/generation/jobs/${id}/`, { method: "DELETE" });
  return res.json();
}

export async function exportGenerationJob(
  id: string,
): Promise<Record<string, any>> {
  const res = await apiFetch(`/generation/jobs/${id}/export/`);
  return res.json();
}

export async function getAppTemplates(): Promise<AppRequirementTemplate[]> {
  const res = await apiFetch("/generation/app-specs/");
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

export async function getStackDetail(slug: string): Promise<StackDetail> {
  const res = await apiFetch(`/generation/stacks/${slug}/`);
  return res.json();
}

export async function getStackBaseImages(): Promise<{
  python: string[];
  node: string[];
}> {
  const res = await apiFetch("/generation/stacks/base-images/");
  return res.json();
}

export async function previewStackDockerfile(
  data: StackWritePayload,
): Promise<{ dockerfile: string }> {
  const res = await apiFetch("/generation/stacks/preview-dockerfile/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createStack(
  data: StackWritePayload,
): Promise<StackDetail> {
  const res = await apiFetch("/generation/stacks/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateStack(
  slug: string,
  data: StackWritePayload,
): Promise<StackDetail> {
  const res = await apiFetch(`/generation/stacks/${slug}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function archiveStack(slug: string): Promise<void> {
  await apiFetch(`/generation/stacks/${slug}/`, { method: "DELETE" });
}

export async function getStacks(): Promise<Stack[]> {
  const res = await apiFetch("/generation/stacks/");
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
  const res = await apiFetch(`/generation/app-specs/${slug}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return res.json();
}
