import { apiFetch } from "./core";

export type ReportStatus = "pending" | "generating" | "completed" | "failed";

export type ReportType =
  | "model_analysis"
  | "template_comparison"
  | "tool_analysis"
  | "generation_analytics"
  | "comprehensive"
  | "experiment_report"
  | "study_report";

export type ReportExportFormat = "md" | "tex" | "json";

/** Download URL of a completed report as Markdown, LaTeX tables or JSON. */
export function reportExportUrl(
  reportId: string,
  format: ReportExportFormat,
): string {
  return `/api/reports/${encodeURIComponent(reportId)}/export/?format=${format}`;
}

/** One curated table of a report, as the server renders it (`renderers.report_tables`). */
export interface ReportTable {
  title: string;
  /** Column headers. */
  columns: string[];
  /** Cells as the server formatted them, in column order; "-" = no value (unmeasured is not zero). */
  rows: string[][];
  note: string;
  /** Rows before the server's row cap (the note says when rows were cut). */
  totalRows: number;
}

export interface ReportDocument {
  tables: ReportTable[];
}

/** `GET /reports/{id}/tables/` (cells formatted as in the Markdown export). */
export interface ReportTablesResponse {
  report_id: string;
  report_type: ReportType;
  title: string;
  tables: {
    title: string;
    columns: { key: string; header: string }[];
    rows: Record<string, string>[];
    note?: string;
    total_rows?: number;
  }[];
}

/** The tables endpoint's keyed rows as cells in column order (a missing cell is ""). */
export function reportDocument(data: ReportTablesResponse): ReportDocument {
  return {
    tables: (data.tables ?? []).map((t) => ({
      title: t.title,
      columns: t.columns.map((c) => c.header),
      rows: t.rows.map((row) => t.columns.map((c) => row[c.key] ?? "")),
      note: t.note ?? "",
      totalRows: t.total_rows ?? t.rows.length,
    })),
  };
}

export interface SummaryItem {
  label: string;
  value: string;
  href?: string;
}

function summaryValue(key: string, v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    // `*_rate` summary values are fractions (0..1).
    if (key.endsWith("_rate")) return `${(v * 100).toFixed(1)}%`;
    return v.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }
  if (Array.isArray(v))
    return v.map((x) => summaryValue("", x)).join(", ") || "—";
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length
      ? entries
          .map(([k, x]) => `${k.replaceAll("_", " ")}: ${summaryValue(k, x)}`)
          .join(", ")
      : "—";
  }
  return String(v);
}

/**
 * The groups sharing the top of a report's ranking, re-read from its data:
 * an experiment report's leaderboard (the top mean pass rate; none when
 * nothing is graded), or a study report's TOPSIS ranking (every row ranked
 * 1). null when the data has neither.
 */
function topGroups(
  data: Record<string, unknown> | null | undefined,
): { names: string[]; noun: string } | null {
  const results = data?.results as { leaderboard?: unknown } | undefined;
  if (Array.isArray(results?.leaderboard)) {
    const graded = (
      results.leaderboard as {
        label?: string;
        pass_rate_mean?: number | null;
      }[]
    ).filter((r) => typeof r.pass_rate_mean === "number");
    const best = Math.max(...graded.map((r) => r.pass_rate_mean as number));
    return {
      names: graded
        .filter((r) => Math.abs((r.pass_rate_mean as number) - best) < 1e-9)
        .map((r) => r.label ?? ""),
      noun: "conditions",
    };
  }
  const ranking = (data?.ranking as { ranking?: unknown } | undefined)?.ranking;
  if (
    Array.isArray(ranking) &&
    ranking.length > 0 &&
    ranking.every((r) => !!r && typeof r === "object" && "topsis_rank" in r)
  ) {
    const rows = ranking as { model?: string; topsis_rank?: number | null }[];
    const grouped = (data?.protocol as { grouped_by?: string } | undefined)
      ?.grouped_by;
    return {
      names: rows.filter((r) => r.topsis_rank === 1).map((r) => r.model ?? ""),
      noun: grouped === "condition" ? "conditions" : "models",
    };
  }
  return null;
}

/**
 * A report's summary card: unmeasured values read "—" (never 0), rates as
 * percentages, and a report with tied leaders says so instead of naming the
 * first group. With the experiment or study report's data, the leader is
 * re-read from its leaderboard or ranking, so summaries stored before ties
 * were detected are right too.
 */
export function summaryItems(
  summary: Record<string, unknown> | null | undefined,
  data?: Record<string, unknown> | null,
): SummaryItem[] {
  const s = { ...(summary ?? {}) };
  const top = "leader" in s ? topGroups(data) : null;
  if (top) {
    s.leader = top.names.length === 1 ? top.names[0] : null;
    s.tied_leaders = top.names.length > 1 ? top.names.length : 0;
  }
  const items: SummaryItem[] = [];
  for (const [key, v] of Object.entries(s)) {
    if (key === "tied_leaders") continue;
    const label = key
      .replaceAll("_", " ")
      .replace(/^./, (c) => c.toUpperCase());
    if (
      key === "leader" &&
      v == null &&
      typeof s.tied_leaders === "number" &&
      s.tied_leaders > 1
    ) {
      items.push({
        label,
        value: `Tie: ${s.tied_leaders} ${top?.noun ?? "conditions"} share the top`,
      });
      continue;
    }
    if (key === "experiment_id" && typeof v === "string" && v) {
      items.push({
        label: "Experiment",
        value: v.slice(0, 8),
        href: `/experiments/${v}`,
      });
      continue;
    }
    items.push({ label, value: summaryValue(key, v) });
  }
  return items;
}

/** The report's curated tables (those of the Markdown/LaTeX export); 400 while it is not completed. */
export async function getReportTables(
  reportId: string,
): Promise<ReportDocument> {
  const res = await apiFetch(
    `/reports/${encodeURIComponent(reportId)}/tables/`,
  );
  return reportDocument((await res.json()) as ReportTablesResponse);
}

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
