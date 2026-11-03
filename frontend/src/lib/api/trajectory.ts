// Agent behaviour analytics: the parsed harness trajectory contract and the per-model, per-harness
// aggregates. Backend: backend/generation/api/trajectory.py (`GET /models/{id}/behaviour/`) and the
// contracts in backend/generation/contracts/harness.py. One run's own trajectory is
// `GET /runs/{id}/trajectory/` (9.5, `lib/api/v3/samples.ts`), which returns these types.
import { apiFetch } from "./core";

/** One parsed harness step (`contracts.harness.Step`). */
export interface TrajectoryStep {
  index: number;
  kind:
    | "llm"
    | "tool"
    | "edit"
    | "shell"
    | "message"
    | "compaction"
    | "subagent";
  tool: string | null;
  ok: boolean | null;
  /** Seconds since the attempt started. */
  started: number | null;
  duration_s: number | null;
  tokens_in: number;
  tokens_out: number;
  files: string[];
  command_sha: string | null;
}

/** The harness adapter's parsed output of one attempt (`contracts.harness.Trajectory`). */
export interface Trajectory {
  steps: TrajectoryStep[];
  final_message: string | null;
  final_message_truncated: boolean;
  tool_calls: number;
  edits: number;
  shell_commands: number;
  compactions: number;
  subagent_calls: number;
  ended_on_llm_error: boolean;
  max_turns_hit: boolean;
  parser: string;
  parser_version: number;
  unparsed_lines: number;
}

export type RequestClass = "main" | "subagent" | "compaction" | "auxiliary";

/** What the harness actually sent (`contracts.harness.ObservedInputs`), from the gateway log. */
export interface ObservedInputs {
  /** Normalized by the harness facts' `system_prompt_normalizers`. */
  system_prompt_sha: string | null;
  tool_names: string[];
  tool_specs_sha: string | null;
  first_user_message_sha: string | null;
  calls_by_class: Partial<Record<RequestClass, number>>;
  native_system_prompt_blob: string | null;
}

/** `n, mean, median, min, max` of one measure; every statistic is null when `n` is 0. */
export interface Describe {
  n: number;
  mean: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
}

/** Trajectory counters aggregated per run (`backend/generation/api/trajectory.py`). */
export type BehaviourMetricKey =
  | "steps"
  | "tool_calls"
  | "edits"
  | "shell_commands"
  | "compactions"
  | "subagent_calls";
export type BehaviourRateKey = "ended_on_llm_error" | "max_turns_hit";
/** `RunAttempt.time_split` parts (seconds). */
export type TimeSplitKey = "llm_s" | "tool_s" | "install_s" | "idle_s";

/** One harness version's aggregates over the caller's completed `app_agentic` runs of a model. */
export interface HarnessBehaviour {
  harness: string;
  harness_version: string;
  /** Completed runs with this harness version. */
  n_runs: number;
  /** Runs whose outcome is not usable (left out of every aggregate, counted here). */
  n_unusable_excluded: number;
  /** Usable runs without a stored trajectory. */
  n_without_trajectory: number;
  /** Usable runs with a trajectory (the n of the counters). */
  n: number;
  /** `parser@version` of the records; compare like with like. */
  parsers: string[];
  metrics: Partial<Record<BehaviourMetricKey, Describe>>;
  time_split: Partial<Record<TimeSplitKey, Describe>>;
  rates: Partial<
    Record<BehaviourRateKey, { n: number; k: number; share: number | null }>
  >;
  /** Measured evaluations only (`metrics.eval.agent_pass_rate`); never a 0 for "not measured". */
  pass_rate: Describe;
}

export interface ModelBehaviour {
  model_id: number;
  harnesses: HarnessBehaviour[];
  notes: string[];
}

export async function getModelBehaviour(
  modelId: number,
  init?: RequestInit,
): Promise<ModelBehaviour> {
  const res = await apiFetch(`/generation/models/${modelId}/behaviour/`, init);
  return res.json();
}
