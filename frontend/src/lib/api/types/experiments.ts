/**
 * Experiments API v2 wire types (spec §F.1). Mirrors
 * backend/generation/api/schemas_experiments.py. The shared preview /
 * comparability shapes live in `../experiments.ts`, which also re-exports
 * everything here.
 */

import type { ExperimentCondition, ExperimentPreview } from "../experiments";
import type { AgentConfig, LLMParams, NamedPresetRef } from "../generation";

export type SchedulingPolicy = "auto" | "interleaved" | "blocked" | "grouped";
export type ExperimentStatusV2 =
  | "draft"
  | "running"
  | "paused"
  | "completed"
  | "cancelled"
  | "archived";
export type PauseReason = "" | "user" | "digest_changed" | "endpoint_removed";
export type ScheduleAction = "top_up" | "add_replicates" | "clone_and_run";
export type PostRunTrigger = "completed" | "manual" | "schedule";
export type PostRunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";
export type BatchKind = "adhoc" | "launch" | "top_up" | "retry" | "schedule";

/** Preview / launch issue. Blocking codes stop a launch. */
export interface Issue {
  code: string;
  detail: string;
  blocking: boolean;
  model_id?: number | null;
  endpoint?: string | null;
  condition_id?: number | null;
}

/** 400 body of create / launch. */
export interface ExperimentErrorBody {
  detail: string;
  code: string;
  errors: Issue[];
  /** Set when a create-and-launch kept a valid draft. */
  experiment_id?: string | null;
}

// ── Design inputs ─────────────────────────────────────────────────────

export interface ConditionChoice {
  model_id: number;
  harness_preset_id?: number;
  harness?: string;
  profile_id?: number;
}

export interface ConditionSetSpec {
  /** agent: models × harness choices; scaffolding: models × profiles (explicit pks; no "Auto"). */
  cross: {
    model_ids: number[];
    harness_preset_ids?: number[];
    harnesses?: string[];
    profile_ids?: number[];
  };
  /** Ablations (badge "ablation"). */
  extra?: (ConditionChoice & { label?: string; param_overrides?: LLMParams })[];
  /** Pre-registered baseline; default: the first cross condition. */
  baseline?: ConditionChoice;
}

export interface ExecutionOptions {
  warmup?: boolean;
  max_resident_models?: number | null;
}

export interface ExecutionSpec {
  scheduling_policy?: SchedulingPolicy;
  /** -10..10 (non-staff clamped to -5..0) */
  priority?: number;
  /** 1..64 or null */
  max_concurrency?: number | null;
  /** 0..5 */
  infra_retries?: number;
  options?: ExecutionOptions;
}

export interface PostRunSpec {
  evaluate: boolean;
  analysis: { enabled: boolean; tool_slugs: string[]; only_passing: boolean };
  report: { enabled: boolean; exclude_truncated: boolean };
}

export interface ScheduleSpec {
  action: ScheduleAction;
  cron: string;
  timezone: string;
  params?: { replicates?: number; name_template?: string };
  skip_if_active: boolean;
  enabled: boolean;
}

export interface ScheduleLastResult {
  outcome?: "fired" | "skipped" | "error";
  batch_id?: string;
  experiment_id?: string;
  jobs_created?: number;
  detail?: string;
}

export interface ScheduleOut extends ScheduleSpec {
  id: number;
  next_run_at: string | null;
  last_run_at: string | null;
  last_result: ScheduleLastResult;
}

export interface PostRunOut {
  id: number;
  trigger: PostRunTrigger;
  status: PostRunStatus;
  progress: {
    analysis_done?: number;
    analysis_total?: number;
    analysis_failed?: number;
  };
  analysis_run_ids: (number | string)[];
  report_id: string | null;
  error: string;
  created_at: string;
  completed_at: string | null;
}

export interface PostRunConfig {
  config: PostRunSpec;
  runs: PostRunOut[];
}

/** Existing create fields (v1 `CreateExperimentPayload`) plus the v2 sections. */
export interface ExperimentCreateV2 {
  name: string;
  slug: string;
  description?: string;
  hypothesis?: string;
  app_requirement_ids?: number[];
  repeats?: number;
  base_seed?: number | null;
  mode?: "agent" | "scaffolding";
  stack_slug?: string | null;
  sampling_preset_id?: number | null;
  budget_preset_id?: number | null;
  agent_config?: AgentConfig;
  continuation_limit?: number;
  enable_repair?: boolean;
  build_healing?: "full" | "no_deps" | "none";
  /** Pre-registered analysis plan; frozen (hashed) at launch. */
  analysis_plan?: AnalysisPlan | null;
  llm_defaults?: LLMParams;
  conditions: ConditionSetSpec;
  execution?: ExecutionSpec;
  post_run?: PostRunSpec;
  schedules?: ScheduleSpec[];
}

