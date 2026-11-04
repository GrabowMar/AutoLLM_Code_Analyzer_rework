/**
 * v3 generation API types (spec section 9): hand-written mirrors of the backend `<Thing>In` / `<Thing>Out`
 * ninja schemas and the content contracts in `backend/generation/contracts/`.
 *
 * Ownership: WP-14 creates this file and owns the shared core (errors, pages, refs, issues), the library
 * (9.1), prompts (9.2), harnesses/config (9.3) and the budget-knob types the Budget preset editor needs.
 * WP-15, WP-16 and WP-17 append their own types in separate marked blocks at the end
 * (`// --- WP-15 ---` … `// --- end WP-15 ---`); nobody edits another WP's block.
 *
 * Conventions: optional (`?`) means the server may omit the key; `| null` means it is sent as `null`.
 * Stored library content is always complete (every default filled in by the server's `normalize`).
 */

// =====================================================================================
// Shared core (WP-14)
// =====================================================================================

/** `{items, total, page, page_size}` (page_size max 200). */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const PAGE_SIZE_MAX = 200;

export type IssueSeverity = "error" | "warn" | "info";

/** A quick fix the UI can offer ("raise cap to $X", "reduce repeats to N"). */
export interface IssueFix {
  label: string;
  action: string;
  params: Record<string, unknown>;
}

/** A registered issue (`contracts/issues.py`); `code` is always one of the registry's codes. */
export interface Issue {
  code: string;
  severity: IssueSeverity;
  message: string;
  cells: string[];
  path: string | null;
  data: Record<string, unknown>;
  fix: IssueFix[];
}

/** Error body of every 400/403/404/409/422 response. */
export interface ErrorOut {
  detail: string;
  code: string;
  issues?: Issue[] | null;
}

/** `{id, username}` of the row's author (`null` for system rows). */
export interface UserRef {
  id: number;
  username: string;
}

/** A pinned library ref `slug@v#hash12`, or the authoring form `slug@v`. */
export type RefString = string;

export interface ParsedRef {
  slug: string;
  version: number;
  hash12: string | null;
}

