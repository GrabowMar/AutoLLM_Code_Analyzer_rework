/**
 * SSE event payloads (spec §F.6). Published by backend/realtime/events.py
 * (`publish_experiment_event`, `publish_endpoint_event`, `publish_capacity`).
 * `publish()` adds `channel` and `published_at` to every payload.
 */

import type { Lanes } from "./execution";
import type {
  CellsPayload,
  PauseReason,
  PostRunStatus,
  ProgressCounts,
  ExperimentStatusV2,
} from "./experiments";

interface EventEnvelope {
  channel?: string;
  published_at?: string;
}

// ── experiment:<id> ───────────────────────────────────────────────────

export interface JobEvent extends EventEnvelope {
  type: "job";
  job_id: string;
  condition_id: number | null;
  app_id: number | null;
  repeat_index: number | null;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  phase: "" | "queued" | "running" | "eval_queued" | "evaluating";
  attempt: number;
  endpoint_id: number | null;
  pass_rate?: number | null;
  ts: string;
}

/**
 * The executor's per-lane tick payload. Only `id` is guaranteed: the executor
 * sends what it computes cheaply every tick, so a client merges the keys present.
 */
export interface TickEndpoint {
  id: number;
  slots_used?: number;
  active?: number;
  queued?: number;
  tokens_per_s?: number | null;
  loaded?: number;
  blocked_reason?: string | null;
  circuit?: "closed" | "open";
}

/**
 * `tick` on `experiment:<id>`: current-attempt-per-cell rollups (same meaning as
 * REST status). `throughput`, `eta_seconds` and `cells.retried` may be absent:
 * keep the last REST values for them.
 */
export interface TickEvent extends EventEnvelope {
  type: "tick";
  counts: ProgressCounts;
  cells?: Partial<{
    total: number;
    done: number;
    failed_final: number;
    in_flight: number;
    retried: number;
  }>;
  throughput?: {
    completed_last_15m: number;
    completed_last_60m: number;
    per_hour_ewma: number;
  };
  eta_seconds?: number | null;
  bottleneck?: string | null;
  endpoints?: TickEndpoint[];
}

export interface StateEvent extends EventEnvelope {
  type: "state";
  status: ExperimentStatusV2;
  pause_reason: PauseReason | string;
}

export interface PostRunEvent extends EventEnvelope {
  type: "post_run";
  id: number;
  status: PostRunStatus;
  progress: {
    analysis_done?: number;
    analysis_total?: number;
    analysis_failed?: number;
  };
}

export type ExperimentEvent = JobEvent | TickEvent | StateEvent | PostRunEvent;

// ── endpoint:<id> (owner/staff) and endpoint:<id>:public ──────────────

export interface EndpointEvent extends EventEnvelope {
  type: "endpoint";
  status: "online" | "offline" | "degraded" | "disabled";
  sessions: {
    id: string;
    role: "active" | "standby";
    hostname: string;
    relay_version: string;
    client_ip: string | null;
  }[];
  inflight: number;
  queued: number;
  loaded: { name: string; size_vram: number | null; cpu_spill: boolean }[];
  last_error: string | null;
}

export interface EndpointPublicEvent extends EventEnvelope {
  type: "endpoint";
  status: "online" | "offline" | "degraded" | "disabled";
  session_count: number;
  inflight: number;
  queued: number;
  loaded_count: number;
}

export interface SessionEvent extends EventEnvelope {
  type: "session";
  event: "connected" | "disconnected";
  role: "active" | "standby";
  hostname: string;
  relay_version: string;
}

export interface SessionPublicEvent extends EventEnvelope {
  type: "session";
  event: "connected" | "disconnected";
}

/** Non-owners are transparently subscribed to the `:public` variant. */
export type EndpointChannelEvent =
  | EndpointEvent
  | EndpointPublicEvent
  | SessionEvent
  | SessionPublicEvent;

// ── capacity ──────────────────────────────────────────────────────────

export interface CapacityEvent extends EventEnvelope {
  type: "capacity";
  lanes: Lanes;
  executor: { online: boolean; draining: boolean; code_stale: boolean };
  warnings: string[];
}

export type ReworkEvent =
  | ExperimentEvent
  | EndpointChannelEvent
  | CapacityEvent;
export type ReworkEventType = ReworkEvent["type"];

/** SSE event names the stream listener must register (sse.ts). */
export const REWORK_EVENT_TYPES: readonly ReworkEventType[] = [
  "job",
  "tick",
  "state",
  "post_run",
  "endpoint",
  "session",
  "capacity",
] as const;

/** Re-exported for consumers that patch the grid from `job` events. */
export type { CellsPayload };
