/**
 * Analysis runs: list, ad-hoc submit/preview, detail, cancel/retry/delete,
 * findings, deduplicated issues, sample files, artifacts and exports.
 *
 * Execution never happens inside a request: a new run comes back `queued`
 * with pending tool results; poll {@link getAnalysisRun} while
 * `isRunActive(run.status)`. Names carry an `Analysis`/`Run` prefix so they
 * never clash with the automation client's `getRun`/`cancelRun`.
 */

import {
  ANALYSIS_URL,
  getJson,
  query,
  sendJson,
  type AnalysisRequestOptions,
} from "./shared";
import type {
  AnalysisRunDetail,
  AnalysisRunListItem,
  ArtifactRef,
  FindingFilters,
  FindingPage,
  IssueFilters,
  IssuePage,
  Page,
  RunCreatePayload,
  RunExportFormat,
  RunFileContent,
  RunFileFilters,
  RunFiles,
  RunListParams,
  RunPreview,
} from "./types";

/** Your runs (staff: all), newest first; self-tests only with `purpose: "canary"`. */
export function listAnalysisRuns(
  params: RunListParams = {},
  options: AnalysisRequestOptions = {},
): Promise<Page<AnalysisRunListItem>> {
  return getJson(`/runs/${query(params)}`, options);
}

/**
 * Submit an ad-hoc run of one completed job (or pasted files) under a
 * profile or an inline config. Throws 400 `{detail, remediation}` when the
 * plan's AI reviewer needs an OpenRouter key the user does not have.
 */
export function createAnalysisRun(
  payload: RunCreatePayload,
): Promise<AnalysisRunDetail> {
  return sendJson("POST", "/runs/", payload);
}

/** Preflight for one target under the resolved plan (nothing is created). */
export function previewAnalysisRun(
  payload: RunCreatePayload,
  options: AnalysisRequestOptions = {},
): Promise<RunPreview> {
  return sendJson("POST", "/runs/preview/", payload, options);
}

/** Detail: plan, target metadata, tool results, review samples, measurement, outcomes. */
export function getAnalysisRun(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<AnalysisRunDetail> {
  return getJson(`/runs/${id}/`, options);
}

/** Final, cooperative cancel (409 when the run already finished). */
export function cancelAnalysisRun(id: string): Promise<AnalysisRunDetail> {
  return sendJson("POST", `/runs/${id}/cancel/`);
}

/**
 * Requeue failed / timed-out / skipped / cancelled tools (or only `tools`).
 * Retrying a run of a completed study re-opens the study.
 */
export function retryAnalysisRun(
  id: string,
  tools?: string[],
): Promise<AnalysisRunDetail> {
  return sendJson(
    "POST",
    `/runs/${id}/retry/`,
    tools && tools.length > 0 ? { tools } : undefined,
  );
}

/** Delete a finished ad-hoc or self-test run (study runs: delete the study). */
export function deleteAnalysisRun(
  id: string,
): Promise<{ deleted: boolean; id: string }> {
  return sendJson("DELETE", `/runs/${id}/`);
}

/** Findings ordered by severity rank (critical first), with facet counts over the filter. */
export function listRunFindings(
  runId: string,
  filters: FindingFilters = {},
  options: AnalysisRequestOptions = {},
): Promise<FindingPage> {
  return getJson(`/runs/${runId}/findings/${query(filters)}`, options);
}

/**
 * Deduplicated issues: `kind: "static"` (counted static findings grouped
 * across tools) or `kind: "ai"` (AI consensus clusters).
 */
export function listRunIssues(
  runId: string,
  filters: IssueFilters = {},
  options: AnalysisRequestOptions = {},
): Promise<IssuePage> {
  return getJson(`/runs/${runId}/issues/${query(filters)}`, options);
}

/** The sample manifest (scope decision, origin, SLOC per file) with finding counts. */
export function getRunFiles(
  runId: string,
  filters: RunFileFilters = {},
  options: AnalysisRequestOptions = {},
): Promise<RunFiles> {
  return getJson(`/runs/${runId}/files/${query(filters)}`, options);
}

/**
 * One file of the analysed sample, re-resolved and sha-verified (owner and
 * staff). Throws 409 when the file changed since the run, 410 when the
 * source is gone; `content` is null for binary or too-large files.
 */
export function getRunFileContent(
  runId: string,
  path: string,
  options: AnalysisRequestOptions = {},
): Promise<RunFileContent> {
  return getJson(`/runs/${runId}/files/content/${query({ path })}`, options);
}

/** URL of a stored artifact (`?download=true` forces an attachment). */
export function artifactUrl(
  artifact: Pick<ArtifactRef, "url">,
  params: { download?: boolean } = {},
): string {
  return `${artifact.url}${params.download ? "?download=true" : ""}`;
}

/** URL of an artifact from its parts (tool result or review sample id + name). */
export function runArtifactUrl(
  runId: string,
  ownerId: string,
  name: string,
  params: { download?: boolean } = {},
): string {
  const path = name.split("/").map(encodeURIComponent).join("/");
  return artifactUrl(
    { url: `${ANALYSIS_URL}/runs/${runId}/artifacts/${ownerId}/${path}` },
    params,
  );
}

/** Download URL of a run export: everything (json), findings (csv) or SARIF 2.1.0. */
export function runExportUrl(runId: string, format: RunExportFormat): string {
  return `${ANALYSIS_URL}/runs/${runId}/export.${format}`;
}
