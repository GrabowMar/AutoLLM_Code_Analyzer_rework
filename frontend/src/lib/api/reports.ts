import { apiFetch } from "./core";

export type ReportStatus = "pending" | "generating" | "completed" | "failed";

export type ReportType =
  | "model_analysis"
  | "template_comparison"
  | "tool_analysis"
  | "generation_analytics"
  | "comprehensive";

export interface GenerateReportPayload {
  report_type: ReportType;
  config?: Record<string, unknown>;
  title?: string;
  description?: string;
  expires_in_days?: number | null;
}

export interface ReportDetail extends ReportSummary {
  config: Record<string, unknown>;
  report_data: Record<string, unknown>;
}

export interface ReportListParams {
  report_type?: ReportType;
  status?: ReportStatus;
  limit?: number;
  offset?: number;
}

export interface ReportListResponse {
  reports: ReportSummary[];
  pagination: { total: number; limit: number; offset: number };
}

export interface ReportSummary {
  id: string;
  report_id: string;
  report_type: ReportType;
  title: string;
  description: string;
  status: ReportStatus;
  progress_percent: number;
  summary: Record<string, unknown>;
  error_message: string;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
  generation_job_id: string | null;
  analysis_task_id: string | null;
}

export async function deleteReport(
  reportId: string,
): Promise<{ success: boolean; message: string }> {
  const res = await apiFetch(`/reports/${reportId}/`, { method: "DELETE" });
  return res.json();
}

export async function generateReport(
  payload: GenerateReportPayload,
): Promise<ReportSummary> {
  const res = await apiFetch("/reports/generate/", {
    method: "POST",
    body: JSON.stringify({ config: {}, ...payload }),
  });
  return res.json();
}

export async function getReport(reportId: string): Promise<ReportDetail> {
  const res = await apiFetch(`/reports/${reportId}/`);
  return res.json();
}

export async function getReportData(reportId: string): Promise<{
  report_id: string;
  report_type: ReportType;
  title: string;
  status: ReportStatus;
  progress: number;
  data: Record<string, unknown>;
}> {
  const res = await apiFetch(`/reports/${reportId}/data/`);
  return res.json();
}

export async function getReports(
  params: ReportListParams = {},
): Promise<ReportListResponse> {
  const query = new URLSearchParams();
  if (params.report_type) query.set("report_type", params.report_type);
  if (params.status) query.set("status", params.status);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  const res = await apiFetch(`/reports/${qs ? `?${qs}` : ""}`);
  return res.json();
}
