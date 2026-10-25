/**
 * /api/inference/ wire types (spec §F.3). Mirrors backend/inference/api/schemas.py.
 * Fields marked "owner/staff" are null/absent unless the viewer owns the endpoint or is staff.
 */

export type EndpointKind = "openrouter" | "ollama" | "openai_compatible";
export type EndpointConnection = "direct" | "relay";
export type ApiFlavor = "native" | "openai";
export type EndpointVisibility = "private" | "shared";
export type EndpointStatus = "online" | "offline" | "degraded" | "disabled";
export type RelaySessionRole = "active" | "standby";

export interface EndpointOwnerRef {
  id: number;
  username: string;
}

export interface EndpointLiveSession {
  id: string;
  role: RelaySessionRole;
  hostname: string;
  relay_version: string;
  client_ip: string | null;
  capacity: number;
  last_seen_at: string;
}

export interface EndpointLoadedModel {
  name: string;
  size_vram: number | null;
  cpu_spill: boolean;
}

export interface EndpointLive {
  session_count: number;
  /** owner/staff */
  sessions?: EndpointLiveSession[] | null;
  inflight_requests: number;
  queued_requests: number;
  active_jobs: number;
  loaded: EndpointLoadedModel[];
  tokens_per_s_5m: number | null;
}

export interface EndpointSummary {
  id: number;
  slug: string;
  name: string;
  kind: EndpointKind;
  connection: EndpointConnection;
  api_flavor: ApiFlavor;
  enabled: boolean;
  is_system: boolean;
  visibility: EndpointVisibility;
  owner: EndpointOwnerRef | null;
  can_edit: boolean;
  has_api_key: boolean;
  /** owner/staff */
  api_key_prefix: string | null;
  /** direct endpoints; owner/staff */
  base_url: string | null;
  max_concurrency: number;
  max_active_jobs_effective: { sandbox: number; llm: number };
  max_resident_models: number | null;
  request_timeout_s: number;
  num_ctx_cap: number;
  keep_alive: string;
  assume_tools: boolean;
  status: EndpointStatus;
  live: EndpointLive;
  models: { total: number; available: number; tools: number };
  last_inventory_at: string | null;
  /** owner/staff */
  last_error?: string | null;
  server_version: string;
}

export interface SessionOut {
  id: string;
  role: RelaySessionRole;
  relay_id: string | null;
  hostname: string;
  relay_version: string;
  upstream_kind: string;
  upstream_hint: string;
  capacity: number;
  client_ip: string | null;
  token_prefix: string;
  model_count: number;
  inflight: number;
  started_at: string;
  last_seen_at: string;
  ended_at: string | null;
  ended_reason: string;
}

export interface EndpointRecentError {
  at: string;
  kind: string;
  message: string;
}

export interface EndpointDetail extends EndpointSummary {
  /** owner/staff */
  sessions_recent?: SessionOut[] | null;
  /** owner/staff */
  recent_errors?: EndpointRecentError[] | null;
}

export interface EndpointModel {
  id: number;
  model_id: string;
  served_model_name: string;
  model_name: string;
  family: string;
  parameter_size: string;
  quantization: string;
  context_window: number;
  num_ctx_override: number | null;
  num_ctx_effective: number | null;
  supports_function_calling: boolean;
  digest: string;
  is_available: boolean;
  is_hidden: boolean;
  loaded: boolean;
  last_used_at: string | null;
}

export interface EndpointCreatePayload {
  name: string;
  slug?: string;
  kind: Exclude<EndpointKind, "openrouter">;
  connection: EndpointConnection;
  base_url?: string;
  api_key?: string;
  api_flavor?: ApiFlavor;
  max_concurrency: number;
  max_active_jobs?: number | null;
  max_resident_models?: number | null;
  request_timeout_s?: number;
  keep_alive?: string;
  num_ctx_cap?: number;
  assume_tools?: boolean;
  /** staff only */
  visibility?: EndpointVisibility;
}

/** PATCH body: every field optional, `slug` is immutable, `api_key: ""` clears the key. */
export type EndpointUpdatePayload = Partial<
  Omit<EndpointCreatePayload, "slug" | "kind">
> & {
  enabled?: boolean;
};

export interface TokenSecret {
  id: number;
  prefix: string;
  /** Shown exactly once (create / rotate responses only). */
  secret_once: string;
}

export interface EndpointCreateResult {
  endpoint: EndpointDetail;
  token?: TokenSecret | null;
}

export interface TokenOut {
  id: number;
  label: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  active_sessions: number;
}

export interface TokenRotateResult {
  new: TokenSecret;
  old_expires_at: string | null;
}

export interface TokenRevokeResult {
  revoked_at: string;
  sessions_ended: number;
  requests_requeued: number;
}

export interface EndpointModelUpdatePayload {
  is_hidden?: boolean;
  num_ctx_override?: number | null;
  supports_function_calling?: boolean;
  model_name?: string;
}

export interface RequestRef {
  request_id: string;
}

export interface ProbeResult {
  status: string;
  latency_ms: number | null;
  error?: string | null;
  sample?: string | null;
}

export interface EndpointMetricsPoint {
  minute: string;
  inflight_avg: number;
  completed: number;
  tokens: number;
  errors: number;
}

export interface EndpointMetrics {
  points: EndpointMetricsPoint[];
}

export interface RelayInstall {
  server_url: string;
  endpoint_slug: string;
  kind: string;
  relay_version: string;
  script_url: string;
  script_sha256: string;
  dockerfile_sha256: string;
  commands: { python: string; docker: string; systemd: string };
  ollama_env: Record<string, string>;
}
