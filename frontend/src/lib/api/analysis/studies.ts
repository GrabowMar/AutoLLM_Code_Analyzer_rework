/**
 * Research studies: draft -> preflight -> locked protocol -> running ->
 * completed, plus progress, the post-run report, the protocol and exports.
 *
 * Lifecycle refusals answer `409 {detail, code, ...}` (e.g. `code:
 * "blocking_issues"` with the `blocking` preflight items on lock); see
 * {@link studyErrorCode} / {@link studyBlockingIssues}. Long work runs in
 * the background: launch and report requests answer 202 with state
 * envelopes; watch `study.launch` / {@link getStudyProgress} and poll the
 * report with {@link waitForStudyReport}.
 */

import {
  ANALYSIS_URL,
  downloadAnalysisFile,
  getJson,
  getText,
  query,
  sendJson,
  sleep,
  type AnalysisRequestOptions,
} from "./shared";
import type {
  Page,
  PreflightIssue,
  PreflightReport,
  ReportState,
  Study,
  StudyCreatePayload,
  StudyDetail,
  StudyErrorBody,
  StudyExportName,
  StudyListParams,
  StudyProgress,
  StudyProtocolEnvelope,
  StudyReportEnvelope,
  StudyRetryResult,
  StudyUpdatePayload,
} from "./types";

// ── CRUD ──────────────────────────────────────────────────────────────

/** Your studies (staff: all), newest first. */
export function listStudies(
  params: StudyListParams = {},
  options: AnalysisRequestOptions = {},
): Promise<Page<Study>> {
  return getJson(`/studies/${query(params)}`, options);
}

/** A draft study (the research-default profile is pinned when none is given). */
export function createStudy(payload: StudyCreatePayload): Promise<StudyDetail> {
  return sendJson("POST", "/studies/", payload);
}

