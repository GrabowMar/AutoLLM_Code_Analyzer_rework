import { apiErrorStatus, apiFetch } from "./core";

// ── MSS v2 (docs/design/MSS_V2.md) ──────────────────────────────────────────
// Every MSS score is null when its input is missing (never 0); a model outside
// the eligible frame has no MSS and therefore no composite.

/** The four MSS components, in weight order. */
export type MssComponent = "popularity" | "price" | "capability" | "recency";
export type PriceTier = "frontier" | "mid" | "budget";
export type RouteKind = "go" | "anthropic" | "openrouter";

/**
 * Nominal weights (backend `rankings.services.constants`). The selection
 * payload carries the weights it actually used (`params.weights`,
 * `params.tier_weights`); prefer those when a payload is at hand.
 */
export const MSS_WEIGHTS: Record<MssComponent, number> = {
  popularity: 0.4,
  price: 0.3,
  capability: 0.2,
  recency: 0.1,
};
/** Amendment A1: the within-tier score (MSS_tier) that drives the stratified selection. */
export const MSS_TIER_WEIGHTS: Record<MssComponent, number> = {
  popularity: 0.45,
  capability: 0.3,
  price: 0.15,
  recency: 0.1,
};
export const PRICE_TIERS: PriceTier[] = ["frontier", "mid", "budget"];

export interface RankingFindings {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

/** Spread over apps: repeats are averaged per (condition, app) first; `n_apps` units, `n_jobs` runs. */
export interface RankingVariance {
  n_jobs: number;
  n_apps?: number;
  density_per_kloc_mean: number | null;
  density_per_kloc_stdev: number | null;
  smoke_pass_rate_stdev: number | null;
}

export interface RankingInstrumentSet {
  tools_counted: string[];
  tools_left_out: string[];
  apps_measured: number;
}

/** Why a model is outside the eligible frame (a frame step K* or E* and a reason code). */
export interface RankingExclusion {
  step: string;
  code: string;
  detail?: string | null;
}

export interface RankingRoute {
  model_id: string | null;
  available: boolean | null;
  paid: boolean | null;
  prerelease: boolean | null;
  served_name: string | null;
  permaslug: string | null;
  identity?: string | null;
}

export interface MssWindow {
  start: string;
  end: string;
  days: number;
}

/** OpenRouter usage behind the popularity score (exposure-adjusted, left-censored days imputed). */
export interface PopularityDetail {
  source?: string | null;
  definition?: string | null;
  volume_share?: number | null;
  value_share?: number | null;
  days_available?: number | null;
  days_listed?: number | null;
  days_censored?: number | null;
  censored?: boolean | null;
  short_exposure?: boolean | null;
  window?: MssWindow | null;
  as_of?: string | null;
}

/** LMArena WebDev rating behind the capability score. */
export interface WebdevRating {
  rating: number | null;
  rating_lower: number | null;
  rating_upper: number | null;
  arena_name: string | null;
  publish_date: string | null;
  /** How the arena name was matched: exact, variant, assumed. */
  match?: string | null;
  from_model?: string | null;
}

/** First-party list price (USD per million tokens) behind the price score. */
export interface ListPrice {
  input: number | null;
  output: number | null;
  cache_read: number | null;
  cache_write: number | null;
  unit?: string | null;
  source: string | null;
  fetched_at: string | null;
  discount_undone?: number | boolean | null;
}

export interface RankingRow {
  model_id: string;
  model_name: string;
  provider: string;
  is_free: boolean;
  context_length: number | null;
  price_per_million_input: number | null;
  price_per_million_output: number | null;
  apps: number;
  apps_completed: number;
  apps_failed?: number;
  /** Mean generation time (s); null until an app completed. */
  avg_duration: number | null;
  /**
   * Code issues the empirical score counts, by normalized level; null when
   * the instrument set measured no app of the model.
   */
  findings: RankingFindings | null;
  /** Known-vulnerability advisories; null when unmeasured or no advisory scanner is in the instrument set. */
  dependency_findings?: number | null;
  /** Dynamic tools' findings; null when unmeasured or no dynamic tool is in the instrument set. */
  runtime_findings?: number | null;
  /** Findings in test code; null when unmeasured. */
  test_findings?: number | null;
  /** How many experiment conditions the row pools (harness, prompt profile or arm). */
  conditions_pooled?: number;
  /** Set when the row pools several conditions; study reports compare them separately. */
  pooling_note?: string | null;
  /** The tools that measured every compared app (the empirical score's instrument). */
  instrument_set?: RankingInstrumentSet | null;
  ai_findings: RankingFindings | null;
  local_loc?: number;
  n_trials: number;
  functional_pass_rate: number | null;
  variance: RankingVariance | null;

