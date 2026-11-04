/**
 * Run-to-run variability API: how much repeats of one setup differ.
 *
 * - `GET /generation/studies/{id}/variability/` — the study Results section
 *   "Run-to-run variation", over the main-stage current runs (usable only, the
 *   majority `config_fingerprint` per cell, common cells; intention-to-treat by
 *   default, `sensitivity=exclude_truncated` as the sensitivity analysis);
 *   `?format=csv` gives one row per (condition, task) cell.
 * - `GET /generation/runs/{id}/replicates/` — every visible run sharing the
 *   run's `config_fingerprint`.
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
  /** Set when the metric is a billing artefact (e.g. $0 on a subscription endpoint), never a floor. */
  billing?: string | null;
}

export interface VariabilityCondition {
  condition_id: number;
  label: string;
  model: string;
  /** Tasks (units) of this condition. */
  n_units: number;
  n_cells: number;
  n_runs: number;
  cells_with_repeats: number;
  pass_agreement: Agreement;
  deploy_agreement: Agreement;
  dispersion: Record<string, DispersionRow>;
}

/** Pairwise code similarity over model-scope, non-test source files. */
export interface PairSummary {
  n_pairs: number;
  median_shingle_jaccard: number | null;
  median_line_jaccard: number | null;
  median_path_jaccard: number | null;
  median_ast_jaccard: number | null;
  median_identical_files: number | null;
}

export interface SimilarityPair {
  run_a: string;
  run_b: string;
  path_jaccard: number | null;
  identical_files: number | null;
  line_jaccard: number | null;
  shingle_jaccard: number | null;
  ast_jaccard: number | null;
  files_a: number;
  files_b: number;
}

export interface CellChecks {
  comparable: boolean;
  reason?: string;
  n_checks?: number;
  disagree?: string[];
  all_agree?: boolean;
  /** Check category per disagreeing check. */
  categories?: Record<string, string>;
}

