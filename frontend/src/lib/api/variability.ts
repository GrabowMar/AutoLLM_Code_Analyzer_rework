/**
 * Run-to-run variability API: how much repeats of one setup differ.
 *
 * - `GET /generation/experiments/{id}/variability/` — the Results tab's
 *   "Run-to-run variation" section (same kept cells and query options as
 *   `/results/`; `?format=csv` gives one row per cell).
 * - `GET /generation/jobs/{id}/replicates/` — contract C3: every visible job
 *   sharing the job's configuration fingerprint (or an approximate legacy key).
 * - `GET /generation/models/{id}/variability/` — within-group spread over a
 *   model's same-setup groups.
 *
 * Everything is descriptive (no p-values). Unmeasured values are `null`
 * (shown as "—"), never 0.
 */
import { apiFetch } from "./core";

interface FetchOptions {
  signal?: AbortSignal;
}

function query(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    q.set(key, String(value));
  }
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

// ── shared ────────────────────────────────────────────────────────────

/** n, mean, sd (n-1), min, max, range, IQR (n >= 4) and CV (ratio-scale metrics only). */
export interface DescribeStats {
  n: number;
  mean: number | null;
  sd: number | null;
  min: number | null;
  max: number | null;
  range?: number | null;
  iqr?: number | null;
  cv: number | null;
}

export type SummaryStats = Pick<
  DescribeStats,
  "n" | "mean" | "sd" | "min" | "max" | "cv"
>;

export interface VariabilityMetric {
  key: string;
  label: string;
  unit: string;
  /** A true zero: CV is defined (never for pass rates or percentages). */
  ratio_scale: boolean;
  better: "higher" | "lower" | null;
  source: string;
  /** Gets an ICC bootstrap interval and a precision plan. */
  core: boolean;
  n_runs: number;
}

export interface Agreement {
  cells: number;
  agree: number;
  share: number | null;
}

export interface DispersionRow {
  n_runs: number;
  /** Cells with 2+ measured repeats (the pooled SD's cells). */
  n_cells: number;
  mean: number | null;
  pooled_sd: number | null;
  df: number;
  median_cv: number | null;
  /** Every measured run has the same value (ceiling / floor). */
  ceiling: boolean;
}

export interface VariabilityCondition {
  condition_id: number;
  label: string;
  model: string;
  n_apps: number;
  n_cells: number;
  n_runs: number;
  cells_with_repeats: number;
  pass_agreement: Agreement;
  deploy_agreement: Agreement;
  dispersion: Record<string, DispersionRow>;
}

export interface PairSummary {
  n_pairs: number;
  median_shingle_jaccard: number | null;
  median_line_jaccard: number | null;
  median_path_jaccard: number | null;
  median_ast_jaccard: number | null;
  median_identical_files: number | null;
  approximate: boolean;
}

export interface SimilarityPair {
  job_a: string;
  job_b: string;
  path_jaccard: number | null;
  identical_files: number | null;
  line_jaccard: number | null;
  shingle_jaccard: number | null;
  ast_jaccard: number | null;
  files_a: number;
  files_b: number;
  approximate: boolean;
}

export interface CellChecks {
  comparable: boolean;
  reason?: string;
  n_checks?: number;
  disagree?: string[];
  all_agree?: boolean;
}

export interface VariabilityCell {
  condition_id: number;
  app_id: number;
  app_slug: string;
  app_name: string;
  n: number;
  job_ids: string[];
  repeat_indexes: number[];
  /** Per metric, one value per repeat (aligned with `job_ids`; null = unmeasured). */
  values: Record<string, (number | null)[]>;
  stats: Record<string, DescribeStats>;
  k_passed: number;
  pass_agree: boolean | null;
  deploy_agree: boolean | null;
  checks: CellChecks | null;
  similarity: (PairSummary & { pairs: SimilarityPair[] }) | null;
  finding_overlap: {
    n_pairs: number;
    median_code_jaccard: number | null;
    median_dependency_jaccard: number | null;
  } | null;
}

export type ComponentSource =
  | "condition"
  | "app"
  | "condition_x_app"
  | "repeat";

export interface VarianceComponent {
  source: ComponentSource;
  variance: number;
  raw: number;
  share: number | null;
  /** The estimate was negative and is shown as 0. */
  truncated: boolean;
  df: number;
}

