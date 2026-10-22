import { apiFetch } from "./core";
import type { AgentConfig, LLMParams, NamedPresetRef } from "./generation";

/** agent: cells run the sandboxed coding-agent harness; scaffolding: template prompts. */
export type ExperimentMode = "agent" | "scaffolding";

export type ExperimentStatus = "draft" | "running" | "completed" | "archived";

export interface Experiment {
  id: string;
  name: string;
  slug: string;
  description: string;
  hypothesis: string;
  status: ExperimentStatus;
  repeats: number;
  base_seed: number | null;
  mode: ExperimentMode;
  agent_config: AgentConfig;
  continuation_limit: number;
  enable_repair: boolean;
  llm_defaults: LLMParams;
  /** Experiment-wide stack; null (legacy) = each condition's profile stack. */
  stack_slug?: string | null;
  /** Sampling preset pk (experiment-wide). */
  sampling_preset?: number | null;
  /** Budget preset pk (experiment-wide). */
  budget_preset?: number | null;
  /** The pinned sampling / budget preset versions (null = system default at launch, agent mode). */
  sampling_preset_ref?: NamedPresetRef | null;
  budget_preset_ref?: NamedPresetRef | null;
  app_requirement_ids: number[];
  condition_count: number;
  created_at: string;
  updated_at: string;
}

export interface ExperimentCondition {
  id: number;
  label: string;
  /** Profile pk; optional (null) for agent-mode conditions. */
  profile: number | null;
  model: number;
  /** Agent harness for agent-mode experiments ("" = experiment default). */
  harness: string;
  /** Harness preset pk; when set, `harness` is the preset's harness name. */
  harness_preset?: number | null;
  harness_preset_ref?: NamedPresetRef | null;
  model_name: string | null;
  bundle_slug: string | null;
  bundle_version: number | null;
  param_overrides: LLMParams;
  created_at: string;
}

export interface ExperimentLaunchError {
  code: string;
  detail: string;
  condition_id: number;
}

export interface ExperimentPreview {
  total_jobs: number;
  /** Cells a launch would create now (failed / cancelled cells are re-run). */
  pending_jobs?: number;
  conditions: number;
  apps: number;
  repeats: number;
  estimated_cost: number;
  /** budget_upper_bound for agent mode (budget cap × jobs), heuristic for scaffolding. */
  cost_estimate_kind?: "budget_upper_bound" | "heuristic";
  mode?: ExperimentMode;
  /** Experiment-wide stack; null = each condition's profile stack (legacy). */
  stack_slug?: string | null;
  stacks?: string[];
  /** Effective presets, system defaults included. */
  presets?: { sampling: NamedPresetRef | null; budget: NamedPresetRef | null };
  /** Effective experiment-wide agent config (agent mode; harness excluded). */
  agent_config?: AgentConfig | null;
  per_condition: {
    condition_id: number;
    label: string;
    /** Model display name. */
    model: string;
    /** Model slug. */
    model_id?: string;
    harness?: string;
    harness_preset?: NamedPresetRef | null;
    /** "slug@v" or null. */
    profile?: string | null;
    bundle_slug: string | null;
    bundle_version: number | null;
    stack_slug?: string;
    jobs: number;
    estimated_cost: number;
  }[];
  /** Agent mode: what each condition will actually run with, and where conditions differ. */
  comparability?: ExperimentComparability | null;
  /** Problems that make a launch refuse (fix them first). */
  launch_errors?: ExperimentLaunchError[];
}

export interface ExperimentComparabilityRow {
  condition_id: number;
  label: string;
  /** Model slug. */
  model: string;
  model_name: string;
  harness: string;
  harness_version: string;
  harness_preset: NamedPresetRef | null;
  /** Non-default options only. */
  harness_options: Record<string, unknown>;
  sampling_policy: string;
  /** Explicit params the gateway sends (seed, provider and max_tokens excluded). */
  forced_params: Record<string, unknown>;
  output_cap: number | null;
  /** "default" or the explicit routing dict. */
  provider_routing: "default" | Record<string, unknown>;
  seed_forwarded: boolean;
  dropped_params: Record<string, string>;
  context_window: number;
  context_window_source: "catalog" | "fallback";
  stack_slug: string;
  /** "slug@v" or null. */
  profile: string | null;
  param_overrides: Record<string, unknown>;
  /** Every problem, blocking or not. */
  problems: string[];
  /** The blocking subset of `problems`. */
  errors?: string[];
}

