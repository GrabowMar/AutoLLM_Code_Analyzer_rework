import { apiFetch } from "./core";

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
  continuation_limit: number;
  enable_repair: boolean;
  temperature: number;
  max_tokens: number;
  top_p: number | null;
  app_requirement_ids: number[];
  condition_count: number;
  created_at: string;
  updated_at: string;
}

export interface ExperimentCondition {
  id: number;
  label: string;
  template_bundle: number;
  model: number;
  model_name: string | null;
  bundle_slug: string | null;
  bundle_version: number | null;
  param_overrides: Record<string, unknown>;
  created_at: string;
}

export interface ExperimentPreview {
  total_jobs: number;
  conditions: number;
  apps: number;
  repeats: number;
  estimated_cost: number;
  per_condition: {
    condition_id: number;
    label: string;
    model: string;
    bundle_slug: string;
    bundle_version: number;
    jobs: number;
    estimated_cost: number;
  }[];
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
  continuation_limit?: number;
  enable_repair?: boolean;
  temperature?: number;
  max_tokens?: number;
  top_p?: number | null;
}

export type UpdateExperimentPayload = Partial<CreateExperimentPayload>;

export interface CreateConditionPayload {
  template_bundle_id: number;
  model_id: number;
  label?: string;
  param_overrides?: Record<string, unknown>;
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