const REF_RE =
  /^([A-Za-z0-9][A-Za-z0-9_-]*)@([1-9][0-9]*)(?:#([0-9a-f]{12}))?$/;

/** `slug@v[#hash12]` → parts, or `null` when the text is not a ref. */
export function parseRef(ref: string | null | undefined): ParsedRef | null {
  const m = REF_RE.exec(ref ?? "");
  return m ? { slug: m[1], version: Number(m[2]), hash12: m[3] ?? null } : null;
}

/** `slug@v` (drops the hash). */
export function shortRef(ref: string): string {
  const parsed = parseRef(ref);
  return parsed ? `${parsed.slug}@${parsed.version}` : ref;
}

// =====================================================================================
// Library kinds (9.1)
// =====================================================================================

/** The `kind` field of a library row (`contracts.library.LIBRARY_KINDS`). */
export type LibraryKind =
  | "task"
  | "stack"
  | "starter"
  | "style"
  | "guidance"
  | "prompt"
  | "prompt_set"
  | "harness_preset"
  | "sampling"
  | "resource_policy"
  | "eval_suite"
  | "detector";

/** The `{kind}` URL segment of `/library/{kind}/…` (and of the frontend routes `/library/[kind]`). */
export type LibraryApiKind =
  | "tasks"
  | "stacks"
  | "starters"
  | "styles"
  | "guidance"
  | "prompts"
  | "prompt-sets"
  | "harness-presets"
  | "sampling-presets"
  | "resource-policies"
  | "eval-suites"
  | "detectors";

export const LIBRARY_API_KINDS: Record<LibraryApiKind, LibraryKind> = {
  tasks: "task",
  stacks: "stack",
  starters: "starter",
  styles: "style",
  guidance: "guidance",
  prompts: "prompt",
  "prompt-sets": "prompt_set",
  "harness-presets": "harness_preset",
  "sampling-presets": "sampling",
  "resource-policies": "resource_policy",
  "eval-suites": "eval_suite",
  detectors: "detector",
};

export const LIBRARY_KIND_API: Record<LibraryKind, LibraryApiKind> =
  Object.fromEntries(
    Object.entries(LIBRARY_API_KINDS).map(([api, kind]) => [kind, api]),
  ) as Record<LibraryKind, LibraryApiKind>;

export function isLibraryApiKind(
  value: string | null | undefined,
): value is LibraryApiKind {
  return !!value && value in LIBRARY_API_KINDS;
}

export type Visibility = "private" | "shared" | "system";

/** Content type per kind (the stored, normalized form). */
export interface LibraryContentMap {
  task: TaskSpecContent;
  stack: StackProfileContent;
  starter: StarterContent;
  style: StyleContent;
  guidance: GuidanceContent;
  prompt: PromptTemplateContent;
  prompt_set: PromptSetContent;
  harness_preset: HarnessPresetContent;
  sampling: SamplingContent;
  resource_policy: ResourcePolicyContent;
  eval_suite: EvalSuiteContent;
  detector: DetectorContent;
}

/** `LibraryItemOut`: one version of one entity. */
export interface LibraryItemOut<K extends LibraryKind = LibraryKind> {
  kind: K;
  slug: string;
  version: number;
  name: string;
  description: string;
  schema_version: number;
  content: LibraryContentMap[K];
  content_hash: string;
  /** `slug@v#hash12`. */
  pinned_ref: string;
  derived_from: string | null;
  is_system: boolean;
  is_archived: boolean;
  visibility: Visibility;
  created_by: UserRef | null;
  created_at: string;
  /** Filterable tags derived from the content (`?tag=`), never free text. */
  tags: string[];
  /** Query columns derived from the content (e.g. `tier`, `role`, `tree_hash`, `structured_hash`). */
  columns: Record<string, unknown>;
  /** Tasks only: `TaskDerived`. */
  derived?: TaskDerived | Record<string, unknown> | null;
  /** Detail/version reads: `{status, issues, linter_version}`. */
  lint?: LibraryLint | null;
  /** Version create: true when the content hashed equal to the base (no new version). */
  unchanged?: boolean | null;
}

export interface LibraryLint {
  status: "ok" | "warn" | "error";
  issues: Issue[];
  linter_version?: number;
}

export interface VersionSummary {
  version: number;
  name: string;
  content_hash: string;
  pinned_ref: string;
  derived_from: string | null;
  is_archived: boolean;
  created_by: UserRef | null;
  created_at: string;
}

export interface LibraryDetailOut<K extends LibraryKind = LibraryKind> {
  latest: LibraryItemOut<K>;
  versions: VersionSummary[];
}

export interface LibraryListQuery {
  q?: string;
  tag?: string;
  archived?: boolean;
  mine?: boolean;
  page?: number;
  page_size?: number;
}

export interface LibraryCreateIn<K extends LibraryKind = LibraryKind> {
  slug: string;
  name: string;
  description?: string;
  content: LibraryContentMap[K] | Record<string, unknown>;
  visibility?: "private" | "shared";
}

export interface LibraryVersionIn<K extends LibraryKind = LibraryKind> {
  base_version: number;
  content: LibraryContentMap[K] | Record<string, unknown>;
  name?: string | null;
  description?: string | null;
  visibility?: "private" | "shared" | null;
}

export interface ValidateOut {
  ok: boolean;
  issues: Issue[];
  content_hash: string | null;
  /** The normalized content (defaults filled, refs pinned) when it validated. */
  content?: Record<string, unknown> | null;
  derived?: Record<string, unknown> | null;
}

export interface UsageOut {
  studies: { id: string; name: string; status: string; version: number }[];
  runs: number;
}

export interface DiffEntry {
  path: string;
  a: unknown;
  b: unknown;
}

export interface LibraryDiffOut {
  unified: string;
  structured: DiffEntry[];
}

export interface ForkIn {
  new_slug: string;
  name?: string;
  version?: number | null;
  description?: string | null;
}

export interface StarterTreeOut {
  files: { path: string; size: number | null; blob: string; mode: number }[];
  dockerfile: string;
}

export interface TaskOracleEntry {
  status: "pass" | "fail" | "missing" | "error";
  run_id: string | null;
  eval_suite_ref: string | null;
  at: string | null;
}

export interface PackageImportOut {
  mode: "dry_run" | "apply" | string;
  created: Record<string, unknown>[];
  reused: Record<string, unknown>[];
  conflicts: Record<string, unknown>[];
}

// =====================================================================================
// Content contracts (section 3.4)
// =====================================================================================

export type FieldType =
  | "string"
  | "text"
  | "int"
  | "float"
  | "bool"
  | "date"
  | "datetime"
  | "enum"
  | "ref"
  | "email"
  | "url"
  | "json"
  | "file";
export const FIELD_TYPES: FieldType[] = [
  "string",
  "text",
  "int",
  "float",
  "bool",
  "date",
  "datetime",
  "enum",
  "ref",
  "email",
  "url",
  "json",
  "file",
];

export type EndpointAuth = "public" | "user" | "owner" | "admin";
export const ENDPOINT_AUTHS: EndpointAuth[] = [
  "public",
  "user",
  "owner",
  "admin",
];
export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export const HTTP_METHODS: HttpMethod[] = [
  "GET",
  "POST",
  "PUT",
  "PATCH",
  "DELETE",
];
export type ProseVariant = "minimal" | "standard" | "exhaustive";
export const PROSE_VARIANTS: ProseVariant[] = [
  "minimal",
  "standard",
  "exhaustive",
];
export type Actor = "anon" | "user_a" | "user_b" | "admin";
export const ACTORS: Actor[] = ["anon", "user_a", "user_b", "admin"];

export interface EntityField {
  name: string;
  type: FieldType;
  required: boolean;
  unique: boolean;
  max_length: number | null;
  min: number | null;
  max: number | null;
  enum: string[] | null;
  ref: string | null;
  default: unknown;
  secret: boolean;
}

export interface Entity {
  name: string;
  fields: EntityField[];
  owned_by_user: boolean;
  soft_delete: boolean;
}

export type QuerySemantics =
  | "filter"
  | "search"
  | "sort"
  | "page"
  | "page_size"
  | "other";
export const QUERY_SEMANTICS: QuerySemantics[] = [
  "filter",
  "search",
  "sort",
  "page",
  "page_size",
  "other",
];

export interface QueryParam {
  name: string;
  type: FieldType;
  required: boolean;
  enum: string[] | null;
  default: unknown;
  max: number | null;
  semantics: QuerySemantics;
}

export type ContentType = "json" | "multipart" | "form" | "none";
export const CONTENT_TYPES: ContentType[] = [
  "json",
  "multipart",
  "form",
  "none",
];
export type Realtime = "none" | "websocket" | "sse";
export const REALTIME_KINDS: Realtime[] = ["none", "websocket", "sse"];

export interface Endpoint {
  /** Stable dotted id ("todos.update"); checks, slots and probes refer to it. */
  id: string;
  method: HttpMethod;
  path: string;
  auth: EndpointAuth;
  owner_entity: string | null;
  success_status: number;
  query: QueryParam[];
  request_schema: Record<string, unknown> | null;
  response_schema: Record<string, unknown> | null;
  content_type: ContentType;
  errors: number[];
  tags: string[];
  realtime: Realtime;
}

export interface UiRequirement {
  id: string;
  text: string;
  endpoints: string[];
}

export interface AuthFlow {
  /** Endpoint id (the contract field is aliased `register`). */
  register: string | null;
  login: string;
  logout: string | null;
  credential: "bearer" | "cookie";
  token_json_path: string | null;
  csrf: "none" | "double_submit_cookie" | "header_from_endpoint";
  csrf_endpoint: string | null;
}

export type CheckCategory =
  | "infra"
  | "functional"
  | "auth"
  | "ownership"
  | "validation"
  | "negative"
  | "persistence"
  | "pagination"
  | "filter";
export const CHECK_CATEGORIES: CheckCategory[] = [
  "infra",
  "functional",
  "auth",
  "ownership",
  "validation",
  "negative",
  "persistence",
  "pagination",
  "filter",
];

export interface CheckRequest {
  method: HttpMethod;
  path: string;
  query: Record<string, unknown>;
  json: unknown;
  form: Record<string, unknown> | null;
  files: Record<string, unknown> | null;
}

export interface CompiledRequest extends CheckRequest {
  actor: Actor;
  headers: Record<string, string>;
  expect_status: number | number[] | null;
}

export interface SetupStep {
  actor: Actor | null;
  request: CheckRequest;
  expect_status: number | number[] | null;
  capture: Record<string, string>;
}

export interface CheckExpect {
  status: number | number[];
  json_schema: Record<string, unknown> | null;
  json_contains: Record<string, unknown> | null;
  header_contains: Record<string, string> | null;
  max_ms: number | null;
}

export interface CheckSpec {
  id: string;
  category: CheckCategory;
  endpoint: string | null;
  actor: Actor;
  setup: SetupStep[];
  request: CheckRequest;
  expect: CheckExpect;
  depends_on: string[];
  restart_before: boolean;
  stated_in: ProseVariant[];
}

export type ProbeKind =
  | "sqli_boolean"
  | "sqli_error"
  | "sqli_time"
  | "xss_stored"
  | "xss_reflected"
  | "path_traversal"
  | "open_redirect"
  | "idor_enumeration"
  | "mass_assignment"
  | "xxe"
  | "ssrf_mock"
  | "csrf_missing"
  | "rate_limit_missing"
  | "plaintext_password_db"
  | "default_credential"
  | "jwt_none_alg"
  | "verbose_errors";
export const PROBE_KINDS: ProbeKind[] = [
  "sqli_boolean",
  "sqli_error",
  "sqli_time",
  "xss_stored",
  "xss_reflected",
  "path_traversal",
  "open_redirect",
  "idor_enumeration",
  "mass_assignment",
  "xxe",
  "ssrf_mock",
  "csrf_missing",
  "rate_limit_missing",
  "plaintext_password_db",
  "default_credential",
  "jwt_none_alg",
  "verbose_errors",
];

export interface ProbeSpec {
  id: string;
  /** "cwe-89". */
  cwe: string;
  kind: ProbeKind;
  endpoint: string | null;
  params: Record<string, unknown>;
  requires_checks: string[] | null;
  positive_control: CompiledRequest | null;
}

/** `TaskSpecVersion.content` (schema_version 4). */
export interface TaskSpecContent {
  title: string;
  summary: string;
  category: string;
  entities: Entity[];
  endpoints: Endpoint[];
  ui: UiRequirement[];
  has_frontend: boolean;
  features: string[];
  surfaces: string[];
  auth_flow: AuthFlow | null;
  seed_data: Record<string, Record<string, unknown>[]>;
  acceptance: CheckSpec[];
  probes: ProbeSpec[];
  prose: Partial<Record<ProseVariant, string>>;
}

/** `TaskSpecVersion.derived`: computed, never hashed. */
export interface TaskDerived {
  endpoint_count: number;
  entity_count: number;
  field_count: number;
  relation_count: number;
  features: string[];
  surfaces: string[];
  difficulty_score: number;
  size_bucket: "S" | "M" | "L" | "XL";
  check_statedness: Record<string, ProseVariant[]>;
}

export interface StackProfileContent {
  choice_mode: "fixed" | "free";
  backend: {
    language: "python" | "javascript" | "typescript" | "go" | "java" | "free";
    framework:
      | "flask"
      | "fastapi"
      | "django"
      | "express"
      | "go-stdlib"
      | "gin"
      | "spring-boot"
      | "free";
    runtime: string;
  };
  frontend: {
    framework: "react" | "vue" | "svelte" | "none" | "free";
    language: "ts" | "js" | "free" | "none";
    build_tool: "vite" | "none" | "free";
  };
  database: "sqlite" | "postgres" | "free";
  run_contract: {
    port: number;
    api_prefix: string;
    health_path: string;
    static_serving: "backend" | "none";
    data_dir: string;
    start: "dockerfile";
  };
  /** PromptTemplate ref (role fragment) describing the stack to the model. */
  brief_fragment: string;
  adherence: {
    backend_markers: string[];
    frontend_markers: string[];
    db_markers: string[];
  };
  resources: { min_memory: string; min_cpus: number };
}

export interface StarterContent {
  stack_profile: string;
  level: "bare" | "minimal" | "full";
  files: Record<string, { blob: string; mode: number }>;
  dockerfile: string;
  base_images: { ref: string; digest: string }[];
  lockfiles: string[];
  install: string[];
  composition: {
    backend_fragment: string;
    frontend_fragment: string | null;
    dockerfile_template: string;
  };
  forbidden_content_check: { passed: boolean; linter_version: number };
}

export interface StyleContent {
  system:
    | "tailwind"
    | "plain-css"
    | "css-modules"
    | "component-library"
    | "model-choice";
  library: { name: string; packages: string[]; frameworks: string[] } | null;
  tone: string | null;
  tone_text: string | null;
  accessibility: "unspecified" | "wcag-aa";
  fragment: string;
  adherence: {
    required_markers: string[];
    forbidden_markers: string[];
    class_share_min: number | null;
  };
  compatible_frontends: string[];
}

export interface GuidanceContent {
  security_hint: "none" | "generic" | "cwe_specific";
  cwe_source: "task_surfaces" | "explicit";
  cwes: string[];
  library_prescriptions: { purpose: string; library: string }[];
  fragment: string | null;
}

export type PromptRole =
  | "brief"
  | "system"
  | "user"
  | "slot_single_shot_system"
  | "slot_single_shot_user"
  | "slot_agent_brief"
  | "fragment";
export const PROMPT_ROLES: PromptRole[] = [
  "brief",
  "system",
  "user",
  "slot_single_shot_system",
  "slot_single_shot_user",
  "slot_agent_brief",
  "fragment",
];
export type PromptMode = "app" | "slot";

export interface PromptTemplateContent {
  role: PromptRole;
  engine: "jinja2-sandbox@1";
  template: string;
  /** Declared top-level context variables (lint: an undeclared one is an error). */
  variables: string[];
  modes: PromptMode[];
  tags: string[];
}

export type FragmentKey =
  | "auth_contract.contract_bearer"
  | "auth_contract.cookie_session"
  | "auth_contract.spec_defined"
  | "network.none"
  | "network.packages"
  | "budget_neutral";
export const FRAGMENT_KEYS: FragmentKey[] = [
  "auth_contract.contract_bearer",
  "auth_contract.cookie_session",
  "auth_contract.spec_defined",
  "network.none",
  "network.packages",
  "budget_neutral",
];

export type SystemPromptMode = "harness_native" | "append" | "override";

export interface PromptSetContent {
  mode: PromptMode;
  /** role → pinned PromptTemplate ref. */
  roles: Partial<Record<PromptRole, string>>;
  system_prompt_mode: SystemPromptMode;
  /** fragment key → pinned PromptTemplate ref (role fragment). */
  fragments: Partial<Record<FragmentKey, string>>;
}

export interface HarnessPresetContent {
  harness: string;
  harness_version: string;
  options: Record<string, unknown>;
  controlled: boolean;
  command_timeout_s: number | null;
  tool_policy: { deny: string[]; allow: string[] | null };
  max_turns: number | null;
  subagents: boolean;
  pin_system_prompt: boolean;
}

export interface SamplingParams {
  temperature: number | null;
  top_p: number | null;
  top_k: number | null;
  min_p: number | null;
  frequency_penalty: number | null;
  presence_penalty: number | null;
}

export interface SamplingContent {
  policy: "provider_default" | "explicit";
  params: SamplingParams;
  forward_seed: boolean;
}

export interface EvalSuiteContent {
  tester: { version: number; image_digest: string };
  acceptance_compiler_version: number;
  adherence_checker: { version: number; image_digest: string };
  probes: "none" | "task" | "all_applicable";
  baseline: "starter" | "none";
  timeouts: { build_s: number; start_s: number; tester_s: number };
  flaky_checks: string[];
  tester_rules_version: number;
  probe_rules_version: number;
}

export type DetectorEngine =
  | "semgrep"
  | "ts_query"
  | "regex"
  | "probe"
  | "manual";
export const DETECTOR_ENGINES: DetectorEngine[] = [
  "semgrep",
  "ts_query",
  "regex",
  "probe",
  "manual",
];
export type DetectorPolarity = "insecure" | "secure" | "neutral";

export interface DetectorContent {
  engine: DetectorEngine;
  languages: string[];
  /** Semgrep YAML, a tree-sitter query or a regex ("" for probe/manual). */
  rule: string;
  tool: { name: string; version: string };
  feature_key: string;
  polarity: DetectorPolarity;
  /** The rule's own label field, copied verbatim (never computed). */
  tool_severity_field: string | null;
  cwe: string[];
  description: string;
  scopes: ("span" | "file")[];
  taint: boolean;
}

// =====================================================================================
// Resource policy = "Budget preset" (section 4.2, contracts/resources.py)
// =====================================================================================

export type KnobClass = "C" | "R" | "O" | "S";
export type PolicyTier = "frugal" | "standard" | "generous";
export const POLICY_TIERS: PolicyTier[] = ["frugal", "standard", "generous"];
export type ResourcePolicyTier = PolicyTier | "custom";
export type ReasoningEffort =
  | "none"
  | "minimal"
  | "low"
  | "medium"
  | "high"
  | "xhigh";

export interface StopRule {
  code:
    | "infra_error_rate"
    | "no_provider_rate"
    | "output_invalid_rate"
    | "consecutive_failures"
    | "cost_overrun"
    | "spend_fraction"
    | "condition_error_rate"
    | "ci_width_reached"
    | "wall_clock_total";
  scope: "study" | "condition" | "unit";
  window: number;
  threshold: number;
  action: "pause" | "stop" | "pause_condition" | "alert";
  stages: ("pilot" | "main")[];
  features: string[];
  n_min: number;
  n_max: number | null;
}

/** `ResourcePolicy.content`: every knob of section 4.2 (always complete). */
export interface ResourcePolicyContent {
  run: {
    max_cost_usd: number;
    max_total_tokens: number;
    max_uncached_input_tokens: number | null;
    max_output_tokens: number | null;
    max_llm_requests: number;
    max_aux_requests: number;
    max_turns: number | null;
    max_workspace_bytes: number;
  };
  call: { max_output_tokens: number; max_input_tokens: number | null };
  context: {
    max_context_tokens: number | null;
    compaction: "harness_default" | "auto" | "auto_prune" | "off";
  };
  reasoning: {
    mode: "provider_default" | "effort" | "max_tokens";
    effort: ReasoningEffort | null;
    max_tokens: number | null;
    exclude: boolean;
  };
  caching: {
    prompt_cache: "auto" | "on" | "off";
    ttl: "5m" | "1h";
    sticky_session: boolean;
  };
  routing: {
    provider_sort: "none" | "price" | "throughput" | "latency";
    /** `{prompt, completion}` USD/Mtok, `"model×k"`, or null. */
    max_price: { prompt: number; completion: number } | string | null;
    allow_fallbacks: boolean;
    only: string[];
    ignore: string[];
    order: string[];
    quantizations: string[];
    require_parameters: boolean;
    data_collection: "allow" | "deny";
    zdr: boolean;
    preferred_min_throughput: number | null;
    preferred_max_latency: number | null;
    serving_tier: "standard" | "flex" | "batch" | "auto_batch";
  };
  time: {
    wall_clock_s: number;
    idle_timeout_s: number | null;
    command_timeout_s: number | null;
    llm_call_timeout_s: number;
  };
  retries: {
    upstream_retries: number;
    backoff_s: number;
    retry_on: string[];
    infra_retries: number;
    count_failed_calls: boolean;
  };
  sandbox: { memory: string; nano_cpus: number };
  network_policy: "none" | "packages";
  concurrency: {
    study_max_parallel: number;
    per_endpoint: number | null;
    per_model: number | null;
    per_provider: number | null;
    rpm_per_model: number | null;
    max_resident_models: number | null;
    eval_parallel: number;
  };
  cell: { max_cost_usd: number };
  study: {
    max_cost_usd: number | "auto";
    max_total_tokens: number | null;
    max_runs: number | null;
    max_wall_hours: number | null;
    cap_basis: "billed" | "list" | "auto";
    budget_basis: "usd" | "tokens" | "auto";
    alert_at: number[];
    on_budget_exhausted: "pause" | "stop_balanced" | "stop";
    stop_rules: StopRule[];
  };
  pilot: {
    enabled: boolean;
    repeats: number;
    units: "all" | number;
    smoke_first: boolean;
    reuse_as_main: boolean;
    gates: {
      max_infra_error_rate: number;
      min_build_rate: number;
      max_cost_overrun: number;
      max_cap_hit_rate: number;
      max_truncation_rate: number;
      max_call_truncation_rate: number;
      max_wall_clock_rate: number;
      max_oom_rate: number;
      usd_cap_binding: number;
    };
    auto_expand: boolean;
  };
  caches: {
    dependency_cache: "off" | "shared_ro";
    reuse_sample_images: boolean;
    reuse_parent_build: boolean;
    reuse_eval_by_tree: boolean;
  };
  slot: { max_output_tokens: number | null };
  estimate: {
    method: "priors" | "heuristic";
    require_confirm_above_usd: number;
  };
}

/** One `KNOB_META` row (`GET /budget/knobs/` → `knobs`). */
export interface KnobMeta {
  /** Dotted path into `ResourcePolicyContent` ("run.max_cost_usd"); `reasoning` covers mode/effort/max_tokens. */
  key: string;
  class: KnobClass;
  /** The class under `study.budget_basis = tokens`, when it differs (`run.max_cost_usd` → O). */
  class_when_tokens: KnobClass | null;
  /** Enforcement points (GW, SBX, HAR, SCH, LED, STU, EXE, PLN, SLT, EVL, BLD, UI). */
  where: string[];
  /** The top-level section of the key ("run", "routing", …); the editor maps it to its accordion groups. */
  ui_group: string;
  key_limit: boolean;
  /** Display type as in the spec table ("int|null", "enum a,b,c", "bool", …). */
  type: string;
  unit: string | null;
  description: string;
  defaults: Record<PolicyTier, unknown>;
}

/** `GET /budget/knobs/`. */
export interface KnobsOut {
  knobs: KnobMeta[];
  key_limits: string[];
  tiers: Record<PolicyTier, ResourcePolicyContent>;
}

/** `GET /budget/policies/{ref}/explain/`. */
export interface PolicyExplainOut {
  ref: string;
  tier: ResourcePolicyTier;
  summary: string;
  effective: {
    content: ResourcePolicyContent;
    budget_basis: "usd" | "tokens";
    cap_basis: "billed" | "list";
    serving_tier: "standard" | "flex" | "batch";
    clamps: { key: string; from: unknown; to: unknown; reason: string }[];
    notes: string[];
    issues: Issue[];
  };
  notes: string[];
  issues: Issue[];
  treatment_view: Record<string, unknown>;
  generation_policy_hash: string;
  knob_meta: KnobMeta[];
  diff_from_tier: {
    key: string;
    tier: PolicyTier;
    tier_value: unknown;
    value: unknown;
    class: KnobClass;
  }[];
}

// =====================================================================================
// Prompts (9.2)
// =====================================================================================

/** `GET /prompts/variables/` row: the documented render context. */
export interface PromptVariable {
  name: string;
  /** "object", "string", "list[string]". */
  type: string;
  description: string;
  example: unknown;
  modes: PromptMode[];
}

export interface DraftTemplateIn {
  template: string;
  engine?: "jinja2-sandbox@1";
  variables?: string[] | null;
}

/** App-mode levels of one cell; any level is a ref (`slug@v[#h]`) or a bare slug (= latest visible). */
export interface AppContextFrom {
  task: string;
  variant?: ProseVariant;
  stack?: string | null;
  starter_level?: "bare" | "minimal" | "full" | null;
  style?: string | null;
  auth_mode?: "contract_bearer" | "cookie_session" | "spec_defined";
  guidance?: string | null;
  harness_preset?: string | null;
  resource_policy?: string | null;
  network_policy?: "none" | "packages" | null;
  allowed_bases?: string[] | null;
}

/** Slot-mode render context (`slot_target` is a SlotTarget uuid); slot keys are WP-12's. */
export interface SlotContextFrom extends Partial<Omit<AppContextFrom, "task">> {
  slot_target: string;
  task?: string;
  hole_mode?: string;
  keep_docstring?: boolean;
  context_policy?: Record<string, unknown>;
}

export interface RenderIn {
  prompt_set: string | null;
  templates?: Partial<Record<PromptRole, DraftTemplateIn>> | null;
  context_from: AppContextFrom | SlotContextFrom;
}

export type SourceOwnerKind =
  | "prompt"
  | "prompt_set"
  | "stack"
  | "style"
  | "guidance"
  | "auth_fragment"
  | "task"
  | "starter";

/** Which library entity owns `text[char_start:char_end]` of a stimulus. */
export interface SourceSpanOut {
  template_ref: string;
  owner_kind: SourceOwnerKind;
  owner_ref: string;
  role: string | null;
  char_start: number;
  char_end: number;
}

export interface StimulusOut {
  role: PromptRole | string;
  template_ref: string;
  text: string;
  sha: string;
  chars: number;
  est_tokens: number;
  sources: SourceSpanOut[];
}

export interface RenderOut {
  stimuli: StimulusOut[];
  prompt_hash: string;
  issues: Issue[];
}

export interface PromptDiffOut {
  per_role: { role: string; unified: string }[];
}

// =====================================================================================
// Harnesses and config (9.3)
// =====================================================================================

export interface HarnessOptionField {
  key: string;
  type: "enum" | "int" | "bool" | "multi_enum" | string;
  label: string;
  help: string;
  default: unknown;
  choices?: { value: string; label: string }[];
  min?: number | null;
  max?: number | null;
}

export interface HarnessOut {
  name: string;
  label: string;
  description: string;
  protocol: string;
  capabilities: Record<string, unknown>;
  enabled: boolean;
  blocked_reason: string | null;
  versions: string[];
  pinned_version: string;
  option_schema: HarnessOptionField[];
  default_options: Record<string, unknown>;
  facts_version: number;
  vendor_affinity: string | null;
  min_call_output_tokens: number;
  open_questions: string[];
  /** Open questions that apply only on some endpoints, keyed by endpoint kind. */
  endpoint_open_questions: Record<string, string[]>;
  /** Why this harness is limited to exploratory studies (`null` when it is not). */
  exploratory_only: string | null;
}

/** `GET /harnesses/{name}/facts/` (`HarnessFacts.to_dict()`); the keys are WP-07's. */
export type HarnessFactsOut = Record<string, unknown>;

/**
 * One row of the issue-code registry (`contracts/issues.registry_payload()`, served as `/config/`'s
 * `issue_codes`). `exploratory`/`confirmatory` are the default severity per study kind (`null` = the code
 * does not apply to that kind); `alternatives` are the other severities an emitter may choose.
 */
export interface IssueCodeOut {
  code: string;
  exploratory: IssueSeverity | null;
  confirmatory: IssueSeverity | null;
  alternatives: IssueSeverity[];
  owner: string;
  summary: string;
}

export interface GenerationConfigOut {
  factor_vocabulary: { keys: string[]; app: string[]; slot: string[] } & Record<
    string,
    unknown
  >;
  stacks_matrix: Record<string, string[]>;
  auth_modes: { value: string; label: string }[];
  prose_variants: ProseVariant[];
  hole_modes: string[];
  context_modes: string[];
  executors: string[];
  eval_levels: string[];
  serving_tiers: string[];
  /** Class → one-line meaning ("confound: changes what the model sees … (12 knobs)"). */
  knob_classes: Record<KnobClass, string>;
  harnesses: { name: string; label: string; enabled: boolean }[];
  limits: {
    max_repeats: number;
    max_cells: number;
    sandbox_max: { memory: string; nano_cpus: number };
  } & Record<string, unknown>;
  /** The issue-code registry (`registry_payload()`); `issueSummaries(config)` maps it to code → summary. */
  issue_codes: IssueCodeOut[];
}

// =====================================================================================
// Appended blocks (other WPs). Keep each block between its markers.
// =====================================================================================

// --- WP-15 ---
// Studies (9.4), budget (9.8) and the `study:<id>` SSE events (4.7, contracts/events.py). Owned by WP-15.

export type StudyMode = "app" | "slot";
export type StudyKind = "exploratory" | "confirmatory";
export type StudyStatus =
  | "draft"
  | "planned"
  | "piloting"
  | "running"
  | "paused"
  | "completed"
  | "cancelled"
  | "stopped";
export const STUDY_STATUSES: StudyStatus[] = [
  "draft",
  "planned",
  "piloting",
  "running",
  "paused",
  "completed",
  "cancelled",
  "stopped",
];

export type FactorKey =
  | "model"
  | "harness_preset"
  | "stack_profile"
  | "starter_level"
  | "style"
  | "auth_mode"
  | "guidance"
  | "prompt_set"
  | "prose_variant"
  | "sampling_preset"
  | "resource_policy"
  | "network_policy"
  | "slot_executor"
  | "hole_mode"
  | "keep_docstring"
  | "context_policy";
export type FactorRole = "crossed" | "fixed" | "fraction";

/** `contracts/plan.py:FactorSpec`: levels are pinned refs, `LLMModel.model_id`s or enum values. */
export interface FactorSpec {
  key: FactorKey;
  role: FactorRole;
  levels: string[];
}

export interface UnitSpec {
  tasks: string[];
  slot_sets: string[];
  slot_targets: string[];
}

export interface GridSpec {
  type: "full" | "fractional";
  fraction: { generator: string } | null;
  /** Level combinations removed from the grid (`{factor_key: level}`). */
  exclude: Record<string, string>[];
}

export type EvalLevel =
  | "none"
  | "static"
  | "build"
  | "functional"
  | "security"
  | "full";
export const EVAL_LEVELS: EvalLevel[] = [
  "none",
  "static",
  "build",
  "functional",
  "security",
  "full",
];

export interface EvaluationOverrides {
  build?: boolean | null;
  functional?: boolean | null;
  probes?: boolean | null;
  adherence?: boolean | null;
  baseline?: boolean | null;
  analysis_static?: "none" | "default" | string[] | null;
  analysis_dynamic?: boolean | null;
  only_passing?: boolean | null;
  human_eval_pool?: boolean | null;
}

/** `Design.evaluation` (4.8): part of the design and of `prereg_hash`, never of the budget preset. */
export interface EvaluationSpec {
  eval_suite: string;
  eval_level: EvalLevel;
  overrides: EvaluationOverrides;
  slot: {
    mode: "full" | "overlay" | "static_only";
    regression: boolean;
  } | null;
  retest_fraction: number;
}

// `ParentSelection` / `PoolQuery` (7.2) are declared once, in the WP-16 block.

/** `contracts/plan.py:Design`: the editable draft on `Study.design`. */
export interface Design {
  mode: StudyMode;
  kind: StudyKind;
  factors: FactorSpec[];
  units: UnitSpec;
  repeats: number;
  grid: GridSpec;
  base_seed: number;
  seed_rule: "crn_unit_repeat";
  scheduling: "auto" | "interleaved" | "blocked";
  order_seed: number;
  /** Study-level Budget preset ref (also the default level of the `resource_policy` factor). */
  resource_policy: string;
  evaluation: EvaluationSpec;
  parent_selection: ParentSelection | null;
  slot_primary_features: string[];
  /** Confirmatory: required (pre-registered at launch). */
  analysis_plan: Record<string, unknown> | null;
}

export interface CellEstimate {
  expected_usd: number;
  expected_list_usd: number;
  p10_usd: number;
  p90_usd: number;
  /** List-price spread; null on estimates stored before E2E-13. */
  p10_list_usd?: number | null;
  p90_list_usd?: number | null;
  cap_usd: number;
  expected_tokens: number;
  expected_wall_s: number;
  p90_wall_s: number;
  basis: string;
}

export interface ConditionEstimate {
  condition_index: number;
  n_cells: number;
  expected_usd: number;
  p90_usd: number;
  expected_list_usd: number;
  p90_list_usd?: number;
}

export interface EstimateFit {
  max_repeats_under_cap: number | null;
  max_units_under_cap: number | null;
  blocks_under_cap: number | null;
}

export interface EstimateTime {
  expected_wall_s_low: number;
  expected_wall_s_high: number;
  effective_parallel: number;
  eval_machine_minutes: number;
}

/** `Estimate` (4.4 step 8): embedded in resolve, stored on `StudyPlan.estimate`. */
export interface Estimate {
  expected_total: number;
  p90_total: number;
  worst_case_total: number;
  expected_list_total: number;
  p90_list_total?: number;
  /** The study cap's unit: `cap_usd`, `fit`, `by_level` and the launch confirmation use the totals of this
   * basis (`estimateBasisTotals`). Absent on plans estimated before E2E-13 (= billed). */
  cap_basis?: "billed" | "list";
  expected_tokens_total: number;
  cap_usd: number | null;
  by_condition: ConditionEstimate[];
  /** factor key → level → expected USD. */
  by_level: Record<string, Record<string, number>>;
  fit: EstimateFit;
  time: EstimateTime | null;
  method: "priors" | "heuristic" | "mixed";
  inputs: Record<string, unknown>;
  issues: Issue[];
}

export interface PlanSummary {
  id: string;
  plan_hash: string;
  design_hash: string;
  status: string;
  n_conditions: number;
  n_units: number;
  n_cells: number;
  n_blocks: number;
  estimate: Estimate | null;
  issues: Issue[];
  resolver_version: number;
  created_at: string | null;
}

export interface FactorOut {
  key: FactorKey;
  role: FactorRole;
  levels: string[];
  position: number;
}

export interface StopReasonOut {
  code: string;
  detail: string;
  at: string;
  rule: string | null;
}

/** `GET /studies/{id}/` (`study/serialize.py:study_out`). */
export interface StudyOut {
  id: string;
  name: string;
  slug: string;
  description: string;
  hypothesis: string;
  kind: StudyKind;
  mode: StudyMode;
  status: StudyStatus;
  pause_reason: string;
  stop_reason: StopReasonOut | null;
  design: Design;
  design_hash: string;
  repeats: number;
  base_seed: number;
  order_seed: number;
  scheduling: string;
  priority: number;
  resource_policy: string | null;
  analysis_plan: Record<string, unknown> | null;
  analysis_plan_hash: string | null;
  prereg_hash: string | null;
  locked_at: string | null;
  locked: boolean;
  active_plan: PlanSummary | null;
  factors: FactorOut[];
  max_parallel: number | null;
  execution_options: Record<string, unknown>;
  budget_basis: string;
  cap_basis: string;
  on_budget_exhausted: string;
  cap_usd: number | null;
  cap_tokens: number | null;
  spent_usd: number | null;
  spent_list_usd: number | null;
  reserved_usd: number | null;
  spent_tokens: number | null;
  cloned_from: string | null;
  launched_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Clone responses only. */
  version_diffs?: { factor: string; from: string; to: string }[];
}

/** `GET /studies/` rows. */
export interface StudyListOut {
  id: string;
  name: string;
  kind: StudyKind;
  mode: StudyMode;
  status: StudyStatus;
  cells_done: number;
  cells_total: number;
  spent_usd: number | null;
  cap_usd: number | null;
  /** `null` until the study has cells (never 0 for "no data"). */
  exclusion_rate: number | null;
  locked: boolean;
  created_at: string | null;
}

export interface StudyListQuery {
  status?: StudyStatus | "";
  mode?: StudyMode | "";
  kind?: StudyKind | "";
  q?: string;
  page?: number;
  page_size?: number;
}

export interface StudyCreateIn {
  name: string;
  description?: string;
  hypothesis?: string;
  design: Design;
}

export interface StudyPatchIn {
  name?: string;
  description?: string;
  hypothesis?: string;
  design?: Design;
  analysis_plan?: Record<string, unknown> | null;
}

export interface ResolvedConditionOut {
  index: number;
  label: string;
  condition_hash: string;
  levels: Record<string, string>;
  treatment_hash: string | null;
  is_baseline: boolean;
}

export interface ResolvedUnitOut {
  index: number;
  unit_key: string;
  task_version: string | null;
  slot_target: string | null;
  unit_order: number;
}

export interface IdentityKeysOut {
  task_hash: string;
  treatment_hash: string;
  prompt_hash: string;
  generation_policy_hash: string;
  config_fingerprint: string;
  run_fingerprint: string;
}

/** One planned cell (`serialize.cell_out`). */
export interface ResolvedCellOut {
  cell_key: string;
  condition_index: number;
  unit_index: number;
  unit_key: string;
  repeat_index: number;
  stage: "pilot" | "main";
  seed: number;
  sequence: number;
  block_index: number;
  levels: Record<string, string>;
  identity: IdentityKeysOut | null;
  estimate: CellEstimate | null;
  excluded: "config_invalid" | null;
}

export interface ResolveIn {
  drop_incompatible?: boolean;
  sample_cells?: number;
  persist?: boolean;
}

/** `POST /studies/{id}/resolve/` and `/freeze-plan/`. */
export interface PlanOut {
  plan_hash: string;
  design_hash: string;
  persisted: boolean;
  resolver_version: number;
  n_conditions: number;
  n_units: number;
  n_cells: number;
  n_cells_by_stage: Partial<Record<"pilot" | "main", number>>;
  n_blocks: number;
  unbalanced: boolean;
  estimate: Estimate | null;
  issues: Issue[];
  conditions: ResolvedConditionOut[];
  units: ResolvedUnitOut[];
  cells_sample: ResolvedCellOut[];
}

export interface CellStimulusOut {
  role: string;
  template_ref: string;
  text: string;
  sha: string;
  chars: number;
  est_tokens: number;
  sources: SourceSpanOut[];
}

export interface EffectiveRoleOut {
  role: string;
  template_ref: string;
  status: "used" | "ignored(harness_native)" | (string & {});
  native_prompt_ref: string | null;
}

export interface EffectiveParamOut {
  key: string;
  value: unknown;
  source: "policy" | "clamp" | (string & {});
  cls: KnobClass;
}

/** `GET /studies/{id}/plans/{plan_hash}/cells/{cell_key}/`. */
export interface ResolvedCellDetailOut {
  cell_key: string;
  levels: Record<string, string>;
  pinned_refs: Record<string, string>;
  stimuli: CellStimulusOut[];
  effective_roles: EffectiveRoleOut[];
  effective_policy: Record<string, unknown>;
  effective_params: EffectiveParamOut[];
  model_effects: {
    dropped_params: string[];
    forced_params: Record<string, unknown>;
    seed_forwarded: boolean | null;
    structured_output: unknown;
  };
  harness: {
    name: string;
    version: string;
    options: Record<string, unknown>;
    unsupported_knobs: string[];
    protocol_mode: string | null;
    vendor_affinity: string | null;
  } | null;
  starter_tree: string[];
  slot_context: {
    files: { path: string; tokens?: number }[];
    sha: string;
    tokens: number;
    truncated_paths: string[];
  } | null;
  identity: IdentityKeysOut | null;
  estimate: CellEstimate | null;
  manifest: Record<string, unknown>;
}

export interface CellDiffOut {
  levels_diff: Record<string, [string | null, string | null]>;
  stimuli_diff: { role: string; unified: string }[];
  params_diff: { key: string; a: unknown; b: unknown }[];
}

export interface LaunchIn {
  plan_hash: string;
  confirm_usd?: number | null;
  study_cap?: number | "auto" | null;
  pilot_override?: boolean;
}

export interface LaunchOut {
  status: StudyStatus;
  created_runs: number;
  stage: "pilot" | "main";
  resolved_cap: number | null;
}

export interface PilotGate {
  code: string;
  ok: boolean;
  value: number | null;
  threshold: number | null;
}

export interface ExpandOut {
  created_runs: number;
  reused_pilot_cells: number;
  gates: PilotGate[];
}

export interface TopUpIn {
  cells: "missing" | "infra" | number[];
  reason: string;
}

export interface TopUpOut {
  created_runs: number;
  refused: { cell_id: number; code: string }[];
}

export interface CloneIn {
  mode: "exact" | "latest_versions";
  name: string;
}

export interface ConditionPauseOut {
  id: number;
  paused: boolean;
  pause_reason: string;
}

export type CellState = string;

/** One row of `GET /studies/{id}/cells/` (`serialize.cells_payload`). */
export interface StudyCellOut {
  id: number;
  condition_id: number;
  condition_index: number;
  unit_id: number;
  unit_key: string;
  repeat_index: number;
  stage: "pilot" | "main";
  block_index: number;
  sequence: number;
  seed: number;
  state: CellState;
  exclusion_reason: string;
  current_run: {
    id: string;
    status: string;
    phase: string;
    outcome: string;
    outcome_reason: string;
    usable: boolean | null;
    attempt: number;
    cell_attempt: number;
  } | null;
  outcome: string;
  attempts: number;
  beyond_plan_runs: number;
  /** A replicate cell (`add_replicates`): listed with `stage: "main"`, but NOT in the planned sample. */
  beyond_plan: boolean;
  estimate_usd: number;
  /** Expected list-price cost (what a list-basis study's gates compare with). */
  estimate_list_usd?: number;
  cap_usd: number;
  cell_cap_usd: number;
  spent_usd: number;
  spent_list_usd: number;
  adaptive_n: boolean;
}

export interface StudyCellsPayload {
  cells: StudyCellOut[];
  counts: Record<string, number>;
  states: string[];
}

export interface StudyCellsQuery {
  state?: string;
  condition?: number;
  unit?: number;
  stage?: "pilot" | "main";
}

/** One row of the attrition table (`runs/queries.py:attrition`). */
export interface AttritionRow {
  condition_id: number;
  label: string;
  planned_cells: number;
  cells: number;
  cells_without_run: number;
  runs_by_outcome: Record<string, number>;
  usable: number;
  truncated: number;
  truncated_by_kind: Record<string, number>;
  excluded: number;
  excluded_by_reason: Record<string, number>;
  infra_error: number;
  infra_retries: number;
  cell_attempts: number;
  beyond_plan_runs: number;
  attrition_rate: number | null;
  install_share_by_stack: Record<string, number | null>;
}

export interface LeaderboardRow {
  condition_id: number;
  index: number;
  label: string;
  levels: Record<string, string>;
  is_baseline: boolean;
  model: { id: number; model_id: string };
  harness: string | null;
  n_units: number;
  repeats_per_unit: number;
  n_cells: number;
  n_graded: number;
  k_passed: number;
  n_missing: Record<string, number>;
  model_failures_scored_zero: number;
  truncated_in_sample: number;
  pass_rate_mean: number | null;
  pass_rate_ci95: [number, number] | null;
  ci_method: string;
  cost_usd: number;
  retry_cost_usd: number;
  tokens_total: number;
  median_seconds: number | null;
  terminations: Record<string, number>;
  mixed_fingerprints: unknown;
  seed_forwarded: boolean | null;
  rank: number;
}

export interface PairedRow {
  condition_id: number;
  method: "paired_crn" | "independent_repeats" | (string & {});
  primary_test: string;
  untested_reason: string | null;
  n_pairs: number;
  mean_delta: number | null;
  wins: number;
  losses: number;
  ties: number;
  sign_test_p: number | null;
  wilcoxon_p: number | null;
  permutation_p: number | null;
  p_value: number | null;
  p_holm: number | null;
  p_adjusted: number | null;
  ci95: [number, number] | null;
  [key: string]: unknown;
}

export interface ByFactorLevel {
  level: string;
  emm: number | null;
  n_conditions: number;
  n_measured_conditions: number;
}

export interface ByFactorOut {
  unbalanced: boolean;
  method: string;
  factors: Record<string, Record<string, ByFactorLevel>>;
}

/** `GET /studies/{id}/results/` (`study/results.py:study_results`); keys this UI reads are typed. */
export interface StudyResultsOut {
  metric: string;
  sensitivity: string | null;
  itt: boolean;
  exclude_truncated: boolean;
  planned_repeats: number;
  stopped_early: boolean;
  common_cells: boolean;
  fingerprint_filter: string;
  baseline_condition_id: number | null;
  baseline_source: string;
  interim: boolean;
  balanced: boolean;
  leaderboard: LeaderboardRow[];
  matrix: {
    units: { id: number; unit_key: string }[];
    cells: {
      condition_id: number;
      unit_id: number;
      k: number;
      n: number;
      mean: number | null;
      runs: {
        run_id: string;
        cell_id: number;
        repeat_index: number;
        value: number | null;
        outcome: string;
        termination: string;
        truncated: boolean;
        code: string;
      }[];
    }[];
  };
  paired: {
    baseline_condition_id: number | null;
    pairing: string;
    status: string;
    correction: string;
    alpha: number;
    n_units: number;
    rows: PairedRow[];
    sensitivity: {
      exclude_truncated: boolean;
      rows: Record<string, unknown>[];
      changed: number[];
    } | null;
    [key: string]: unknown;
  };
  gate: {
    status: string;
    locked: boolean;
    deviations: string[];
    stopped_early: boolean;
    planned_cells: number;
    multiplicity: {
      family: string | null;
      correction: string;
      family_size: number;
    };
    significance_unreachable: {
      units_needed: number;
      [key: string]: unknown;
    } | null;
    exclusion_rate: {
      threshold: number;
      over: number[];
      rates: Record<string, number>;
    };
    differential_attrition: { threshold: number; conditions: number[] };
  };
  attrition: AttritionRow[];
  by_factor: ByFactorOut;
  by_prose_variant: Record<
    string,
    { stated: number | null; unstated: number | null; n: number }
  >;
  common_pass_rate: Record<string, number | null>;
  retest_agreement: {
    n: number;
    check_agreement: number | null;
    run_agreement: number | null;
    kappa: number | null;
    status?: string;
  };
  issues: Issue[];
  notes: string[];
  generated_at: string;
}

export interface StudyResultsQuery {
  metric?: string;
  fingerprint?: "all" | "majority";
  sensitivity?: string;
  baseline?: number;
  common_cells?: boolean;
}

export interface PromptOverrideIn {
  owner_kind: SourceOwnerKind;
  owner_ref: string;
  role?: string | null;
  template: string;
}

export interface StudyPromptOverrideOut {
  id: number;
  owner_kind: string;
  owner_ref_base: string;
  owner_ref_new: string;
  role: string;
  swapped_factor: string;
  diff: string;
  created_at: string;
}

export type ScheduleAction = "top_up" | "add_replicates" | "clone_and_run";

export interface ScheduleIn {
  action: ScheduleAction;
  cron: string;
  timezone?: string;
  params?: Record<string, unknown>;
  skip_if_active?: boolean;
  enabled?: boolean;
}

export interface ScheduleOut {
  id: number;
  action: ScheduleAction;
  cron: string;
  timezone: string;
  params: Record<string, unknown>;
  skip_if_active: boolean;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_result: Record<string, unknown> | null;
}

export interface CronPreviewOut {
  valid: boolean;
  detail: string;
  next_runs: string[];
}

export interface PostRunIn {
  kind: "analysis" | "patterns";
  trigger: "manual" | "completed";
  config: Record<string, unknown>;
}

export interface PostRunOut {
  id: number;
  kind: "analysis" | "patterns";
  trigger: "manual" | "completed";
  status: string;
  config: Record<string, unknown>;
  progress: Record<string, unknown>;
  analysis_run_ids: string[];
  report_id: string | null;
  pattern_run_id: string | null;
  error: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface AdhocIn {
  design: Design;
  launch?: boolean;
  name?: string | null;
  confirm_usd?: number | null;
}

export interface AdhocOut {
  study_id: string;
  plan_hash: string;
  run_ids: string[];
  [key: string]: unknown;
}

// ── Budget (9.8, 4.7) ──────────────────────────────────────────────────────

/** `GET /budget/studies/{id}/` (`budget/report.py:study_payload`). */
export interface StudyBudgetOut {
  study_id: string;
  status: StudyStatus;
  pause_reason: string;
  cap: number | null;
  cap_basis: string;
  budget_basis: string;
  on_budget_exhausted: string;
  spent: number | null;
  spent_billed: number | null;
  spent_list: number | null;
  reserved: number | null;
  open_reserved: number | null;
  remaining: number | null;
  expected_remaining: number | null;
  tokens: { spent: number; reserved: number; cap: number | null };
  burn_rate_usd_per_h: number | null;
  eta_s: number | null;
  by_condition: {
    condition: number;
    label: string;
    spent: number | null;
    runs: number;
    usable: number;
    tested: number;
    cost_per_usable_sample: number | null;
    cost_per_tested_sample: number | null;
    excluded_cell_budget: number;
    excluded_budget_stop: number;
    excluded_stopped_by_rule: number;
  }[];
  by_model: {
    model: string;
    spent: number | null;
    runs: number;
    usable: number;
  }[];
  alerts: { fraction: number; crossed: boolean }[];
  rules: Record<string, unknown>[];
  blocks: { started: number; complete: number; total: number };
  deviations: Record<string, unknown>;
}

export type SpendPeriod = "5h" | "day" | "week" | "month";
export type SpendUnit = "usd" | "list_usd" | "tokens";

/** `GET /budget/me/`. */
export interface BudgetMeOut {
  periods: {
    period: SpendPeriod;
    unit: SpendUnit;
    scope?: string;
    spent: number;
    spent_list: number;
    tokens: number;
    used?: number | null;
    cap: number | null;
    action: "block" | "warn" | null;
    cap_id?: number;
  }[];
  studies: {
    id: string;
    name: string;
    status: StudyStatus;
    spent: number;
    spent_list: number;
    cap: number | null;
    /** The cap's unit: `list` caps are measured against `spent_list`. */
    cap_basis?: "billed" | "list";
  }[];
}

/** `GET /budget/header/` (the header SpendChip). */
export interface BudgetHeaderOut {
  period: SpendPeriod;
  unit: SpendUnit;
  period_spend: number;
  period_cap: number | null;
  paused_studies: { id: string; reason: string }[];
}

export interface SpendCapIn {
  scope: "user" | "endpoint" | "model" | "global";
  user?: number | null;
  endpoint?: number | null;
  model_slug?: string;
  period: SpendPeriod;
  unit: SpendUnit;
  limit: number;
  action?: "block" | "warn";
  on_hit?: "pause";
  enabled?: boolean;
}

export interface SpendCapPatch {
  limit?: number;
  action?: "block" | "warn";
  enabled?: boolean;
  period?: SpendPeriod;
  unit?: SpendUnit;
}

export interface SpendCapOut {
  id: number;
  scope: "user" | "endpoint" | "model" | "global";
  user: number | null;
  endpoint: number | null;
  model_slug: string;
  period: SpendPeriod;
  unit: SpendUnit;
  limit: number;
  action: "block" | "warn";
  on_hit: "pause";
  enabled: boolean;
  created_by: number | null;
  staff_cap: boolean;
}

export interface SpendLedgerEntryOut {
  id: number;
  kind: string;
  run: string | null;
  attempt: number | null;
  endpoint: number | null;
  model_slug: string;
  amount_usd: number;
  list_cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  requests: number;
  source: string;
  dedupe_key: string;
  created_at: string;
}

export interface CostPriorOut {
  key: string;
  n: number;
  [key: string]: unknown;
}

export type EstimateIn =
  | { design: Design }
  | { study: string; plan_hash: string };

// ── study:<id> SSE events (contracts/events.py) ────────────────────────────

export interface StudyRunEvent {
  type: "run";
  run_id: string;
  cell_id: number | null;
  condition_id: number | null;
  unit_key: string | null;
  repeat_index: number | null;
  status: string;
  phase: string;
  outcome: string;
  attempt: number;
  endpoint_id: number | null;
  agent_pass_rate: number | null;
  ts: string | null;
}

export interface StudyTickEvent {
  type: "tick";
  counts: Record<string, number>;
  cells: Record<string, number>;
  endpoints: Record<string, unknown>[];
  bottleneck: string | null;
}

export interface StudyStateEvent {
  type: "state";
  status: StudyStatus;
  pause_reason: string;
}

export interface StudyPostRunEvent {
  type: "post_run";
  id: number;
  status: string;
  progress: Record<string, unknown>;
}

export interface SpendEvent {
  type: "spend";
  spent: number;
  spent_list: number;
  reserved: number;
  delta: number;
  run_id: string | null;
}

export interface BudgetAlertEvent {
  type: "budget_alert";
  fraction: number;
}

export interface StoppedEvent {
  type: "stopped";
  reason: string;
}

export interface PausedEvent {
  type: "paused";
  reason: string;
  condition_id: number | null;
}

export type StudyEvent =
  | StudyRunEvent
  | StudyTickEvent
  | StudyStateEvent
  | StudyPostRunEvent
  | SpendEvent
  | BudgetAlertEvent
  | StoppedEvent
  | PausedEvent;

export const STUDY_EVENT_TYPES = [
  "run",
  "tick",
  "state",
  "post_run",
  "spend",
  "budget_alert",
  "stopped",
  "paused",
] as const;

/** `run:<id>` `trajectory_step` (the Operations RunStreamPanel). */
export interface TrajectoryStepEvent {
  type: "trajectory_step";
  run_id: string;
  attempt: number;
  n_steps: number;
  step: {
    kind?: string;
    tool?: string;
    summary?: string;
    text?: string;
    [key: string]: unknown;
  } | null;
}

export interface RunUsageEvent {
  type: "usage";
  run_id: string;
  attempt: number;
  requests_by_class: Record<string, number>;
  prompt_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  list_cost_usd: number;
  inflight: number;
  budget_exhausted: string | null;
}
// --- end WP-15 ---

// --- WP-16 ---
// Runs (9.5, `runs/api.py`), slots (9.6, `slot/api.py`) and patterns (9.7, `patterns/api.py`).
// Mirrors of the ninja payloads; JSON columns the server copies through stay open records.

// ── 9.5 Runs ───────────────────────────────────────────────────────────────

export type RunKind =
  | "app_agentic"
  | "slot_single_shot"
  | "slot_agent"
  | "reference";
export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";
/** `contracts.outcome.Outcome` (6.12); `""` while the run has none yet. */
export type RunOutcome =
  | "output_invalid"
  | "build_fail"
  | "start_fail"
  | "tested"
  | "infra_error"
  | "excluded";
export const RUN_OUTCOMES: RunOutcome[] = [
  "tested",
  "output_invalid",
  "build_fail",
  "start_fail",
  "infra_error",
  "excluded",
];
/** Model-attributable outcomes (6.13): analysed as data. */
export const USABLE_OUTCOMES: RunOutcome[] = [
  "tested",
  "build_fail",
  "start_fail",
  "output_invalid",
];

export interface RunListOut {
  id: string;
  kind: RunKind | string;
  status: RunStatus | string;
  phase: string;
  outcome: RunOutcome | "";
  usable: boolean | null;
  model: string | null;
  /** factor key → human label */
  labels: Record<string, string>;
  cost_usd: number | null;
  wall_s: number | null;
  /** `null` = not measured (never 0). */
  agent_pass_rate: number | null;
  created_at: string;
  /** pinned task ref `slug@v` and its title (WP-17 → WP-08 ask; absent until the server sends them) */
  task?: string | null;
  task_title?: string | null;
}

export interface RunListQuery {
  study?: string;
  condition?: number;
  kind?: RunKind | string;
  outcome?: RunOutcome | string;
  model?: string;
  usable?: boolean;
  beyond_plan?: boolean;
  page?: number;
  page_size?: number;
}

export interface RunAttemptOut {
  attempt: number;
  executor_id: string;
  started_at: string | null;
  ended_at: string | null;
  termination: string;
  outcome: string;
  infra_error: string;
  oom_killed: boolean;
  budget_exhausted: string;
  /** `contracts.outcome.AttemptServed` */
  served: {
    providers?: Record<string, number>;
    models?: Record<string, number>;
    response_models?: Record<string, number>;
    system_fingerprints?: string[];
    routes?: Record<string, number>;
  } & Record<string, unknown>;
  /** `contracts.outcome.AttemptUsage` */
  usage: {
    requests_by_class?: Record<string, number>;
    prompt_tokens?: number;
    uncached_prompt_tokens?: number;
    completion_tokens?: number;
    reasoning_tokens?: number;
    cached_tokens?: number;
    cache_write_tokens?: number;
    cost_usd?: number;
    list_cost_usd?: number;
    call_truncations?: number;
    translation_dropped?: Record<string, number>;
    tokens_per_s_p50?: number | null;
  } & Record<string, unknown>;
  /** `contracts.outcome.TimeSplit` (seconds) */
  time_split: {
    install_s?: number;
    llm_s?: number;
    tool_s?: number;
    idle_s?: number;
  } & Record<string, unknown>;
  observed_inputs: ObservedInputsOut | null;
  has_trajectory: boolean;
  evidence_dir: string;
}

export type EvalStageStatus =
  | "pending"
  | "running"
  | "ok"
  | "fail"
  | "error"
  | "skipped"
  | "not_measured";

export interface EvalStageRecord {
  status: EvalStageStatus;
  seconds?: number | null;
  detail?: Record<string, unknown>;
}

export type EvalStageKey =
  | "lock"
  | "build"
  | "start"
  | "tester"
  | "adherence"
  | "probes";
export const EVAL_STAGE_KEYS: EvalStageKey[] = [
  "lock",
  "build",
  "start",
  "tester",
  "adherence",
  "probes",
];

export interface EvalCheckResult {
  id: string;
  category: CheckCategory | string;
  endpoint: string | null;
  status: "pass" | "fail" | "error" | "skipped";
  expected?: unknown;
  observed?: unknown;
  ms?: number | null;
  note?: string | null;
  skipped_due_to?: string[];
}

export interface EvalProbeResult {
  id: string;
  cwe: string;
  kind: ProbeKind | string;
  endpoint: string | null;
  status: "exploited" | "not_exploited" | "error" | "skipped";
  evidence?: string | null;
  skip_reason?:
    | "precondition_failed"
    | "positive_control_failed"
    | "disabled"
    | "level"
    | null;
}

export interface EvalMetricsOut {
  infra_ok: boolean;
  agent_checks: number;
  agent_passed: number;
  agent_pass_rate: number | null;
  by_category: Record<string, { n: number; passed: number }>;
  baseline_pass_rate: number | null;
  over_baseline: number | null;
  probes_tested: number;
  probes_exploited: number;
  probes_skipped: number;
  sec_pass: boolean | null;
  slot_pass_rate?: number | null;
  regression_pass_rate?: number | null;
  common_pass_rate?: number | null;
  stated_pass_rate?: number | null;
  unstated_pass_rate?: number | null;
}

/** `contracts.evaluation.EvalResult` (`RunEvaluation.result`). */
export interface EvalResultOut {
  tester_version: number;
  compiler_version: number;
  auth_mode: string;
  checks: EvalCheckResult[];
  probes: EvalProbeResult[];
  metrics: EvalMetricsOut;
  measured: boolean;
  not_measured_reason: string | null;
  flaky_checks: string[];
}

export interface AdherenceDetectedValue {
  value: string | null;
  evidence: string[];
  confidence: "high" | "medium" | "low";
}

/** `contracts.evaluation.AdherenceRecord` (`RunEvaluation.adherence`). */
export interface AdherenceRecordOut {
  checker_version: number;
  status: "compliant" | "partial" | "violated" | "not_measured";
  detected: Partial<
    Record<
      "backend" | "frontend" | "database" | "styling" | "auth",
      AdherenceDetectedValue
    >
  > | null;
  deviations: {
    dimension: string;
    expected: string;
    observed: string;
    evidence: string[];
  }[];
  packages: {
    ecosystem: string;
    name: string;
    version: string | null;
    pinned: boolean;
    declared_in: string;
    exists: boolean | null;
  }[];
  hallucinated: string[];
  unpinned: string[];
  style_metrics: {
    tailwind_class_share: number | null;
    css_files: number;
    css_modules: number;
    component_library_imports: Record<string, number>;
  };
  sbom_blob: string | null;
}

export type RunEvaluationStatus =
  | "pending"
  | "running"
  | "measured"
  | "not_measured"
  | "error";

export interface RunEvaluationOut {
  id: number;
  eval_suite: string;
  eval_level: string;
  mode: string;
  status: RunEvaluationStatus | string;
  stages: Partial<Record<EvalStageKey, EvalStageRecord | null>>;
  result: EvalResultOut | null;
  adherence: AdherenceRecordOut | null;
  build_key: string;
  image_id: string;
  reused_from: number | null;
  retest_of: number | null;
  baseline: number | null;
  created_at: string;
}

export interface StudyRefOut {
  study_id: string | null;
  study_name: string | null;
  condition_id: number | null;
  cell_id: number | null;
  unit_key: string | null;
  repeat_index: number | null;
  stage: string;
}

export interface ValidityNoteOut {
  code: string;
  level: "info" | "warn" | "error";
  detail?: string | null;
}

/** `contracts.outcome.Validity` (`GenerationRun.validity`). */
export interface RunValidityOut {
  termination?: string | null;
  truncated?: boolean;
  notes?: ValidityNoteOut[];
}

/** `contracts.evaluation.ResolvedLock`. */
export interface ResolvedLockOut {
  ecosystems: Record<
    string,
    { name: string; version: string; hash?: string | null }[]
  >;
  files: Record<string, string>;
  resolved_at: string;
}

export interface RunOut extends RunListOut {
  protocol: string;
  outcome_reason: string;
  /** task_hash, treatment_hash, prompt_hash, config_fingerprint, run_fingerprint */
  identity: Record<string, string>;
  study: StudyRefOut;
  cell_attempt: number | null;
  beyond_plan: boolean;
  lock_source: string;
  resolved_lock: ResolvedLockOut | null;
  parent_run: string | null;
  slot_target: string | null;
  attempt: number;
  attempts: RunAttemptOut[];
  validity: RunValidityOut;
  /** `contracts.outcome.RunResult` */
  result: Record<string, unknown>;
  /** `contracts.outcome.RunMetrics` */
  metrics: Record<string, unknown>;
  latest_evaluation: RunEvaluationOut | null;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunStimulusOut {
  role: string;
  template_ref: string;
  sha: string;
  /** `null` when the blob is gone. */
  text: string | null;
  /** Source spans when the server records them; absent otherwise. */
  sources?: SourceSpanOut[];
}

export interface RunManifestOut {
  manifest: Record<string, unknown>;
  manifest_hash: string;
  stimuli: RunStimulusOut[];
}

export type FileScope = "model" | "app" | "slot";
export const FILE_SCOPES: FileScope[] = ["model", "app", "slot"];
/** `FileOrigin` choices plus `parent` (a slot variant's overlaid parent row in app scope). */
export type FileOriginValue =
  | "starter"
  | "authored"
  | "modified"
  | "deleted"
  | "artifact"
  | "slot"
  | "parent";
export const FILE_ORIGINS: FileOriginValue[] = [
  "authored",
  "modified",
  "slot",
  "starter",
  "parent",
  "deleted",
  "artifact",
];

export interface RunFileOut {
  path: string;
  origin: FileOriginValue | string;
  size: number;
  loc: number;
  language: string;
  is_test: boolean;
  /** `""` = included; otherwise the exclusion reason (binary, build_output, …). */
  excluded: string;
  blob: string;
}

/** `contracts.harness.Step`. */
export interface TrajectoryStepOut {
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
  started: number | null;
  duration_s: number | null;
  tokens_in: number;
  tokens_out: number;
  files: string[];
  command_sha: string | null;
}

/** `contracts.harness.Trajectory`. */
export interface TrajectoryContentOut {
  steps: TrajectoryStepOut[];
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

export interface ObservedInputsOut {
  system_prompt_sha?: string | null;
  tool_names?: string[];
  tool_specs_sha?: string | null;
  first_user_message_sha?: string | null;
  calls_by_class?: Record<string, number>;
  native_system_prompt_blob?: string | null;
}

export interface RunTrajectoryOut {
  attempt: number;
  trajectory: TrajectoryContentOut | null;
  observed_inputs: ObservedInputsOut | null;
}

export interface RunCallOut {
  index: number;
  attempt: number;
  stage: string;
  request_class: string | null;
  prompt_tokens: number | null;
  cached_tokens: number | null;
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  finish_reason: string | null;
  cost: number | null;
  list_cost: number | null;
  provider: string | null;
  response_model: string | null;
  ms: number | null;
  tokens_per_s: number | null;
  front_protocol: string | null;
  protocol_mode: string | null;
  serving_tier: string | null;
  translation_dropped: Record<string, number>;
  status: number | null;
}

export interface EvaluateRunIn {
  eval_suite?: string;
  force_rebuild?: boolean;
}

/** SSE `run:<id>` payloads (`contracts/events.py`). */
export type RunEvent =
  | {
      type: "status";
      run_id: string;
      status: RunStatus;
      phase: string;
      outcome: string;
      usable: boolean | null;
      attempt: number;
    }
  | {
      type: "step";
      run_id: string;
      step: string;
      status: "started" | "ok" | "failed" | "skipped";
      seconds?: number | null;
      detail?: string | null;
    }
  | {
      type: "usage";
      run_id: string;
      attempt: number;
      requests_by_class: Record<string, number>;
      prompt_tokens: number;
      completion_tokens: number;
      reasoning_tokens: number;
      cached_tokens: number;
      cost_usd: number;
      list_cost_usd: number;
      inflight: number;
      budget_exhausted: string | null;
    }
  | {
      type: "trajectory_step";
      run_id: string;
      attempt: number;
      n_steps: number;
      step: TrajectoryStepOut | null;
    }
  | {
      type: "log";
      run_id: string;
      stream: "stdout" | "stderr" | "system";
      level: string;
      line: string;
    }
  | {
      type: "evaluation";
      run_id: string;
      evaluation_id: number | null;
      status: RunEvaluationStatus;
      stage: string | null;
      agent_pass_rate: number | null;
    };

export const RUN_EVENT_TYPES = [
  "status",
  "step",
  "usage",
  "trajectory_step",
  "log",
  "evaluation",
] as const;

// ── 9.6 Slots ──────────────────────────────────────────────────────────────

export type SlotKind = "file" | "region" | "symbol" | "endpoint" | "component";
export const SLOT_KINDS: SlotKind[] = [
  "file",
  "region",
  "symbol",
  "endpoint",
  "component",
];
export type HoleMode = "stub" | "remove" | "rewrite";
export const HOLE_MODES: HoleMode[] = ["stub", "remove", "rewrite"];
export type EditPolicy = "revert" | "fail";
export type ContextMode =
  | "slot_file_only"
  | "related_files"
  | "full_app"
  | "interface_only";
export const CONTEXT_MODES: ContextMode[] = [
  "slot_file_only",
  "related_files",
  "full_app",
  "interface_only",
];

/** 1-based inclusive lines; byte offsets into the UTF-8 file. */
export interface Span {
  start_line: number;
  end_line: number;
  start_byte: number;
  end_byte: number;
}

/** A selection sent to the server: lines are required, bytes are checked when present. */
export interface SpanIn {
  start_line: number;
  end_line: number;
  start_byte?: number | null;
  end_byte?: number | null;
}

export type SymbolKind =
  | "function"
  | "method"
  | "class"
  | "component"
  | "route_handler"
  | "module"
  | "block";

export interface SymbolEntry {
  id: string;
  path: string;
  language: string;
  kind: SymbolKind;
  qualname: string;
  span: Span;
  signature: string;
  decorators: string[];
  content_sha: string;
  parent: string | null;
}

export interface EndpointEntry {
  method: string;
  path: string;
  framework: string;
  /** symbol id of the handler */
  handler: string;
  spec_endpoint_id: string | null;
  confidence: "exact" | "pattern" | "heuristic";
  linked_checks: number;
  linked_probes: number;
}

export interface ComponentEntry {
  name: string;
  path: string;
  framework: "react" | "vue" | "svelte";
  symbol: string | null;
  whole_file: boolean;
}

export interface EndpointAddress {
  method: string;
  path: string;
  spec_endpoint_id?: string | null;
}

export interface Anchors {
  before_sha: string;
  after_sha: string;
}

export interface SlotAddress {
  kind: SlotKind;
  path: string;
  span: Span | null;
  symbol_id: string | null;
  endpoint: EndpointAddress | null;
  content_sha: string;
  anchors: Anchors;
}

export interface ContextPolicy {
  mode: ContextMode;
  max_context_tokens: number;
  include_spec: "none" | "endpoint" | "full";
  include_tests: boolean;
  related: {
    imports_depth: number;
    include_models: boolean;
    include_routes_file: boolean;
  };
}

export const DEFAULT_CONTEXT_POLICY: ContextPolicy = {
  mode: "related_files",
  max_context_tokens: 24000,
  include_spec: "endpoint",
  include_tests: false,
  related: {
    imports_depth: 1,
    include_models: true,
    include_routes_file: true,
  },
};

export interface ResolvedContextOut {
  files: { path: string; sha: string; tokens: number }[];
  sha: string;
  tokens: number;
  truncated_paths: string[];
}

export interface LeakReportOut {
  score: number;
  matches: { path: string; shingles: number; verbatim: boolean }[];
  scope: ("context" | "workspace")[];
  version: number;
}

export type TargetValidationStatus =
  | "pending"
  | "ok"
  | "uninformative"
  | "error";

export interface TargetValidationOut {
  status: TargetValidationStatus;
  original_pass: Record<string, boolean[]>;
  holed_fail: Record<string, boolean>;
  flaky_checks: string[];
  dropped_checks: string[];
  discriminating_checks: string[];
  probe_stability: Record<string, string[]>;
  runs: string[];
  at: string | null;
  error?: string | null;
}

export interface SlotInterfaceOut {
  signature: string;
  decorators: string[];
  imports_in_scope: string[];
  exported_names: string[];
}

/** Body of `preview-target` / `targets` (`slot/types.py:SlotTargetIn`); omitted fields take kind defaults. */
export interface SlotTargetIn {
  kind: SlotKind;
  path?: string | null;
  span?: SpanIn | null;
  symbol_id?: string | null;
  endpoint?: EndpointAddress | null;
  component?: string | null;
  content_sha?: string | null;
  anchors?: Anchors | null;
  default_hole_mode?: HoleMode | null;
  default_keep_docstring?: boolean | null;
  default_context_policy?: ContextPolicy | null;
  edit_policy?: EditPolicy | null;
  allowlist?: string[] | null;
  linked_checks?: string[] | null;
  linked_probes?: string[] | null;
  /** `LLMModel.model_id` for the cost hint. */
  cost_model?: string | null;
}

export interface SlotTargetCreateIn extends SlotTargetIn {
  label?: string;
}

export interface SlotCostHint {
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  /** `null` when the model has no price. */
  per_variant_usd: number | null;
  per_30_usd: number | null;
}

export interface SlotTargetPreviewOut {
  address: SlotAddress;
  kind: SlotKind;
  language: string;
  match_key: string;
  default_hole_mode: HoleMode;
  default_keep_docstring: boolean;
  default_context_policy: ContextPolicy;
  edit_policy: EditPolicy;
  allowlist: string[];
  /** Names of the fields the server filled with kind defaults. */
  defaults: string[];
  interface: SlotInterfaceOut;
  original_text: string;
  hole_applied_text: string;
  hole_removes_file: boolean;
  resolved_context: ResolvedContextOut;
  linked_endpoints: string[];
  linked_checks: string[];
  linked_checks_source: "auto" | "edited";
  linked_checks_diff: { added: string[]; removed: string[] };
  linked_probes: string[];
  leak: LeakReportOut;
  target_hash: string;
  cost_hint: SlotCostHint;
  issues: Issue[];
}

export interface SlotTargetOut {
  id: string;
  slot_set: string;
  label: string;
  kind: SlotKind;
  address: SlotAddress;
  match_key: string;
  language: string;
  original_sha: string;
  default_hole_mode: HoleMode;
  default_keep_docstring: boolean;
  default_context_policy: ContextPolicy;
  edit_policy: EditPolicy;
  allowlist: string[];
  interface: SlotInterfaceOut;
  linked_endpoints: string[];
  linked_checks: string[];
  linked_checks_source: "auto" | "edited";
  linked_checks_diff: { added: string[]; removed: string[] };
  linked_probes: string[];
  validation: TargetValidationOut;
  leak: LeakReportOut;
  target_hash: string;
  original_run: string | null;
  usable: boolean;
  unusable_reason: string | null;
  issues: Issue[];
  created_at: string;
}

export interface SlotTargetDetailOut extends SlotTargetOut {
  original_text: string | null;
  original: {
    run_id: string;
    evaluation: number | null;
    slot_pass_rate: number | null;
  } | null;
  variants_summary: {
    n: number;
    by_outcome: Record<string, number>;
    by_parse_status: Record<string, number>;
  };
}

export interface PoolQuery {
  task: string;
  stack?: string | null;
  models?: string[] | null;
  harnesses?: string[] | null;
  studies?: string[] | null;
  min_pass_rate?: number | null;
}

export interface ParentSelection {
  rule: "random" | "all" | "manual";
  pool_query: PoolQuery;
  k: number;
  stratify_by: "none" | "parent_model";
  seed?: number | null;
}

/** `SlotSet.selection` as stored. */
export interface SlotSetSelectionOut {
  rule?: "random" | "all" | "manual";
  pool_query?: PoolQuery | null;
  pool_size?: number | null;
  seed?: number | null;
  picked_by?: string | null;
  at?: string | null;
}

export interface SlotSetOut {
  id: string;
  name: string;
  parent_run: string;
  task: string | null;
  parent_model: string;
  parent_harness: string;
  frozen_tree_hash: string;
  parent_manifest_hash: string;
  index_stats: {
    files: number;
    symbols: number;
    endpoints: number;
    components: number;
    unparsed: string[];
  };
  parent_eval: {
    id: number;
    status: string;
    eval_suite: string;
    agent_pass_rate: number | null;
  } | null;
  selection: SlotSetSelectionOut;
  /** Levels fixed by the parent run (read from its manifest columns; WP-12 `slot.api.parent_levels`). */
  parent_levels: {
    stack_profile: string | null;
    starter_level: "bare" | "minimal" | "full" | null;
    auth_mode: string | null;
    prose_variant: string | null;
  };
  notes: string;
  created_at: string;
}

export interface SlotSetDetailOut extends SlotSetOut {
  targets: SlotTargetOut[];
}

export interface SlotSetIn {
  parent_run: string;
  name?: string;
  allow_unbuilt_parent?: boolean;
  selection?: ParentSelection | null;
  pool_size?: number | null;
}

export interface SlotTreeEntry {
  path: string;
  language: string;
  loc: number | null;
  origin: FileOriginValue | string;
  is_test: boolean;
  excluded: string;
  parsed: boolean;
  symbols: number;
}

export interface SlotFileOut {
  path: string;
  text: string;
  language: string;
  symbols: SymbolEntry[];
}

export interface SlotParentRow {
  run_id: string;
  task: string | null;
  stack: string | null;
  model: string | null;
  harness: string | null;
  outcome: string;
  agent_pass_rate: number | null;
  probes_exploited: number | null;
  cost_usd: number | null;
  frozen: boolean;
  slot_sets: number;
  created_at: string;
}

export interface SlotParentsQuery {
  task?: string;
  stack?: string;
  model?: string;
  harness?: string;
  outcome?: "tested" | "build_fail" | "start_fail";
  study?: string;
  page?: number;
  page_size?: number;
}

export interface ParentDrawOut {
  parents: string[];
  seed: number;
  pool_size: number;
}

export interface SlotMatchOut {
  created: SlotTargetOut[];
  unmatched: { slot_set: string; reason: string }[];
}

export interface SlotRenderIn {
  prompt_set: string;
  executor?: "single_shot" | "restricted_agent";
  hole_mode?: HoleMode | null;
  keep_docstring?: boolean | null;
  context_policy?: ContextPolicy | null;
  guidance?: string | null;
  style?: string | null;
}

export interface SlotRenderOut {
  stimuli: StimulusOut[];
  prompt_hash: string;
  est_tokens: number;
}

export interface SlotVariantRow {
  run_id: string;
  kind: string;
  condition: number | null;
  repeat_index: number | null;
  outcome: string;
  outcome_reason: string;
  parse_status: string | null;
  normalized_sha: string | null;
  cluster_id: number | null;
  /** `"<feature>:<scope>"` → present | absent | error | not_applicable */
  features: Record<string, string>;
  slot_pass_rate: number | null;
  diff_vs_original: {
    lines_added: number;
    lines_removed: number;
    similarity: number;
    ast_edit_distance?: number | null;
    normalized_equal?: boolean | null;
  } | null;
  edit_violation: boolean | null;
}

export interface SlotVariantsQuery {
  study?: string;
  condition?: number;
  cluster?: number;
  feature?: string;
  page?: number;
  page_size?: number;
}

export interface SlotVariantDiffOut {
  unified: string;
  replacement: string;
  original: string;
  path: string;
}

/** `POST /studies/adhoc/` (9.4) as the "Generate a parent app" flow uses it. */
export interface AdhocStudyIn {
  design: Record<string, unknown>;
  launch?: boolean;
  name?: string | null;
  confirm_usd?: number | null;
}

export interface AdhocStudyOut {
  study_id: string;
  plan_hash: string;
  run_ids: string[];
  status: string;
  issues: Issue[];
  estimate: Record<string, unknown> | null;
}

// ── 9.7 Patterns ───────────────────────────────────────────────────────────

export type PatternStatus = "present" | "absent" | "error" | "not_applicable";

export interface PatternRunOut {
  id: string;
  study: string;
  detectors: string[];
  detector_set_hash: string;
  normalizer_version: number;
  cluster_params: Record<string, unknown>;
  scope: Record<string, unknown>;
  is_live: boolean;
  label: "planned" | "exploratory" | string;
  status: string;
  /** Light summary (the heavy keys are served by the per-table endpoints). */
  summary: Record<string, unknown>;
  post_run: number | null;
  completed_at: string | null;
  created_at: string | null;
}

export interface PatternRunIn {
  study: string;
  detectors?: string[] | "default";
  cluster_params?: Record<string, unknown> | null;
  scope?: Record<string, unknown> | null;
}

export interface PrevalenceRow {
  condition: number;
  condition_label?: string;
  /** a target uuid, or `"all"` for the pooled row */
  target: string;
  feature: string;
  scope: string;
  column?: string;
  detector?: string | null;
  polarity?: string;
  pooled?: boolean;
  conditional_on?: string;
  k: number;
  n_measured: number;
  /** `null` for provisional live rows */
  n_itt: number | null;
  errors: number;
  na: number;
  missing?: number;
  p: number | null;
  ci_low: number | null;
  ci_high: number | null;
  cp_low?: number | null;
  cp_high?: number | null;
  method: string;
  adaptive_n: boolean;
  looks?: number | null;
  attrition?: number | null;
  bounds?: { low: number; high: number } | null;
  low_effective_n?: boolean;
  correctness?: { k: number; n_measured: number; p: number | null } | null;
  provisional?: boolean;
  audit: { precision: number | null; npv: number | null } | null;
  audit_status?: string;
}

export interface ComparisonRow {
  feature: string;
  scope?: string;
  column?: string;
  a: number;
  b: number;
  a_label?: string;
  b_label?: string;
  diff: number | null;
  ci_low: number | null;
  ci_high: number | null;
  method: "paired" | "independent";
  test?: string;
  n_pairs?: number;
  k_a?: number;
  n_a?: number;
  k_b?: number;
  n_b?: number;
  /** planned rows only; withheld while interim */
  p: number | null;
  /** exploratory rows only (BH); withheld while interim */
  q: number | null;
  /** planned rows only: p adjusted over the planned family under the plan's `correction`; withheld while interim */
  p_adjusted?: number | null;
  /** the plan's correction for planned rows (`holm` | `bh` | `none`); `bh` for exploratory rows */
  correction?: string | null;
  /** the plan's alpha for planned rows; null for exploratory rows */
  alpha?: number | null;
  /** planned rows only: `p_adjusted < alpha`; null for exploratory rows and while interim */
  significant?: boolean | null;
  family_size: number;
  planned: boolean;
  interim?: boolean;
}

/** One pooled-across-targets row (`patterns_run_pooled`): the raw permutation p replaces `p`. */
export interface PooledRow {
  feature?: string;
  scope?: string;
  column?: string;
  a?: number;
  b?: number;
  n_targets?: number;
  planned?: boolean;
  method?: "mh_stratified" | "per_target";
  pooled?: {
    rd?: number | null;
    ci?: [number, number] | null;
    se?: number | null;
  } | null;
  permutation?: { method: string; arrangements: number | null } | null;
  /** planned rows only: the raw stratified permutation p; withheld while interim */
  permutation_p?: number | null;
  /** the raw p before the pass that publishes `permutation_p` (not normally present) */
  p_value?: number | null;
  p_adjusted?: number | null;
  q?: number | null;
  correction?: string | null;
  alpha?: number | null;
  significant?: boolean | null;
  family_size?: number;
  interim?: boolean;
  notes?: string[];
}

export interface PooledOut {
  method: "mh_stratified" | "per_target";
  rows: Record<string, unknown>[];
  notes: string[];
}

export interface ClusterLabelOut {
  id: number;
  slot_target: string;
  normalized_sha: string;
  detector: string;
  feature_key: string;
  value: "present" | "absent" | "unsure";
  note: string;
  labeler: string;
  created_at: string | null;
}

export interface ClusterRow {
  cluster_id: number;
  target: string;
  method: string;
  key: string;
  size: number;
  share: number | null;
  representative: string;
  includes_original: boolean;
  labels: Partial<ClusterLabelOut>[];
  members_sample: string[];
}

export interface ClusterStats {
  condition?: number;
  target?: string;
  n: number;
  unparsed?: number;
  distinct: number;
  exact_dup_share: number | null;
  top1_share: number | null;
  entropy: number | null;
  chao1: number | null;
  low_effective_n: boolean;
}

export interface ClustersOut {
  clusters: ClusterRow[];
  /** one row when both target and condition are given, else a list */
  stats: ClusterStats | ClusterStats[];
}

export interface DetectorFromSelectionIn {
  run_id: string;
  path: string;
  span: { start_line: number; end_line: number };
  engine: "regex" | "ts_query";
  feature_key: string;
  polarity?: DetectorPolarity;
}

export interface ClusterLabelIn {
  slot_target: string;
  normalized_sha: string;
  detector: string;
  value: "present" | "absent" | "unsure";
  note?: string;
}

export interface DetectorAuditOut {
  id: number;
  pattern_run: string;
  feature_key: string;
  status: "pending" | "in_progress" | "complete" | string;
  sample: { hits: string[]; non_hits: string[]; seed: number };
  eval_task: string | null;
  precision: number | null;
  npv: number | null;
  summary: Record<string, unknown>;
  created_at: string | null;
}

export interface DetectorAuditIn {
  feature_key: string;
  n_hits?: number;
  n_non_hits?: number;
  scope?: "span" | "file";
}

/** The light `PatternRunOut.summary` (heavy tables are served by their own endpoints). */
export interface PatternSummary {
  version?: number;
  label?: string;
  interim?: boolean;
  error?: string;
  normalizer_version?: number;
  cluster_params?: Record<string, unknown>;
  scope?: Record<string, unknown>;
  detector_set_hash?: string;
  detectors?: {
    ref: string;
    engine: string;
    feature_key: string;
    tool: string | null;
  }[];
  tool_versions?: Record<string, string>;
  planned_features?: string[];
  n_planned?: number;
  n_with_output?: number;
  n_unparsed?: number;
  conditions?: Record<
    string,
    {
      index: number;
      label: string;
      levels?: Record<string, unknown>;
      seed_forwarded?: boolean;
    }
  >;
  targets?: Record<
    string,
    {
      label: string;
      kind: string;
      language: string;
      path: string;
      match_key: string;
      slot_set: string;
      parent_run: string;
      parent_model: string;
      parent_harness: string;
      original_normalized_sha: string;
      original_parse_reason: string;
      informative: boolean;
      leak_score: number | null;
    }
  >;
  features?: {
    key: string;
    feature: string;
    scope: string;
    kind: string;
    detector: string | null;
    polarity: string;
  }[];
  issues?: Issue[];
}
// --- end WP-16 ---

// --- WP-17 ---
// --- end WP-17 ---
