/** /api/execution/ wire types. Mirrors backend/execution/api_schemas.py (v3: runs and studies). */

export type BlockedReason =
  | "sandbox_full"
  | "llm_threads_full"
  | "endpoint_offline"
  | "endpoint_full"
  | "model_not_served"
  | "residency_wait"
  | "warming_model"
  | "study_cap"
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
  /** Runs and post-run steps still finishing while the executor drains. */
  draining_remaining: { runs: number; post_runs: number };
}

export interface SlotSettings {
  sandbox_slots: number;
  eval_slots: number;
  llm_job_threads: number;
  /** 0-2 (spec §12: `post_run_slots` bound). */
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
  /** active runs / the endpoint's job cap (`max_active_jobs`; the key keeps its name) */
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

/** One running study (`Study.max_parallel` caps its runs; blocked reason `study_cap`). */
export interface StudyCapacityRow {
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
  studies: StudyCapacityRow[];
}

export interface QueueItem {
  /** GenerationRun id. */
  run_id: string;
  /** Run kind (`app_agentic`, `slot_single_shot`, ...). */
  kind: string;
  study_id: string | null;
  condition_id: number | null;
  cell_id: number | null;
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
  /** endpoint slug -> other users' runs ahead (count only) */
  others_ahead: Record<string, number>;
}

export interface QueueQuery {
  study_id?: string;
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
