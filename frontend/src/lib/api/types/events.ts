/**
 * SSE event payloads and the event names the stream listener registers (`lib/api/sse.ts`).
 *
 * - `run:<uuid>` and `study:<uuid>` (they replace the v2 job and experiment channels) mirror
 *   `backend/generation/contracts/events.py`. Their payload types live with the v3 API types
 *   (`lib/api/v3/types.ts`: `RunEvent` in WP-16's block, `StudyEvent` and its members in WP-15's)
 *   and are re-exported here, so there is one definition per event.
 * - `endpoint:<id>` / `endpoint:<id>:public` and `capacity` mirror `backend/realtime/events.py`.
 *
 * `publish()` adds `channel` and `published_at` to every payload. Payloads never carry other
 * tenants' model names, hosts or addresses.
 */

import type { Lanes } from "./execution";
import {
  RUN_EVENT_TYPES,
  STUDY_EVENT_TYPES,
  type RunEvent,
  type StudyEvent,
} from "../v3/types";

export type {
  BudgetAlertEvent,
  PausedEvent,
  RunEvent,
  SpendEvent,
  StoppedEvent,
  StudyEvent,
  StudyPostRunEvent,
  StudyRunEvent,
  StudyStateEvent,
  StudyTickEvent,
} from "../v3/types";
export { RUN_EVENT_TYPES, STUDY_EVENT_TYPES };

interface EventEnvelope {
  channel?: string;
  published_at?: string;
}

/** `run:<uuid>` (staff, the run's creator, or the creator of its study). */
export function runChannel(runId: string): string {
  return `run:${runId}`;
}

/** `study:<uuid>` (the study's creator or staff). */
export function studyChannel(studyId: string): string {
  return `study:${studyId}`;
}

export type RunEventType = RunEvent["type"];
export type StudyEventType = StudyEvent["type"];

/**
 * The executor's per-lane entry of a study `tick` (`StudyTickEvent.endpoints[]`). Only `id` is
 * guaranteed: the executor sends what it computes cheaply every tick, so a client merges the
 * keys present.
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
  | RunEvent
  | StudyEvent
  | EndpointChannelEvent
  | CapacityEvent;
export type ReworkEventType = ReworkEvent["type"];

/** SSE event names the stream listener must register (sse.ts). */
export const REWORK_EVENT_TYPES: readonly ReworkEventType[] = [
  ...new Set<ReworkEventType>([
    ...RUN_EVENT_TYPES,
    ...STUDY_EVENT_TYPES,
    "endpoint",
    "session",
    "capacity",
  ]),
];
