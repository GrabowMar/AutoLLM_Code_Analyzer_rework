import { apiFetch } from "./core";

/**
 * Generation API client (`/api/generation/...`).
 *
 * Names that `automation.ts` already exports through the `client.ts` barrel
 * (`BatchSummary`, `listBatches`, `getBatch`, `cancelBatch`, ...) get a
 * `Generation` prefix here, otherwise `export *` reports TS2308.
 */

// ── Shared vocabulary ─────────────────────────────────────────────────

export type GenerationMode = "agent" | "scaffolding" | "custom";
export type SamplingPolicy = "provider_default" | "harness_native";
export type NetworkPolicy = "packages" | "none";
/** Persisted run phase ("" = not started / finished). */
export type JobPhase = "" | "queued" | "running" | "evaluating";

/** Optional request knobs shared by every fetch helper that may be superseded. */
export interface RequestOptions {
  /** Abort a stale request (e.g. a debounced preview superseded by newer input). */
  signal?: AbortSignal;
}

// ── App specs ─────────────────────────────────────────────────────────

export type AppSpecDifficulty = "" | "basic" | "standard" | "advanced";

export interface AppRequirementTemplate {
  id: number;
  name: string;
  slug: string;
  category: string;
  description: string;
  /** "" when the spec has no difficulty rating. */
  difficulty?: AppSpecDifficulty;
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

// ── LLM params / agent config ─────────────────────────────────────────

export interface LLMReasoning {
  effort?: string;
  max_tokens?: number;
  enabled?: boolean;
  exclude?: boolean;
  [key: string]: unknown;
}

/** Sparse OpenRouter sampling params: a missing key means "inherit". */
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
  reasoning?: LLMReasoning | null;
}

/** Agent-run budget / network overrides (missing = server default from AGENT_* settings). */
export interface AgentConfig {
  harness?: string;
  /** provider_default: the gateway strips harness-sent sampling knobs; harness_native: passes them through. */
  sampling_policy?: SamplingPolicy;
  wall_clock_seconds?: number;
  max_llm_requests?: number;
  max_total_tokens?: number;
  max_cost_usd?: number;
  network_policy?: NetworkPolicy;
}

/** Inline agent overrides on create/preview; the harness comes from presets instead. */
export type AgentConfigOverrides = Omit<AgentConfig, "harness">;

export type AgentBudgetKey =
  | "wall_clock_seconds"
  | "max_llm_requests"
  | "max_total_tokens"
  | "max_cost_usd";

// ── Harnesses ─────────────────────────────────────────────────────────

export interface AgentHarness {
  name: string;
  label: string;
  version: string;
  requires_tool_calling: boolean;
}

export type HarnessOptionValue = string | number | boolean | string[];
/** Sparse harness options: only keys that differ from the harness defaults. */
export type HarnessOptions = Record<string, HarnessOptionValue>;

export interface HarnessOptionChoice {
  value: string;
  label: string;
}

/** One tunable harness option (`Harness.option_schema()` on the backend). */
export interface HarnessOptionField {
  key: string;
  type: "enum" | "int" | "bool" | "multi_enum";
  label: string;
  help: string;
  default: HarnessOptionValue;
  /** enum / multi_enum only. */
  choices?: HarnessOptionChoice[];
  /** int only. */
  min?: number;
  max?: number;
}

/** Spec name for {@link HarnessOptionField}. */
export type OptionField = HarnessOptionField;

export interface AgentHarnessInfo extends AgentHarness {
  description: string;
  is_default: boolean;
  /** Empty for harnesses without tunable options (no "new preset" action). */
  option_schema: HarnessOptionField[];
  default_options: HarnessOptions;
}

export interface AgentLimit {
  type: "int" | "float";
  min: number;
  max: number;
}

export interface AgentConfigDefaults {
  harness: string;
  sampling_policy: SamplingPolicy;
  wall_clock_seconds: number;
  max_llm_requests: number;
  max_total_tokens: number;
  max_cost_usd: number;
  network_policy: NetworkPolicy;
}