export interface ExperimentComparability {
  conditions: ExperimentComparabilityRow[];
  differs_on: string[];
  has_problems: boolean;
  has_errors?: boolean;
}

export interface ExperimentBatch {
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

export interface ExperimentStatusReport {
  status: ExperimentStatus;
  total_cells: number;
  jobs_created: number;
  by_status: Record<string, number>;
  running_cost: number;
  grid: {
    condition_id: number;
    app_id: number;
    runs: { job_id: string; repeat_index: number; status: string }[];
  }[];
}

export interface CreateExperimentPayload {
  name: string;
  slug: string;
  description?: string;
  hypothesis?: string;
  app_requirement_ids?: number[];
  repeats?: number;
  base_seed?: number | null;
  mode?: ExperimentMode;
  stack_slug?: string | null;
  sampling_preset_id?: number | null;
  budget_preset_id?: number | null;
  agent_config?: AgentConfig;
  continuation_limit?: number;
  enable_repair?: boolean;
  llm_defaults?: LLMParams;
}

export type UpdateExperimentPayload = Partial<CreateExperimentPayload>;

export interface CreateConditionPayload {
  /** Required for scaffolding conditions; optional for agent conditions. */
  profile_id?: number | null;
  model_id: number;
  harness?: string;
  /** Agent conditions: the harness preset (sets `harness` server-side). */
  harness_preset_id?: number | null;
  label?: string;
  param_overrides?: LLMParams;
}

export async function listExperiments(): Promise<Experiment[]> {
  const res = await apiFetch("/generation/experiments/");
  return res.json();
}

export async function getExperiment(id: string): Promise<Experiment> {
  const res = await apiFetch(`/generation/experiments/${id}/`);
  return res.json();
}

export async function createExperiment(
  data: CreateExperimentPayload,
): Promise<Experiment> {
  const res = await apiFetch("/generation/experiments/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateExperiment(
  id: string,
  data: UpdateExperimentPayload,
): Promise<Experiment> {
  const res = await apiFetch(`/generation/experiments/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteExperiment(
  id: string,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`/generation/experiments/${id}/`, {
    method: "DELETE",
  });
  return res.json();
}

export async function archiveExperiment(id: string): Promise<Experiment> {
  const res = await apiFetch(`/generation/experiments/${id}/archive/`, {
    method: "POST",
  });
  return res.json();
}

export async function listConditions(
  experimentId: string,
): Promise<ExperimentCondition[]> {
  const res = await apiFetch(
    `/generation/experiments/${experimentId}/conditions/`,
  );
  return res.json();
}

export async function createCondition(
  experimentId: string,
  data: CreateConditionPayload,
): Promise<ExperimentCondition> {
  const res = await apiFetch(
    `/generation/experiments/${experimentId}/conditions/`,
    {
      method: "POST",
      body: JSON.stringify(data),
    },
  );
  return res.json();
}

export async function deleteCondition(
  experimentId: string,
  conditionId: number,
): Promise<{ success: boolean }> {
  const res = await apiFetch(
    `/generation/experiments/${experimentId}/conditions/${conditionId}/`,
    { method: "DELETE" },
  );
  return res.json();
}

export async function previewExperiment(
  id: string,
): Promise<ExperimentPreview> {
  const res = await apiFetch(`/generation/experiments/${id}/preview/`, {
    method: "POST",
  });
  return res.json();
}

export async function launchExperiment(id: string): Promise<ExperimentBatch> {
  const res = await apiFetch(`/generation/experiments/${id}/launch/`, {
    method: "POST",
  });
  return res.json();
}

export async function getExperimentStatus(
  id: string,
): Promise<ExperimentStatusReport> {
  const res = await apiFetch(`/generation/experiments/${id}/status/`);
  return res.json();
}

export async function exportExperiment(
  id: string,
): Promise<Record<string, unknown>> {
  const res = await apiFetch(`/generation/experiments/${id}/export/`);
  return res.json();
}
