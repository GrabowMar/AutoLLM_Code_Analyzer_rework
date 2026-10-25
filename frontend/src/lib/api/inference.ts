/**
 * /api/inference/ client: endpoints (OpenRouter + local Ollama / OpenAI-compatible
 * servers), their discovered models, relay tokens and sessions (spec §F.3).
 *
 * Secrets: `secret_once` exists only in the create / rotate responses. Callers
 * show it once (TokenRevealDialog) and never store it.
 */
import { apiFetch } from "./core";
import type {
  EndpointCreatePayload,
  EndpointCreateResult,
  EndpointDetail,
  EndpointMetrics,
  EndpointModel,
  EndpointModelUpdatePayload,
  EndpointSummary,
  EndpointUpdatePayload,
  ProbeResult,
  RelayInstall,
  RequestRef,
  SessionOut,
  TokenOut,
  TokenRevokeResult,
  TokenRotateResult,
  TokenSecret,
} from "./types/inference";

export type * from "./types/inference";

const base = (id: number) => `/inference/endpoints/${id}`;

function json(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body) };
}

// ── endpoints ─────────────────────────────────────────────────────────

/**
 * Endpoints the caller can see. `includeSystem` adds the OpenRouter row. The
 * flag is always sent: the server's default includes the system row.
 */
export async function listEndpoints(
  options: { includeSystem?: boolean; signal?: AbortSignal } = {},
): Promise<EndpointSummary[]> {
  const qs = `?include_system=${options.includeSystem ? "true" : "false"}`;
  const res = await apiFetch(`/inference/endpoints/${qs}`, {
    signal: options.signal,
  });
  return res.json();
}

export async function getEndpoint(
  id: number,
  options: { signal?: AbortSignal } = {},
): Promise<EndpointDetail> {
  const res = await apiFetch(`${base(id)}/`, { signal: options.signal });
  return res.json();
}

/** Relay endpoints come back with a one-time `token`. Direct endpoints are staff-only. */
export async function createEndpoint(
  payload: EndpointCreatePayload,
): Promise<EndpointCreateResult> {
  const res = await apiFetch("/inference/endpoints/", json(payload));
  return res.json();
}

