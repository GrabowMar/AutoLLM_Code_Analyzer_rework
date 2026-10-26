/**
 * Types of the analysis API v2 (`/api/analysis/...`).
 *
 * They mirror `backend/analysis/api/schema.py` and the dict payloads built by
 * the engine and research services (frozen plans, measurements, preflight
 * reports, protocols, post-run reports), verified against real payloads of
 * the backend. Research-validity conventions carried by these types:
 *
 * - `null` is missing data (a tool failed, nothing was measured, a density
 *   below the SLOC floor), never zero. Render it as "—", never as 0.
 * - A tool result is one of {@link TOOL_STATUSES}; only `succeeded` and
 *   `not_applicable` are OK. Zero findings never means "clean" unless every
 *   required tool is OK ({@link AnalysisRunListItem.is_complete}).
 * - Static findings and AI findings are separate measurements; AI counts are
 *   consensus clusters (grounded, majority of review samples).
 */

import type { HarnessOptionField, HarnessOptions } from "$lib/api/generation";

// ── Vocabularies ──────────────────────────────────────────────────────

/** Severities, most severe first (`severity_rank` is the index). */
export const SEVERITIES = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
] as const;
export type Severity = (typeof SEVERITIES)[number];
export type SeverityCounts = Record<Severity, number>;

export const RUN_STATUSES = [
  "queued",
  "running",
  "completed",
  "partial",
  "failed",
  "cancelled",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];
/** Work in flight (poll while any run is in one of these). */
export const RUN_ACTIVE_STATUSES: readonly RunStatus[] = ["queued", "running"];
/** Finished with measurements (partial = a required tool did not succeed). */
export const RUN_FINISHED_STATUSES: readonly RunStatus[] = [
  "completed",
  "partial",
];
export const RUN_TERMINAL_STATUSES: readonly RunStatus[] = [
  "completed",
  "partial",
  "failed",
  "cancelled",
];

export const RUN_PURPOSES = ["research", "adhoc", "canary"] as const;
export type RunPurpose = (typeof RUN_PURPOSES)[number];

/**
 * Executor phase: `queued`, `waiting_for_slot`, `requeued`, `starting`,
 * `loading_sample`, `preparing`, `image`, `running:<tool slug>`,
 * `reviewing`, `finalizing`, `cancelling`, `done` ("" before submission).
 */
export type RunPhase = string;

export const TOOL_STATUSES = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "timed_out",
  "not_applicable",
  "skipped",
  "cancelled",
] as const;
export type ToolStatus = (typeof TOOL_STATUSES)[number];
/** The only statuses that leave a tool's measurement intact. */
export const TOOL_OK_STATUSES: readonly ToolStatus[] = [
  "succeeded",
  "not_applicable",
];
/** Statuses that mean the tool produced no trustworthy data. */
export const TOOL_NO_DATA_STATUSES: readonly ToolStatus[] = [
  "failed",
  "timed_out",
  "skipped",
  "cancelled",
];
/** Progress/report matrices add `missing` (no result row) and `not_run`. */
export type ToolCellStatus = ToolStatus | "missing" | "not_run";

/** `ToolResult.failure_reason` ("" when the tool did not fail). */
export type ToolFailureReason =
  | ""
  | "tool_error"
  | "timeout"
  | "oom"
  | "parse_error"
  | "infra_error"
  | "unavailable"
  | "invalid_config"
  | "invalid_output"
  | "budget"
  | "cancelled";

export const REVIEW_SAMPLE_STATUSES = [
  "pending",
  "succeeded",
  "failed",
  "invalid_output",
  "truncated",
  "budget_exhausted",
  "timed_out",
  "cancelled",
] as const;
export type ReviewSampleStatus = (typeof REVIEW_SAMPLE_STATUSES)[number];

export type ToolKind = "static" | "metrics" | "ai";
export type ToolCategory =
  | "security"
  | "secrets"
  | "lint"
  | "types"
  | "dead_code"
  | "complexity"
  | "duplication"
  | "style"
  | "container"
  | "ai";

export const FINDING_CATEGORIES = [
  "security",
  "secrets",
  "bug",
  "type",
  "maintainability",
  "dead_code",
  "duplication",
  "complexity",
  "style",
  "container",
  "dependency",
  "other",
] as const;
export type FindingCategory = (typeof FINDING_CATEGORIES)[number];

export const FILE_ORIGINS = [
  "authored",
  "added",
  "modified",
  "scaffold",
  "platform",
  "unknown",
] as const;
export type FileOrigin = (typeof FILE_ORIGINS)[number];
/** Origins that mean "the model wrote this". */
export const MODEL_ORIGINS: readonly FileOrigin[] = [
  "authored",
  "added",
  "modified",
];

export const FILE_KINDS = [
  "source",
  "config",
  "manifest",
  "lockfile",
  "doc",
  "asset",
  "build",
  "env",
  "other",
] as const;
export type FileKind = (typeof FILE_KINDS)[number];

/** AI finding grounding verdicts ("" for static findings). */
export const GROUNDINGS = [
  "exact",
  "relocated",
  "ungrounded",
  "invalid_file",
  "out_of_range",
] as const;
export type GroundingVerdict = (typeof GROUNDINGS)[number];
export type Grounding = GroundingVerdict | "";
export type GroundingCounts = Record<GroundingVerdict, number>;

export type FindingConfidence = "high" | "medium" | "low";

export const STUDY_STATUSES = [
  "draft",
  "locked",
  "running",
  "completed",
  "cancelled",
] as const;
export type StudyStatus = (typeof STUDY_STATUSES)[number];

/** `preflight.launch.status`: runs are submitted by a background job after launch. */
export type LaunchStatus =
  "submitting" | "interrupted" | "submitted" | "failed" | "cancelled";
/** Launch states in which the study keeps submitting (launch again resumes `interrupted`). */
export const LAUNCH_PENDING_STATUSES: readonly LaunchStatus[] = [
  "submitting",
  "interrupted",
];

export type ReportStatus = "generating" | "failed" | "ready";

export type ReviewerEngine = "single_shot" | "agent";
export const REASONING_EFFORTS = [
  "none",
  "minimal",
  "low",
  "medium",
  "high",
  "max",
] as const;
export type ReasoningEffort = (typeof REASONING_EFFORTS)[number];
export type ReviewOutputMode = "json_schema" | "json_object" | "prompt";

export const DESIGN_FACTORS = [
  "model",
  "condition",
  "harness",
  "mode",
  "stack",
  "app",
] as const;
export type DesignFactor = (typeof DESIGN_FACTORS)[number];
export type Correction = "holm" | "bh" | "none";
export type CellAggregate = "mean" | "median";
export type DesignMode = "blocked" | "independent";

export type TargetSpecKind = "experiment" | "batch" | "jobs" | "filter";
export type EligibilitySeverity = "exclude" | "warn" | "info";
/** Per target × tool in a preflight: would the tool scan anything? */
export type ToolApplicability =
  "run" | "not_applicable" | "unavailable" | "error";
/** Preflight tool readiness on the plan's analyzer image. */
export type ToolReadiness =
  "ready" | "image_missing" | "canary_failed" | "canary_unknown";
/** Self-test verdict per tool (`untested`: no self-test evidence at all). */
export type CanaryVerdict = "passed" | "failed" | "unverified";

/** Outcome keys guaranteed by the measurement contract (more may exist). */
export const OUTCOME_KEYS = [
  "static_findings_per_kloc",
  "static_issues_per_kloc",
  "security_issues_per_kloc",
  "high_plus_issues_per_kloc",
  "has_high_security",
  "ai_consensus_per_kloc",
  "complexity_max",
  "complexity_mean",
  "duplication_pct",
  "functional_pass_rate",
  "measured_sloc",
  "suppression_markers_per_kloc",
  "static_issues",
  "security_issues",
  "high_plus_issues",
  "weighted_per_kloc",
  "ai_grounding_rate",
  "ai_mean_pairwise_jaccard",
] as const;
export type OutcomeKey = (typeof OUTCOME_KEYS)[number];
export type OutcomeKind = "rate" | "count" | "binary" | "metric";

export const FINDING_ORDERINGS = [
  "severity",
  "-severity",
  "file",
  "-file",
  "tool",
  "rule",
  "category",
  "created",
  "-created",
] as const;
export type FindingOrdering = (typeof FINDING_ORDERINGS)[number];

export const RUN_ORDERINGS = [
  "-created_at",
  "created_at",
  "status",
  "name",
  "-completed_at",
  "completed_at",
  "-duration",
  "duration",
] as const;
export type RunOrdering = (typeof RUN_ORDERINGS)[number];

export const STUDY_EXPORTS = [
  "tables.zip",
  "replication.zip",
  "findings.csv",
  "runs.csv",
] as const;
export type StudyExportName = (typeof STUDY_EXPORTS)[number];
export type RunExportFormat = "json" | "csv" | "sarif";

/** JSON value (tool configs, protocol values shown verbatim). */
export type JsonValue =
  string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

// ── Shared envelopes and errors ───────────────────────────────────────

/** Pagination envelope; a page past the end is empty, never clamped. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface ProfileRef {
  id: string;
  slug: string;
  version: number;
  name: string;
}

/** 422 body: Ninja's `detail` list plus `field_errors` keyed by dotted path. */
export interface ApiValidationErrorBody {
  detail: { loc: (string | number)[]; msg: string; type: string }[];
  field_errors?: Record<string, string>;
}

/** Any other error body (`{detail}`; 409 conflicts may add context such as `study_id`). */
export interface ApiErrorBody {
  detail: string;
  [key: string]: unknown;
}