/** One (condition, task) cell of kept repeats. */
export interface VariabilityCell {
  condition_id: number;
  /** `task:<slug>@<v>`. */
  unit_key: string;
  /** Task slug and title. */
  task: string;
  task_title: string;
  n: number;
  run_ids: string[];
  repeat_indexes: number[];
  /** Per metric, one value per repeat (aligned with `run_ids`; null = unmeasured). */
  values: Record<string, (number | null)[]>;
  stats: Record<string, DescribeStats>;
  /** Repeats with a known pass/fail verdict (a measured evaluation). */
  n_pass_known: number;
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

/** Which current runs were kept, and why the others were left out (attrition, never silent). */
export interface VariabilitySelection {
  current_runs: number;
  not_usable: number;
  excluded_fingerprint: number;
  excluded_truncated: number;
  outside_common_cells: number;
  kept: number;
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

export interface StudyVariability {
  study_id: string;
  status: string;
  /** `exploratory` | `confirmatory`. */
  kind: string;
  /** The study is still running: every block is interim and the planner is withheld. */
  interim: boolean;
  generated_at: string;
  filters: {
    stage: "main";
    fingerprint: VariabilityFingerprint;
    sensitivity: VariabilitySensitivity | null;
    /** Intention-to-treat (truncated runs kept); false under the exclude-truncated sensitivity. */
    itt: boolean;
    planned_only: boolean;
    common_cells: boolean;
  };
  selection: VariabilitySelection;
  design: {
    n_conditions: number;
    n_units: number;
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
  issue_coverage: IssueCoverage | null;
  ceilings: string[];
  /** Metrics whose values are billing artefacts (subscription endpoints), with the sources. */
  billing: {
    sources: string[];
    n_runs: number;
    n_kept: number;
    metrics: string[];
  } | null;
  notes: string[];
}

export type VariabilityFingerprint = "all" | "majority";
export type VariabilitySensitivity = "exclude_truncated";

/**
 * The study variability options: `fingerprint` (default `majority`: the majority config
 * fingerprint per cell) and the optional `sensitivity` (default: intention-to-treat).
 */
export interface VariabilityQuery {
  fingerprint?: VariabilityFingerprint;
  sensitivity?: VariabilitySensitivity | null;
}

/** The query string of a variability request (exported for tests). */
export function variabilityQuery(
  q: VariabilityQuery,
  extra: Record<string, string> = {},
): string {
  return query({
    fingerprint: q.fingerprint,
    sensitivity: q.sensitivity,
    ...extra,
  });
}

export async function getStudyVariability(
  studyId: string,
  q: VariabilityQuery = {},
  options: FetchOptions = {},
): Promise<StudyVariability> {
  const res = await apiFetch(
    `/generation/studies/${studyId}/variability/${variabilityQuery(q)}`,
    { signal: options.signal },
  );
  return res.json();
}

/** The per-cell CSV (one row per condition × task) as a Blob. */
export async function downloadStudyVariabilityCsv(
  studyId: string,
  q: VariabilityQuery = {},
): Promise<Blob> {
  const res = await apiFetch(
    `/generation/studies/${studyId}/variability/${variabilityQuery(q, { format: "csv" })}`,
  );
  return new Blob([await res.text()], { type: "text/csv" });
}

// ── same setup (one config fingerprint) ───────────────────────────────

/**
 * `self`; `retry` (same cell); `replay` (same `run_fingerprint`); `rerun` (ad hoc, beyond plan,
 * or not its cell's current run); `replicate`.
 */
export type ReplicateRelation =
  | "self"
  | "retry"
  | "replicate"
  | "replay"
  | "rerun";

export interface ReplicateMember {
  run_id: string;
  is_self: boolean;
  relation: ReplicateRelation;
  seed: number | null;
  repeat_index: number | null;
  cell_attempt: number | null;
  beyond_plan: boolean;
  study_id: string | null;
  study_name: string | null;
  condition_label: string | null;
  created_at: string;
  status: string;
  outcome: string | null;
  usable: boolean | null;
  pass_rate: number | null;
  source_loc: number | null;
  source_files: number | null;
  cost: number | null;
  total_tokens: number | null;
  llm_calls: number | null;
  wall_seconds: number | null;
  termination: string | null;
  truncated: boolean;
  code_issues: number | null;
  issues_per_kloc: number | null;
  findings_measured: boolean;
  similarity_to_self: {
    path_jaccard: number | null;
    shingle_jaccard: number | null;
    line_jaccard?: number | null;
    identical_files?: number | null;
  } | null;
}

/** Where a replay's or retry's LLM calls first differ from this run's. */
export interface DeterminismProbe {
  run_id: string;
  relation: ReplicateRelation;
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

export interface IssueCoverage {
  apps_measured: number;
  tools_common: string[];
  tools_partial: string[];
}

export interface RunReplicates {
  run_id: string;
  /** `key` is null for a run without a config fingerprint (it is then its own group). */
  grouping: {
    key: "config_fingerprint" | null;
    value: string | null;
  };
  n: number;
  members: ReplicateMember[];
  summary: Record<string, SummaryStats>;
  notes: string[];
  determinism: DeterminismProbe[];
  issue_coverage: IssueCoverage | null;
}

export async function getRunReplicates(
  runId: string,
  options: FetchOptions = {},
): Promise<RunReplicates> {
  const res = await apiFetch(`/generation/runs/${runId}/replicates/`, {
    signal: options.signal,
  });
  return res.json();
}

// ── model ─────────────────────────────────────────────────────────────

export interface ModelVariabilityGroup {
  key: "config_fingerprint";
  value: string;
  n: number;
  /** Pinned task ref (`slug@v`). */
  task: string | null;
  unit_key: string | null;
  /** Run kind. */
  kind: string;
  /** Pinned stack profile ref. */
  stack: string | null;
  /** `[name, version]` from the manifest. */
  harness: [string, string] | null;
  /** Names of the studies the runs belong to. */
  studies: string[];
  run_ids: string[];
  pass: { n: number; passed: number; agree: boolean | null };
  stats: Record<string, Pick<DescribeStats, "n" | "mean" | "sd" | "cv">>;
  similarity: { n_pairs: number; median_shingle_jaccard: number | null };
  replays: {
    run_a: string;
    run_b: string;
    first_divergent_call: number | null;
  }[];
}

export interface ModelVariability {
  model_id: number;
  model_name: string;
  n_groups: number;
  n_runs: number;
  n_completed_runs: number;
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