export type ExperimentUpdateV2 = Partial<
  Omit<ExperimentCreateV2, "conditions" | "execution" | "schedules">
> & {
  baseline_condition_id?: number | null;
  scheduling_policy?: SchedulingPolicy;
  priority?: number;
  max_concurrency?: number | null;
  infra_retries?: number;
  execution_options?: ExecutionOptions;
};

// ── Lifecycle ─────────────────────────────────────────────────────────

export interface LaunchBody {
  mode?: "launch" | "top_up";
  condition_ids?: number[];
}

export interface ResumeBody {
  accept_digest_drift?: boolean;
}

export interface CancelBody {
  scope?: "all" | "pending";
}

export interface CloneBody {
  name?: string;
  slug?: string;
  include_schedules?: boolean;
  include_post_run?: boolean;
  overrides?: Partial<ExperimentCreateV2>;
  launch?: boolean;
}

export interface ExperimentState {
  status: ExperimentStatusV2;
  pause_reason: PauseReason | string;
  paused_at: string | null;
  running: number;
  pending: number;
}

export interface CancelResult extends ExperimentState {
  cancelled_pending: number;
  signalled_running: number;
  finishing_evaluations: number;
}

export interface ConditionBulkResult {
  created: ExperimentConditionV2[];
  skipped: {
    key: string;
    reason: "duplicate" | "ineligible";
    detail: string;
  }[];
}

export interface RequeueLegacyResult {
  requeued: number;
  batches_refreshed: number;
}

export interface ScheduleRunNowResult {
  outcome: string;
  batch_id?: string | null;
  experiment_id?: string | null;
  jobs_created: number;
}

export interface CronPreview {
  valid: boolean;
  detail: string;
  next_runs: string[];
}

// ── List / detail ─────────────────────────────────────────────────────

