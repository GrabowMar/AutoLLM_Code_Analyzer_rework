import { apiFetch } from "./core";

export interface AnalysisFinding {
  id: number;
  result_id: number;
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
  tool_specific_data: Record<string, any>;
  analyzer_name: string;
  created_at: string;
}

export interface AnalysisResult {
  id: number;
  task_id: string;
  analyzer_type: string;
  analyzer_name: string;
  status: string;
  raw_output: Record<string, any>;
  summary: Record<string, any>;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  findings_count: number;
  finding_summary: Record<string, number>;
  created_at: string;
}

export interface AnalysisStats {
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  running_tasks: number;
  total_findings: number;
  findings_by_severity: Record<string, number>;
  findings_by_category: Record<string, number>;
  most_common_issues: Array<{ title: string; count: number }>;
}

export interface AnalysisTask {
  id: string;
  name: string;
  status: string;
  generation_job_id: string | null;
  source_code: Record<string, string>;
  configuration: Record<string, any>;
  results_summary: Record<string, any>;
  generation_job_name: string | null;
  created_by_email: string;
  results_count: number;
  findings_count: number;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string;
  created_at: string;
  updated_at: string;
  target_url: string | null;
  container_instance_id: string | null;
}

export interface AnalysisTaskList {
  id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  generation_job_id: string | null;
  created_by_email: string;
  results_summary: Record<string, any>;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  target_url: string | null;
  container_instance_id: string | null;
}

export interface AnalyzerInfo {
  name: string;
  type: string;
  display_name: string;
  description: string;
  available: boolean;
  availability_message: string;
  default_config: Record<string, any>;
}

export interface PaginatedAnalysisTasks {
  items: AnalysisTaskList[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface PaginatedFindings {
  items: AnalysisFinding[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export async function cancelAnalysisTask(
  taskId: string,
): Promise<{ success: boolean; status: string; message?: string }> {
  const res = await apiFetch(`/analysis/tasks/${taskId}/cancel/`, {
    method: "POST",
  });
  return res.json();
}

export async function createAnalysisTask(data: {
  name?: string;
  generation_job_id?: string;
  source_code?: Record<string, string>;
  analyzers: string[];
  settings?: Record<string, any>;
  auto_start?: boolean;
  live_target?: boolean;
}): Promise<AnalysisTask> {
  const res = await apiFetch("/analysis/tasks/", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteAnalysisTask(
  taskId: string,
): Promise<{ success: boolean }> {
  const res = await apiFetch(`/analysis/tasks/${taskId}/`, {
    method: "DELETE",
  });
  return res.json();
}

export async function getAnalysisFindings(
  taskId: string,
  params?: {
    page?: number;
    per_page?: number;
    severity?: string;
    category?: string;
    analyzer?: string;
  },
): Promise<PaginatedFindings> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.per_page) searchParams.set("per_page", String(params.per_page));
  if (params?.severity) searchParams.set("severity", params.severity);
  if (params?.category) searchParams.set("category", params.category);
  if (params?.analyzer) searchParams.set("analyzer", params.analyzer);
  const qs = searchParams.toString();
  const res = await apiFetch(
    `/analysis/tasks/${taskId}/findings/${qs ? "?" + qs : ""}`,
  );
  return res.json();
}

export async function getAnalysisResult(
  taskId: string,
  resultId: number,
): Promise<AnalysisResult> {
  const res = await apiFetch(`/analysis/tasks/${taskId}/results/${resultId}/`);
  return res.json();
}

export async function getAnalysisResults(
  taskId: string,
): Promise<AnalysisResult[]> {
  const res = await apiFetch(`/analysis/tasks/${taskId}/results/`);
  return res.json();
}

export async function getAnalysisStats(): Promise<AnalysisStats> {
  const res = await apiFetch("/analysis/stats/");
  return res.json();
}

export async function getAnalysisTask(taskId: string): Promise<AnalysisTask> {
  const res = await apiFetch(`/analysis/tasks/${taskId}/`);
  return res.json();
}

export async function getAnalysisTasks(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  search?: string;
  generation_job_id?: string;
}): Promise<PaginatedAnalysisTasks> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.per_page) searchParams.set("per_page", String(params.per_page));
  if (params?.status) searchParams.set("status", params.status);
  if (params?.search) searchParams.set("search", params.search);
  if (params?.generation_job_id)
    searchParams.set("generation_job_id", params.generation_job_id);
  const qs = searchParams.toString();
  const res = await apiFetch(`/analysis/tasks/${qs ? "?" + qs : ""}`);
  return res.json();
}

export async function getAnalyzers(): Promise<AnalyzerInfo[]> {
  const res = await apiFetch("/analysis/analyzers/");
  return res.json();
}