/** 409 body of a refused study lifecycle action (`code` e.g. `blocking_issues`, `invalid_state`). */
export interface StudyErrorBody {
  detail: string;
  code: string;
  /** `blocking_issues`: the preflight problems that prevent locking. */
  blocking?: PreflightIssue[];
  /** `nothing_to_launch`: targets that could not be submitted. */
  skipped?: LaunchSkip[];
  [key: string]: unknown;
}

/** 409 for a missing report (`report.md`, `replication.zip`). */
export interface ReportNotReadyBody {
  detail: string;
  report_status: ReportStatus | "none";
  error: string | null;
}

/** 400 when the AI reviewer is enabled but no OpenRouter key is available. */
export interface MissingKeyErrorBody {
  detail: string;
  remediation: string;
}

// ── Catalog: tools, image, self-test ──────────────────────────────────

export type ToolConfigFieldType =
  "string" | "number" | "integer" | "boolean" | "select" | "multiselect";

export interface ToolConfigOption {
  value: string;
  label: string;
}

/** One option of a tool's config (schema-driven editors). */
export interface ToolConfigField {
  name: string;
  type: ToolConfigFieldType;
  label: string;
  description: string;
  default: JsonValue;
  /** `argv` changes the command line; `interpretation` only how output is read. */
  applies_to: "argv" | "interpretation";
  /** select / multiselect */
  options?: ToolConfigOption[];
  /** multiselect */
  min_items?: number;
  max_items?: number;
  /** string */
  pattern?: string;
  min_length?: number;
  max_length?: number;
  placeholder?: string;
  /** number / integer */
  min?: number;
  max?: number;
  step?: number;
}

export type ToolConfigValues = Record<string, JsonValue>;

/** One canary expectation: a rule glob (and optional path glob) that must match. */
export interface CanaryExpectation {
  rule: string;
  path?: string;
}

export interface ToolExpectations {
  must_find: CanaryExpectation[];
  clean_categories: string[];
  min_findings: number | null;
}

/** A tool's verdict in one self-test run (`canary.evaluate`). */
export interface CanaryToolEvaluation {
  passed: boolean;
  verdict: CanaryVerdict;
  status: ToolStatus | "missing";
  failure_reason: string;
  version: string;
  expected: CanaryExpectation[];
  found: (CanaryExpectation & { matched: string[] })[];
  missing: CanaryExpectation[];
  unexpected_in_clean: { rule: string; path: string; category: string }[];
  clean_categories: string[];
  findings_total: number;
  positive_findings: number;
  min_findings: number | null;
  has_expectations: boolean;
}

/** Latest verdict of a tool across self-tests (`canary.latest_per_tool`). */
export interface CanaryToolVerdict extends CanaryToolEvaluation {
  run_id: string;
  image_tag: string;
  evaluated_at: string | null;
}

/** A whole self-test run evaluated (run detail `canary`, `selftest/latest`). */
export interface CanaryEvaluation {
  passed: boolean;
  finished: boolean;
  run_id: string;
  run_status: RunStatus;
  image_tag: string;
  plan_hash: string;
  evaluated_at: string | null;
  tools: Record<string, CanaryToolEvaluation>;
}

/** `GET /tools/` item: catalog spec + verified version + latest canary verdict. */
export interface AnalysisTool {
  slug: string;
  name: string;
  description: string;
  kind: ToolKind;
  category: ToolCategory;
  languages: string[];
  file_kinds: string[];
  output_kind: "findings" | "metrics" | "mixed" | string;
  /** Lucide icon name. */
  icon: string;
  docs_url: string;
  display_order: number;
  default_enabled: boolean;
  default_required: boolean;
  mem_limit: string;
  timeout_s: number;
  config_schema: ToolConfigField[];
  default_config: ToolConfigValues;
  spec_hash: string;
  /** "" when unknown (AI reviewer). */
  version: string;
  /** Only `image_manifest` is a verified version; `catalog` is what it should be. */
  version_source: "image_manifest" | "catalog";
  is_enabled: boolean;
  expectations: ToolExpectations | null;
  canary: CanaryToolVerdict | null;
}

export interface ImageManifestTool {
  version: string;
  version_output?: string;
  runtime?: "python" | "node" | "binary" | string;
  packages?: Record<string, string>;
  lock_sha256?: string;
  sha256?: string;
  rules?: Record<string, unknown>;
  [key: string]: unknown;
}

/** `/opt/analyzer/manifest.json` of the built analyzer image. */
export interface ImageManifest {
  schema_version: number;
  built_at: string;
  release_cutoff: string;
  runtime: { python: string; node: string; os: string; uid: number };
  tools: Record<string, ImageManifestTool>;
  configs: Record<string, string>;
  [key: string]: unknown;
}

/** The provenance digest of a manifest frozen into every plan. */
export interface ImageManifestDigest {
  schema_version: number | null;
  built_at: string | null;
  release_cutoff: string | null;
  runtime: ImageManifest["runtime"] | null;
  configs: Record<string, string> | null;
  tools: Record<string, Omit<ImageManifestTool, "version_output">>;
}

/** `GET /image/` (also embedded in preflight reports). */
export interface ImageStatus {
  /** `llmlab/analyzer:<sha12>` of the image definition. */
  tag: string;
  built: boolean;
  image_id?: string;
  created?: string;
  size_bytes?: number | null;
  manifest?: ImageManifest | null;
  building: boolean;
  daemon_ok: boolean;
  error: string;
  /** API only: a build was requested and the worker has not reported building yet. */
  build_requested?: boolean;
}

/**
 * The image as seen by a preflight: when the daemon is unreachable only
 * `daemon_ok: false`, `built: false` and `error` are certain.
 */
export type PreflightImageStatus = Partial<ImageStatus> &
  Pick<ImageStatus, "built" | "daemon_ok">;

export interface SelftestLatest {
  /** null with `any_image=true`. */
  image_tag: string | null;
  latest: CanaryEvaluation | null;
  per_tool: Record<string, CanaryToolVerdict>;
}

// ── Reviewer ──────────────────────────────────────────────────────────

export interface ReviewerBudget {
  /** Per replicate (one of the k samples). */
  max_cost_usd: number;
  max_llm_requests: number;
  wall_clock_seconds: number;
}

/** OpenRouter provider routing preferences. */
export interface ProviderRouting {
  order?: string[];
  only?: string[];
  ignore?: string[];
  quantizations?: string[];
  allow_fallbacks?: boolean;
  require_parameters?: boolean;
  zdr?: boolean;
  sort?: string;
  data_collection?: string;
  max_price?: Record<string, number>;
}

/** `profile.config.reviewer` (validated, defaults merged). */
export interface ReviewerConfig {
  enabled: boolean;
  required: boolean;
  engine: ReviewerEngine;
  harness: string | null;
  harness_options: HarnessOptions;
  /** OpenRouter model id, default `deepseek/deepseek-v4.1-flash`. */
  model: string;
  samples: number;
  /** null = provider default. */
  temperature: number | null;
  /** null = the model's own default effort. */
  reasoning_effort: ReasoningEffort | null;
  max_output_tokens: number;
  provider: ProviderRouting | null;
  prompt_version: string;
  schema_version: string;
  include_origins: FileOrigin[];
  include_kinds: FileKind[];
  chunk_char_budget: number;
  seed_base: number;
  budget: ReviewerBudget;
}

export interface NumberLimit {
  type: "int" | "float";
  min: number;
  max: number;
}

export interface ReviewerEngineOption {
  name: ReviewerEngine;
  label: string;
  description: string;
  requires_harness: boolean;
}

export interface ReviewHarnessOption {
  name: string;
  label: string;
  description: string;
  version: string;
  option_schema: HarnessOptionField[];
  default_options: HarnessOptions;
  requires_tool_calling: boolean;
  /** `file`: the agent writes a findings file; `answer`: findings in its final answer. */
  output_channel: "file" | "answer" | string;
  notes: string[];
}

export interface VersionedArtifact {
  version: string;
  sha256: string;
  file: string;
}

/** `GET /reviewer/options/`. */
export interface ReviewerOptions {
  engines: ReviewerEngineOption[];
  harnesses: ReviewHarnessOption[];
  default_model: string;
  defaults: ReviewerConfig;
  prompt_versions: VersionedArtifact[];
  schema_versions: VersionedArtifact[];
  reasoning_efforts: ReasoningEffort[];
  output_modes: ReviewOutputMode[];
  categories: string[];
  severities: Severity[];
  include_origins: { choices: FileOrigin[]; default: FileOrigin[] };
  include_kinds: { choices: FileKind[]; default: FileKind[] };
  limits: {
    samples: NumberLimit;
    temperature: NumberLimit;
    max_output_tokens: NumberLimit;
    chunk_char_budget: NumberLimit;
    seed_base: NumberLimit;
    budget: Record<keyof ReviewerBudget, NumberLimit>;
  };
  engine_version: string;
  /** Only on `/reviewer/options/`: the requesting user has an OpenRouter key. */
  key_present?: boolean;
}

// ── Profiles ──────────────────────────────────────────────────────────

export interface ProfileToolEntry {
  slug: string;
  required: boolean;
  /** Effective (defaults-merged) config. */
  config: ToolConfigValues;
}

export interface ScopeConfig {
  /** Origins whose lines are measured (numerator and denominator). */
  measured_origins: FileOrigin[];
  measured_kinds: FileKind[];
  line_attribution: boolean;
  exclude_globs: string[];
  exclude_kinds: FileKind[];
  max_file_bytes: number;
}

