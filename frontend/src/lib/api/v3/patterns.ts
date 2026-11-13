/**
 * Patterns client (spec 9.7, `patterns/api.py`; operation ids `patterns_*`), mounted at `/api/generation/`.
 *
 * Result tables (prevalence, comparisons, pooled, clusters) answer 409 `pattern_run_not_completed` until the
 * run completes; the live (provisional) run answers at any time. A detector draft from a selection is only
 * a prefill: saving goes through the library API (`createLibraryItem("detectors", …)`).
 */
import { apiFetch } from "../core";
import { buildQuery, GENERATION_API, type RequestOptions } from "./samples";
import type {
  ClusterLabelIn,
  ClusterLabelOut,
  ClustersOut,
  ComparisonRow,
  DetectorAuditIn,
  DetectorAuditOut,
  DetectorContent,
  DetectorFromSelectionIn,
  PatternRunIn,
  PatternRunOut,
  PooledOut,
  PrevalenceRow,
} from "./types";

async function getJson<T>(path: string, options?: RequestOptions): Promise<T> {
  const res = await apiFetch(`${GENERATION_API}${path}`, {
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

async function postJson<T>(
  path: string,
  body: unknown,
  options?: RequestOptions,
): Promise<T> {
  const res = await apiFetch(`${GENERATION_API}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

const seg = encodeURIComponent;

/** Queue a PatternRun (labelled `planned` when it matches the pre-registered plan, else `exploratory`). */
export function createPatternRun(body: PatternRunIn): Promise<PatternRunOut> {
  return postJson("/patterns/runs/", { detectors: "default", ...body });
}

export function listPatternRuns(
  studyId: string,
  live?: boolean,
  options?: RequestOptions,
): Promise<PatternRunOut[]> {
  return getJson(
    `/patterns/runs/${buildQuery({ study: studyId, live })}`,
    options,
  );
}

export function getPatternRun(
  runId: string,
  options?: RequestOptions,
): Promise<PatternRunOut> {
  return getJson(`/patterns/runs/${seg(runId)}/`, options);
}

export function getPrevalence(
  runId: string,
  query: { target?: string; feature?: string; scope?: string } = {},
  options?: RequestOptions,
): Promise<PrevalenceRow[]> {
  return getJson(
    `/patterns/runs/${seg(runId)}/prevalence/${buildQuery({ ...query })}`,
    options,
  );
}

export function getComparisons(
  runId: string,
  feature?: string,
  options?: RequestOptions,
): Promise<ComparisonRow[]> {
  return getJson(
    `/patterns/runs/${seg(runId)}/comparisons/${buildQuery({ feature })}`,
    options,
  );
}

export function getPooled(
  runId: string,
  feature?: string,
  options?: RequestOptions,
): Promise<PooledOut> {
  return getJson(
    `/patterns/runs/${seg(runId)}/pooled/${buildQuery({ feature })}`,
    options,
  );
}

export function getClusters(
  runId: string,
  query: { target?: string; condition?: number } = {},
  options?: RequestOptions,
): Promise<ClustersOut> {
  return getJson(
    `/patterns/runs/${seg(runId)}/clusters/${buildQuery({ ...query })}`,
    options,
  );
}

export function draftDetectorFromSelection(
  body: DetectorFromSelectionIn,
): Promise<{ draft: DetectorContent }> {
  return postJson("/patterns/detectors/from-selection/", body);
}

export function labelCluster(body: ClusterLabelIn): Promise<ClusterLabelOut> {
  return postJson("/patterns/clusters/label/", body);
}

export function createDetectorAudit(
  runId: string,
  body: DetectorAuditIn,
): Promise<DetectorAuditOut> {
  return postJson(`/patterns/runs/${seg(runId)}/audits/`, body);
}

export function listDetectorAudits(
  runId: string,
  options?: RequestOptions,
): Promise<DetectorAuditOut[]> {
  return getJson(`/patterns/runs/${seg(runId)}/audits/`, options);
}

export function patternExportUrl(runId: string): string {
  return `/api${GENERATION_API}/patterns/runs/${seg(runId)}/export.zip`;
}