export interface VarianceComponents {
  n_conditions: number;
  n_apps: number;
  n_runs: number;
  n_cells: number;
  apps_dropped: number;
  estimable: boolean;
  reason: string | null;
  r_harmonic?: number;
  balanced?: boolean;
  components: VarianceComponent[];
  total?: number;
  truncated?: string[];
  ceiling?: boolean;
  icc_within_condition?: number | null;
  between_app_variance?: number | null;
  interaction_variance?: number | null;
  interim: boolean;
}

export interface IccRow {
  condition_id: number;
  n_apps: number;
  n_runs: number;
  icc: number | null;
  icc_raw?: number;
  truncated?: boolean;
  r0?: number;
  design_effect?: number;
  effective_n?: number;
  reason: string | null;
  ci95: [number, number] | null;
  interim: boolean;
}

export interface PassAtK {
  k: number;
  n_cells: number;
  pass_at_k: number | null;
  pass_hat_k: number | null;
  pass_at_k_ci95: [number, number] | null;
}

export interface RankRow {
  condition_id: number;
  mean: number | null;
  rank: number;
  median_rank: number;
  rank_ci95: [number, number];
  p_rank_1: number;
  p_top_3: number;
}

export interface RankBlock {
  n_conditions: number;
  n_apps: number;
  draws: number;
  rows: RankRow[];
  reason: string | null;
  metric: string;
  direction: "higher" | "lower";
  interim: boolean;
}

export interface PlannerRow {
  apps: number;
  repeats: number;
  runs_per_condition: number;
  mean_half_width: number | null;
  delta_half_width: number | null;
}

export interface PlannerMetric {
  available: boolean;
  reason: string | null;
  between_app_variance?: number;
  interaction_variance?: number | null;
  repeat_variance?: number;
  rows: PlannerRow[];
}

export interface ExperimentVariability {
  experiment_id: string;
  status: string;
  interim: boolean;
  generated_at: string;
  filters: {
    exclude_invalid: boolean;
    exclude_truncated: boolean;
    common_cells: boolean;
    fingerprint: string;
    planned_only: boolean;
  };
  design: {
    n_conditions: number;
    n_apps: number;
    n_cells: number;
    n_runs: number;
    cells_with_repeats: number;
    repeats_min: number | null;
    repeats_max: number | null;
    repeats_median: number | null;
    planned_repeats: number;
  };
  metrics: VariabilityMetric[];
  conditions: VariabilityCondition[];
  cells: VariabilityCell[];
  components: Record<string, VarianceComponents>;
  icc: Record<string, IccRow[]>;
  pass_at_k: {
    k_max: number;
    conditions: { condition_id: number; n_cells: number; by_k: PassAtK[] }[];
    interim: boolean;
  };
  checks: {
    cells: number;
    conditions: {
      condition_id: number;
      cells: number;
      cells_all_agree: number;
      share_all_agree: number | null;
      check_cells: number;
      check_cells_agree: number;
    }[];
    flaky: {
      name: string;
      category: string;
      cells: number;
      conditions: number[];
    }[];
    interim: boolean;
  };
  similarity: {
    algo_version: number;
    shingle_k: number;
    conditions: {
      condition_id: number;
      within: PairSummary;
      reference: PairSummary;
      excess_shingle: number | null;
    }[];
    within: PairSummary;
    reference: PairSummary;
    interim: boolean;
  };
  finding_overlap: {
    measured: boolean;
    conditions: {
      condition_id: number;
      n_pairs: number;
      n_pairs_measured: number;
      code: { n: number; median_jaccard: number | null; both_empty: number };
      dependency: {
        n: number;
        median_jaccard: number | null;
        both_empty: number;
      };
    }[];
    by_tool: {
      tool: string;
      kind: "code" | "dependency";
      n_pairs: number;
      median_jaccard: number | null;
    }[];
    interim: boolean;
  };
  rank_stability: Record<string, RankBlock>;
  planner: {
    metrics: Record<string, PlannerMetric>;
    min_apps_exact_test: number;
    z: number;
    basis: string;
  } | null;
  planner_reason: string | null;
  issue_coverage: {
    apps_measured: number;
    tools_common: string[];
    tools_partial: string[];
  } | null;
  ceilings: string[];
  notes: string[];
}

/** The Results tab's analysis options (the kept cells match `/results/`). */
export interface VariabilityQuery {
  exclude_invalid?: boolean;
  exclude_truncated?: boolean;
  common_cells?: boolean;
  fingerprint?: "all" | "majority";
  planned_only?: boolean;
}

