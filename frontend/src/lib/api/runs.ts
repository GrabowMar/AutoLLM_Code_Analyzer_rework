// Client for the container/tool-shop analysis API (`/analysis/runs/`).
// The legacy `analysis.ts` (tasks/profiles/analyzers) targets removed endpoints
// and is kept only for the not-yet-migrated applications/workflow views.
import { apiFetch } from "./core";

export interface RunToolResult {
  id: string;
  tool_slug: string;
  category: string;
  status: string;
  // e.g. { severity_counts: { high: 2, ... }, total: 3 }
  summary: Record<string, any>;
  error_message: string;
}

export interface AnalysisRunDetail {
  id: string;
  name: string;
  status: string;
  tool_slugs: string[];
  // e.g. { total_findings: 5, severity_counts: { high: 2 }, tools_run: 3 }
  summary: Record<string, any>;
  error_message: string;
  generation_job_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  results: RunToolResult[];
}

export interface AnalysisRunListItem {
  id: string;
  name: string;
  status: string;
  tool_slugs: string[];
  summary: Record<string, any>;
  created_at: string;
}

export interface RunFinding {
  id: string;
  severity: string;
  category: string;
  confidence: string;
  title: string;
  description: string;
  suggestion: string;
  file_path: string;
  line_number: number | null;
  column_number: number | null;
  code_snippet: string;
  rule_id: string;
  tool_slug: string;
}

export interface PaginatedRuns {
  items: AnalysisRunListItem[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface PaginatedRunFindings {
  items: RunFinding[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface CreateRunPayload {
  name?: string;
  tool_slugs: string[];
  generation_job_id?: string | null;
  source_code?: Record<string, string> | null;
  auto_start?: boolean;
}

export async function listRuns(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  generation_job_id?: string;
}): Promise<PaginatedRuns> {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.status) sp.set("status", params.status);
  if (params?.generation_job_id)
    sp.set("generation_job_id", params.generation_job_id);
  const qs = sp.toString();
  const res = await apiFetch(`/analysis/runs/${qs ? "?" + qs : ""}`);
  return res.json();
}

export async function getRun(runId: string): Promise<AnalysisRunDetail> {
  const res = await apiFetch(`/analysis/runs/${runId}/`);
  return res.json();
}

export async function createRun(
  payload: CreateRunPayload,
): Promise<AnalysisRunDetail> {
  const res = await apiFetch("/analysis/runs/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function cancelRun(runId: string): Promise<AnalysisRunDetail> {
  const res = await apiFetch(`/analysis/runs/${runId}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

export async function deleteRun(runId: string): Promise<void> {
  await apiFetch(`/analysis/runs/${runId}/`, { method: "DELETE" });
}

export async function getRunFindings(
  runId: string,
  params?: {
    page?: number;
    per_page?: number;
    severity?: string;
    tool_slug?: string;
  },
): Promise<PaginatedRunFindings> {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.severity) sp.set("severity", params.severity);
  if (params?.tool_slug) sp.set("tool_slug", params.tool_slug);
  const qs = sp.toString();
  const res = await apiFetch(
    `/analysis/runs/${runId}/findings/${qs ? "?" + qs : ""}`,
  );
  return res.json();
}
