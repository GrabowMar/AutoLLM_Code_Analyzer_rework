/**
 * /api/execution/ client: executor status, slot lanes and per-endpoint
 * capacity, the caller's queue, and (staff) executor settings (spec §F.4).
 */
import { apiFetch } from "./core";
import type {
  Capacity,
  ExecutorSettings,
  ExecutorSettingsPatch,
  MemoryRuleError,
  QueueOut,
  QueueQuery,
} from "./types/execution";

export type * from "./types/execution";

/** SSE channel with `capacity` events (any authenticated user; aggregates only). */
export const CAPACITY_CHANNEL = "capacity";

export async function getCapacity(
  options: { signal?: AbortSignal } = {},
): Promise<Capacity> {
  const res = await apiFetch("/execution/capacity/", {
    signal: options.signal,
  });
  return res.json();
}

/** The caller's next pending jobs in scheduler order (staff: everyone's). */
export async function getQueue(
  query: QueueQuery = {},
  options: { signal?: AbortSignal } = {},
): Promise<QueueOut> {
  const q = new URLSearchParams();
  if (query.experiment_id) q.set("experiment_id", query.experiment_id);
  if (query.endpoint) q.set("endpoint", query.endpoint);
  if (query.limit != null) q.set("limit", String(query.limit));
  const qs = q.toString();
  const res = await apiFetch(`/execution/queue/${qs ? "?" + qs : ""}`, {
    signal: options.signal,
  });
  return res.json();
}

/** Staff only. */
export async function getExecutorSettings(): Promise<ExecutorSettings> {
  const res = await apiFetch("/execution/settings/");
  return res.json();
}

/**
 * Staff only. A 400 carrying the memory arithmetic is thrown as-is; read it
 * with {@link memoryRuleError}.
 */
export async function updateExecutorSettings(
  patch: ExecutorSettingsPatch,
): Promise<ExecutorSettings> {
  const res = await apiFetch("/execution/settings/", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return res.json();
}

/** The memory-rule body of a rejected settings PATCH, or null for any other error. */
export function memoryRuleError(error: unknown): MemoryRuleError | null {
  if (!error || typeof error !== "object") return null;
  const body = error as Record<string, unknown>;
  return typeof body.arithmetic === "string"
    ? (body as unknown as MemoryRuleError)
    : null;
}