/** Detail incl. the stored preflight, the frozen plan and protocol verification. */
export function getStudy(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<StudyDetail> {
  return getJson(`/studies/${id}/`, options);
}

/** Edit a draft (409 otherwise); any change discards the stored preflight. */
export function updateStudy(
  id: string,
  payload: StudyUpdatePayload,
): Promise<StudyDetail> {
  return sendJson("PATCH", `/studies/${id}/`, payload);
}

/** Delete a study that is not running, with its runs, findings and artifacts. */
export function deleteStudy(
  id: string,
): Promise<{ deleted: boolean; id: string; runs: number }> {
  return sendJson("DELETE", `/studies/${id}/`);
}

// ── Lifecycle ─────────────────────────────────────────────────────────

/** Pre-run checks of a draft (stored on the study): blocking / warnings / info, estimates, design. */
export function runStudyPreflight(id: string): Promise<PreflightReport> {
  return sendJson("POST", `/studies/${id}/preflight/`);
}

/** Freeze the plan and the protocol; 409 `blocking_issues` when the preflight blocks. */
export function lockStudy(id: string): Promise<StudyDetail> {
  return sendJson("POST", `/studies/${id}/lock/`);
}

/** Back to draft (only before launch). */
export function unlockStudy(id: string): Promise<StudyDetail> {
  return sendJson("POST", `/studies/${id}/unlock/`);
}

/**
 * Verify the protocol and start submitting runs in the background (202).
 * `study.launch` tracks the submission; launching a study whose launch is
 * `pending` resumes it.
 */
export function launchStudy(id: string): Promise<StudyDetail> {
  return sendJson("POST", `/studies/${id}/launch/`);
}

/** Cancel every active run and mark the study cancelled. */
export function cancelStudy(id: string): Promise<StudyDetail> {
  return sendJson("POST", `/studies/${id}/cancel/`);
}

/** Requeue the failed, partial and cancelled runs of a running or completed study. */
export function retryFailedStudyRuns(id: string): Promise<StudyRetryResult> {
  return sendJson("POST", `/studies/${id}/retry-failed/`);
}

/** Targets × tools status matrix, counts, cost so far, stale runs, queue and capacity. */
export function getStudyProgress(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<StudyProgress> {
  return getJson(`/studies/${id}/progress/`, options);
}

// ── Report ────────────────────────────────────────────────────────────

/**
 * Start building the post-run report (202, `status: "generating"`); a
 * request while one is in flight returns that one. Allowed when the study
 * is completed or cancelled (`study.actions.report`).
 */
export function requestStudyReport(id: string): Promise<StudyReportEnvelope> {
  return sendJson("POST", `/studies/${id}/report/`);
}

/** The report state, with the report once ready; throws 404 when never requested. */
export function getStudyReport(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<StudyReportEnvelope> {
  return getJson(`/studies/${id}/report/`, options);
}

export interface WaitForReportOptions extends AnalysisRequestOptions {
  /** First poll delay in ms (default 1500); grows by 1.5x up to `maxIntervalMs`. */
  intervalMs?: number;
  /** Poll delay cap in ms (default 8000). */
  maxIntervalMs?: number;
  /**
   * Give up after this many ms (default 25 min; the backend reports a
   * `generating` request older than 20 min as failed, so polling ends).
   */
  timeoutMs?: number;
  /** Called with every polled state (e.g. to show `requested_at`). */
  onUpdate?: (envelope: StudyReportEnvelope) => void;
}

/**
 * Poll `GET /report/` until the report is no longer `generating`; resolves
 * with the final envelope (`ready` with the report, or `failed` with
 * `error`). Rejects with an `AbortError` when `signal` aborts, and with an
 * `Error` on timeout.
 */
export async function waitForStudyReport(
  id: string,
  options: WaitForReportOptions = {},
): Promise<StudyReportEnvelope> {
  const {
    signal,
    intervalMs = 1500,
    maxIntervalMs = 8000,
    timeoutMs = 25 * 60 * 1000,
    onUpdate,
  } = options;
  const deadline = Date.now() + timeoutMs;
  let delay = intervalMs;
  for (;;) {
    const envelope = await getStudyReport(id, { signal });
    onUpdate?.(envelope);
    if (envelope.status !== "generating") return envelope;
    if (Date.now() + delay > deadline) {
      throw new Error(
        "The report is still being generated; check again in a few minutes.",
      );
    }
    await sleep(delay, signal);
    delay = Math.min(Math.round(delay * 1.5), maxIntervalMs);
  }
}

/** True while a report request is in flight. */
export function isReportPending(
  state: Pick<ReportState, "status"> | null | undefined,
): boolean {
  return state?.status === "generating";
}

/** The stored report as thesis-ready Markdown (throws 409 until a report is ready). */
export function getStudyReportMarkdown(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<string> {
  return getText(`/studies/${id}/report.md`, options);
}

export function studyReportMarkdownUrl(id: string): string {
  return `${ANALYSIS_URL}/studies/${id}/report.md`;
}

// ── Protocol ──────────────────────────────────────────────────────────

/** The frozen protocol and its sha256 (throws 404 until the study is locked). */
export function getStudyProtocol(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<StudyProtocolEnvelope> {
  return getJson(`/studies/${id}/protocol/`, options);
}

/** The protocol as Markdown (throws 404 until the study is locked). */
export function getStudyProtocolMarkdown(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<string> {
  return getText(`/studies/${id}/protocol.md`, options);
}

export function studyProtocolMarkdownUrl(id: string): string {
  return `${ANALYSIS_URL}/studies/${id}/protocol.md`;
}

// ── Exports ───────────────────────────────────────────────────────────

/**
 * Download URL of a study export (after launch): `tables.zip` (tidy CSVs),
 * `replication.zip` (tables + protocol + report + plan + SHA256SUMS; 409
 * until a report is ready), `findings.csv`, `runs.csv`.
 */
export function studyExportUrl(
  id: string,
  name: StudyExportName,
  options: { escape?: boolean } = {},
): string {
  return `${ANALYSIS_URL}/studies/${id}/export/${name}${options.escape ? "?escape=true" : ""}`;
}

// ── Downloads ─────────────────────────────────────────────────────────
// Exports, `report.md` and `protocol.md` answer 409/404 with a JSON body
// (`{detail, report_status, error}`) when the file is not available yet; a
// plain `<a href download>` would save that body as the file. These go
// through `downloadAnalysisFile`, so a refusal is thrown like any other API
// error (show it with `formatApiError`) and only real files reach the disk.

/** Short, filesystem-friendly stem used by the backend (`study-<id8>`). */
export function studyFileStem(id: string): string {
  return `study-${id.slice(0, 8)}`;
}

/**
 * Download a study export (`tables.zip`, `replication.zip`, `findings.csv`,
 * `runs.csv`). CSV cells are verbatim by default (pandas / R read the exact
 * values; import them as text in a spreadsheet); `escape` asks for
 * spreadsheet-safe CSVs (untrusted text starting with = + - @ gets a leading
 * apostrophe). The replication package always stays verbatim.
 */
export function downloadStudyExport(
  id: string,
  name: StudyExportName,
  options: { escape?: boolean } = {},
): Promise<string> {
  const escape = options.escape && name !== "replication.zip";
  return downloadAnalysisFile(
    `/studies/${id}/export/${name}${escape ? "?escape=true" : ""}`,
    `${studyFileStem(id)}-${name}`,
  );
}

/** Download the stored post-run report as Markdown (409 until a report is ready). */
export function downloadStudyReportMarkdown(id: string): Promise<string> {
  return downloadAnalysisFile(
    `/studies/${id}/report.md`,
    `${studyFileStem(id)}-report.md`,
  );
}

/** Download the frozen protocol as Markdown (404 until the study is locked). */
export function downloadStudyProtocolMarkdown(id: string): Promise<string> {
  return downloadAnalysisFile(
    `/studies/${id}/protocol.md`,
    `${studyFileStem(id)}-protocol.md`,
  );
}

// ── Errors ────────────────────────────────────────────────────────────

function studyErrorBody(error: unknown): Partial<StudyErrorBody> | null {
  if (!error || typeof error !== "object") return null;
  return error as Partial<StudyErrorBody>;
}

/** The `code` of a refused lifecycle action (`blocking_issues`, `invalid_state`, ...), else null. */
export function studyErrorCode(error: unknown): string | null {
  const code = studyErrorBody(error)?.code;
  return typeof code === "string" ? code : null;
}

/** The blocking preflight items of a refused lock (empty for any other error). */
export function studyBlockingIssues(error: unknown): PreflightIssue[] {
  const blocking = studyErrorBody(error)?.blocking;
  return Array.isArray(blocking) ? blocking : [];
}
