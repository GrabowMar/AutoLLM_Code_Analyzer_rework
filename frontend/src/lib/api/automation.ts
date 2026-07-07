import { apiFetch } from "./core";

export interface BatchDetail extends BatchSummary {
  items: {
    id: string;
    batch_id: string;
    pipeline_run_id: string | null;
    status: string;
    params: Record<string, unknown>;
    created_at: string;
  }[];
}

export interface BatchSummary {
  id: string;
  owner_id: number;
  name: string;
  description: string;
  config: Record<string, unknown>;
  status: string;
  created_at: string;
}

export interface PaginatedBatches {
  items: BatchSummary[];
  total: number;
  page: number;
  pages: number;
}

export interface PaginatedPipelines {
  items: PipelineListItem[];
  total: number;
  page: number;
  pages: number;
}

export interface PaginatedRuns {
  items: PipelineRunListItem[];
  total: number;
  page: number;
  pages: number;
}

export interface PaginatedSchedules {
  items: ScheduleSummary[];
  total: number;
  page: number;
  pages: number;
}

export interface PipelineDetail extends PipelineListItem {
  config: Record<string, unknown>;
  steps: PipelineStep[];
}

export interface PipelineListItem {
  id: string;
  owner_id: number;
  name: string;
  description: string;
  version: number;
  status: string;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface PipelineRunDetail extends PipelineRunListItem {
  result_summary: Record<string, unknown>;
  step_runs: PipelineStepRun[];
}

export interface PipelineRunListItem {
  id: string;
  pipeline_id: string;
  triggered_by_id: number | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  error: string;
  params: Record<string, unknown>;
  created_at: string;
}

export interface PipelineStep {
  id: string;
  pipeline_id: string;
  order: number;
  name: string;
  kind: string;
  config: Record<string, unknown>;
  depends_on: string[];
}

export interface PipelineStepRun {
  id: string;
  run_id: string;
  step_id: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  output: Record<string, unknown>;
  error: string;
  attempt: number;
  retries_remaining: number;
  created_at: string;
}

export interface ScheduleSummary {
  id: string;
  pipeline_id: string;
  owner_id: number;
  cron_expression: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
}

export async function cancelBatch(batchId: string): Promise<BatchDetail> {
  const res = await apiFetch(`/automation/batches/${batchId}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

export async function cancelRun(id: string): Promise<PipelineRunDetail> {
  const res = await apiFetch(`/automation/runs/${id}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

export async function clonePipeline(
  id: string,
  newName: string,
): Promise<PipelineDetail> {
  const res = await apiFetch(`/automation/pipelines/${id}/clone/`, {
    method: "POST",
    body: JSON.stringify({ new_name: newName }),
  });
  return res.json();
}

export async function createBatch(data: {
  pipeline_id: string;
  name: string;
  description?: string;
  matrix?: Record<string, unknown>;
  repeats?: number;
}): Promise<BatchDetail> {
  const res = await apiFetch("/automation/batches/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createPipeline(data: {
  name: string;
  description?: string;
  status?: string;
  config?: Record<string, unknown>;
  tags?: string[];
}): Promise<PipelineDetail> {
  const res = await apiFetch("/automation/pipelines/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function createSchedule(data: {
  pipeline_id: string;
  cron_expression: string;
  enabled?: boolean;
}): Promise<ScheduleSummary> {
  const res = await apiFetch("/automation/schedules/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deletePipeline(id: string): Promise<void> {
  await apiFetch(`/automation/pipelines/${id}/`, { method: "DELETE" });
}

export async function deleteSchedule(id: string): Promise<void> {
  await apiFetch(`/automation/schedules/${id}/`, { method: "DELETE" });
}

export async function getBatch(id: string): Promise<BatchDetail> {
  const res = await apiFetch(`/automation/batches/${id}/`);
  return res.json();
}

export async function getPipeline(id: string): Promise<PipelineDetail> {
  const res = await apiFetch(`/automation/pipelines/${id}/`);
  return res.json();
}

export async function getRun(id: string): Promise<PipelineRunDetail> {
  const res = await apiFetch(`/automation/runs/${id}/`);
  return res.json();
}

export async function getRunLogs(runId: string): Promise<{ logs: string }> {
  const res = await apiFetch(`/automation/runs/${runId}/logs/`);
  return res.json();
}

export async function listBatches(page = 1): Promise<PaginatedBatches> {
  const res = await apiFetch(`/automation/batches/?page=${page}`);
  return res.json();
}

export async function listPipelineRuns(
  pipelineId: string,
  page = 1,
): Promise<PaginatedRuns> {
  const res = await apiFetch(
    `/automation/pipelines/${pipelineId}/runs/?page=${page}`,
  );
  return res.json();
}

export async function listPipelines(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  tag?: string;
  search?: string;
  owner_me?: boolean;
}): Promise<PaginatedPipelines> {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.per_page) q.set("per_page", String(params.per_page));
  if (params?.status) q.set("status", params.status);
  if (params?.tag) q.set("tag", params.tag);
  if (params?.search) q.set("search", params.search);
  if (params?.owner_me) q.set("owner_me", "true");
  const res = await apiFetch(`/automation/pipelines/?${q}`);
  return res.json();
}

export async function listSchedules(page = 1): Promise<PaginatedSchedules> {
  const res = await apiFetch(`/automation/schedules/?page=${page}`);
  return res.json();
}

export async function retryRun(runId: string): Promise<PipelineRunDetail> {
  const res = await apiFetch(`/automation/runs/${runId}/retry/`, {
    method: "POST",
  });
  return res.json();
}

export async function setScheduleEnabled(
  id: string,
  enabled: boolean,
): Promise<ScheduleSummary> {
  const res = await apiFetch(
    `/automation/schedules/${id}/enabled/?enabled=${enabled}`,
    {
      method: "PATCH",
    },
  );
  return res.json();
}

export async function triggerPipelineRun(
  id: string,
  params?: Record<string, unknown>,
): Promise<PipelineRunDetail> {
  const res = await apiFetch(`/automation/pipelines/${id}/runs/`, {
    method: "POST",
    body: JSON.stringify({ params: params ?? {} }),
  });
  return res.json();
}

export async function updatePipeline(
  id: string,
  data: Partial<{
    name: string;
    description: string;
    status: string;
    config: Record<string, unknown>;
    tags: string[];
    version: number;
  }>,
): Promise<PipelineDetail> {
  const res = await apiFetch(`/automation/pipelines/${id}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return res.json();
}
