/**
 * Export API (`/api/export/`): CSV/JSON/SARIF downloads with normal API auth
 * (session cookie or Bearer token). Responses carry `X-Total-Count` and
 * `X-Export-Truncated`; findings are ordered by severity rank so a
 * truncated export drops the least severe rows first.
 */

export type ExportParamValue = string | number | boolean | null | undefined;

/** Root-relative download URL, e.g. `exportUrl('reports.csv', {status: 'completed'})`. */
export function exportUrl(
  path: string,
  params: Record<string, ExportParamValue> = {},
): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    usp.set(key, String(value));
  }
  const qs = usp.toString();
  return `/api/export/${path}${qs ? `?${qs}` : ""}`;
}

/** Filters of `/api/export/findings.{csv,json,sarif}`. */
export interface FindingsExportParams {
  task_id?: string;
  job_id?: string;
  plan_hash?: string;
  analyzer?: string;
  severity?: string;
  category?: string;
  /** Only each job's canonical run. */
  canonical?: boolean;
  /** Only findings counted in the measurement. */
  counted?: boolean;
  is_ai?: boolean;
  limit?: number;
}

export function findingsExportUrl(
  format: "csv" | "json" | "sarif",
  params: FindingsExportParams = {},
): string {
  return exportUrl(
    `findings.${format}`,
    params as Record<string, ExportParamValue>,
  );
}

/**
 * Navigate to an export so the browser downloads it with the session cookie.
 * Prefer a plain `<a href={exportUrl(…)}>` where a link fits.
 */
export function downloadExport(
  path: string,
  params: Record<string, ExportParamValue> = {},
): void {
  window.location.href = exportUrl(path, params);
}