export async function updateEndpoint(
  id: number,
  payload: EndpointUpdatePayload,
): Promise<EndpointDetail> {
  const res = await apiFetch(`${base(id)}/`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return res.json();
}

/** Throws `{code: "in_use"}` (HTTP 409) while runs reference the endpoint: disable it instead. */
export async function deleteEndpoint(id: number): Promise<void> {
  await apiFetch(`${base(id)}/`, { method: "DELETE" });
}

/** True for the 409 `deleteEndpoint` throws while the endpoint is still referenced. */
export function isEndpointInUseError(error: unknown): boolean {
  return (
    !!error &&
    typeof error === "object" &&
    (error as Record<string, unknown>).code === "in_use"
  );
}

/** Ask for a model re-scan; relays pick it up on their next poll. */
export async function refreshEndpoint(id: number): Promise<void> {
  await apiFetch(`${base(id)}/refresh/`, { method: "POST" });
}

// ── probes (Test) ─────────────────────────────────────────────────────

/** Start a probe: a 1-token chat on `modelPk`, or a version check without one. */
export async function testEndpoint(
  id: number,
  modelPk?: number | null,
): Promise<RequestRef> {
  const res = await apiFetch(
    `${base(id)}/test/`,
    json(modelPk != null ? { model_id: modelPk } : {}),
  );
  return res.json();
}

export async function getProbeResult(
  id: number,
  requestId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ProbeResult> {
  const res = await apiFetch(
    `${base(id)}/test/${encodeURIComponent(requestId)}/`,
    { signal: options.signal },
  );
  return res.json();
}

/** Probe statuses that will not change any more (the LLMRequest is finished). */
export const PROBE_FINAL_STATUSES: ReadonlySet<string> = new Set([
  "done",
  "failed",
  "cancelled",
  "expired",
]);

/**
 * Run a probe and poll its result until it finishes, `timeoutMs` passes
 * (resolves with the last non-final result) or `signal` aborts (rejects).
 */
export async function runProbe(
  id: number,
  modelPk?: number | null,
  options: {
    signal?: AbortSignal;
    intervalMs?: number;
    timeoutMs?: number;
  } = {},
): Promise<ProbeResult> {
  const { signal, intervalMs = 1_000, timeoutMs = 120_000 } = options;
  const { request_id } = await testEndpoint(id, modelPk);
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const result = await getProbeResult(id, request_id, { signal });
    if (PROBE_FINAL_STATUSES.has(result.status) || Date.now() >= deadline) {
      return result;
    }
    await sleep(intervalMs, signal);
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

// ── discovered models ─────────────────────────────────────────────────

export async function listEndpointModels(
  id: number,
  options: { includeHidden?: boolean; signal?: AbortSignal } = {},
): Promise<EndpointModel[]> {
  const qs = options.includeHidden ? "?include_hidden=true" : "";
  const res = await apiFetch(`${base(id)}/models/${qs}`, {
    signal: options.signal,
  });
  return res.json();
}

export async function updateEndpointModel(
  id: number,
  modelPk: number,
  payload: EndpointModelUpdatePayload,
): Promise<EndpointModel> {
  const res = await apiFetch(`${base(id)}/models/${modelPk}/`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return res.json();
}

/** Unload a resident model (`keep_alive: 0`). */
export async function unloadEndpointModel(
  id: number,
  modelPk: number,
): Promise<RequestRef> {
  const res = await apiFetch(
    `${base(id)}/unload/`,
    json({ model_pk: modelPk }),
  );
  return res.json();
}

// ── relay tokens and sessions ─────────────────────────────────────────

export async function listTokens(id: number): Promise<TokenOut[]> {
  const res = await apiFetch(`${base(id)}/tokens/`);
  return res.json();
}

export async function createToken(
  id: number,
  label = "",
): Promise<TokenSecret> {
  const res = await apiFetch(`${base(id)}/tokens/`, json({ label }));
  return res.json();
}

/** New token now; the old one keeps working for `graceMinutes` (0..10080, default 24 h). */
export async function rotateToken(
  id: number,
  tokenId: number,
  graceMinutes = 1440,
): Promise<TokenRotateResult> {
  const res = await apiFetch(
    `${base(id)}/tokens/${tokenId}/rotate/`,
    json({ grace_minutes: graceMinutes }),
  );
  return res.json();
}

/** Revoke now: its sessions end and their in-flight requests are requeued. */
export async function revokeToken(
  id: number,
  tokenId: number,
): Promise<TokenRevokeResult> {
  const res = await apiFetch(`${base(id)}/tokens/${tokenId}/revoke/`, {
    method: "POST",
  });
  return res.json();
}

export async function listSessions(
  id: number,
  options: { active?: boolean; signal?: AbortSignal } = {},
): Promise<SessionOut[]> {
  const qs = options.active ? "?active=true" : "";
  const res = await apiFetch(`${base(id)}/sessions/${qs}`, {
    signal: options.signal,
  });
  return res.json();
}

// ── metrics and install ───────────────────────────────────────────────

export async function getEndpointMetrics(
  id: number,
  minutes = 60,
  options: { signal?: AbortSignal } = {},
): Promise<EndpointMetrics> {
  const res = await apiFetch(`${base(id)}/metrics/?minutes=${minutes}`, {
    signal: options.signal,
  });
  return res.json();
}

/** Relay install commands. They never contain the token; it is read from stdin into a 0600 file. */
export async function getInstall(id: number): Promise<RelayInstall> {
  const res = await apiFetch(`${base(id)}/install/`);
  return res.json();
}

/** SSE channel of an endpoint (non-owners are mapped to `:public` server side). */
export function endpointChannel(id: number): string {
  return `endpoint:${id}`;
}