/** Current attempt per cell. */
export interface ProgressCounts {
  pending: number;
  running: number;
  eval_queued: number;
  evaluating: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface ExperimentEndpointRef {
  id: number;
  slug: string;
  name: string;
  kind: string;
  online: boolean;
}

export interface ExperimentListItem {
  id: string;
  name: string;
  slug: string;
  status: ExperimentStatusV2;
  pause_reason: PauseReason | string;
  mode: "agent" | "scaffolding";
  scheduling_policy: SchedulingPolicy;
  priority: number;
  created_at: string;
  updated_at: string;
  launched_at: string | null;
  completed_at: string | null;
  condition_count: number;
  model_count: number;
  app_count: number;
  repeats: number;
  total_cells: number;
  progress: ProgressCounts;
  cost_usd: number;
  eta_seconds: number | null;
  endpoints: ExperimentEndpointRef[];
  has_schedule: boolean;
  next_run_at: string | null;
}

export interface PaginatedExperiments {
  items: ExperimentListItem[];
  total: number;
  page: number;
  pages: number;
}

export interface ExperimentListQuery {
  page?: number;
  /** ≤ 100 */
  per_page?: number;
  status?: "active" | "draft" | "finished" | "archived" | ExperimentStatusV2;
  mode?: "agent" | "scaffolding";
  search?: string;
  endpoint?: string;
  ordering?: "-updated_at" | "name" | "-launched_at";
}

export interface ExperimentConditionV2 extends ExperimentCondition {
  endpoint?: { slug: string; kind: string } | null;
  served_model_name?: string;
  digest?: string;
  pinned_digest?: string;
  /** `label`, or "model / method" when the label is blank (older conditions). */
  display_label?: string;
}

export interface ExperimentBatchRef {
  id: string;
  kind: BatchKind | string;
  created_at: string;
  total_jobs: number;
}

export interface ExperimentDetail extends ExperimentListItem {
  description: string;
  hypothesis: string;
  base_seed: number | null;
  agent_config: AgentConfig;
  llm_defaults: LLMParams;
  stack_slug: string | null;
  sampling_preset_ref: NamedPresetRef | null;
  budget_preset_ref: NamedPresetRef | null;
  app_requirement_ids: number[];
  max_concurrency: number | null;
  infra_retries: number;
  execution_options: ExecutionOptions;
  post_run: Partial<PostRunSpec>;
  cloned_from: { id: string; name: string } | null;
  created_by_schedule: { id: number; action: ScheduleAction } | null;
  baseline_condition_id: number | null;
  conditions: ExperimentConditionV2[];
  batches: ExperimentBatchRef[];
  schedules: ScheduleOut[];
  latest_post_run: PostRunOut | null;
}

export interface LaunchResult {
  experiment: ExperimentDetail;
  batch: ExperimentBatchRef | null;
  jobs_created: number;
  policy_effective_by_endpoint: Record<string, string>;
  warnings: Issue[];
}

// ── Preview ───────────────────────────────────────────────────────────

export interface PreviewEndpoint {
  id: number;
  slug: string;
  name: string;
  kind: string;
  connection: string;
  online: boolean;
  sessions: number;
  max_concurrency: number;
  job_cap: number;
  resident_limit: number | null;
  models: {
    id: number;
    name: string;
    digest: string;
    num_ctx: number | null;
    tools: boolean;
  }[];
  cells: number;
  est_run_s_p50: number | null;
  est_hours: number | null;
  issues: Issue[];
}

export type ExperimentPreviewV2 = ExperimentPreview & {
  issues?: Issue[];
  schedule: {
    policy_effective_by_endpoint: Record<string, string>;
    reason: string;
    order_seed: number | null;
    estimated_swaps: Record<string, number>;
  };
  endpoints: PreviewEndpoint[];
  capacity: {
    sandbox_slots: number;
    eval_slots: number;
    llm_job_threads: number;
    est_parallel_runs: number;
  };
  estimate: {
    total_hours: number | null;
    bottleneck: "sandbox" | "eval" | `endpoint:${string}` | null;
  };
  cost: {
    openrouter_upper_bound_usd: number;
    local_cells: number;
    kind: "budget_upper_bound" | "heuristic" | "local_zero" | "mixed";
  };
};

// ── Status / cells / results ──────────────────────────────────────────

export interface UsedTotal {
  used: number;
  total: number;
}

export interface StatusCondition {
  condition_id: number;
  label: string;
  model: {
    id: number;
    name: string;
    endpoint_slug: string | null;
    kind: string;
  };
  harness: string;
  counts: ProgressCounts;
  k_passed: number;
  n_graded: number;
  pass_rate: number | null;
  median_s: number | null;
  tokens: number;
  cost_usd: number;
  last_activity_at: string | null;
}

export interface StatusEndpoint {
  id: number;
  slug: string;
  name: string;
  kind: string;
  connection: string;
  online: boolean;
  sessions: number;
  health: "ok" | "degraded" | "offline";
  slots: UsedTotal;
  jobs: { active: number; cap: number; held_by_others: number };
  /** `models`: this user's models only */
  resident: { models: string[]; others: number; limit: number | null };
  /** own models; others as a count */
  loaded: { name: string; size_vram_gb: number | null; cpu_spill: boolean }[];
  loaded_others: number;
  queued_requests: number;
  queued_jobs_this_experiment: number;
  tokens_per_s_5m: number | null;
  p50_request_s_15m: number | null;
  circuit: "closed" | "open";
  blocked_reason: string | null;
  /** owner/staff only */
  last_error?: string | null;
}

export interface ExperimentStatusV2Payload {
  status: ExperimentStatusV2;
  pause_reason: PauseReason | string;
  policy_effective: Record<string, string> | string | null;
  launched_at: string | null;
  updated_at: string;
  counts: ProgressCounts;
  cells: {
    total: number;
    done: number;
    failed_final: number;
    in_flight: number;
    retried: number;
  };
  attempts_total: number;
  throughput: {
    completed_last_15m: number;
    completed_last_60m: number;
    per_hour_ewma: number;
  };
  eta_seconds: number | null;
  bottleneck: string | null;
  conditions: StatusCondition[];
  endpoints: StatusEndpoint[];
  lanes: {
    sandbox: UsedTotal;
    eval: UsedTotal;
    llm: UsedTotal;
    post: UsedTotal;
  };
  executor: { online: boolean; draining: boolean; code_stale: boolean };
  latest_post_run: PostRunOut | null;
  /**
   * Thread-era rows (`queued_at IS NULL`) still PENDING / RUNNING; the page
   * offers "Requeue" (`POST /jobs/requeue-legacy/`). Optional: 0 when absent.
   */
  legacy_pending?: number;
}

/**
 * Cell state codes: p pending, b backoff, w waiting for model prefetch, r running,
 * q eval_queued, e evaluating, c completed-pass, f completed-fail, u completed-ungraded,
 * x failed, k cancelled.
 */
export type CellCode =
  | "p"
  | "b"
  | "w"
  | "r"
  | "q"
  | "e"
  | "c"
  | "f"
  | "u"
  | "x"
  | "k";

/** [conditionIndex, appIndex, repeat, state, job_id, pass_rate|null, attempt] */
export type CellTuple = [
  number,
  number,
  number,
  CellCode,
  string,
  number | null,
  number,
];

export interface CellsPayload {
  conditions: {
    id: number;
    label: string;
    model_id: number;
    endpoint_slug: string | null;
  }[];
  apps: { id: number; slug: string; name: string }[];
  repeats: number;
  cells: CellTuple[];
}

export interface ResultsQuery {
  baseline?: number;
  exclude_invalid?: boolean;
  exclude_truncated?: boolean;
  common_cells?: boolean;
  fingerprint?: "all" | "majority";
  metric?: "pass_rate";
}

export interface LeaderboardRow {
  rank: number;
  condition_id: number;
  label: string;
  model: { id: number; name: string; endpoint_slug: string | null };
  harness: string;
  n_apps: number;
  repeats_per_app: number;
  n_cells: number;
  n_graded: number;
  k_passed: number;
  n_missing: {
    failed: number;
    ungraded: number;
    excluded_invalid: number;
    excluded_truncated: number;
    not_common: number;
  };
  pass_rate_mean: number | null;
  /** cluster bootstrap over apps */
  pass_rate_ci95: [number, number] | null;
  cost_usd: number;
  tokens_total: number;
  median_seconds: number | null;
  timing_endpoint_slug: string | null;
  terminations: Record<string, number>;
  validity: { ok: number; warn: number; invalid: number };
  mixed_fingerprints: { count: number; majority_share: number } | null;
}

export interface ResultsMatrixRun {
  job_id: string;
  repeat_index: number;
  pass_rate: number | null;
  termination: string | null;
  valid: boolean | null;
  code: CellCode;
}

export interface ResultsMatrixCell {
  condition_id: number;
  app_id: number;
  k: number;
  n: number;
  mean: number | null;
  runs: ResultsMatrixRun[];
}

export interface PairedRow {
  condition_id: number;
  /** apps with both means */
  n_pairs: number;
  mean_delta: number | null;
  /** paired cluster bootstrap over apps */
  ci95: [number, number] | null;
  wins: number;
  losses: number;
  ties: number;
  sign_test_p: number | null;
  wilcoxon_p: number | null;
  /** null while interim */
  p_holm: number | null;
}

export interface ExperimentResults {
  metric: string;
  exclude_invalid: boolean;
  exclude_truncated: boolean;
  common_cells: boolean;
  fingerprint_filter: string;
  baseline_condition_id: number | null;
  baseline_source: "preregistered" | "post_hoc";
  interim: boolean;
  balanced: boolean;
  leaderboard: LeaderboardRow[];
  matrix: {
    apps: { id: number; slug: string; name: string }[];
    cells: ResultsMatrixCell[];
  };
  paired: {
    baseline_condition_id: number | null;
    pairing: "app_blocked";
    n_apps: number;
    rows: PairedRow[];
  };
  notes: string[];
  generated_at: string;
}

export type PlanMetric =
  | "deploy_rate"
  | "pass_rate"
  | "functional_passed"
  | "unique_issues_per_app"
  | "issues_per_kloc"
  | "security_per_app"
  | "cost_per_app";

/** backend/generation/services/analysis_plan.py */
export interface AnalysisPlan {
  research_questions?: {
    id?: string;
    question: string;
    hypothesis?: string;
    metric?: PlanMetric;
    direction?: "two-sided" | "greater" | "less";
  }[];
  primary_metric: PlanMetric;
  secondary_metrics?: PlanMetric[];
  alpha?: number;
  correction?: "holm" | "bh" | "none";
  ranking_criteria?: {
    key: PlanMetric;
    direction: "benefit" | "cost";
    weight: number;
    label?: string;
  }[];
  exclusions?: { truncated?: boolean };
  min_apps_per_condition?: number;
  notes?: string;
}