  // MSS v2
  canonical_id: string | null;
  vendor: string | null;
  /** US, CN, EU, … or "unknown". */
  origin: string | null;
  open_weights: boolean | null;
  release_date: string | null;
  line_key?: string | null;
  pinned_version: Record<string, unknown> | null;
  routes: Partial<Record<RouteKind, RankingRoute>>;
  preferred_route: RouteKind | null;
  eligible: boolean;
  exclusion: RankingExclusion | null;
  popularity_score: number | null;
  price_score: number | null;
  capability_score: number | null;
  recency_score: number | null;
  mss_score: number | null;
  /** Share of the MSS weight that was present (null without an MSS). */
  mss_coverage: number | null;
  mss_missing: MssComponent[];
  mss_flags: string[];
  /** Set on a route copy / dated member: the canonical model whose MSS it shows. */
  mss_inherited_from: string | null;
  /** Cost of one reference OpenCode run at list prices (USD). */
  run_cost_usd: number | null;
  /** π: 30 runs × retry factor at the planning basket (USD). */
  planning_cost_usd: number | null;
  price_tier: PriceTier | null;
  popularity: PopularityDetail | null;
  webdev_rating: WebdevRating | null;
  list_price: ListPrice | null;

  empirical_quality_score: number | null;
  composite_score: number | null;
  [key: string]: unknown;
}

export interface RankingsListParams {
  page?: number;
  per_page?: number;
  /** A backend `SORT_KEY_MAP` key (see `$lib/components/rankings/mss.ts`). */
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  search?: string;
  provider?: string;
  max_price?: number;
  min_context?: number;
  min_composite?: number;
  min_mss?: number;
  eligible_only?: boolean;
  include_free?: boolean;
  has_capability?: boolean;
  prompt_hash?: string;
  config_fingerprint?: string;
  study_id?: string;
}

export interface RankingsPagination {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface RankingsStatistics {
  total: number;
  eligible: number;
  with_popularity: number;
  with_capability: number;
  unique_vendors: number;
  /** Mean MSS over the filtered rows that have one; null when none has. */
  avg_mss: number | null;
}

export interface RankingsResponse {
  count: number;
  rankings: RankingRow[];
  pagination: RankingsPagination;
  statistics: RankingsStatistics;
}

export interface RankingsTopResponse {
  models: RankingRow[];
  count: number;
}

// ── Sources, flow and tiers ─────────────────────────────────────────────────

/** One MSS input source with its provenance. Extra keys (plan, value_usd, publish_dates, …) vary per source. */
export interface SourceInfo {
  name: string;
  url?: string | null;
  license?: string | null;
  citation?: string | null;
  as_of?: string | null;
  /** One timestamp, or the distinct fetch dates of a per-model input. */
  fetched_at?: string | string[] | null;
  window?: MssWindow | null;
  snapshot_id?: number | null;
  [key: string]: unknown;
}

/** One step of the PRISMA-style frame flow (K* canonicalisation, E* eligibility). */
export interface FlowStep {
  step: string;
  label: string;
  remaining: number;
  excluded: { id: string; code: string; detail?: string | null }[];
}

export interface TierInfo {
  rule: string;
  budget_max_usd_per_run: number | null;
  frontier_min_usd_per_run_exclusive: number | null;
  scale?: number[];
  counts: Partial<Record<PriceTier, number>>;
  bands_usd: Partial<Record<PriceTier, [number, number]>>;
  /** Selection payloads only: the capability scores in each tier (sorted). */
  capability_by_tier?: Partial<Record<PriceTier, number[]>>;
}

export interface RankingSourcesCoverage {
  canonical_models: number;
  passing_filters_e1_e7: number;
  eligible: number;
  with_popularity: number;
  with_price: number;
  with_capability: number;
  with_recency: number;
  popularity_proxy: number;
}

export interface RankingSourcesResponse {
  as_of: string;
  sources: SourceInfo[];
  coverage: RankingSourcesCoverage;
  /** Empty when no model is eligible yet. */
  tiers: Partial<TierInfo>;
  flow: FlowStep[];
}

// ── Study selection (Amendment A1: stratified quotas) ───────────────────────

export interface GoUsage {
  monthly_limit_usd: number;
  planning_cost_usd: number;
  share_of_monthly_limit: number;
  weeks_needed: number;
  /** Null when the per-run Go cost is unknown or zero. */
  runs_per_5h: number | null;
}

export type Robustness = "robust" | "borderline" | "fragile";

export interface SelectionPick {
  position: number;
  canonical_id: string;
  display_name: string;
  tier: PriceTier;
  tier_rank: number;
  mss_rank: number | null;
  mss: number | null;
  mss_tier: number | null;
  scores: Record<MssComponent, number | null>;
  /** Price re-normalised inside the tier band (the C that enters MSS_tier). */
  tier_price_score: number | null;
  coverage: number | null;
  missing: MssComponent[];
  flags: string[];
  /** `rank`, `forced` or `quota:<codes>`. */
  admitted_by: string;
  vendor: string;
  origin: string;
  open_weights: boolean | null;
  /** Capability percentile within the tier as a 0–1 fraction (null without capability). */
  capability_pct_in_tier: number | null;
  route: RouteKind;
  route_reason: string;
  pinned_version: Record<string, unknown> | null;
  run_cost_usd: number | null;
  planning_cost_usd: number | null;
  planning_cost_h0_usd?: number | null;
  go_usage: GoUsage | null;
  webdev: WebdevRating | null;
  release_date: string | null;
  /** min(S1, S3) selection frequency (S1 alone when S3 did not run); null when the sensitivity analysis did not run. */
  selection_frequency: number | null;
  /** The per-run frequencies the minimum is taken over. */
  selection_frequency_by_run?: Partial<Record<"S1" | "S3", number>>;
  /** e.g. "min(S1, S3)". */
  robustness_basis?: string | null;
  robustness: Robustness | null;
  /** Leaves the set under an S6 quality-floor variant (40th percentile, no floor, rating upper bound). */
  floor_sensitive?: boolean;
}

/** An eligible model ranked above the last pick of its tier but not picked. */
export interface BlockedModel {
  canonical_id: string;
  tier: PriceTier;
  tier_rank: number;
  mss_tier: number | null;
  mss: number | null;
  /** `cap:Q-vendor(x)`, `budget:B-openrouter`, `floor:Q-quality(...)`, `lookahead:<codes>`, `no_paid_route`, … */
  blocked_by: string;
}

export interface NextInTier {
  canonical_id: string;
  tier_rank: number;
  mss_tier: number | null;
  route: RouteKind | null;
  selectable: boolean;
  note: string | null;
}

export interface Replacement {
  replace: string;
  /** Who enters when this pick is excluded; null when no feasible set exists without it. */
  with: string[] | null;
  also_leaves: string[] | null;
  feasible: boolean;
}

export interface Alternates {
  next_in_tier?: Partial<Record<PriceTier, NextInTier[]>>;
  replacements?: Replacement[];
}

export interface ConstraintStatus {
  code: string;
  kind: string;
  value: unknown;
  limit: unknown;
  satisfied: boolean;
  /** Relaxing it alone changes the set; null when not tested (exact quotas). */
  binding: boolean | null;
}

export interface FrequencyRow {
  canonical_id: string;
  frequency: number;
  picked: boolean;
  /** robust | borderline | fragile for picks; contender (or other labels) for non-picks. */
  class: string;
}

export interface SensitivityRun {
  run: string;
  description: string;
  draws: number;
  infeasible_draws: number;
  frequencies: FrequencyRow[];
  jaccard_mean: number | null;
  jaccard_p5: number | null;
  /** τ_b, top-k τ and RBO are computed within each nominal price tier and averaged over the tiers. */
  rank_metrics_scope?: string;
  kendall_tau_b_mean: number | null;
  kendall_tau_b_p5: number | null;
  top_k_per_tier?: number;
  kendall_tau_top_k_mean?: number | null;
  rbo_mean: number | null;
  rbo_p5: number | null;
}

export interface SensitivityChange {
  change: string;
  feasible: boolean;
  entered: string[];
  left: string[];
  jaccard: number | null;
}

export interface SensitivityChangeRun {
  run: string;
  description: string;
  changes: SensitivityChange[];
}

export interface SelectionSensitivity {
  S1?: SensitivityRun;
  S2?: SensitivityRun;
  S3?: SensitivityRun;
  S4?: SensitivityChangeRun;
  S5?: SensitivityChangeRun;
  S6?: SensitivityChangeRun;
  contenders?: FrequencyRow[];
  robustness_basis?: string;
}

export interface RouteBudgetUse {
  planned_usd: number;
  available_usd: number | null;
  effective_usd: number | null;
  source?: string | null;
  /** Anthropic only: the prompt-cache hit share the Claude runs are planned at. */
  cache_hit_assumed?: number;
  /** Anthropic only: the Claude picks' cost with no cache hits. */
  planned_h0_usd?: number;
  /** Anthropic only: the smallest cache-hit share at which B-anthropic still holds (0 = holds at any share). */
  break_even_cache_hit?: number | null;
}

export interface BudgetUse {
  anthropic: RouteBudgetUse;
  openrouter: RouteBudgetUse;
  go: ({ canonical_id: string } & GoUsage)[];
  contingency: number;
  runs_per_model: number;
  retry_factor: number;
}

export interface SelectionResult {
  picks: SelectionPick[];
  by_tier: Partial<Record<PriceTier, string[]>>;
  budget_use: BudgetUse;
  ties_at_4dp: string[][];
  origin_mix: Record<string, number>;
  open_weight_mix: Record<string, number>;
}

export interface InfeasibleDiagnosis {
  relaxations: { constraint: string; feasible_if_relaxed: boolean }[];
  selectable_per_tier: Partial<Record<PriceTier, number>>;
  quota_per_tier: Partial<Record<PriceTier, number>>;
  min_openrouter_budget_effective_usd: number | null;
  min_anthropic_budget_effective_usd: number | null;
  floor_relaxations_tried: unknown[];
  feasible_without_quality_floor: boolean;
  feasible_with_study_months?: number | null;
  /** The floor level the relaxations and minimal budgets were computed at. */
  diagnosed_at_floor_percentiles?: Partial<Record<PriceTier, number | null>>;
  /** Set when the forced picks themselves break a quota, cap or budget. */
  forced_violation?: string;
  note: string;
}

export interface SelectionBudgets {
  contingency: number;
  anthropic_usd: number;
  anthropic_effective: number;
  openrouter_available_usd: number | null;
  openrouter_source: string | null;
  openrouter_credit: number | null;
  openrouter_effective: number | null;
}

export interface FloorInfo {
  levels: Partial<Record<PriceTier, number>>;
  relaxations: {
    tier: PriceTier;
    from_percentile: number;
    to_percentile: number;
    reason: string;
  }[];
  popularity_cut_for_missing_capability: number | null;
}

/** One frame model as the selection scored it (eligible or not). */
export interface SelectionCandidate {
  canonical_id: string;
  anchor_model_id?: string;
  display_name: string;
  eligible: boolean;
  exclusion: RankingExclusion | null;
  vendor: string;
  origin: string;
  open_weights: boolean | null;
  scores?: Record<MssComponent, number | null>;
  mss?: number | null;
  mss_tier?: number | null;
  tier?: PriceTier | null;
  route?: RouteKind | null;
  admissible?: boolean;
  floor_reason?: string | null;
  [key: string]: unknown;
}

export interface SelectionPayload {
  version: string;
  as_of: string;
  params: Record<string, unknown> & {
    weights?: Record<MssComponent, number>;
    tier_weights?: Record<MssComponent, number>;
    n?: number;
    tier_quotas?: Record<PriceTier, number>;
    study_months?: number;
  };
  constraints: Record<string, unknown>;
  budgets: SelectionBudgets;
  flow: FlowStep[];
  tiers: Partial<TierInfo>;
  floor: FloorInfo | null;
  sources: SourceInfo[];
  validity: {
    spearman_popularity_vs_lmarena_interactions?: {
      rho: number | null;
      n: number;
    };
    spearman_popularity_vs_hf_downloads_open_weight?: {
      rho: number | null;
      n: number;
    };
    in_code_tag_top10?: string[];
  } | null;
  candidates: SelectionCandidate[];
  selection: SelectionResult | null;
  blocked: BlockedModel[];
  alternates: Alternates;
  constraint_status: ConstraintStatus[];
  sum_optimal: {
    set: string[];
    sum_mss_tier: number;
    exhaustive: boolean;
    nodes: number;
    differs_from_greedy: boolean;
    sum_mss_tier_greedy: number;
  } | null;
  infeasible: InfeasibleDiagnosis | null;
  sensitivity: SelectionSensitivity | null;
  warnings?: string[];
  /** Live previews only. */
  preview?: {
    written: boolean;
    sensitivity: boolean;
    draws_capped: boolean;
    draws: Record<string, number>;
  };
  /** Snapshots only (the record's inputs). */
  inputs?: Record<string, unknown>;
}

export type SnapshotStatus = "draft" | "confirmed" | "superseded";

export interface SelectionOverride {
  replace: string;
  with: string;
  reason: string;
}

export interface SelectionConfirmation {
  final_ids?: string[];
  overrides?: SelectionOverride[];
  notes?: string;
  nominal_ids?: string[];
}

export interface SelectionSnapshotSummary {
  id: number;
  created_at: string;
  kind: string;
  version: string;
  status: SnapshotStatus;
  as_of: string | null;
  inputs_sha256: string;
  code_version: string;
  picks: string[];
  by_tier: Partial<Record<PriceTier, string[]>>;
  created_by: string | null;
  confirmed_at: string | null;
  confirmed_by: string | null;
  confirmation: SelectionConfirmation;
}

export interface SelectionSnapshotDetail extends SelectionSnapshotSummary {
  params: Record<string, unknown>;
  payload: SelectionPayload;
}

export interface SelectionPreviewParams {
  n?: number;
  frontier?: number;
  mid?: number;
  budget?: number;
  include_previews?: boolean;
  /** true runs capped draws (S1 1,000 / S2 500 / S3 0). */
  sensitivity?: boolean;
  /** Omit to use the live OpenRouter credit (staff) or the cached credit. */
  openrouter_budget?: number;
  anthropic_budget?: number;
  study_months?: number;
}

export interface SelectionCreateInput {
  n?: number;
  tier_quotas?: Record<PriceTier, number>;
  include_previews?: boolean;
  exclude?: string[];
  force?: string[];
  anthropic_budget?: number;
  openrouter_budget?: number;
  study_months?: number;
  sensitivity?: boolean;
}

export interface SelectionConfirmInput {
  final_ids: string[];
  overrides: SelectionOverride[];
  notes: string;
}

// ── Requests ────────────────────────────────────────────────────────────────

function _qs(params: Record<string, unknown>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

export function exportRankingsUrl(): string {
  return "/api/rankings/export/";
}

export async function getRankings(
  params: RankingsListParams = {},
): Promise<RankingsResponse> {
  const res = await apiFetch(
    `/rankings/${_qs(params as Record<string, unknown>)}`,
  );
  return res.json();
}

/** The top eligible canonical models by MSS. */
export async function getTopModels(count = 10): Promise<RankingsTopResponse> {
  const res = await apiFetch(`/rankings/top/?count=${count}`);
  return res.json();
}

/** Provenance of every MSS input, coverage counts, the tier cuts and the frame flow. */
export async function getRankingSources(): Promise<RankingSourcesResponse> {
  const res = await apiFetch("/rankings/sources/");
  return res.json();
}

/** Live selection preview on the current data; writes nothing. */
export async function getSelectionPreview(
  params: SelectionPreviewParams = {},
  init: RequestInit = {},
): Promise<SelectionPayload> {
  const res = await apiFetch(
    `/rankings/selection/${_qs(params as Record<string, unknown>)}`,
    init,
  );
  return res.json();
}

export async function listSelectionSnapshots(
  limit = 50,
): Promise<SelectionSnapshotSummary[]> {
  const res = await apiFetch(`/rankings/selection/snapshots/?limit=${limit}`);
  return res.json();
}

export async function getSelectionSnapshot(
  id: number,
): Promise<SelectionSnapshotDetail> {
  const res = await apiFetch(`/rankings/selection/snapshots/${id}/`);
  return res.json();
}

/** The confirmed selection, else the newest draft; null when there is none yet. */
export async function getLatestSelection(): Promise<SelectionSnapshotDetail | null> {
  try {
    const res = await apiFetch("/rankings/selection/latest/");
    return await res.json();
  } catch (e) {
    if (apiErrorStatus(e) === 404) return null;
    throw e;
  }
}

/** Staff only. Runs the full sensitivity draws synchronously (about a minute). */
export async function createSelectionSnapshot(
  body: SelectionCreateInput,
): Promise<SelectionSnapshotDetail> {
  const res = await apiFetch("/rankings/selection/snapshots/", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return res.json();
}

/** Staff only. Every change against the nominal picks needs an override with a reason. */
export async function confirmSelection(
  id: number,
  body: SelectionConfirmInput,
): Promise<SelectionSnapshotDetail> {
  const res = await apiFetch(`/rankings/selection/snapshots/${id}/confirm/`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return res.json();
}

// ── Empirical-ranking sensitivity (severity weights) ────────────────────────

export interface SensitivityRankEntry {
  model_id: string;
  empirical_quality: number | null;
}

export interface SensitivityScheme {
  scheme: string;
  weights: Record<string, number>;
  ranking: SensitivityRankEntry[];
  kendall_tau: number;
  adjacent_swaps: string[][];
}

export interface SensitivityResponse {
  models_evaluated: number;
  baseline_ranking: SensitivityRankEntry[];
  schemes: SensitivityScheme[];
}

export async function getRankingsSensitivity(): Promise<SensitivityResponse> {
  const res = await apiFetch("/rankings/sensitivity/");
  return res.json();
}
