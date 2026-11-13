/**
 * Runs client (spec 9.5, `runs/api.py`), mounted at `/api/generation/`.
 *
 * Named `samples.ts` because `lib/api/runs.ts` is the analysis-run client; in the UI a generation run is a
 * **Sample** (`/samples/[id]`). Every helper takes an optional `{signal}` so a superseded request can be
 * aborted. Errors are the `apiFetch` throw (`{detail, code}`), readable with `formatApiError`.
 *
 * Live updates are not HTTP: `subscribeRun` follows the SSE channel `run:<id>` (`status`, `step`, `usage`,
 * `trajectory_step`, `log`, `evaluation`).
 */
import { apiFetch } from "../core";
import { subscribe, type SseState } from "../sse";
import type {
  EvaluateRunIn,
  FileScope,
  Page,
  RunCallOut,
  RunEvaluationOut,
  RunEvent,
  RunFileOut,
  RunListOut,
  RunListQuery,
  RunFacetsOut,
  RunManifestOut,
  RunOut,
  RunTrajectoryOut,
} from "./types";
import { RUN_EVENT_TYPES } from "./types";

export const GENERATION_API = "/generation";

export interface RequestOptions {
  signal?: AbortSignal;
}

type QueryValue = string | number | boolean | null | undefined;

/** `?a=1&b=x`; drops `undefined`, `null` and `""` (but keeps `false` and `0`). */
export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

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
    body: JSON.stringify(body ?? {}),
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

const seg = encodeURIComponent;

export function listSamples(
  query: RunListQuery = {},
  options?: RequestOptions,
): Promise<Page<RunListOut>> {
  return getJson(`/runs/${buildQuery({ ...query })}`, options);
}

export function getSampleFacets(
  query: RunListQuery = {},
  options?: RequestOptions,
): Promise<RunFacetsOut> {
  return getJson(`/runs/facets/${buildQuery({ ...query })}`, options);
}

export function getSample(
  runId: string,
  options?: RequestOptions,
): Promise<RunOut> {
  return getJson(`/runs/${seg(runId)}/`, options);
}

export function getSampleManifest(
  runId: string,
  options?: RequestOptions,
): Promise<RunManifestOut> {
  return getJson(`/runs/${seg(runId)}/manifest/`, options);
}

export function listSampleFiles(
  runId: string,
  scope: FileScope = "model",
  origin?: string,
  options?: RequestOptions,
): Promise<RunFileOut[]> {
  return getJson(
    `/runs/${seg(runId)}/files/${buildQuery({ scope, origin })}`,
    options,
  );
}

/** One file's text (app scope, so a variant's overlaid parent files are readable too). */
export async function getSampleFileContent(
  runId: string,
  path: string,
  options?: RequestOptions,
): Promise<string> {
  const res = await apiFetch(
    `${GENERATION_API}/runs/${seg(runId)}/files/content/${buildQuery({ path })}`,
    {
      signal: options?.signal,
      headers: { Accept: "text/plain" },
    },
  );
  const text = await res.text();
  // ninja serialises a `str` response as JSON; the view answers plain text. Accept both.
  if (text.startsWith('"') && text.endsWith('"')) {
    try {
      return JSON.parse(text) as string;
    } catch {
      return text;
    }
  }
  return text;
}

export function getSampleTrajectory(
  runId: string,
  attempt?: number,
  options?: RequestOptions,
): Promise<RunTrajectoryOut> {
  return getJson(
    `/runs/${seg(runId)}/trajectory/${buildQuery({ attempt })}`,
    options,
  );
}

export function listSampleCalls(
  runId: string,
  attempt?: number,
  options?: RequestOptions,
): Promise<RunCallOut[]> {
  return getJson(
    `/runs/${seg(runId)}/calls/${buildQuery({ attempt })}`,
    options,
  );
}

export function listSampleEvaluations(
  runId: string,
  options?: RequestOptions,
): Promise<RunEvaluationOut[]> {
  return getJson(`/runs/${seg(runId)}/evaluations/`, options);
}

export function evaluateSample(
  runId: string,
  body: EvaluateRunIn = {},
): Promise<{ evaluation_id: number }> {
  return postJson(`/runs/${seg(runId)}/evaluate/`, body);
}

/** Only for `outcome=infra_error` (409 `not_infra` otherwise): a new attempt on the same row. */
export function retrySample(
  runId: string,
): Promise<{ run_id: string; attempt: number }> {
  return postJson(`/runs/${seg(runId)}/retry/`, {});
}

export function cancelSample(runId: string): Promise<RunOut> {
  return postJson(`/runs/${seg(runId)}/cancel/`, {});
}

/** Zip of the run's evidence directory (a plain link; the browser downloads it). */
export function sampleEvidenceUrl(runId: string): string {
  return `/api${GENERATION_API}/runs/${seg(runId)}/evidence/`;
}

const RUN_EVENT_SET = new Set<string>(RUN_EVENT_TYPES);

/** Follow `run:<id>`; returns the cleanup function. Unknown event types are ignored. */
export function subscribeRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onState?: (state: SseState) => void,
): () => void {
  return subscribe(
    [`run:${runId}`],
    (e) => {
      const type = typeof e.data.type === "string" ? e.data.type : e.type;
      if (!RUN_EVENT_SET.has(type)) return;
      onEvent({ ...e.data, type } as RunEvent);
    },
    { onState },
  );
}
