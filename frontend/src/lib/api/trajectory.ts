// Agent trajectory analytics: per-run record + timeline, one step in full,
// per-model behaviour aggregates. Backend: backend/generation/api/views/trajectory.py
// (records built by backend/generation/services/agent/trajectory.py).
import { apiFetch } from "./core";

/** `result_data.agent.trajectory` (schema_version 1). Every numeric field may be null (= not measured). */
export interface TrajectoryRecord {
  schema_version: number;
  parser_version: string;
  computed_at?: string;
  harness?: string;
  harness_version?: string | null;
  source?: {
    calls: string;
    n_calls: number;
    n_ok_calls: number;
    harness_events: boolean;
    mini_traj: boolean;
  };
  comparable: boolean;
  comparable_reason?: string | null;
  /** null for harnesses without native tool calls (aider) — not comparable, never 0. */
  tool_calls_gateway: number | null;
  actions_total: number | null;
  tool_names?: Record<string, number>;
  action_categories?: Record<string, number>;
  activity_counts?: Record<string, number>;
  outcomes?: Record<string, number>;
  failed_actions: number | null;
  failure_kinds?: Record<string, number>;
  killed_actions: number | null;
  policy_denials: number | null;
  edit_no_match: number | null;
  outcome_unknown?: number;
  format_errors?: number;
  no_tool_responses?: number;
  verification?: {
    built: boolean | null;
    ran_server: boolean | null;
    probed_http: boolean | null;
    ran_tests: boolean | null;
    syntax_checked: boolean | null;
    n_builds: number | null;
    builds_ok?: number;
    builds_failed?: number;
    last_build_ok?: boolean | null;
    first_build_action: number | null;
    first_probe_action: number | null;
  };
  files_written: number | null;
  write_events?: number | null;
  max_writes_per_file: number | null;
  writes_after_first_build: number | null;
  most_rewritten?: { path: string; writes: number }[];
  first_pass?: {
    method: string;
    files_with_first_version: number;
    final_files: number;
    coverage: number | null;
    unchanged: number | null;
    mean_similarity: number | null;
  } | null;
  time?: {
    llm_s: number | null;
    between_calls_s: number | null;
    llm_share: number | null;
    tool_exec_s: number | null;
    first_call_at: string | null;
    last_call_at: string | null;
    head_s: number | null;
    tail_s: number | null;
  };
  context?: {
    prompt_tokens_first: number | null;
    prompt_tokens_last: number | null;
    prompt_tokens_max: number | null;
    series: number[];
    series_scope?: string;
  };
  cache_hit_ratio: number | null;
  reasoning_share: number | null;
  call_roles?: Record<string, number>;
  model_turns?: number;
  observed_inputs?: ObservedInputs | null;
  harness_counters?: {
    steps: number | null;
    tool_calls: number | null;
    report_missing: boolean;
  };
  list_cost?: {
    list_cost: number;
    billing_source: string | null;
    calls_priced: number;
  } | null;
}

export interface ObservedInputs {
  seq: number | null;
  system_prompt_sha256: string | null;
  system_prompt_template_sha256: string | null;
  system_prompt_chars: number;
  system_messages: number;
  tools_offered: string[];
  tool_spec_sha256: string | null;
  tool_spec_chars: number;
  first_call_prompt_tokens: number | null;
  first_message_count: number;
}

/** `metrics.behaviour` (contract C2). */
export interface BehaviourSummary {
  parser_version: string | null;
  actions_total: number | null;
  tool_calls_gateway: number | null;
  failed_actions: number | null;
  killed_actions: number | null;
  policy_denials: number | null;
  builds: number | null;
  ran_server: boolean | null;
  probed_http: boolean | null;
  ran_tests: boolean | null;
  files_written: number | null;
  writes_after_first_build: number | null;
  llm_seconds: number | null;
  between_calls_seconds: number | null;
  context_tokens_first: number | null;
  context_tokens_max: number | null;
  cache_hit_ratio: number | null;
  reasoning_share: number | null;
  first_build_action_index: number | null;
  comparable: boolean;
}