function variabilityQuery(
  q: VariabilityQuery,
  extra: Record<string, string> = {},
): string {
  return query({
    exclude_invalid: q.exclude_invalid,
    exclude_truncated: q.exclude_truncated,
    common_cells: q.common_cells,
    fingerprint: q.fingerprint,
    planned_only: q.planned_only,
    ...extra,
  });
}

export async function getExperimentVariability(
  experimentId: string,
  q: VariabilityQuery = {},
  options: FetchOptions = {},
): Promise<ExperimentVariability> {
  const res = await apiFetch(
    `/generation/experiments/${experimentId}/variability/${variabilityQuery(q)}`,
    { signal: options.signal },
  );
  return res.json();
}

/** The per-cell CSV (one row per condition × app) as a Blob. */
export async function downloadExperimentVariabilityCsv(
  experimentId: string,
  q: VariabilityQuery = {},
): Promise<Blob> {
  const res = await apiFetch(
    `/generation/experiments/${experimentId}/variability/${variabilityQuery(q, { format: "csv" })}`,
  );
  return new Blob([await res.text()], { type: "text/csv" });
}

// ── same setup (contract C3) ──────────────────────────────────────────

export type ReplicateRelation = "self" | "replicate" | "replay" | "rerun";

export interface ReplicateMember {
  job_id: string;
  is_self: boolean;
  relation: ReplicateRelation;
  seed: number | null;
  repeat_index: number | null;
  experiment_id: string | null;
  experiment_name: string | null;
  condition_label: string | null;
  batch_id: string | null;
  created_at: string;
  status: string;
  pass_rate: number | null;
  source_loc: number | null;
  source_files: number | null;
  cost: number | null;
  total_tokens: number | null;
  llm_calls: number | null;
  wall_seconds: number | null;
  termination: string | null;
  code_issues: number | null;
  issues_per_kloc: number | null;
  findings_measured: boolean;
  similarity_to_self: {
    path_jaccard: number | null;
    shingle_jaccard: number | null;
    line_jaccard?: number | null;
    identical_files?: number | null;
    approximate?: boolean;
  } | null;
}

/** Where a seeded replay's LLM calls first differ from this run's. */
export interface DeterminismProbe {
  job_id: string;
  comparable: boolean;
  first_divergent_call: number | null;
  identical_first_response: boolean | null;
  reproduced: boolean;
  calls_a: number;
  calls_b: number;
  providers_a: string[];
  providers_b: string[];
  same_providers: boolean;
  basis: string;
}

export interface JobReplicates {
  job_id: string;
  grouping: {
    key: "config_fingerprint" | "approximate" | null;
    value: string | null;
    approximate: boolean;
  };
  n: number;
  members: ReplicateMember[];
  summary: Record<string, SummaryStats>;
  notes: string[];
  determinism?: DeterminismProbe[];
}

export async function getJobReplicates(
  jobId: string,
  options: FetchOptions = {},
): Promise<JobReplicates> {
  const res = await apiFetch(`/generation/jobs/${jobId}/replicates/`, {
    signal: options.signal,
  });
  return res.json();
}

// ── model ─────────────────────────────────────────────────────────────

export interface ModelVariabilityGroup {
  key: "config_fingerprint" | "approximate";
  value: string;
  approximate: boolean;
  n: number;
  app_slug: string | null;
  method: string;
  mode: string;
  experiments: string[];
  job_ids: string[];
  pass: { n: number; passed: number; agree: boolean | null };
  stats: Record<string, Pick<DescribeStats, "n" | "mean" | "sd" | "cv">>;
  similarity: { n_pairs: number; median_shingle_jaccard: number | null };
  replays: {
    job_a: string;
    job_b: string;
    first_divergent_call: number | null;
  }[];
}

export interface ModelVariability {
  model_id: number;
  model_name: string;
  n_groups: number;
  n_runs: number;
  n_completed_jobs: number;
  overall: {
    metrics: Record<
      string,
      {
        pooled_sd: number | null;
        df: number;
        median_cv: number | null;
        groups_with_cv: number;
      }
    >;
    pass_agreement: { groups: number; agree: number; share: number | null };
    median_shingle_jaccard: number | null;
    replays: { pairs: number; reproduced: number };
  };
  groups: ModelVariabilityGroup[];
  notes: string[];
}

export async function getModelVariability(
  modelId: number,
  options: FetchOptions = {},
): Promise<ModelVariability> {
  const res = await apiFetch(`/generation/models/${modelId}/variability/`, {
    signal: options.signal,
  });
  return res.json();
}