export interface OutcomeConfig {
  /** Only feed the secondary weighted score. */
  severity_weights: Record<Severity, number>;
  count_style: boolean;
  count_metric_findings: boolean;
  /** Densities are null below this measured SLOC. */
  min_sloc_for_density: number;
}

export interface ProfileConfig {
  tools: ProfileToolEntry[];
  reviewer: ReviewerConfig;
  scope: ScopeConfig;
  outcomes: OutcomeConfig;
}

/** A draft config sent for validation/saving (anything omitted gets defaults). */
export interface ProfileConfigInput {
  tools?: { slug: string; required?: boolean; config?: ToolConfigValues }[];
  reviewer?: Partial<ReviewerConfig>;
  scope?: Partial<ScopeConfig>;
  outcomes?: Partial<OutcomeConfig>;
}

export interface ProfileSummary {
  tools: string[];
  tool_count: number;
  reviewer: {
    enabled: boolean;
    engine: ReviewerEngine | "";
    harness: string;
    model: string;
    samples: number | null;
  };
}

/** `GET /profiles/` item (latest version per slug). */
export interface AnalysisProfile {
  id: string;
  slug: string;
  version: number;
  latest_version: number;
  name: string;
  description: string;
  config: ProfileConfig;
  content_hash: string;
  is_system: boolean;
  is_archived: boolean;
  is_owner: boolean;
  created_by_id: number | null;
  summary: ProfileSummary;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProfileVersion {
  id: string;
  version: number;
  content_hash: string;
  is_archived: boolean;
  created_at: string | null;
}

export interface AnalysisProfileDetail extends AnalysisProfile {
  /** Newest first. */
  versions: ProfileVersion[];
  usage: { runs: number; studies: number };
}

/** `POST /profiles/validate/` (200 with `ok: false` for an invalid config). */
export interface ProfileValidation {
  ok: boolean;
  /** Dotted paths relative to the config, e.g. `reviewer.samples`. */
  errors: Record<string, string>;
  config: ProfileConfig | null;
  content_hash: string | null;
}

export interface ProfileCreatePayload {
  name: string;
  description?: string;
  slug?: string;
  config: ProfileConfigInput;
}

export interface ProfileUpdatePayload {
  name?: string;
  description?: string;
  /** A content change creates a new version under the same slug. */
  config?: ProfileConfigInput;
}

export interface ProfileClonePayload {
  name?: string;
  slug?: string;
}

/** Outcome catalog entry (`measurement.OUTCOMES`). */
export interface OutcomeMeta {
  label: string;
  kind: OutcomeKind;
  unit: string | null;
  /** null: no direction (e.g. measured SLOC). */
  higher_is_better: boolean | null;
  description: string;
  /** Availability the value depends on: `static`, `static:<category>`, `ai`, `complexity`, ... */
  requires: string[];
}

export interface ExclusionRules {
  exclude_invalid: boolean;
  exclude_empty_output: boolean;
  exclude_truncated: boolean;
  min_measured_sloc: number;
  require_functional: boolean;
}

/** `study.analysis_design` (normalized; defaults filled in). */
export interface AnalysisDesign {
  group_by: DesignFactor[];
  block_by: DesignFactor | null;
  alpha: number;
  correction: Correction;
  exploratory_correction: Correction;
  bootstrap_resamples: number;
  effect_ci_resamples: number;
  ci_level: number;
  seed: number;
  primary_outcome: string;
  /** null = every outcome. */
  outcomes: string[] | null;
  cell_aggregate: CellAggregate;
}

/** Design input: `group_by` / `block_by` accept a single factor too. */
export type AnalysisDesignInput = Partial<
  Omit<AnalysisDesign, "group_by"> & { group_by: DesignFactor | DesignFactor[] }
>;

/** `GET /profiles/defaults/`: everything the profile and study editors need. */
export interface ProfileDefaults {
  config: ProfileConfig;
  tools: AnalysisTool[];
  reviewer: ReviewerOptions | { error: string };
  scope: ScopeConfig;
  exclusion_rules: ExclusionRules;
  outcomes: Record<string, OutcomeMeta>;
  design: AnalysisDesign;
  design_choices: {
    factors: DesignFactor[];
    corrections: Correction[];
    cell_aggregates: CellAggregate[];
  };
}

// ── Frozen plan ───────────────────────────────────────────────────────

export interface PlanTool {
  slug: string;
  name: string;
  kind: ToolKind;
  category: ToolCategory;
  required: boolean;
  config: ToolConfigValues;
  config_hash: string;
  spec_hash: string;
  version: string;
  /** `spec`: the image was not built when frozen (version unverified). */
  version_source: "image_manifest" | "spec";
  languages: string[];
}

export interface FrozenModel {
  slug: string;
  id: number;
  canonical_slug: string;
  openrouter_canonical_slug: string;
  provider: string;
  name: string;
  context_window: number;
  context_window_source: "catalog" | "fallback";
  max_output_tokens: number | null;
  pricing: {
    input_per_token: number;
    output_per_token: number;
    cache_read_per_token: number | null;
    snapshot_at: string;
  };
  supports: {
    structured_outputs: boolean;
    response_format: boolean;
    tools: boolean;
    seed: boolean;
    temperature: boolean;
    reasoning: boolean;
    reasoning_efforts: string[];
    reasoning_default_effort: string | null;
    parameters_known: boolean;
  };
}

/** `plan.reviewer`: the reviewer resolved against the model catalog. */
export interface FrozenReviewer {
  enabled: true;
  required: boolean;
  tool_slug: string;
  engine: ReviewerEngine;
  engine_version: string;
  harness: string | null;
  harness_options: HarnessOptions;
  harness_version: string;
  harness_images: Record<string, string>;
  model: FrozenModel;
  params: {
    temperature: number | null;
    max_tokens: number;
    reasoning: { effort: ReasoningEffort } | null;
    seed_base: number;
    seed_forwarded: boolean;
    /** Params the model does not accept, dropped (and recorded) at freeze. */
    dropped: Record<string, string>;
  };
  provider: ProviderRouting;
  provider_routing_source: "explicit" | "default";
  output_mode: ReviewOutputMode;
  samples: number;
  quorum: number;
  prompt_version: string;
  prompt_sha256: string;
  schema_version: string;
  schema_sha256: string;
  include_origins: FileOrigin[];
  include_kinds: FileKind[];
  chunk_char_budget: number;
  chunking: {
    effective_chars: number;
    chars_per_token: number;
    context_safety_tokens: number;
  };
  consensus: {
    line_window: number;
    grounding_window: number;
    threshold: string;
    quorum: string;
  };
  budget: ReviewerBudget;
  notes: string[];
  /** Agent engine only: the frozen harness section. */
  agent?: Record<string, unknown>;
}

export interface PlanNote {
  code: string;
  detail: string;
}

/**
 * A frozen plan: two runs are comparable iff their `plan_hash` is equal
 * (`profile`, `provenance` and `notes` are not hashed).
 */
export interface FrozenPlan {
  schema_version: number;
  profile: {
    id: string;
    slug: string;
    version: number;
    content_hash: string;
  } | null;
  tools: PlanTool[];
  reviewer: FrozenReviewer | null;
  scope: ScopeConfig;
  outcomes: OutcomeConfig;
  analyzer_image: { tag: string };
  engine_versions: Record<string, string>;
  mapping_hashes: Record<string, string>;
  provenance: {
    frozen_at: string;
    image_id: string;
    image_manifest: ImageManifestDigest | null;
  };
  notes: PlanNote[];
}

// ── Target metadata and sample manifest ───────────────────────────────

export interface FunctionalResult {
  /** e.g. passed, failed, infra_error, not_applicable, not_evaluated. */
  status: string;
  /** null unless a real conformance rate exists (never 0 for "not evaluated"). */
  pass_rate: number | null;
  source: string | null;
  evaluator_version: string | null;
  contract_stated_in_prompt?: boolean | null;
}

export interface ValidityNote {
  code?: string;
  severity?: "exclude" | "warn" | "info" | string;
  detail?: string;
  [key: string]: unknown;
}

/**
 * Identity + quality snapshot of the analysed job (`AnalysisRun.target_meta`).
 * Inline samples carry only the quality keys (`job_id: null`, `mode: "inline"`).
 */
export interface TargetMeta {
  job_id: string | null;
  mode: string;
  status?: string;
  model_id?: string | null;
  model_slug?: string | null;
  model_canonical_slug?: string | null;
  model_name?: string | null;
  model_provider?: string | null;
  generator_family?: string | null;
  harness?: string | null;
  harness_version?: string | null;
  stack_slug?: string | null;
  stack_version?: number | string | null;
  app_slug?: string | null;
  app_name?: string | null;
  prompt_hash?: string | null;
  bundle_key?: string | null;
  method_key?: string | null;
  config_fingerprint?: string | null;
  experiment_id?: string | null;
  condition_id?: string | null;
  condition_label?: string | null;
  batch_id?: string | null;
  repeat_index?: number | null;
  seed?: number | null;
  created_at?: string | null;
  termination?: string | null;
  validity?: {
    valid: boolean | null;
    exclude: boolean;
    notes: ValidityNote[];
    source: "recomputed" | "stored" | "none" | "not_applicable";
    stored_valid?: boolean | null;
  };
  truncated?: boolean;
  truncation_reasons?: string[];
  truncation?: {
    truncated_calls: number | null;
    evidence: string;
  };
  empty_output?: boolean;
  functional?: FunctionalResult;
  generation?: {
    cost: number | null;
    total_tokens: number | null;
    duration_seconds: number | null;
  };
  [key: string]: unknown;
}

export interface ManifestTotals {
  files: number;
  included: number;
  measured: number;
  sloc: number;
  measured_sloc: number;
  suppression_markers: number;
}

/** One file of the sample manifest (scope decision, origin, SLOC). */
export interface ManifestFile {
  path: string;
  sha256: string;
  size: number;
  language: string;
  kind: FileKind;
  origin: FileOrigin;
  /** Handed to the tools at all. */
  included: boolean;
  /** lockfile | build_artifact | binary | too_large | minified | vendored | glob ("" when included). */
  exclude_reason: string;
  sloc: number;
  /** Counts toward measured SLOC and counted findings. */
  measured: boolean;
  measured_sloc: number;
  /** Number of model-changed lines of a modified file (null: whole file attributed). */
  changed_lines: number | null;
  suppression_markers: number;
}

/** Run detail `manifest`: totals of the sample manifest. */
export interface ManifestSummary {
  /** agent | scaffolding | custom | legacy | inline */
  source: string;
  resolver_version: string;
  sloc_version: string;
  totals: Partial<ManifestTotals>;
  warnings: string[];
  /** Number of files in the manifest. */
  files: number;
}

// ── Measurement, summary and outcomes ─────────────────────────────────

export interface AiStaticOverlap {
  window: number;
  static_issues: number;
  ai_issues: number;
  matched: number;
  jaccard: number | null;
  ai_confirmed_share: number | null;
  static_found_by_ai_share: number | null;
  by_category: Record<string, { static: number; ai: number; matched: number }>;
}

export interface StaticMeasurement {
  /** False when a required static tool is not OK: static outcomes are NA. */
  available: boolean;
  required_failed: string[];
  findings: {
    total: number;
    by_severity: SeverityCounts;
    by_category: Record<string, number>;
    by_tool: Record<string, number>;
  };
  issues: {
    total: number;
    by_severity: SeverityCounts;
    by_category: Record<string, number>;
  };
  weighted: number | null;
  weighted_per_kloc: number | null;
  findings_per_kloc: number | null;
  issues_per_kloc: number | null;
  security_issues: number | null;
  security_issues_per_kloc: number | null;
  high_plus_issues: number | null;
  high_plus_issues_per_kloc: number | null;
  /** True as soon as one is found; null when none was found and security tools failed. */
  has_high_security: boolean | null;
  /** Per finding category: false when a tool that can emit it failed. */
  category_available: Record<string, boolean>;
  style_findings: number;
  out_of_scope_findings: number;
  scaffold_findings: number;
  uncounted_findings: number;
}

export interface AiMeasurement {
  available: boolean;
  status: ToolStatus | "missing";
  failure_reason: string;
  engine: ReviewerEngine | null;
  harness: string | null;
  model: string | null;
  samples_ok: number;
  samples: number;
  /** Consensus threshold over the successful samples (ceil(m/2)). */
  threshold: number | null;
  consensus: {
    total: number;
    by_severity: SeverityCounts;
    by_category: Record<string, number>;
  };
  per_kloc: number | null;
  findings_total: number;
  uncounted_consensus: number;
  grounding: GroundingCounts;
  grounding_rate: number | null;
  agreement: { mean_pairwise_jaccard: number | null };
  overlap_with_static: AiStaticOverlap;
  cost_usd: number;
  tokens: {
    prompt: number;
    completion: number;
    reasoning: number;
    cached: number;
    total: number;
  };
}

export interface ComplexityMetrics {
  source: string;
  scope: "measured_files" | "all_scanned";
  functions: number | null;
  max: number | null;
  mean: number | null;
  p90: number | null;
  rank_distribution: Record<string, number> | null;
}

export interface DuplicationMetrics {
  source: string;
  scope: "measured_files" | "all_scanned";
  percentage: number | null;
  clones: number | null;
  duplicated_lines: number | null;
  total_lines: number | null;
}

export interface ToolCoverageSummary {
  status: ToolStatus;
  files_in_scope: number | null;
  files_scanned: number | null;
  unparseable: number;
}

/** `AnalysisRun.measurement` (schema_version 1): the record reports and studies read. */
export interface Measurement {
  schema_version: number;
  version: string;
  plan_hash: string;
  sample_hash: string;
  complete: boolean;
  required_failed: string[];
  tool_status: Record<string, ToolStatus>;
  sloc: {
    measured: number | null;
    total: number | null;
    by_language: Record<string, number>;
    by_origin: Record<string, number>;
  };
  files: {
    measured: number | null;
    included: number | null;
    excluded: number | null;
  };
  static: StaticMeasurement;
  /** null when the plan has no AI reviewer. */
  ai: AiMeasurement | null;
  metrics: {
    complexity: ComplexityMetrics | null;
    duplication: DuplicationMetrics | null;
  };
  coverage: Record<string, ToolCoverageSummary>;
  parse_coverage: {
    unparseable_files: string[];
    share_unparseable_sloc: number | null;
  };
  suppression_markers: number | null;
  suppression_markers_per_kloc: number | null;
  functional: FunctionalResult;
  flags: {
    truncated: boolean;
    truncation_reasons: string[];
    empty_output: boolean;
    validity: { valid: boolean | null; exclude: boolean };
    excluded_reasons: string[];
  };
  outcome_config: OutcomeConfig;
}

/** Flat research outcomes of a run; `null` is missing data (NA), never 0. */
export interface RunOutcomes {
  static_findings_per_kloc: number | null;
  static_issues_per_kloc: number | null;
  security_issues_per_kloc: number | null;
  high_plus_issues_per_kloc: number | null;
  has_high_security: boolean | null;
  ai_consensus_per_kloc: number | null;
  complexity_max: number | null;
  complexity_mean: number | null;
  duplication_pct: number | null;
  functional_pass_rate: number | null;
  measured_sloc: number | null;
  suppression_markers_per_kloc: number | null;
  static_issues: number | null;
  security_issues: number | null;
  high_plus_issues: number | null;
  weighted_per_kloc: number | null;
  ai_grounding_rate: number | null;
  ai_mean_pairwise_jaccard: number | null;
  [key: string]: number | boolean | null;
}

export type ToolStatusCounts = Record<ToolStatus | "total", number>;

/** `AnalysisRun.summary` (schema_version 2); `{}` until the run finalizes. */
export interface RunSummary {
  schema_version: number;
  tools: ToolStatusCounts;
  complete: boolean;
  /** Counted static findings. */
  total_findings: number;
  severity_counts: SeverityCounts;
  /** AI consensus clusters (counted). */
  ai_findings: number;
  ai_severity_counts: SeverityCounts;
  issues: number;
  sloc: { measured: number | null; total: number | null };
  cost_usd: number | null;
  warnings: string[];
  tools_run: number;
  /** succeeded + not_applicable */
  tools_completed: number;
  /** failed + timed_out */
  tools_failed: number;
}

// ── Runs ──────────────────────────────────────────────────────────────

/** List-row view of `target_meta` plus a human label. */
export interface RunTarget {
  model_slug: string | null;
  model_name: string | null;
  app_slug: string | null;
  app_name: string | null;
  harness: string | null;
  mode: string | null;
  stack_slug: string | null;
  experiment_id: string | null;
  condition_id: string | null;
  condition_label: string | null;
  batch_id: string | null;
  repeat_index: number | null;
  /** "model · app · harness/mode · r0 · job" or "inline source". */
  label: string;
}

export interface AnalysisRunListItem {
  id: string;
  name: string;
  purpose: RunPurpose;
  status: RunStatus;
  phase: RunPhase;
  /** Every required tool succeeded or was not applicable. */
  is_complete: boolean;
  cancel_requested: boolean;
  attempt: number;
  source: "job" | "inline";
  generation_job_id: string | null;
  study_id: string | null;
  profile: ProfileRef | null;
  plan_hash: string;
  sample_hash: string;
  target: RunTarget;
  /** Plan order, AI reviewer last. */
  tool_slugs: string[];
  /** Live tool result counts by status. */
  tools: ToolStatusCounts;
  summary: Partial<RunSummary>;
  error_message: string;
  /** Running with a heartbeat older than the stale threshold. */
  stale: boolean;
  heartbeat_at: string | null;
  created_by_id: number;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
}

/** A stored raw output; `url` is root-relative (`/api/analysis/runs/...`). */
export interface ArtifactRef {
  name: string;
  bytes: number | null;
  sha256: string;
  compressed: boolean;
  url: string;
}

export interface UnparseableFile {
  path: string;
  reason?: string;
}

export interface ToolResultCoverage {
  files_in_scope?: number | null;
  files_selected?: number;
  files_scanned?: number | null;
  scanned?: string[] | null;
  skipped?: unknown[];
  partial?: unknown[];
  unparseable?: (string | UnparseableFile)[];
  [key: string]: unknown;
}

/** Static tool diagnostics (the AI result has `model`, `engine`, `samples`, ...). */
export interface ToolResultDiagnostics {
  image_id?: string;
  container?: string;
  exit_code?: number | null;
  mem_limit?: string;
  timeout_s?: number;
  timed_out?: boolean;
  oom_killed?: boolean;
  infra_error?: string | null;
  out_files?: string[];
  out_truncated?: boolean;
  stderr_tail?: string;
  stderr_bytes?: number;
  stdout_bytes?: number;
  stderr_truncated?: boolean;
  stdout_truncated?: boolean;
  wall_seconds?: number;
  files_selected?: number;
  notes?: string[];
  /** AI result */
  model?: string;
  engine?: string;
  harness?: string;
  prompt_version?: string;
  samples?: {
    index: number;
    status: ReviewSampleStatus;
    failure_reason: string;
  }[];
  [key: string]: unknown;
}

export interface ToolResultSummary {
  findings?: number;
  counted?: number;
  by_severity?: SeverityCounts;
  files_in_scope?: number;
  /** AI result */
  samples?: number;
  samples_ok?: number;
  consensus?: number;
  threshold?: number | null;
  /** null when no replicate reported a cost. */
  cost_usd?: number | null;
  [key: string]: unknown;
}

export interface ReviewUsage {
  llm_calls?: number;
  llm_calls_failed?: number;
  http_attempts?: number;
  retries?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  reasoning_tokens?: number;
  cached_tokens?: number;
  total_tokens?: number;
  cost_usd?: number | null;
  /** True when the provider reported no cost and it was computed from the catalog price. */
  cost_estimated?: boolean;
  catalog_cost_usd?: number | null;
  served_models?: Record<string, number>;
  providers?: Record<string, number>;
  samples?: number;
  wall_seconds?: number;
  [key: string]: unknown;
}

/** AI result `metrics.agreement` (consensus bookkeeping). */
export interface ReviewAgreement {
  samples?: number;
  samples_run?: number;
  successful_samples?: number;
  quorum?: number;
  threshold?: number | null;
  line_window?: number;
  clusters?: number;
  consensus?: number;
  counted?: number;
  statuses?: Partial<Record<ReviewSampleStatus, number>>;
  grounding?: GroundingCounts;
  grounding_rate?: number | null;
  repaired_samples?: number;
  per_sample_counts?: number[];
  per_sample_clusters?: number[];
  uncounted_category?: number;
  uncounted_categories?: string[];
  mean_pairwise_jaccard?: number | null;
  mean_pairwise_jaccard_grounded?: number | null;
  /** Why replicates stopped early (`quorum_unreachable`, a fatal reason), else ""/null. */
  stopped_early?: string | null;
  [key: string]: unknown;
}

export interface ToolResultMetrics {
  /** AI result */
  usage?: ReviewUsage;
  agreement?: ReviewAgreement;
  [key: string]: unknown;
}

export interface ToolResult {
  id: string;
  tool_slug: string;
  tool_kind: ToolKind;
  category: ToolCategory;
  position: number;
  required: boolean;
  status: ToolStatus;
  failure_reason: ToolFailureReason;
  attempt: number;
  /** From the image manifest; the AI result records the model's canonical slug. */
  tool_version: string;
  /** `<tag>@<image id>` the tool ran in. */
  image_ref: string;
  /** Effective tool config (the AI result: the frozen reviewer section). */
  config: Record<string, unknown>;
  config_hash: string;
  argv: string[];
  exit_code: number | null;
  duration_seconds: number | null;
  started_at: string | null;
  finished_at: string | null;
  coverage: ToolResultCoverage;
  metrics: ToolResultMetrics;
  summary: ToolResultSummary;
  diagnostics: ToolResultDiagnostics;
  artifacts: ArtifactRef[];
  error_message: string;
  findings: { total: number; counted: number };
}

export interface ReviewSampleCoverage {
  chunks?: number;
  chunks_completed?: number;
  chars_sent?: number;
  chunk_char_budget?: number;
  files_planned?: number;
  files_in_scope?: number;
  files_sent?: string[];
  files_not_sent?: string[];
  files_split?: string[];
  files_claimed_not_shown?: string[];
  chunk_files?: string[][];
  out_of_scope?: Record<string, string>;
  review_complete?: boolean | null;
  [key: string]: unknown;
}

/** One AI review replicate (k per run). */
export interface ReviewSample {
  id: string;
  result_id: string;
  index: number;
  engine: ReviewerEngine;
  harness: string;
  harness_version: string;
  model_slug: string;
  model_canonical_slug: string;
  status: ReviewSampleStatus;
  failure_reason: string;
  error_message: string;
  prompt_version: string;
  prompt_sha256: string;
  schema_sha256: string;
  file_order_seed: number | null;
  files_reviewed: string[];
  coverage: ReviewSampleCoverage;
  usage: ReviewUsage;
  /** Provider -> calls served. */
  providers: Record<string, number>;
  finish_reasons: Record<string, number>;
  grounding: Partial<GroundingCounts>;
  finding_count: number;
  /** The answer was fixed by one repair call. */
  repaired: boolean;
  latency_seconds: number | null;
  artifacts: ArtifactRef[];
  created_at: string | null;
}

export interface AnalysisRunDetail extends AnalysisRunListItem {
  plan: FrozenPlan;
  target_meta: TargetMeta;
  manifest: ManifestSummary;
  results: ToolResult[];
  review_samples: ReviewSample[];
  /** `{}` until the run finalizes (see {@link isMeasurement}). */
  measurement: Partial<Measurement>;
  outcomes: RunOutcomes | null;
  lease_owner: string;
  updated_at: string | null;
  /** Self-test runs only: the evaluation against the canary expectations. */
  canary: CanaryEvaluation | null;
}

export interface RunCreatePayload {
  name?: string;
  /** Exactly one of `generation_job_id` and `source_code`. */
  generation_job_id?: string | null;
  /** Pasted files `{path: text}` (non-research ad-hoc analysis). */
  source_code?: Record<string, string> | null;
  /** At most one of `profile_id` and `config` (neither: the research default). */
  profile_id?: string | null;
  config?: ProfileConfigInput | null;
}

export interface RunListParams {
  page?: number;
  per_page?: number;
  status?: RunStatus | RunStatus[];
  /** Default: every purpose but `canary`. */
  purpose?: RunPurpose | RunPurpose[];
  study_id?: string;
  generation_job_id?: string;
  profile_id?: string;
  plan_hash?: string;
  is_complete?: boolean;
  q?: string;
  ordering?: RunOrdering;
}

// ── Findings, issues, files ───────────────────────────────────────────

export interface Finding {
  id: string;
  result_id: string;
  tool_slug: string;
  rule_id: string;
  title: string;
  description: string;
  suggestion: string;
  severity: Severity;
  severity_rank: number;
  /** The tool's own severity token (e.g. bandit HIGH, eslint 2). */
  native_severity: string;
  category: FindingCategory;
  cwe: string[];
  confidence: FindingConfidence | string;
  file_path: string;
  line_number: number | null;
  end_line: number | null;
  column_number: number | null;
  code_snippet: string;
  file_origin: FileOrigin;
  in_measured_scope: boolean;
  /** Counts toward the measurement (static: in scope + counted category; AI: consensus + grounded). */
  counted: boolean;
  fingerprint: string;
  /** Dedup cluster ("" when not clustered). */
  issue_key: string;
  is_ai: boolean;
  review_sample_id: string | null;
  review_sample_index: number | null;
  grounding: Grounding;
  is_consensus: boolean;
  tool_specific_data: Record<string, unknown>;
  created_at: string | null;
}

export interface FindingFacets {
  severity: SeverityCounts;
  tool: Record<string, number>;
  category: Record<string, number>;
  origin: Record<string, number>;
}

export interface FindingPage extends Page<Finding> {
  ordering: FindingOrdering;
  /** `{}` with `facets=false`. */
  facets: Partial<FindingFacets>;
}

export interface FindingFilters {
  page?: number;
  per_page?: number;
  severity?: Severity | Severity[];
  tool?: string | string[];
  category?: FindingCategory | FindingCategory[];
  origin?: FileOrigin | FileOrigin[];
  /** Exact path, or a directory prefix ending in "/". */
  file?: string;
  rule?: string | string[];
  grounding?: GroundingVerdict | GroundingVerdict[];
  is_ai?: boolean;
  counted?: boolean;
  consensus?: boolean;
  in_scope?: boolean;
  issue_key?: string;
  /** AI review sample index. */
  sample?: number;
  result_id?: string;
  q?: string;
  ordering?: FindingOrdering;
  facets?: boolean;
}

export type IssueKind = "static" | "ai";

/** A deduplicated issue (static) or an AI consensus cluster. */
export interface Issue {
  issue_key: string;
  kind: IssueKind;
  severity: Severity;
  severity_rank: number;
  title: string;
  category: string;
  categories: string[];
  cwe: string[];
  file_path: string;
  files: string[];
  line_start: number | null;
  line_end: number | null;
  /** Tools that detected it. */
  tools: string[];
  rule_ids: string[];
  findings: number;
  /** AI: the review sample indexes that reported it. */
  samples: number[];
}

export interface IssuePage extends Page<Issue> {
  kind: IssueKind;
}

export interface IssueFilters {
  kind?: IssueKind;
  page?: number;
  per_page?: number;
  severity?: Severity | Severity[];
  category?: string | string[];
  tool?: string | string[];
  file?: string;
}

export interface RunFile extends ManifestFile {
  findings: { total: number; counted: number; ai_consensus: number };
}

export interface RunFiles {
  source: string;
  resolver_version: string;
  sloc_version: string;
  sample_hash: string;
  totals: Partial<ManifestTotals>;
  warnings: string[];
  count: number;
  files: RunFile[];
}

export interface RunFileFilters {
  included?: boolean;
  measured?: boolean;
  origin?: FileOrigin | FileOrigin[];
  language?: string | string[];
  kind?: FileKind | FileKind[];
  q?: string;
}

/** One file of the analysed sample, re-resolved and sha-verified. */
export interface RunFileContent {
  path: string;
  language: string;
  kind: FileKind;
  origin: FileOrigin;
  included: boolean;
  measured: boolean;
  exclude_reason: string;
  sha256: string;
  size: number;
  sloc: number;
  measured_sloc: number;
  /** 1-based lines the model changed in a modified file (null: whole file attributed). */
  changed_lines: number[] | null;
  binary: boolean;
  too_large: boolean;
  /** null for binary or too-large files. */
  content: string | null;
  lines: number;
}

// ── Preflight (pre-run report) ────────────────────────────────────────

/** A blocking / warning / info item; extra keys depend on `code`. */
export interface PreflightIssue {
  code: string;
  detail: string;
  severity?: string;
  field_errors?: Record<string, string>;
  /** e.g. `canary_failed`, `tool_disabled` */
  tools?: string[];
  tool?: string;
  /** `targets_excluded`: reason code -> targets */
  reasons?: Record<string, number>;
  job_ids?: string[];
  apps?: string[];
  [key: string]: unknown;
}

export interface TargetReason {
  code: string;
  severity: EligibilitySeverity;
  detail: string;
}

export interface CostEstimate {
  /** USD per target for all k replicates. */
  low: number;
  expected: number;
  high: number;
  per_sample: { low: number; expected: number; high: number };
  tokens: { input: number; output: number };
  calls: number;
  samples: number;
  method?: string;
}

/** A candidate target (generation job or inline source) with eligibility and sample totals. */
export interface TargetDescription {
  job_id: string | null;
  label: string;
  meta: TargetMeta;
  eligible: boolean;
  reasons: TargetReason[];
  /** Included files. */
  files: number;
  files_total: number;
  files_measured: number;
  sloc_total: number;
  sloc_measured: number;
  /** Language -> SLOC */
  languages: Record<string, number>;
  /** Origin -> included files */
  origins: Record<string, number>;
  sample_hash: string;
  source: string;
  warnings: string[];
  /** Empty on `/targets/resolve/` (applicability is a preflight check). */
  tools: Record<string, ToolApplicability>;
  tool_files: Record<string, number>;
}

export interface PreflightTarget extends TargetDescription {
  group: string;
  block: string | null;
  /** Eligible targets with files for the AI reviewer. */
  ai_cost_estimate?: CostEstimate | Record<string, never>;
}

/** Preflight canary entry: `passed` null means no evidence, never a pass. */
export interface PreflightCanary extends Partial<
  Omit<
    CanaryToolEvaluation,
    "passed" | "verdict" | "missing" | "unexpected_in_clean"
  >
> {
  passed: boolean | null;
  verdict: CanaryVerdict | "untested";
  missing: CanaryExpectation[];
  unexpected_in_clean: { rule: string; path: string; category: string }[];
  run_id: string | null;
  evaluated_at: string | null;
  image_tag?: string;
}

export interface ToolReadinessRow {
  slug: string;
  name: string;
  kind: ToolKind | "";
  category: ToolCategory | "";
  required: boolean;
  version: string;
  version_source: string;
  config_hash: string;
  status: ToolReadiness;
  canary: PreflightCanary;
}

export interface ReviewerCostSummary {
  low: number;
  expected: number;
  high: number;
  per_target: number | null;
  calls: number;
  targets: number;
}

export type PreflightReviewer =
  | { enabled: false }
  | {
      enabled: true;
      required: boolean;
      engine: ReviewerEngine;
      harness: string | null;
      harness_version: string;
      model: string;
      model_canonical_slug: string;
      model_found: boolean;
      structured_outputs: boolean;
      tool_calling: boolean;
      key_present: boolean;
      samples: number;
      prompt_version: string;
      budget: Partial<ReviewerBudget>;
      pricing: Partial<FrozenModel["pricing"]>;
      cost_estimate: ReviewerCostSummary;
    };

export interface PreflightEstimates {
  targets: number;
  eligible: number;
  excluded: number;
  tool_executions: number;
  ai_reviews: number;
  ai_samples: number;
  ai_calls: number;
  /** Serial estimate divided by the run concurrency. */
  wall_seconds: number;
  ai_cost_usd: number | null;
  ai_cost_usd_low: number | null;
  ai_cost_usd_high: number | null;
  sloc_measured: number;
  files: number;
}

export type IdentityKey =
  "prompt_hash" | "bundle_key" | "stack_slug" | "mode" | "harness";

export interface Comparability {
  plan_hash: string;
  /** Distinct values of each identity key among eligible targets. */
  values: Record<IdentityKey, string[]>;
  /** Only the keys with more than one value. */
  mixed: Partial<Record<IdentityKey, string[]>>;
  notes: PreflightIssue[];
}

/** Group × block cell structure (`report.design_matrix`). */
export interface DesignMatrix {
  group_by: DesignFactor[];
  block_by: DesignFactor | null;
  /** Group label -> targets */
  groups: Record<string, number>;
  blocks: Record<string, number>;
  /** Group -> block -> targets */
  cells: Record<string, Record<string, number>>;
  complete: boolean;
  mode: DesignMode;
  reason: string;
  replicates: { min: number; max: number } | null;
}

export interface PreflightDesign extends DesignMatrix {
  complete_blocks: boolean;
  n_groups: number;
  min_group_size: number;
  balanced: boolean;
  primary_outcome: string;
  outcomes: string[] | null;
  alpha: number;
  correction: Correction;
  bootstrap_resamples: number;
  notes: string[];
}

/** The pre-run report (`POST /studies/{id}/preflight/`, stored on the study). */
export interface PreflightReport {
  generated_at: string;
  plan_hash: string;
  /** Any blocking issue prevents locking the protocol. */
  blocking: PreflightIssue[];
  warnings: PreflightIssue[];
  info: PreflightIssue[];
  rules: ExclusionRules;
  targets: PreflightTarget[];
  /** Tool -> eligible targets by applicability. */
  applicability: Record<
    string,
    { run: number; not_applicable: number; [key: string]: number }
  >;
  image: PreflightImageStatus;
  tools: ToolReadinessRow[];
  reviewer: PreflightReviewer;
  estimates: PreflightEstimates;
  comparability: Comparability;
  design: PreflightDesign;
}

/** `POST /runs/preview/`: a one-target preflight plus the plan it would freeze. */
export interface RunPreview extends PreflightReport {
  plan: FrozenPlan;
}

// ── Studies ───────────────────────────────────────────────────────────

export type ResearchQuestionKind = "confirmatory" | "exploratory";

export interface ResearchQuestion {
  id: string;
  text: string;
  kind: ResearchQuestionKind;
  hypothesis?: string;
}

export interface ResearchQuestionInput {
  id?: string;
  text: string;
  kind?: ResearchQuestionKind;
  hypothesis?: string;
}

export interface TargetFilters {
  /** Catalog ids (int) or model slugs. */
  model_ids?: (number | string)[];
  modes?: string[];
  harnesses?: string[];
  stacks?: string[];
  app_slugs?: string[];
  created_after?: string;
  created_before?: string;
  status?: "completed";
}

/** Which generation jobs a study analyses (only completed jobs are targets). */
export type TargetSpec =
  | {
      kind: "experiment";
      experiment_id: string;
      condition_ids?: number[];
      app_slugs?: string[];
      filters?: TargetFilters;
    }
  | { kind: "batch"; batch_id: string; filters?: TargetFilters }
  | { kind: "jobs"; job_ids: string[]; filters?: TargetFilters }
  | { kind: "filter"; filters?: TargetFilters };

export interface StudyPreflightSummary {
  generated_at: string | null;
  plan_hash: string;
  blocking: number;
  warnings: number;
  targets: number | null;
  eligible: number | null;
  ai_cost_usd: number | null;
}

/** Why a planned target got no run. */
export interface LaunchSkip {
  job_id: string;
  label: string;
  reason:
    | "job_missing"
    | "job_not_completed"
    | "sample_unavailable"
    | "sample_changed"
    | "submit_failed"
    | string;
  detail?: string;
}

/** `study.launch`: the launch record without run ids (`submitted` is the live run count). */
export interface LaunchSummary {
  status: LaunchStatus;
  /** Submitting or resumable: launching again resumes it. */
  pending: boolean;
  planned: number | null;
  submitted: number;
  skipped: number;
  attempts: number | null;
  launched_at: string | null;
  requested_at: string | null;
  finished_at: string | null;
  error: string | null;
}

/** `study.preflight.launch`: the full launch record. */
export interface LaunchState {
  status?: LaunchStatus;
  planned: number;
  eligible_targets: number;
  submitted: number;
  run_ids: string[];
  skipped: LaunchSkip[];
  launched_at: string | null;
  launched_by: number | null;
  requested_at: string | null;
  finished_at: string | null;
  attempts: number;
  error: string;
}

/** Report lifecycle; null on the study when never requested. */
export interface ReportState {
  status: ReportStatus;
  request_id: string | null;
  requested_at: string | null;
  generated_at: string | null;
  error: string | null;
  report_hash: string | null;
}

export type StudyAction =
  | "edit"
  | "preflight"
  | "lock"
  | "unlock"
  | "launch"
  | "cancel"
  | "retry_failed"
  | "report"
  | "delete";

export interface Study {
  id: string;
  name: string;
  description: string;
  status: StudyStatus;
  research_questions: ResearchQuestion[];
  /** `{}` until targets are chosen. */
  target_spec: TargetSpec | Record<string, never>;
  profile: ProfileRef | null;
  exclusion_rules: ExclusionRules;
  analysis_design: AnalysisDesign;
  plan_hash: string;
  protocol_hash: string;
  report_hash: string;
  preflight_summary: StudyPreflightSummary | null;
  /** Run status -> runs (only statuses present). */
  run_counts: Partial<Record<RunStatus, number>>;
  launch: LaunchSummary | null;
  report_state: ReportState | null;
  /** Which lifecycle actions the status allows. */
  actions: Record<StudyAction, boolean>;
  created_by_id: number;
  locked_at: string | null;
  launched_at: string | null;
  completed_at: string | null;
  report_generated_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** The stored preflight (`{}` before the first preflight) plus the launch record. */
export type StoredPreflight = Partial<PreflightReport> & {
  launch?: LaunchState;
};

export interface StudyDetail extends Study {
  preflight: StoredPreflight;
  /** `{}` until the study is locked (see {@link isFrozenPlan}). */
  plan: Partial<FrozenPlan>;
  /** null before lock; false when the stored protocol no longer matches its hash. */
  protocol_verified: boolean | null;
}

export interface StudyCreatePayload {
  name: string;
  description?: string;
  research_questions?: ResearchQuestionInput[];
  target_spec?: TargetSpec | Record<string, never>;
  /** Default: the research-default system profile. */
  profile_id?: string | null;
  exclusion_rules?: Partial<ExclusionRules>;
  analysis_design?: AnalysisDesignInput;
}

/** PATCH (draft only); any change discards the stored preflight. */
export type StudyUpdatePayload = Partial<
  Omit<StudyCreatePayload, "profile_id">
> & { profile_id?: string | null };

export interface StudyListParams {
  page?: number;
  per_page?: number;
  status?: StudyStatus | StudyStatus[];
  q?: string;
}

export interface StudyRetryResult {
  retried: string[];
  skipped: { run_id: string; reason: string }[];
  study: StudyDetail;
}

export interface CapacityContainer {
  name: string;
  mem_bytes: number;
  unlimited: boolean;
  /** An analysis sandbox (vs sample apps / agents sharing the daemon). */
  analysis: boolean;
  labels: Record<string, string>;
}

export type Capacity =
  | {
      budget_bytes: number;
      used_bytes: number;
      free_bytes: number;
      containers: CapacityContainer[];
    }
  | { error: string };

export interface ProgressRow {
  run_id: string;
  job_id: string | null;
  label: string;
  group: string;
  app_slug: string;
  model_slug: string;
  status: RunStatus;
  phase: RunPhase;
  is_complete: boolean;
  attempt: number;
  cancel_requested: boolean;
  stale: boolean;
  heartbeat_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  cost_usd: number;
  error_message: string;
  /** Every planned tool (`missing` when the run has no such result). */
  tools: Record<
    string,
    { status: ToolStatus | "missing"; failure_reason: string }
  >;
}

/** `GET /studies/{id}/progress/`: targets × tools matrix, counts, cost, queue. */
export interface StudyProgress {
  study_id: string;
  status: StudyStatus;
  generated_at: string;
  group_by: DesignFactor[];
  /** Planned tools in plan order (AI reviewer last). */
  tools: string[];
  launch: {
    status: LaunchStatus | null;
    planned: number | null;
    submitted: number;
    skipped: number;
    pending: boolean;
    attempts: number | null;
    requested_at: string | null;
    finished_at: string | null;
    error: string | null;
  };
  counts: {
    targets: number;
    eligible: number;
    excluded: number;
    planned: number;
    submitted: number;
    not_launched: number;
    runs: Record<RunStatus, number>;
    terminal: number;
    active: number;
    complete: number;
  };
  /** Terminal / submitted runs; null before any run exists. */
  progress: number | null;
  tool_counts: Record<string, Partial<Record<ToolStatus, number>>>;
  rows: ProgressRow[];
  not_launched: (LaunchSkip & { status: "not_launched" })[];
  cost: { so_far_usd: number; estimate_usd: number | null };
  timing: {
    launched_at: string | null;
    elapsed_seconds: number | null;
    eta_seconds: number | null;
  };
  stale: { count: number; run_ids: string[]; threshold_seconds: number };
  queue: {
    max_concurrent_runs: number;
    running_global: number;
    queued_global: number;
    queued_ahead: number;
  };
  /** Only while runs are active. */
  capacity: Capacity | null;
}

// ── Protocol ──────────────────────────────────────────────────────────

export interface OutcomeDefinition {
  key: string;
  label: string;
  kind: OutcomeKind;
  unit: string;
  higher_is_better: boolean | null;
  description: string;
  requires: string[];
  primary: boolean;
  family: ResearchQuestionKind;
}

export interface AnalysisPlan {
  unit: string;
  mode: DesignMode;
  omnibus: "friedman" | "kruskal_wallis";
  omnibus_effect_size: "kendalls_w" | "epsilon_squared";
  pairwise: "wilcoxon_signed_rank" | "mann_whitney_u";
  pairwise_effect_sizes: string[];
  cell_aggregate: CellAggregate | null;
  alpha: number;
  pairwise_correction: Correction;
  confirmatory_correction: Correction;
  exploratory_correction: Correction;
  descriptives: string;
  ci: {
    method: string;
    level: number;
    resamples: number;
    cluster: DesignFactor | null;
    seed: number;
  };
  effect_ci: { method: string; resamples: number };
  proportions: string;
  reliability: string;
  missing_data: string;
  magnitude_thresholds: Record<string, number>;
}

/** A target as frozen in the protocol (identity part of the metadata only). */
export interface ProtocolTarget {
  job_id: string;
  label: string;
  eligible: boolean;
  reasons: TargetReason[];
  group: string;
  block: string | null;
  meta: Partial<TargetMeta>;
  sample_hash: string;
  files: number | null;
  sloc_total: number | null;
  sloc_measured: number | null;
  languages: Record<string, number>;
  tools: Record<string, ToolApplicability>;
}

export interface ProtocolControlTool {
  slug: string;
  status: ToolReadiness | ToolStatus | string | null;
  passed: boolean | null;
  missing: CanaryExpectation[];
  unexpected_in_clean: { rule: string; path: string; category: string }[];
}

export interface Threat {
  kind: "construct" | "internal" | "external" | "conclusion" | string;
  threat: string;
  mitigation: string;
}

/** The frozen pre-run protocol (sha256 = `protocol_hash`). */
export interface StudyProtocol {
  schema_version: number;
  kind: "analysis_study_protocol";
  generated_at: string;
  study: {
    id: string;
    name: string;
    description: string;
    researcher: { id: number; name: string };
    created_at: string | null;
  };
  research_questions: ResearchQuestion[];
  targets: {
    spec: TargetSpec | Record<string, never>;
    resolved: ProtocolTarget[];
    counts: {
      total: number;
      eligible: number;
      excluded: number;
      by_reason: Record<string, number>;
    };
  };
  exclusion_rules: ExclusionRules;
  design: DesignMatrix & { settings: AnalysisDesign; notes: string[] };
  plan_hash: string;
  plan: FrozenPlan;
  toolchain: Pick<
    PlanTool,
    | "slug"
    | "name"
    | "kind"
    | "category"
    | "required"
    | "version"
    | "config_hash"
    | "spec_hash"
    | "languages"
  >[];
  analyzer_image: { tag?: string };
  engine_versions: Record<string, string>;
  reviewer: FrozenReviewer | null;
  controls: {
    /** `preflight`, `canary.latest` or null (no evidence). */
    source: "preflight" | "canary.latest" | null;
    passed: boolean | null;
    tools: ProtocolControlTool[];
    evaluated?: Record<string, string | null>;
  };
  outcomes: {
    primary: string;
    confirmatory: string[];
    exploratory: string[];
    definitions: OutcomeDefinition[];
    config: Partial<OutcomeConfig>;
    notes: string[];
  };
  analysis_plan: AnalysisPlan;
  estimates: Partial<PreflightEstimates> & {
    reviewer_cost: ReviewerCostSummary | null;
    budget: Partial<ReviewerBudget> | null;
  };
  comparability: Partial<Comparability>;
  preflight: {
    generated_at: string | null;
    plan_hash: string | null;
    blocking: PreflightIssue[];
    warnings: PreflightIssue[];
    info: PreflightIssue[];
    image: Partial<ImageStatus>;
  };
  threats: Threat[];
  platform: {
    git: { sha: string | null; ref: string | null; source: string | null };
    stats_version?: string;
    report_schema_version?: number;
    protocol_schema_version?: number;
    [key: string]: unknown;
  };
  timestamps: {
    created_at: string | null;
    preflight_generated_at: string | null;
    locked_at: string | null;
  };
}

/** `GET /studies/{id}/protocol/` (404 until the study is locked). */
export interface StudyProtocolEnvelope {
  protocol_hash: string;
  locked_at: string | null;
  plan_hash: string;
  /** The stored protocol still matches its hash. */
  verified: boolean;
  protocol: StudyProtocol;
}

// ── Post-run report ───────────────────────────────────────────────────

export type ReportSectionKey =
  | "overview"
  | "deviations"
  | "data_quality"
  | "sample"
  | "descriptives"
  | "findings_profile"
  | "inferential"
  | "ai_reliability"
  | "tool_overlap"
  | "cost"
  | "provenance";

/**
 * How a table cell renders: `pct` is a share in [0, 1], `ci` / `pct_ci` a
 * `[lo, hi]` pair (or null), `p` a p-value, `code` a JSON-ish value shown
 * verbatim, `status` a status/severity token. `null` is always missing.
 */
export type ReportColumnKind =
  | "text"
  | "int"
  | "float"
  | "pct"
  | "p"
  | "ci"
  | "pct_ci"
  | "usd"
  | "bool"
  | "hash"
  | "list"
  | "status"
  | "code";

export interface ReportColumn {
  key: string;
  label: string;
  kind: ReportColumnKind;
}

export type ReportCell =
  | string
  | number
  | boolean
  | null
  | ReportCell[]
  | { [key: string]: ReportCell };

export type ReportRow = Record<string, ReportCell>;

export interface ReportTable {
  key: string;
  title: string;
  description: string;
  notes: string[];
  columns: ReportColumn[];
  rows: ReportRow[];
}

interface SeriesBase {
  key: string;
  title: string;
}

export interface FunnelSeries extends SeriesBase {
  kind: "funnel";
  steps: {
    key: string;
    step: string;
    n: number | null;
    share: number | null;
  }[];
}

/** Targets × tools status grid; `cells[i][j]` / `reasons[i][j]` for `rows[i]`, `columns[j]`. */
export interface StatusMatrixSeries extends SeriesBase {
  kind: "status_matrix";
  rows: {
    key: string;
    label: string;
    group: string;
    block: string | null;
    in_analysis: boolean;
  }[];
  columns: string[];
  cells: ToolCellStatus[][];
  reasons: string[][];
  legend: ToolCellStatus[];
}

export interface HeatmapSeries extends SeriesBase {
  kind: "heatmap";
  rows: string[];
  row_labels?: string[];
  columns: string[];
  /** `values[i][j]` for `rows[i]`, `columns[j]`. */
  values: (number | null)[][];
  /** CWE heatmap: issues per run. */
  per_run?: (number | null)[][];
}

/** Per-group distribution (all null when the group has no data). */
export interface BoxGroup {
  group: string;
  n: number;
  n_missing?: number;
  median: number | null;
  q1: number | null;
  q3: number | null;
  iqr: number | null;
  mean: number | null;
  sd: number | null;
  min: number | null;
  max: number | null;
  whisker_low: number | null;
  whisker_high: number | null;
  outliers: number[];
  values: number[];
}

export interface BoxSeries extends SeriesBase {
  /** `proportion`: a binary outcome (values 0/1). */
  kind: "box" | "proportion";
  outcome: string;
  unit: string | null;
  higher_is_better: boolean | null;
  groups: BoxGroup[];
}

export interface MeanRanksSeries extends SeriesBase {
  kind: "mean_ranks";
  outcome: string;
  groups: { group: string; mean_rank: number }[];
  n_blocks: number;
}

/** Cliff's δ with a CI per pair of groups (primary outcome). */
export interface ForestSeries extends SeriesBase {
  kind: "forest";
  outcome: string;
  items: {
    label: string;
    delta: number | null;
    lo: number | null;
    hi: number | null;
    p_adj: number | null;
  }[];
}

export interface BarSeries extends SeriesBase {
  kind: "bar";
  items: { group: string; value: number | null }[];
}

export interface UpsetSeries extends SeriesBase {
  kind: "upset";
  sets: string[];
  intersections: { tools: string[]; count: number }[];
}

export interface ScatterSeries extends SeriesBase {
  kind: "scatter";
  outcome: string;
  points: {
    group: string;
    primary_median: number | null;
    ai_cost_per_run: number | null;
    generation_cost_per_target: number | null;
  }[];
}

export type ReportSeries =
  | FunnelSeries
  | StatusMatrixSeries
  | HeatmapSeries
  | BoxSeries
  | MeanRanksSeries
  | ForestSeries
  | BarSeries
  | UpsetSeries
  | ScatterSeries;
export type ReportSeriesKind = ReportSeries["kind"];

export interface ReportSection {
  key: ReportSectionKey;
  title: string;
  description: string;
  notes: string[];
  tables: ReportTable[];
  series: ReportSeries[];
}

export interface ReportSummary {
  targets_planned: number | null;
  targets_eligible: number | null;
  targets_with_run: number;
  analysed: number;
  complete: number;
  groups: string[];
  mode: DesignMode;
  primary_outcome: string;
  primary_label: string;
  primary_omnibus: {
    test: string | null;
    statistic: number | null;
    df: number | null;
    p: number | null;
    p_adj: number | null;
    effect: number | null;
    effect_name: string | null;
    n: number | null;
  };
  /** Deviations by severity. */
  deviations: { major: number; warn: number; info: number };
  ai_cost_total: number | null;
  generation_cost_total: number | null;
  targets_text: string;
  primary_text: string;
  deviations_text: string;
}

export interface ReportSettings extends Omit<AnalysisDesign, "outcomes"> {
  mode: DesignMode;
  planned_mode: DesignMode | null;
  outcomes: string[];
  outcome_definitions: OutcomeDefinition[];
  analysis_plan: AnalysisPlan;
  notes: string[];
}

/** The post-run report: generated from stored data only, sha256 = `report_hash`. */
export interface StudyReport {
  schema_version: number;
  kind: "analysis_study_report";
  generated_at: string;
  report_hash: string;
  status: "ready";
  study: {
    id: string;
    name: string;
    description: string;
    status: StudyStatus;
    locked_at: string | null;
    launched_at: string | null;
    completed_at: string | null;
  };
  protocol_hash: string | null;
  protocol_integrity: {
    stored: string | null;
    recomputed: string | null;
    match: boolean | null;
  };
  plan_hash: string | null;
  settings: ReportSettings;
  inputs: {
    runs: number;
    analysis_run_ids: string[];
    findings_read: number;
  };
  summary: ReportSummary;
  sections: ReportSection[];
}

/**
 * `GET /studies/{id}/report/` and `POST .../report/` (202): the report
 * lifecycle plus, once ready, its integrity checks and the document.
 */
export interface StudyReportEnvelope extends ReportState {
  protocol_hash: string;
  /** Ready only: the stored report matches its sha256. */
  verified: boolean | null;
  /** Ready only: a study run changed after the report was generated. */
  stale: boolean | null;
  /** Only on GET of a ready report. */
  report: StudyReport | null;
}

// ── Targets ───────────────────────────────────────────────────────────

export interface TargetOptions {
  completed_jobs: number;
  experiments: {
    id: string;
    name: string;
    status: string;
    mode: string;
    repeats: number;
    total_jobs: number;
    completed_jobs: number;
    conditions: {
      id: number;
      label: string;
      model_id: string | null;
      harness: string;
    }[];
    app_slugs: string[];
    created_at: string;
  }[];
  batches: {
    id: string;
    name: string;
    status: string;
    mode: string;
    total_jobs: number;
    completed_jobs: number;
    created_at: string;
  }[];
  models: {
    id: number;
    model_id: string;
    name: string;
    provider: string;
    jobs: number;
  }[];
  harnesses: { name: string; jobs: number }[];
  stacks: { slug: string; jobs: number }[];
  apps: { id: number; slug: string; name: string; jobs: number }[];
  modes: { mode: string; jobs: number }[];
}

export interface TargetResolvePayload {
  target_spec: TargetSpec;
  exclusion_rules?: Partial<ExclusionRules> | null;
  /** Resolve under this profile's scope (measured SLOC, file kinds). */
  profile_id?: string | null;
  page?: number;
  /** 1..100 */
  per_page?: number;
}

export interface TargetResolveResult extends Page<TargetDescription> {
  eligible_on_page: number;
  /** Counts by identity factor over every resolved job ("(none)" when unknown). */
  summary: {
    models: Record<string, number>;
    apps: Record<string, number>;
    harnesses: Record<string, number>;
    modes: Record<string, number>;
    stacks: Record<string, number>;
  };
  /** Resolution notes (e.g. jobs that are not completed). */
  notes: PreflightIssue[];
  target_spec: TargetSpec;
  exclusion_rules: ExclusionRules;
}

// ── Guards ────────────────────────────────────────────────────────────

/** True once the run finalized (the measurement is `{}` before). */
export function isMeasurement(
  value: Partial<Measurement> | null | undefined,
): value is Measurement {
  return !!value && typeof value.schema_version === "number" && !!value.static;
}

/** True once the run finalized (the summary is `{}` before). */
export function isRunSummary(
  value: Partial<RunSummary> | null | undefined,
): value is RunSummary {
  return !!value && typeof value.schema_version === "number" && !!value.tools;
}

/** True for a frozen plan (a study's plan is `{}` until it is locked). */
export function isFrozenPlan(
  value: Partial<FrozenPlan> | null | undefined,
): value is FrozenPlan {
  return (
    !!value &&
    typeof value.schema_version === "number" &&
    Array.isArray(value.tools)
  );
}

/** True when a stored study preflight exists (it is `{}` before the first one). */
export function isPreflightReport(
  value: StoredPreflight | null | undefined,
): value is PreflightReport & { launch?: LaunchState } {
  return !!value && typeof value.generated_at === "string" && !!value.estimates;
}

/** True when a target spec has been chosen (`{}` on a fresh draft). */
export function hasTargetSpec(
  value: TargetSpec | Record<string, never> | null | undefined,
): value is TargetSpec {
  return !!value && typeof (value as { kind?: unknown }).kind === "string";
}

export function isRunActive(status: RunStatus | string): boolean {
  return (RUN_ACTIVE_STATUSES as readonly string[]).includes(status);
}

/** Tool status that leaves its measurement intact (succeeded / not applicable). */
export function isToolOk(status: ToolStatus | string): boolean {
  return (TOOL_OK_STATUSES as readonly string[]).includes(status);
}