export interface StepToolCall {
  index: number | null;
  name: string;
  category: string;
  labels: string[];
  summary: string;
  outcome: string;
  failed: boolean;
  exit_code: number | null;
}

export interface TrajectoryStep {
  seq: number;
  ts: string | null;
  offset_s: number | null;
  duration_s: number | null;
  /** null when the step comes from a compact artifact (no system prompt to classify). */
  role: string | null;
  status: number | null;
  failed: boolean;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  cached_tokens: number | null;
  reasoning_tokens: number | null;
  finish_reason: string | null;
  provider: string | null;
  upstream_retries: number | null;
  format_error_feedback: boolean;
  has_reasoning: boolean;
  content_chars: number;
  tool_calls: StepToolCall[];
}

export interface RuleTable {
  parser_version: string;
  priority: string[];
  categories: Record<string, string>;
  tool_categories: Record<string, string>;
  shell_rules: { label: string; pattern: string; description: string }[];
  roles: { role: string; pattern: string; description: string }[];
  failed_outcomes: string[];
  notes: string[];
}

export interface JobTrajectory {
  job_id: string;
  harness: string | null;
  parser_version: string;
  rules: RuleTable;
  trajectory_error: string | null;
  record: TrajectoryRecord | null;
  record_source: "stored" | "computed" | null;
  steps: TrajectoryStep[];
  truncated: boolean;
  total_steps: number;
  source: "llm_calls" | "artifacts";
  notes: string[];
}

export interface StepDetailToolCall {
  id: string | null;
  name: string;
  category: string;
  labels: string[];
  summary: string;
  arguments: string;
  outcome: string;
  failed: boolean;
  exit_code: number | null;
  duration_s: number | null;
  error: string | null;
  result: string | null;
  result_source: "harness_events" | "gateway" | null;
}

export interface StepDetail {
  seq: number | null;
  ts: string | null;
  duration_s: number | null;
  status: number | null;
  role: string | null;
  upstream_retries: number | null;
  policy_diff: Record<string, unknown> | null;
  request: {
    model: string | null;
    params: Record<string, unknown>;
    message_count: number | null;
    tools_offered: string[];
    system_prompt: string | null;
    new_messages: {
      role: string | null;
      name: string | null;
      tool_call_id: string | null;
      content: string | null;
    }[];
  };
  response: {
    id: string | null;
    model: string | null;
    provider: string | null;
    finish_reason: string | null;
    content: string | null;
    reasoning: string | null;
    usage: Record<string, unknown> | null;
    error: unknown;
    tool_calls: StepDetailToolCall[];
  };
  source: "llm_calls" | "artifact";
  notes: string[];
}

export interface Describe {
  n: number;
  mean: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
}

export interface HarnessBehaviour {
  harness: string;
  harness_versions: string[];
  /** Distinct agent.budget.max_llm_requests of the runs with a record (pooled when more than one). */
  max_llm_requests: number[];
  n_jobs: number;
  n_invalid_excluded: number;
  n_without_record: number;
  n: number;
  comparable: boolean;
  parser_versions: string[];
  metrics: Record<string, Describe>;
  rates: Record<string, { n: number; k: number; share: number | null }>;
  pass_rate: Describe;
}

export interface ModelBehaviour {
  model_id: number;
  parser_version: string;
  harnesses: HarnessBehaviour[];
  notes: string[];
}

export async function getJobTrajectory(
  jobId: string,
  init?: RequestInit,
): Promise<JobTrajectory> {
  const res = await apiFetch(`/generation/jobs/${jobId}/trajectory/`, init);
  return res.json();
}

export async function getTrajectoryStep(
  jobId: string,
  seq: number,
  init?: RequestInit,
): Promise<StepDetail> {
  const res = await apiFetch(
    `/generation/jobs/${jobId}/trajectory/steps/${seq}/`,
    init,
  );
  return res.json();
}

export async function getModelBehaviour(
  modelId: number,
  init?: RequestInit,
): Promise<ModelBehaviour> {
  const res = await apiFetch(`/generation/models/${modelId}/behaviour/`, init);
  return res.json();
}