/** `GET /agent/config/`: platform defaults, input limits and the harness catalog. */
export interface AgentConfigInfo {
  defaults: AgentConfigDefaults;
  /** Keyed by {@link AgentBudgetKey}. */
  limits: Partial<Record<string, AgentLimit>>;
  network_policies: string[];
  sampling_policies: string[];
  default_provider_routing: Record<string, unknown>;
  default_stack_slug: string;
  default_max_output_tokens: number;
  harnesses: AgentHarnessInfo[];
}

export async function getAgentConfig(): Promise<AgentConfigInfo> {
  const res = await apiFetch("/generation/agent/config/");
  return res.json();
}

export async function getAgentHarnesses(): Promise<AgentHarness[]> {
  const res = await apiFetch("/generation/agent/harnesses/");
  return res.json();
}

// ── Run presets (harness / sampling / budget) ─────────────────────────

export type PresetKind = "harness" | "sampling" | "budget";

export interface HarnessPresetConfig {
  harness: string;
  options: HarnessOptions;
}

export interface SamplingPresetConfig {
  llm_params: LLMParams;
  sampling_policy?: SamplingPolicy;
}

export interface BudgetPresetConfig {
  wall_clock_seconds?: number;
  max_llm_requests?: number;
  max_total_tokens?: number;
  max_cost_usd?: number;
  network_policy?: NetworkPolicy;
}

export interface PresetConfigByKind {
  harness: HarnessPresetConfig;
  sampling: SamplingPresetConfig;
  budget: BudgetPresetConfig;
}

export interface RunPreset<K extends PresetKind = PresetKind> {
  id: number;
  kind: K;
  slug: string;
  version: number;
  name: string;
  description: string;
  config: PresetConfigByKind[K];
  content_hash: string;
  is_system: boolean;
  is_archived: boolean;
  /** System preset that is the default for its kind (harness: per harness name). */
  is_default: boolean;
  latest_version: number;
  created_at: string;
  updated_at: string;
}

/** Preset provenance embedded in jobs, batches and previews. */
export interface PresetRef {
  id: number;
  slug: string;
  version: number;
}

export interface NamedPresetRef extends PresetRef {
  name: string;
}

export interface PresetValidation<K extends PresetKind = PresetKind> {
  ok: boolean;
  errors: string[];
  /** Normalized config (harness: default-valued options dropped); null when invalid. */
  config: PresetConfigByKind[K] | null;
}

export interface PresetWritePayload<K extends PresetKind = PresetKind> {
  name: string;
  description?: string;
  config: PresetConfigByKind[K];
}

/** PUT body: omit `config` for a metadata-only edit (same version). */
export interface PresetUpdatePayload<K extends PresetKind = PresetKind> {
  name: string;
  description?: string;
  config?: PresetConfigByKind[K];
}

/** Latest version per (kind, slug), visible = system ∪ own; defaults first. */
export async function listRunPresets<K extends PresetKind>(
  kind?: K,
  params: { include_archived?: boolean } = {},
): Promise<RunPreset<K>[]> {
  const q = new URLSearchParams();
  if (kind) q.set("kind", kind);
  if (params.include_archived) q.set("include_archived", "true");
  const qs = q.toString();
  const res = await apiFetch(`/generation/presets/${qs ? "?" + qs : ""}`);
  return res.json();
}

/** Any version (archived included) of a visible preset. */
export async function getRunPreset(id: number): Promise<RunPreset> {
  const res = await apiFetch(`/generation/presets/${id}/`);
  return res.json();
}

/** Every version of a preset, newest first. */
export async function getRunPresetVersions<K extends PresetKind>(
  kind: K,
  slug: string,
): Promise<RunPreset<K>[]> {
  const res = await apiFetch(
    `/generation/presets/${kind}/${encodeURIComponent(slug)}/versions/`,
  );
  return res.json();
}

