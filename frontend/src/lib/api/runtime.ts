import { apiFetch } from "./core";

export type ActionStatus = "pending" | "running" | "completed" | "failed";
export type ActionType = "build" | "start" | "stop" | "restart" | "remove";
export type ContainerStatus =
  | "pending"
  | "building"
  | "running"
  | "stopped"
  | "failed"
  | "removed";

export interface ContainerAction {
  id: string;
  container_id: string;
  action_type: ActionType;
  status: ActionStatus;
  progress_percent: number;
  log_output: string;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export interface ContainerHealthResponse {
  container_id: string;
  health: string;
  status: ContainerStatus;
}

export interface ContainerInstance {
  id: string;
  generation_job_id: string | null;
  name: string;
  image: string;
  container_id: string;
  status: ContainerStatus;
  app_port: number | null;
  health_status: string;
  last_health_check: string | null;
  config: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  last_error: string;
  created_by_id: number | null;
}

export interface DockerInfo {
  daemon_available: boolean;
  version: string | null;
  containers_running: number | null;
  containers_stopped: number | null;
  images: number | null;
  os: string | null;
}

export interface GenericResponse {
  success: boolean;
  message: string;
  action_id: string | null;
}

export interface PaginatedContainers {
  containers: ContainerInstance[];
  pagination: { total: number; page: number; per_page: number; pages: number };
}

export async function buildContainerForJob(
  jobId: string,
): Promise<GenericResponse> {
  const res = await apiFetch(`/runtime/jobs/${jobId}/build/`, {
    method: "POST",
  });
  return res.json();
}

export async function getAction(actionId: string): Promise<ContainerAction> {
  const res = await apiFetch(`/runtime/actions/${actionId}/`);
  return res.json();
}

export async function getContainer(id: string): Promise<ContainerInstance> {
  const res = await apiFetch(`/runtime/containers/${id}/`);
  return res.json();
}

export async function getContainerActions(
  id: string,
): Promise<ContainerAction[]> {
  const res = await apiFetch(`/runtime/actions/?container_id=${id}`);
  return res.json();
}

export async function getContainerHealth(
  id: string,
): Promise<ContainerHealthResponse> {
  const res = await apiFetch(`/runtime/containers/${id}/health/`);
  return res.json();
}

export async function getContainerLogs(
  id: string,
  tail = 200,
): Promise<{ logs: string }> {
  const res = await apiFetch(`/runtime/containers/${id}/logs/?tail=${tail}`);
  const body = await res.json();
  if (typeof body === "string") return { logs: body };
  return body as { logs: string };
}

export interface ContainerInspect {
  image: string;
  command: string[];
  state: string;
  started_at: string;
  finished_at: string;
  env: Record<string, string>;
  mounts: Array<Record<string, unknown>>;
  ports: Record<string, unknown>;
  error: string;
}

export interface ContainerExecResult {
  action: string;
  cmd: string[];
  exit_code: number;
  output: string;
  error: string;
}

export async function inspectContainer(id: string): Promise<ContainerInspect> {
  const res = await apiFetch(`/runtime/containers/${id}/inspect/`);
  return res.json();
}

export async function execContainer(
  id: string,
  action: string,
): Promise<ContainerExecResult> {
  const res = await apiFetch(`/runtime/containers/${id}/exec/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  return res.json();
}

export async function getContainers(
  params: {
    page?: number;
    per_page?: number;
    status?: ContainerStatus;
    job_id?: string;
  } = {},
): Promise<PaginatedContainers> {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.per_page) q.set("per_page", String(params.per_page));
  if (params.status) q.set("status", params.status);
  if (params.job_id) q.set("job_id", params.job_id);
  const qs = q.toString();
  const res = await apiFetch(`/runtime/containers/${qs ? "?" + qs : ""}`);
  return res.json();
}

export async function getDockerInfo(): Promise<DockerInfo> {
  const res = await apiFetch("/runtime/docker/info/");
  return res.json();
}

export async function removeContainer(id: string): Promise<GenericResponse> {
  const res = await apiFetch(`/runtime/containers/${id}/remove/`, {
    method: "POST",
  });
  return res.json();
}

export async function restartContainer(id: string): Promise<GenericResponse> {
  const res = await apiFetch(`/runtime/containers/${id}/restart/`, {
    method: "POST",
  });
  return res.json();
}

export async function startContainer(id: string): Promise<GenericResponse> {
  const res = await apiFetch(`/runtime/containers/${id}/start/`, {
    method: "POST",
  });
  return res.json();
}

export async function stopContainer(id: string): Promise<GenericResponse> {
  const res = await apiFetch(`/runtime/containers/${id}/stop/`, {
    method: "POST",
  });
  return res.json();
}
