/** /api/execution/ wire types (spec §F.4). Mirrors backend/execution/api_schemas.py. */

export type BlockedReason =
  | "sandbox_full"
  | "llm_threads_full"
  | "endpoint_offline"
  | "endpoint_full"
  | "model_not_served"
  | "residency_wait"
  | "warming_model"
  | "experiment_cap"
  | "circuit_open"
  | "draining"
  | "retry_backoff";

export interface UsedTotal {
  used: number;
  total: number;
}

export interface Lanes {
  sandbox: UsedTotal;
  eval: UsedTotal;
  llm: UsedTotal;
  post: UsedTotal;
}

export interface ExecutorStatus {
  online: boolean;
  leader: {
    id: string;
    started_at: string;
    last_seen_at: string;
    code_version: string;
  } | null;
  code_stale: boolean;
  draining: boolean;
  draining_remaining: { jobs: number; post_runs: number };
}

export interface SlotSettings {
  sandbox_slots: number;
  eval_slots: number;
  llm_job_threads: number;
  post_run_slots: number;
}

export interface CapacityLimits {
  apps_dind_mem: string;
  max_sandbox_slots_allowed: number;
  warnings: string[];
}

/** Per visible endpoint; other tenants' models appear only as counts. */
export interface EndpointCapacity {
  id: number;
  slug: string;
  name: string;
  kind: string;
  connection: string;
  online: boolean;
  status: "online" | "offline" | "degraded" | "disabled";
  /** in-flight upstream requests / max_concurrency */
  slots: UsedTotal;
  /** active jobs / job cap */
  jobs: UsedTotal;
  held_by_others: number;
  queued_requests: number;
  /** the caller's own models */
  resident_models: string[];
  resident_others: number;
  resident_limit: number | null;
  loaded_count: number;
  circuit: "closed" | "open";
  blocked_reason: string | null;
}

export interface ExperimentCapacityRow {
  id: string;
  name: string;
  status: string;
  priority: number;
  running: number;
  pending: number;
  eta_seconds: number | null;
}

export interface Capacity {
  executor: ExecutorStatus;
  settings: SlotSettings;
  limits: CapacityLimits;
  lanes: Lanes;
  endpoints: EndpointCapacity[];
  experiments: ExperimentCapacityRow[];
}

export interface QueueItem {
  job_id: string;
  experiment_id: string | null;
  condition_id: number | null;
  model_name: string;
  endpoint_slug: string;
  lane: "sandbox" | "llm";
  sequence: number | null;
  priority: number;
  queued_at: string;
  run_after: string | null;
  attempt: number;
  blocked_reason: BlockedReason | null;
}

export interface QueueOut {
  items: QueueItem[];
  /** endpoint slug -> other users' jobs ahead (count only) */
  others_ahead: Record<string, number>;
}

export interface QueueQuery {
  experiment_id?: string;
  endpoint?: string;
  limit?: number;
}

export interface ExecutorSettings extends SlotSettings {
  draining: boolean;
}

export type ExecutorSettingsPatch = Partial<ExecutorSettings>;

/** 400 body when the memory rule is violated. */
export interface MemoryRuleError {
  detail: string;
  arithmetic: string;
  mem_limit: string;
  required: string;
}