/** Dry-run validation; 200 with `ok: false` + `errors` for invalid configs. */
export async function validateRunPreset<K extends PresetKind>(
  kind: K,
  config: PresetConfigByKind[K],
  options: RequestOptions = {},
): Promise<PresetValidation<K>> {
  const res = await apiFetch("/generation/presets/validate/", {
    method: "POST",
    body: JSON.stringify({ kind, config }),
    signal: options.signal,
  });
  return res.json();
}

export async function createRunPreset<K extends PresetKind>(
  data: PresetWritePayload<K> & { kind: K; slug: string },
): Promise<RunPreset<K>> {
  const res = await apiFetch("/generation/presets/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

/** A content change creates version n+1; a name/description edit updates in place. */
export async function updateRunPreset<K extends PresetKind>(
  kind: K,
  slug: string,
  data: PresetUpdatePayload<K>,
): Promise<RunPreset<K>> {
  const res = await apiFetch(
    `/generation/presets/${kind}/${encodeURIComponent(slug)}/`,
    { method: "PUT", body: JSON.stringify(data) },
  );
  return res.json();
}

/** Archives every version (system presets: 403). */
export async function archiveRunPreset(
  kind: PresetKind,
  slug: string,
): Promise<{ success: boolean }> {
  const res = await apiFetch(
    `/generation/presets/${kind}/${encodeURIComponent(slug)}/`,
    { method: "DELETE" },
  );
  return res.json();
}

// ── Jobs ──────────────────────────────────────────────────────────────

export interface GenerationJob {
  id: string;
  mode: string;
  status: string;
  model_name: string | null;
  model_id_str: string | null;
  /** LLMModel pk (`model_id_str` is the OpenRouter id). */
  model_pk?: number | null;
  batch_id: string | null;
  batch_name: string | null;
  app_requirement_id?: number | null;
  app_slug?: string | null;
  /** Experiment UUID, when an experiment launched the job. */
  experiment_id?: string | null;
  condition_id?: number | null;
  repeat_index?: number | null;
  template_name: string | null;
  /** @deprecated never returned by the backend; kept for legacy readers. */
  scaffolding_name?: string | null;
  bundle_name?: string | null;
  bundle_slug?: string | null;
  /** Profile pk, when the job used one. */
  profile?: number | null;
  created_by_email: string | null;
  /** Effective temperature; null for agent jobs that sampled at the provider default. */
  temperature: number | null;
  max_tokens: number;
  llm_params: LLMParams;
  custom_system_prompt: string;
  custom_user_prompt: string;
  task_description: string;
  agent_config: AgentConfig;
  /** Sparse non-default harness options the run used. */
  harness_options?: HarnessOptions;
  harness_preset?: NamedPresetRef | null;
  sampling_preset?: NamedPresetRef | null;
  budget_preset?: NamedPresetRef | null;
  phase?: JobPhase;
  /** Seed-free condition identity; replicates share it. */
  config_fingerprint?: string | null;
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
  stack_slug: string;
  prompt_hash: string;
  bundle_key: string;
  resolved_bundle: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export type JobValidity = "valid" | "warn" | "exclude";

export interface GenerationJobList {
  id: string;
  mode: string;
  status: string;
  model_name: string | null;
  model_id_str: string | null;
  template_name: string | null;
  /** @deprecated never returned by the backend; kept for legacy readers. */
  scaffolding_name?: string | null;
  stack_slug?: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string;
  created_at: string;
  batch_id?: string | null;
  harness?: string | null;
  harness_preset?: PresetRef | null;
  condition_id?: number | null;
  /** "slug@version" of the prompt profile, if any. */
  profile_ref?: string | null;
  repeat_index?: number | null;
  app_slug?: string | null;
  cost_usd?: number | null;
  eval_status?: string | null;
  pass_rate?: number | null;
  termination?: string | null;
  validity?: JobValidity | null;
  phase?: JobPhase;
  experiment_seed?: number | null;
  config_fingerprint?: string | null;
}

export interface PaginatedJobs {
  items: GenerationJobList[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface GenerationJobsQuery {
  page?: number;
  per_page?: number;
  mode?: string;
  status?: string;
  model_id?: string;
  search?: string;
  sort_by?: string;
  container_status?: string;
  batch_id?: string;
  harness?: string;
}

export async function getGenerationJobs(
  params?: GenerationJobsQuery,
): Promise<PaginatedJobs> {
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
  if (params?.batch_id) q.set("batch_id", params.batch_id);
  if (params?.harness) q.set("harness", params.harness);
  const qs = q.toString();
  const res = await apiFetch(`/generation/jobs/${qs ? "?" + qs : ""}`);
  return res.json();
}

export async function getGenerationJob(id: string): Promise<GenerationJob> {
  const res = await apiFetch(`/generation/jobs/${id}/`);
  return res.json();
}

export async function getGenerationJobsStats(): Promise<{
  total: number;
  completed: number;
  /** Includes `queued`: running jobs still waiting for a sandbox slot. */
  running: number;
  queued?: number;
  failed: number;
  pending: number;
  cancelled: number;
}> {
  const res = await apiFetch("/generation/jobs/stats/");
  return res.json();
}

/** 200 with `success: false` (+ message) when the job can no longer be cancelled. */
export async function cancelGenerationJob(
  id: string,
): Promise<{ success: boolean; status?: string; message?: string }> {
  const res = await apiFetch(`/generation/jobs/${id}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

export async function deleteGenerationJob(
  id: string,
): Promise<{ success: boolean; message?: string }> {
  const res = await apiFetch(`/generation/jobs/${id}/`, { method: "DELETE" });
  return res.json();
}

/** Failed/cancelled job → fresh job in the same batch (snapshot re-resolved, new seed). */
export async function retryGenerationJob(id: string): Promise<GenerationJob> {
  const res = await apiFetch(`/generation/jobs/${id}/retry/`, {
    method: "POST",
  });
  return res.json();
}

/** Exact reproduction: copies the snapshot and the seed into a detached job. */
export async function replayGenerationJob(id: string): Promise<GenerationJob> {
  const res = await apiFetch(`/generation/jobs/${id}/replay/`, {
    method: "POST",
  });
  return res.json();
}

/** Another trial of the same condition: copied snapshot, new seed, same batch. */
export async function rerunGenerationJob(id: string): Promise<GenerationJob> {
  const res = await apiFetch(`/generation/jobs/${id}/rerun/`, {
    method: "POST",
  });
  return res.json();
}

/** Re-run the platform evaluation (build, run, hidden conformance suite) for a finished job. */
export async function evaluateGenerationJob(
  jobId: string,
): Promise<{ status: string }> {
  const res = await apiFetch(`/generation/jobs/${jobId}/evaluate/`, {
    method: "POST",
  });
  return res.json();
}

/** URL of one agent run-record file (served only while it matches its recorded sha256). */
export function runRecordUrl(jobId: string, name: string): string {
  return `/api/generation/jobs/${jobId}/run-record/${encodeURIComponent(name)}`;
}

export async function getJobArtifacts(
  id: string,
): Promise<GenerationArtifact[]> {
  const res = await apiFetch(`/generation/jobs/${id}/artifacts/`);
  return res.json();
}

export async function exportGenerationJob(
  id: string,
): Promise<Record<string, any>> {
  const res = await apiFetch(`/generation/jobs/${id}/export/`);
  return res.json();
}

export async function getCopilotIterations(
  id: string,
): Promise<CopilotIteration[]> {
  const res = await apiFetch(`/generation/jobs/${id}/copilot-iterations/`);
  return res.json();
}

// ── Job creation: agent (primary) ─────────────────────────────────────

/** Body of `POST /jobs/agent/` and `POST /jobs/agent/preview/`. */
export interface AgentBatchRequest {
  /** null/omitted → profile stack → platform default (`GET /agent/config/`). */
  stack_slug?: string | null;
  app_requirement_ids?: number[];
  /** Free-form task without app specs, or extra context added to every spec. */
  description?: string;
  model_ids: number[];
  /** The comparison axis: one job per preset per (task, trial, model) cell. */
  harness_preset_ids?: number[];
  /** Legacy harness names → each harness's system default preset. Not with harness_preset_ids. */
  harnesses?: string[];
  /** null → sampling/provider-default. */
  sampling_preset_id?: number | null;
  /** null → budget/platform-default. */
  budget_preset_id?: number | null;
  /** Inline overrides (may include `provider` routing). */
  llm_params?: LLMParams;
  /** Inline overrides; never `harness` (use presets). */
  agent_config?: AgentConfigOverrides;
  /** Legacy lowest llm layer (256..200000). */
  max_tokens?: number | null;
  profile_id?: number | null;
  trials?: number;
  base_seed?: number | null;
}

/**
 * 400 body of `POST /jobs/agent/` when the plan has blocking issues: `detail`
 * joins every message; `errors` lists them like preview warnings.
 */
export interface AgentRequestError {
  detail: string;
  code?: string;
  errors?: AgentPreviewWarning[];
}

/** Agent mode: tasks × models × harness presets × trials, each run in a sandbox. */
export async function createAgentBatch(
  data: AgentBatchRequest,
): Promise<BatchCreateResponse> {
  const res = await apiFetch("/generation/jobs/agent/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export type PreviewSeverity = "error" | "warn" | "info";

/**
 * Known preview codes. errors: no_tasks, no_models, models_without_tools,
 * missing_api_key, duplicate_harness_config, invalid_preset,
 * reasoning_exceeds_output_cap, resolution_failed. warns: params_dropped,
 * harness_native_sampling, context_window_fallback, cost_cap_may_bind,
 * wall_clock_may_bind, whole_file_small_output, large_batch, ungraded_task.
 * infos: non_default_harness_options, budget_changes_brief, single_trial,
 * random_seeds.
 */
export type AgentPreviewCode =
  | "no_tasks"
  | "no_models"
  | "models_without_tools"
  | "missing_api_key"
  | "duplicate_harness_config"
  | "invalid_preset"
  | "reasoning_exceeds_output_cap"
  | "resolution_failed"
  | "params_dropped"
  | "harness_native_sampling"
  | "context_window_fallback"
  | "cost_cap_may_bind"
  | "wall_clock_may_bind"
  | "whole_file_small_output"
  | "large_batch"
  | "ungraded_task"
  | "non_default_harness_options"
  | "budget_changes_brief"
  | "single_trial"
  | "random_seeds";

export interface AgentPreviewWarning {
  /** One of {@link AgentPreviewCode}; unknown codes are possible from newer backends. */
  code: AgentPreviewCode | (string & {});
  severity: PreviewSeverity;
  detail: string;
  model_id?: number | null;
}

/** Where an effective llm value came from (agent preview). */
export type AgentLLMSource = "max_tokens" | "profile" | "preset" | "inline";

/** Effective llm params and where each key came from. */
export interface AgentPreviewLLM {
  effective: LLMParams;
  sources: Record<string, AgentLLMSource | (string & {})>;
}

/** Flat variant (`{...effective, sources}`) accepted by {@link splitPreviewLLMParams}. */
export type AgentPreviewLLMParams = LLMParams & {
  sources?: Record<string, AgentLLMSource>;
};

export interface AgentPreviewHarness {
  /** null only for a legacy harness name without a system preset. */
  preset: NamedPresetRef | null;
  harness: string;
  label: string;
  version: string;
  requires_tool_calling: boolean;
  effective_options: HarnessOptions;
  non_default_options: string[];
}

export interface AgentPreviewModel {
  model_id: number;
  /** OpenRouter model id. */
  model_slug?: string;
  name: string;
  provider: string;
  supports_tools: boolean;
  /** False when a selected harness needs tool calling the model lacks. */
  tool_harness_ok: boolean;
  context_window: number | null;
  context_window_source: "catalog" | "fallback";
  /** Effective per-call output cap (min of the llm max_tokens and the model cap). */
  output_cap: number | null;
  forced_params: Record<string, unknown>;
  /** Params the model does not support, with the reason (seed never listed). */
  dropped_params: Record<string, string>;
  seed_forwarded: boolean;
  /** "unknown": the catalog lists no supported parameters, so params are forwarded unverified. */
  params_support?: "catalog" | "unknown";
}

export interface AgentPreview {
  run_count: number;
  task_count: number;
  model_count: number;
  harness_count: number;
  trials: number;
  stack_slug: string;
  /** False for a free-form task without app specs. */
  graded: boolean;
  /** Effective budget / network / sampling policy (no harness). */
  agent_config: Omit<AgentConfigDefaults, "harness"> & Record<string, unknown>;
  /** key → "default" | "preset" | "inline" (where each value came from). */
  agent_config_sources?: Record<string, string>;
  llm_params: AgentPreviewLLM;
  /** Resolved batch-wide presets. */
  presets?: { sampling: NamedPresetRef | null; budget: NamedPresetRef | null };
  harnesses: AgentPreviewHarness[];
  models: AgentPreviewModel[];
  /** Upper bound: sum of the USD cap over every run. */
  max_cost_usd_total: number;
  warnings: AgentPreviewWarning[];
}

/** Same body as create; resolves everything without writing. 4xx = invalid payload. */
export async function previewAgentBatch(
  data: AgentBatchRequest,
  options: RequestOptions = {},
): Promise<AgentPreview> {
  const res = await apiFetch("/generation/jobs/agent/preview/", {
    method: "POST",
    body: JSON.stringify(data),
    signal: options.signal,
  });
  return res.json();
}

/**
 * Normalize the preview's llm block to `{effective, sources}`. Accepts the flat
 * `{...effective, sources}` form too, so callers don't depend on the wire shape.
 */
export function splitPreviewLLMParams(
  block:
    | AgentPreviewLLMParams
    | { effective: LLMParams; sources?: Record<string, string> }
    | null
    | undefined,
): { effective: LLMParams; sources: Record<string, string> } {
  if (!block) return { effective: {}, sources: {} };
  if (
    "effective" in block &&
    block.effective &&
    typeof block.effective === "object"
  ) {
    return { effective: block.effective, sources: block.sources ?? {} };
  }
  const { sources, ...effective } = block as AgentPreviewLLMParams;
  return { effective, sources: sources ?? {} };
}

// ── Job creation: scaffolding / single prompt ─────────────────────────

/** Where an effective llm value came from (scaffolding / single prompt). */
export type LLMValueSource = "mode_default" | "profile" | "preset" | "inline";

export interface LLMPreviewRequest {
  mode: "scaffolding" | "custom";
  profile_id?: number | null;
  sampling_preset_id?: number | null;
  llm_params?: LLMParams;
}

export interface LLMPreviewResult {
  effective: LLMParams;
  sources: Record<string, LLMValueSource>;
}

/** Effective llm params for the composer (base = mode default 0.3 / 32000). */
export async function previewEffectiveLLMParams(
  data: LLMPreviewRequest,
  options: RequestOptions = {},
): Promise<LLMPreviewResult> {
  const res = await apiFetch("/generation/jobs/llm-preview/", {
    method: "POST",
    body: JSON.stringify(data),
    signal: options.signal,
  });
  return res.json();
}

export async function createCustomJob(data: {
  model_id: number;
  system_prompt: string;
  user_prompt: string;
  temperature?: number;
  max_tokens?: number;
  sampling_preset_id?: number | null;
  llm_params?: LLMParams;
  seed?: number | null;
}): Promise<GenerationJob> {
  const res = await apiFetch("/generation/jobs/custom/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createScaffoldingBatch(data: {
  /** Optional; must match the profile's stack when both are given (400 otherwise). */
  stack_slug?: string | null;
  app_requirement_ids: number[];
  model_ids: number[];
  temperature?: number;
  max_tokens?: number;
  sampling_preset_id?: number | null;
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

// ── Batches ───────────────────────────────────────────────────────────

export type GenerationBatchStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export interface GenerationBatchCounts {
  pending: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface GenerationBatchSummary {
  id: string;
  name: string;
  mode: string;
  /** Derived from live job counts. */
  status: GenerationBatchStatus | (string & {});
  /** Jobs actually created (retries increment it). */
  total_jobs: number;
  counts: GenerationBatchCounts;
  /** No pending, queued or running jobs left. */
  is_terminal: boolean;
  created_at: string;
  updated_at: string;
  stack_slug: string | null;
  harnesses: string[];
  harness_presets: PresetRef[];
  model_count: number;
  app_count: number;
  trials: number | null;
  base_seed: number | null;
  cost_usd: number;
  graded: boolean;
  mean_pass_rate: number | null;
  experiment: { id: string; name: string } | null;
  /** What the batch was created from; {} for batches that predate it. */
  request?: GenerationBatchRequest;
}

/**
 * `GenerationBatch.request`: the create body as the server resolved it (preset
 * ids included). Every key is optional: older batches lack some or all of them.
 */
export interface GenerationBatchRequest {
  mode?: string;
  base_seed?: number | null;
  order_seed?: number | null;
  trials?: number | null;
  stack_slug?: string | null;
  app_requirement_ids?: number[];
  description?: string;
  model_ids?: number[];
  profile_id?: number | null;
  harnesses?: string[];
  harness_preset_ids?: number[];
  sampling_preset_id?: number | null;
  budget_preset_id?: number | null;
  llm_params?: LLMParams;
  agent_config?: Record<string, unknown>;
  max_tokens?: number | null;
  temperature?: number | null;
  experiment_id?: string;
}

export interface PaginatedGenerationBatches {
  items: GenerationBatchSummary[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface GenerationBatchesQuery {
  page?: number;
  per_page?: number;
  mode?: string;
  /** true: only experiment batches; false: only ad-hoc; omitted: both. */
  experiment?: boolean;
}

export async function listGenerationBatches(
  params: GenerationBatchesQuery = {},
): Promise<PaginatedGenerationBatches> {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.per_page) q.set("per_page", String(params.per_page));
  if (params.mode) q.set("mode", params.mode);
  if (params.experiment !== undefined)
    q.set("experiment", String(params.experiment));
  const qs = q.toString();
  const res = await apiFetch(`/generation/batches/${qs ? "?" + qs : ""}`);
  return res.json();
}

export async function getGenerationBatch(
  id: string,
): Promise<GenerationBatchSummary> {
  const res = await apiFetch(`/generation/batches/${id}/`);
  return res.json();
}

export async function getGenerationBatchJobs(
  id: string,
): Promise<GenerationJobList[]> {
  const res = await apiFetch(`/generation/batches/${id}/jobs/`);
  return res.json();
}

/** Cancels every pending/queued/running job in the batch. */
export async function cancelGenerationBatch(
  id: string,
): Promise<{ cancelled: number }> {
  const res = await apiFetch(`/generation/batches/${id}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

// ── Profiles & blocks ─────────────────────────────────────────────────

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

export interface ProfileSuggestion {
  app_id: number;
  app_slug: string;
  profile_id: number | null;
  slug: string | null;
  version: number | null;
  name: string | null;
  provenance: "app-pilot" | "stack-default" | "system-default" | "catalog";
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

export async function getContentBlocks(): Promise<ContentBlock[]> {
  const res = await apiFetch("/generation/blocks/");
  return res.json();
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

export async function getGenerationProfiles(): Promise<GenerationProfile[]> {
  const res = await apiFetch("/generation/profiles/");
  return res.json();
}

export async function getProfileVersions(
  slug: string,
): Promise<GenerationProfile[]> {
  const res = await apiFetch(`/generation/profiles/${slug}/versions/`);
  return res.json();
}

export async function getProfilePreview(
  slug: string,
  appSlug?: string,
  options: RequestOptions = {},
): Promise<ProfilePreview> {
  const qs = appSlug ? `?app_slug=${encodeURIComponent(appSlug)}` : "";
  const res = await apiFetch(`/generation/profiles/${slug}/preview/${qs}`, {
    signal: options.signal,
  });
  return res.json();
}

export async function previewDraftProfile(
  data: {
    block_refs: BlockRef[];
    app_slug?: string;
    llm_config?: LLMParams;
  },
  options: RequestOptions = {},
): Promise<ProfilePreview> {
  const res = await apiFetch("/generation/profiles/preview-draft/", {
    method: "POST",
    body: JSON.stringify(data),
    signal: options.signal,
  });
  return res.json();
}

export async function suggestProfiles(
  appIds: number[],
  stack?: string,
  options: RequestOptions = {},
): Promise<ProfileSuggestion[]> {
  const params = new URLSearchParams({ app_ids: appIds.join(",") });
  if (stack) params.set("stack", stack);
  const res = await apiFetch(`/generation/profiles/suggest/?${params}`, {
    signal: options.signal,
  });
  return res.json();
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

// ── App specs (CRUD) ──────────────────────────────────────────────────

export async function getAppTemplates(): Promise<AppRequirementTemplate[]> {
  const res = await apiFetch("/generation/app-specs/");
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

export async function deleteAppTemplate(slug: string): Promise<void> {
  await apiFetch(`/generation/app-specs/${slug}/`, { method: "DELETE" });
}

// ── Stacks ────────────────────────────────────────────────────────────

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
  /** Builtin stacks are always usable; a user stack needs approval except for its owner. */
  is_approved: boolean;
  /** The requester created it (only then may they edit or archive it). */
  is_own?: boolean;
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

/** Every stack the user may run: builtin, approved, or their own (approved or not). */
export async function getStacks(): Promise<Stack[]> {
  const res = await apiFetch("/generation/stacks/");
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

// ── Template packages ─────────────────────────────────────────────────

export interface StarterTemplatePackage {
  slug: string;
  name: string;
  description: string;
  app_spec_count?: number;
  block_count: number;
  profile_count?: number;
  stack_count?: number;
  preset_count?: number;
  /** Legacy alias of app_spec_count. */
  app_template_count: number;
  /** Legacy alias of profile_count. */
  bundle_count: number;
}

export interface PresetSlugRef {
  kind: PresetKind;
  slug: string;
}

export type PackageConflictStrategy = "rename" | "overwrite" | "error";

/** Created/reused asset names per section (app_specs, blocks, profiles, stacks, presets). */
export type TemplatePackageImportResult = Record<string, string[]>;

export async function exportTemplatePackage(
  data: {
    app_template_slugs?: string[];
    bundle_slugs?: string[];
    block_refs?: { type: string; slug: string; version: number }[];
    stack_slugs?: string[];
    /** Latest version of each referenced preset (v3 packages). */
    preset_refs?: PresetSlugRef[];
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
  conflict_strategy?: PackageConflictStrategy;
}): Promise<TemplatePackageImportResult> {
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
    conflict_strategy?: PackageConflictStrategy;
  } = {},
): Promise<TemplatePackageImportResult> {
  const res = await apiFetch(`/generation/packages/starters/${slug}/import/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}
