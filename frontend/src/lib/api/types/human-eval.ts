// Wire types for the human-evaluation module. Mirrors backend/human_eval
// (services/design.py for the design/settings schema, api/schemas_*.py for
// payloads). Researcher routes live under /api/human-eval/, participant
// (portal) routes under /api/human-eval/portal/. Field names are the JSON
// names. Datetimes are ISO 8601 strings.

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export type TaskMode = "single" | "pairwise";
export type TaskStatus = "draft" | "open" | "paused" | "closed" | "archived";
export type TaskAction = "open" | "pause" | "close" | "reopen" | "archive";

export type ParticipantKind = "invite" | "code" | "account" | "preview";
export type ParticipantStatus =
  | "invited"
  | "active"
  | "completed"
  | "withdrawn";

export type AssignmentStatus =
  | "assigned"
  | "in_progress"
  | "submitted"
  | "skipped"
  | "expired";

/** app_broken: the app itself fails (model outcome); platform_unavailable: hosting/queue problem (platform). */
export type SkipReason =
  | "app_broken"
  | "platform_unavailable"
  | "cannot_evaluate"
  | "conflict_of_interest"
  | "other";

export type ObservationKind = "bug" | "vulnerability" | "note";
/** The tester's own severity judgement (never a tool's). */
export type TesterSeverity = "critical" | "high" | "medium" | "low" | "info";
export type FindingVerdictValue = "tp" | "fp" | "unsure";

export type HostingState =
  | "idle" // no container, or stopped
  | "queued" // waiting for a free app slot or build slot
  | "building"
  | "starting"
  | "running"
  | "stopping"
  | "failed"
  | "unavailable"; // apps cannot be reached by participants in this routing mode

// ---------------------------------------------------------------------------
// Design (task.design) — validated by backend/human_eval/services/design.py
// ---------------------------------------------------------------------------

/** Integer rating scale min..max (max - min between 1 and 100), values min, min+step, … max. */
export interface Scale {
  min: number;
  max: number;
  /** default 1; must divide max − min (NASA-TLX uses 5) */
  step?: number;
  min_label?: string;
  max_label?: string;
  /** Optional per-value labels, keys are the stringified integers. */
  labels?: Record<string, string>;
}

export interface Criterion {
  /** ^[a-z0-9_]{1,40}$, unique within its block */
  id: string;
  label: string;
  help?: string;
  scale: Scale;
  required: boolean;
  /** Offer "can't judge" (stored as "na"). */
  allow_na: boolean;
}

export type QuestionKind =
  | "text"
  | "textarea"
  | "single"
  | "multi"
  | "scale"
  | "number"
  | "attention";

export interface QuestionOption {
  value: string;
  label: string;
}

export interface Question {
  id: string;
  kind: QuestionKind;
  label: string;
  help?: string;
  required: boolean;
  /** single, multi, attention */
  options?: QuestionOption[];
  /** scale */
  scale?: Scale;
  /** number */
  min?: number | null;
  max?: number | null;
  /** attention: the option value a careful participant picks. Never sent to the portal. */
  expected?: string;
}

export type CommentMode = "off" | "optional" | "required";

export interface RatingBlock {
  id: string;
  type: "rating";
  title: string;
  instructions?: string;
  criteria: Criterion[];
  comment: CommentMode;
}

/**
 * Spec sections a requirements block turns into checklist items: `frontend` (the task's UI
 * requirements), `api` (non-admin endpoints) and `admin_api` (endpoints with `auth == "admin"`).
 */
export type RequirementSource = "frontend" | "api" | "admin_api";
/** blocked: an app defect prevents testing (scores 0); cannot_test: the tester could not test it (excluded). */
export type RequirementVerdict =
  | "met"
  | "partial"
  | "not_met"
  | "blocked"
  | "cannot_test";

export interface CustomRequirement {
  id: string;
  text: string;
}

export interface RequirementsBlock {
  id: string;
  type: "requirements";
  title: string;
  instructions?: string;
  /** Spec sections turned into checklist items (from the run's pinned task version). */
  sources: RequirementSource[];
  /** Extra items added by the researcher, identical for every app. */
  custom: CustomRequirement[];
  /** "required_unless_met": a note is required for partial / not_met / cannot_test. */
  note: "off" | "optional" | "required_unless_met";
}

export interface SecurityChecklistItem {
  id: string;
  label: string;
  help?: string;
  /** e.g. "A01:2021 Broken Access Control" */
  category?: string;
}

export type ChecklistOutcome = "vulnerable" | "not_vulnerable" | "not_tested";

export interface FindingReviewConfig {
  enabled: boolean;
  /** 1..50 */
  max_findings: number;
  /** Finding.category values to sample from, e.g. ["security", "secrets"]. Empty = all. */
  categories: string[];
  include_ai: boolean;
  include_dependency: boolean;
  include_dynamic: boolean;
}

export interface SecurityBlock {
  id: string;
  type: "security";
  title: string;
  instructions?: string;
  checklist: SecurityChecklistItem[];
  /** Allow vulnerability reports (Observation kind "vulnerability"). */
  reports: boolean;
  finding_review: FindingReviewConfig;
  http_console: boolean;
}

export interface Scenario {
  id: string;
  title: string;
  /** Markdown */
  description: string;
  success_criteria?: string;
}

export type ScenarioOutcome =
  | "success"
  | "partial"
  | "failure"
  | "not_attempted";

export interface ScenariosBlock {
  id: string;
  type: "scenarios";
  title: string;
  instructions?: string;
  scenarios: Scenario[];
  /** Show start/stop timers and record seconds. */
  timed: boolean;
  /** Ask the Single Ease Question (1..7) after each scenario. */
  ease_question: boolean;
}

export type StandardInstrument = "sus" | "umux_lite" | "nasa_tlx";

export interface QuestionnaireBlock {
  id: string;
  type: "questionnaire";
  title: string;
  instructions?: string;
  instrument: StandardInstrument | "custom";
  /** custom only; standard instruments use fixed questions from the server. */
  questions: Question[];
}

export interface BugsBlock {
  id: string;
  type: "bugs";
  title: string;
  instructions?: string;
  /** Minimum bug reports; 0 means the participant may instead confirm "none found". */
  min_reports: number;
}

export type PairwiseScale = "ab_tie" | "five_point";

export interface PairwiseCriterion {
  id: string;
  label: string;
  help?: string;
}

export interface PairwiseBlock {
  id: string;
  type: "pairwise";
  title: string;
  instructions?: string;
  criteria: PairwiseCriterion[];
  scale: PairwiseScale;
  /** Ask an overall preference in addition to per-criterion ones. */
  overall: boolean;
  rationale: CommentMode;
}

export type Block =
  | RatingBlock
  | RequirementsBlock
  | SecurityBlock
  | ScenariosBlock
  | QuestionnaireBlock
  | BugsBlock
  | PairwiseBlock;
export type BlockType = Block["type"];

/** Blocks allowed per mode (backend enforces the same). */
export const SINGLE_BLOCK_TYPES: BlockType[] = [
  "rating",
  "requirements",
  "security",
  "scenarios",
  "questionnaire",
  "bugs",
];
export const PAIRWISE_BLOCK_TYPES: BlockType[] = ["pairwise", "bugs"];

export interface ConsentConfig {
  enabled: boolean;
  /** Markdown */
  text: string;
}

/**
 * Pre-registered analysis plan, frozen with the design at first open.
 * metric per block type: rating → "composite" | criterion id; requirements → "compliance";
 * security → "vulnerable_share"; scenarios → "success_rate"; questionnaire → "score";
 * bugs → "bugs_per_rater"; pairwise → "overall" | criterion id.
 */
export interface AnalysisPlan {
  primary: { block_id: string; metric: string } | null;
  /** Planned sample: balanced → ratings per item; all → participants. */
  planned_ratings_per_item: number | null;
  planned_participants: number | null;
  /** Rule-based exclusions applied in results (computed, never stored on rows). */
  exclusion_rules: {
    speeding: boolean;
    attention_failed: boolean;
    straightlining: boolean;
    app_not_viewed: boolean;
  };
  /** exclude: complete-case; floor: unevaluable apps count at the scale minimum / compliance 0. */
  unevaluable_policy: "exclude" | "floor";
}

export interface TaskDesign {
  version: 1;
  blocks: Block[];
  analysis: AnalysisPlan;
  consent: ConsentConfig;
  /** Asked once per participant before the first item (e.g. experience). */
  pre_questions: Question[];
  /** Asked once when the participant finishes. */
  post_questions: Question[];
}

// ---------------------------------------------------------------------------
// Settings (task.settings)
// ---------------------------------------------------------------------------

/** Participants never see model / condition / run ids (always; not configurable). */
export interface BlindingSettings {
  show_spec: boolean;
  show_source_code: boolean;
  /**
   * Show the app's admin login. It is the run's real runtime credential (there is no default
   * password); when the app has none the portal says "admin login not available".
   */
  show_admin_credentials: boolean;
}

export type AllocationStrategy = "balanced" | "all";

export interface AllocationSettings {
  strategy: AllocationStrategy;
  /** balanced: target submitted ratings per item (1..50). */
  ratings_per_item: number;
  /** balanced: hand out items beyond the target once every item reached it. */
  allow_overflow: boolean;
  /** null = unlimited */
  max_items_per_participant: number | null;
  /** An assigned / in-progress assignment with no activity for this long expires (5..1440). */
  assignment_timeout_minutes: number;
  allow_skip: boolean;
}

export type PairingStrategy = "all_pairs" | "random_k" | "vs_baseline";

export interface PairingSettings {
  strategy: PairingStrategy;
  /** random_k: target pairs per app (1..20). */
  k: number;
  /** Only pair apps from different conditions (models when there is no study). */
  cross_condition_only: boolean;
  /** vs_baseline: StudyCondition id; default = the study's baseline condition. */
  baseline_condition_id: number | null;
}

export interface AccessSettings {
  allow_invite_links: boolean;
  allow_access_codes: boolean;
  allow_accounts: boolean;
}

export type HostingIsolation = "shared" | "exclusive";

export interface HostingSettings {
  /** Start an app when a participant opens it. */
  on_demand: boolean;
  /** Stop task-started, unpinned apps idle this long (5..240). */
  idle_stop_minutes: number;
  /** Per task; also capped by the server-wide HUMAN_EVAL_MAX_RUNNING_APPS. */
  max_running_apps: number;
  /** exclusive: an app is in at most one open assignment at a time. Frozen at open. */
  isolation: HostingIsolation;
  /**
   * per_assignment (requires exclusive): recreate the app container from its image before each
   * new assignment gets it, so raters never inherit earlier raters' data. Frozen at open.
   */
  reset: "none" | "per_assignment";
}

export interface QualitySettings {
  /** Submissions faster than this are flagged as speeding (0 = off). */
  min_seconds_per_item: number;
}

export interface TaskSettings {
  blinding: BlindingSettings;
  allocation: AllocationSettings;
  pairing: PairingSettings;
  access: AccessSettings;
  hosting: HostingSettings;
  quality: QualitySettings;
}

// ---------------------------------------------------------------------------
// Answers (Response.answers) per block type
// ---------------------------------------------------------------------------

export interface RatingAnswers {
  /** criterion id → integer on the scale, or "na" */
  criteria: Record<string, number | "na">;
  comment?: string;
}

export interface RequirementsAnswers {
  items: Record<string, { verdict: RequirementVerdict; note?: string }>;
}

export interface SecurityAnswers {
  checklist: Record<string, { outcome: ChecklistOutcome; note?: string }>;
}

export interface ScenariosAnswers {
  scenarios: Record<
    string,
    {
      outcome: ScenarioOutcome;
      seconds?: number | null;
      ease?: number | null;
      note?: string;
    }
  >;
}

export type QuestionAnswer = string | number | string[] | null;

export interface QuestionnaireAnswers {
  answers: Record<string, QuestionAnswer>;
}

export interface BugsAnswers {
  /** slot ("single" | "A" | "B") → the participant confirms finding no bugs in that app */
  none_found: Record<string, boolean>;
}

/** ab_tie: "A" | "B" | "tie". five_point: -2 (A much better) .. 2 (B much better). As displayed. */
export type PairwiseValue = "A" | "B" | "tie" | number;

export interface PairwiseAnswers {
  criteria: Record<string, PairwiseValue>;
  overall?: PairwiseValue;
  rationale?: string;
}

export type BlockAnswers =
  | RatingAnswers
  | RequirementsAnswers
  | SecurityAnswers
  | ScenariosAnswers
  | QuestionnaireAnswers
  | BugsAnswers
  | PairwiseAnswers;

// ---------------------------------------------------------------------------
// Shared payloads
// ---------------------------------------------------------------------------

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

/** Structured 400/409 body: {detail, code, errors[]} */
export interface HumanEvalError {
  detail: string;
  code: string;
  errors: { path: string; message: string }[];
}

export interface HostingInfo {
  state: HostingState;
  /** Public app URL when running and reachable by participants. */
  url: string | null;
  /** 0..100 while building/starting, else null. */
  progress: number | null;
  /** Human-readable status line, e.g. "Waiting for a free app slot (3 of 3 in use)". */
  message: string;
  error: string;
}

export interface ResearcherHostingInfo extends HostingInfo {
  container_id: string | null;
  container_name: string | null;
  container_status: string | null;
  started_by_task: boolean;
  pinned: boolean;
  last_activity_at: string | null;
}

export interface ObservationAttachment {
  id: string;
  content_type: string;
  size: number;
  original_name: string;
  /** API path that serves the file (portal or researcher variant). */
  url: string;
  created_at: string;
}

export interface Observation {
  id: string;
  assignment_id: string;
  /** "A" | "B" | "single" as the participant saw it */
  app_slot: string;
  block_id: string;
  kind: ObservationKind;
  title: string;
  description: string;
  steps: string;
  location: string;
  category: string;
  severity: TesterSeverity | "";
  cwe: string;
  checklist_item_id: string;
  attachments: ObservationAttachment[];
  created_at: string;
  updated_at: string;
}

export interface ObservationInput {
  slot: "A" | "B" | "single";
  block_id?: string;
  kind: ObservationKind;
  title: string;
  description?: string;
  steps?: string;
  location?: string;
  category?: string;
  severity?: TesterSeverity | "";
  cwe?: string;
  checklist_item_id?: string;
}

// ---------------------------------------------------------------------------
// Researcher API (/api/human-eval/)
// ---------------------------------------------------------------------------

export interface Preset {
  slug: string;
  name: string;
  description: string;
  mode: TaskMode;
  design: TaskDesign;
  settings: TaskSettings;
}

export interface HumanEvalEnvironment {
  /** False in path-only routing: participants cannot open apps. */
  apps_reachable: boolean;
  /** e.g. "apps_not_reachable", "shared_client_ip" (TRUSTED_PROXY_CIDRS empty: throttles are global) */
  warnings: string[];
  routing_mode: "subdomain" | "traefik" | "path" | "direct";
  global_max_running_apps: number;
  global_running_apps: number;
  max_concurrent_builds: number;
  default_settings: TaskSettings;
  default_design: TaskDesign;
  /** Server-defined checklists and instruments, for editors. */
  security_checklist_default: SecurityChecklistItem[];
  instruments: Record<
    StandardInstrument,
    { name: string; questions: Question[] }
  >;
}

export interface TaskCounts {
  apps: number;
  active_apps: number;
  items: number;
  participants: number;
  participants_active: number;
  participants_completed: number;
  assignments_submitted: number;
  assignments_in_progress: number;
  assignments_skipped: number;
  /** balanced: items × ratings_per_item; all: items × participants; null if unknown. */
  target_ratings: number | null;
}

export interface TaskListItem {
  id: string;
  title: string;
  status: TaskStatus;
  mode: TaskMode;
  counts: TaskCounts;
  study: StudyRef | null;
  opens_at: string | null;
  closes_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskDetail extends TaskListItem {
  description: string;
  instructions: string;
  design: TaskDesign;
  settings: TaskSettings;
  seed: number;
  design_hash: string;
  frozen_at: string | null;
  opened_at: string | null;
  closed_at: string | null;
  /** Which status actions are currently allowed. */
  allowed_actions: TaskAction[];
  /** Blocking problems for opening (e.g. no apps, invalid design). */
  open_blockers: string[];
  /** Non-blocking validity warnings (shared app state, possible model leaks in shown code, …). */
  open_warnings: string[];
  first_closed_at: string | null;
  protocol_log: {
    at: string;
    by: string;
    field: string;
    old_sha256: string;
    new_sha256: string;
  }[];
  owner: { email: string; name: string } | null;
}

export interface TaskCreateInput {
  title: string;
  description?: string;
  instructions?: string;
  mode: TaskMode;
  /** Start from a preset's design/settings (explicit design/settings win). */
  preset?: string;
  design?: TaskDesign;
  settings?: TaskSettings;
  study_id?: string | null;
  /** GenerationRun ids of the initial app pool. */
  run_ids?: string[];
  opens_at?: string | null;
  closes_at?: string | null;
}

export type TaskUpdateInput = Partial<
  Omit<TaskCreateInput, "preset" | "run_ids">
>;

export interface DesignValidation {
  ok: boolean;
  errors: { path: string; message: string }[];
  /** The normalized design/settings (defaults filled in). */
  design: TaskDesign | null;
  settings: TaskSettings | null;
}

export interface StudyRef {
  id: string;
  name: string;
}

/**
 * Evaluation status of an app as the human-eval module shows it: `tested` (a measured EvalSuite
 * run), the evaluation status (`pending`, `running`, `not_measured`, `error`), or the run outcome
 * when the app was never deployed or evaluated (`build_fail`, `start_fail`, `infra_error`, ...).
 */
export type FunctionalStatus =
  | "tested"
  | "pending"
  | "running"
  | "not_measured"
  | "error"
  | "build_fail"
  | "start_fail"
  | "infra_error"
  | (string & {});

export interface FunctionalSummary {
  /** null: no evaluation and no outcome recorded. */
  status: FunctionalStatus | null;
  /** Set only for a measured evaluation; null is "not measured", never 0. */
  pass_rate: number | null;
  /** true / false / null (unknown) */
  deployed: boolean | null;
  source: "eval_suite" | null;
}

/** A completed app run (`app_agentic` or slot variant) that can join a task's pool. */
export interface CandidateRun {
  /** GenerationRun id. */
  id: string;
  model_name: string;
  model_id: string;
  /** Task title and slug. */
  app_name: string;
  app_slug: string;
  study: StudyRef | null;
  condition: { id: number; label: string } | null;
  repeat_index: number | null;
  /** Run kind (`app_agentic`, `slot_*`). */
  kind: string;
  /** Stack profile ref, when known. */
  stack: string | null;
  created_at: string;
  functional: FunctionalSummary;
  /** A RUNNING llm-* container exists for the run. */
  running: boolean;
}

export interface CandidateRunQuery {
  study_id?: string;
  app_slug?: string;
  model_id?: string;
  search?: string;
  /** only the current run per study cell (default true) */
  current_only?: boolean;
  page?: number;
  per_page?: number;
}

export interface TaskApp {
  id: string;
  label: string;
  /** GenerationRun id (null once the run was deleted). */
  run_id: string | null;
  group_key: string;
  position: number;
  is_active: boolean;
  /** De-blinded (researcher only). */
  model_name: string;
  model_id: string;
  condition_label: string;
  app_name: string;
  app_slug: string;
  repeat_index: number | null;
  functional: FunctionalSummary;
  hosting: ResearcherHostingInfo;
  stats: {
    submitted: number;
    in_progress: number;
    skipped: number;
    unavailable_skips: number;
  };
}

export interface TaskAppsAddInput {
  run_ids?: string[];
  study_id?: string;
  /** with study_id: only the current run per main-stage cell (default true) */
  current_only?: boolean;
}

export type AppHostingAction = "start" | "stop" | "pin" | "unpin" | "rebuild";
export type TaskHostingAction = "start_all" | "stop_all";

export interface TaskHosting {
  apps: (ResearcherHostingInfo & { app_id: string; label: string })[];
  task_max_running: number;
  global_max_running: number;
  global_running: number;
  running: number;
  queued: number;
  apps_reachable: boolean;
}

export interface Item {
  id: string;
  app_a: { id: string; label: string };
  app_b: { id: string; label: string } | null;
  group_key: string;
  is_active: boolean;
  submitted: number;
  in_progress: number;
  skipped: number;
  target: number | null;
}

export interface ResearcherParticipant {
  id: string;
  label: string;
  kind: ParticipantKind;
  status: ParticipantStatus;
  email: string | null;
  invite: { id: string; label: string } | null;
  access_code_hint: string;
  consent_at: string | null;
  joined_at: string | null;
  last_seen_at: string | null;
  completed_at: string | null;
  submitted: number;
  skipped: number;
  in_progress: number;
  excluded: boolean;
  exclusion_reason: string;
  excluded_at: string | null;
  note: string;
  /** e.g. "speeding", "straightlining", "attention_failed", "app_not_viewed", "possible_duplicate" */
  quality_flags: string[];
  created_at: string;
}

export interface ParticipantQuery {
  kind?: ParticipantKind;
  status?: ParticipantStatus;
  search?: string;
  page?: number;
  per_page?: number;
}

export interface ParticipantUpdateInput {
  excluded?: boolean;
  exclusion_reason?: string;
  note?: string;
  status?: "withdrawn";
}

export interface CreatedCode {
  participant_id: string;
  label: string;
  /** Plaintext access code — shown only in this response. */
  code: string;
}

export interface CodesCreated {
  codes: CreatedCode[];
}

export interface AccountsInput {
  emails: string[];
  /**
   * Default true: missing users are created as restricted evaluator accounts. false: only link
   * existing users (missing emails get a per-email error). New users are never unrestricted.
   */
  restricted?: boolean;
  /** Email the set-password / portal link (default false). */
  send_email?: boolean;
}

export interface AccountResult {
  email: string;
  participant: ResearcherParticipant | null;
  user_created: boolean;
  /**
   * For newly created accounts: a set-password link (relative path). Expires after
   * PASSWORD_RESET_TIMEOUT or once the user logs in; reissue with `setup-link/`.
   */
  setup_path: string | null;
  error: string | null;
}

export interface AccountsCreated {
  results: AccountResult[];
}

/** POST tasks/{id}/participants/{pid}/setup-link/ — only for accounts you created that never logged in. */
export interface SetupLink {
  setup_path: string;
  expires_at: string;
}

export interface Invite {
  id: string;
  token: string;
  /** Relative portal path, e.g. /eval/join/<token> */
  path: string;
  label: string;
  max_uses: number | null;
  uses: number;
  expires_at: string | null;
  is_active: boolean;
  created_at: string;
}

export interface InviteInput {
  label?: string;
  max_uses?: number | null;
  expires_at?: string | null;
  is_active?: boolean;
}

export interface PreviewSession {
  participant_id: string;
  /** e.g. /eval/t/<task_id> */
  portal_path: string;
}

export interface TaskProgress {
  counts: TaskCounts;
  assignments_by_status: Record<AssignmentStatus, number>;
  participants_by_status: Record<ParticipantStatus, number>;
  /** histogram: number of submitted ratings → number of items */
  coverage_histogram: { ratings: number; items: number }[];
  /** submissions per day (UTC date) */
  timeline: { date: string; submitted: number }[];
  median_minutes_per_item: number | null;
}

export interface AssignmentSummary {
  id: string;
  participant: { id: string; label: string; kind: ParticipantKind };
  item_id: string;
  apps: { id: string; label: string; slot: "A" | "B" | "single" }[];
  status: AssignmentStatus;
  position: number;
  swapped: boolean;
  skip_reason: string;
  started_at: string | null;
  submitted_at: string | null;
  active_seconds: number;
  wall_seconds: number;
  excluded: boolean;
  exclusion_reason: string;
  excluded_at: string | null;
  observation_count: number;
  http_request_count: number;
  quality_flags: string[];
}

export interface AssignmentQuery {
  participant_id?: string;
  app_id?: string;
  status?: AssignmentStatus;
  page?: number;
  per_page?: number;
}

export interface FindingVerdictRecord {
  finding_key: string;
  verdict: FindingVerdictValue;
  note: string;
  finding_snapshot: ReviewFinding | Record<string, never>;
  updated_at: string;
}

export interface AssignmentDetail extends AssignmentSummary {
  context: AssignmentContext;
  responses: Record<string, { answers: BlockAnswers; updated_at: string }>;
  observations: Observation[];
  finding_verdicts: FindingVerdictRecord[];
  skip_note: string;
}

export interface AssignmentExclusionInput {
  excluded: boolean;
  exclusion_reason?: string;
}

export interface HttpExchangeRecord {
  id: string;
  assignment_id: string;
  participant_label: string;
  app_label: string;
  method: string;
  path: string;
  request_headers: Record<string, string>;
  request_body: string;
  status_code: number | null;
  response_headers: Record<string, string>;
  response_body: string;
  response_truncated: boolean;
  elapsed_ms: number | null;
  error: string;
  created_at: string;
}

export interface ResearcherObservation extends Observation {
  participant: { id: string; label: string };
  /** task app id (researcher only) */
  app_id: string;
  app_label: string;
}

export interface ObservationQuery {
  kind?: ObservationKind;
  app_id?: string;
  participant_id?: string;
  page?: number;
  per_page?: number;
}

// ---------------------------------------------------------------------------
// Results (/api/human-eval/tasks/{id}/results/)
// ---------------------------------------------------------------------------

export type GroupBy = "condition" | "model" | "app";

export interface Ci {
  low: number | null;
  high: number | null;
}

export interface ResultWarning {
  /** e.g. provisional, low_n, low_agreement, position_bias, unbalanced_coverage, app_unavailable,
   * mixed_evaluator_versions, shared_app_state, review_items_differ, instructions_changed, no_baseline */
  code: string;
  message: string;
  block_id?: string;
}

export interface ResultGroup {
  key: string;
  label: string;
  /** apps in the pool for this group */
  n_apps_pool: number;
  /** apps with ≥ 1 included submitted assignment */
  n_apps_rated: number;
  /** apps with no included submission and ≥ 1 app_broken skip */
  n_apps_unevaluable: number;
}

export interface ResultApp {
  app_id: string;
  label: string;
  group: string;
  model_name: string;
  condition_label: string;
  app_slug: string;
  run_id: string | null;
  n_submitted: number;
  n_unavailable: number;
}

export interface Agreement {
  /** Krippendorff's alpha at the stated level; null when not computable. */
  alpha: number | null;
  level: "nominal" | "ordinal" | "interval";
  /** Share of agreeing pairable rater pairs. */
  percent_agreement: number | null;
  /** Units (apps / items / findings) with ≥ 2 ratings. */
  n_units: number;
  n_values: number;
}

export interface AppScore {
  app_id: string;
  label: string;
  group: string;
  n: number;
  mean: number | null;
  sd: number | null;
  median: number | null;
  na_count: number;
}

/** Group value = mean of per-app values (apps are the unit of analysis). */
export interface GroupScore {
  group: string;
  label: string;
  n_apps: number;
  n_ratings: number;
  mean: number | null;
  /** Percentile bootstrap over apps (seeded); null with < 3 apps. */
  ci: Ci | null;
  /** Sensitivity: unevaluable apps included at the floor (scale minimum / 0); null when not applicable. */
  floor: { mean: number | null; ci: Ci | null; n_apps: number } | null;
}

export interface RatingCriterionResult {
  criterion_id: string;
  label: string;
  scale: Scale;
  per_app: AppScore[];
  per_group: GroupScore[];
  agreement: Agreement;
}

export interface RatingBlockResult {
  type: "rating";
  block_id: string;
  title: string;
  criteria: RatingCriterionResult[];
  /** Mean over criteria, each rescaled to 0..1. */
  composite: { per_app: AppScore[]; per_group: GroupScore[] };
}

export interface RequirementsBlockResult {
  type: "requirements";
  block_id: string;
  title: string;
  /** met = 1, partial = 0.5, not_met = blocked = 0; cannot_test excluded (compliance_strict counts it as 0) */
  per_app: {
    app_id: string;
    label: string;
    group: string;
    n_raters: number;
    compliance: number | null;
    compliance_strict: number | null;
    by_source: Record<string, number | null>;
    blocked: number;
    cannot_test: number;
    conformance_pass_rate: number | null;
    /** conformance pass rate over the check categories matching the block's endpoint sources (api → api, admin_api → admin) */
    conformance_endpoint_pass_rate: number | null;
    conformance_status: string | null;
  }[];
  per_group: GroupScore[];
  per_requirement: {
    req_id: string;
    source: RequirementSource | "custom";
    text: string;
    n_ratings: number;
    met: number;
    partial: number;
    not_met: number;
    blocked: number;
    cannot_test: number;
  }[];
  agreement: Agreement;
  /**
   * Convergent validity against the automated conformance suite, on within-spec ranks, restricted
   * to the modal (evaluator_version, tester_version). scope "endpoints" (primary): the block's
   * api / admin_api items vs the matching check categories (api → api, admin_api → admin);
   * "overall" (secondary, different constructs).
   */
  vs_conformance: {
    scope: "endpoints" | "overall";
    rho: number | null;
    ci: Ci | null;
    n: number;
    excluded_apps: string[];
    note: string;
  }[];
  /** api/admin_api requirements matched to conformance checks by "METHOD /path". */
  endpoint_agreement: {
    n: number;
    kappa: number | null;
    both_pass: number;
    human_only: number;
    auto_only: number;
    both_fail: number;
    /** majority ties and items with no matching check */
    n_unmatched: number;
  } | null;
}

/**
 * Finding precision. Primary estimator: per-finding soft label tp/(tp+fp) votes, averaged with
 * inverse-probability weights from the per-app sampling frame (except per_tool, unweighted);
 * CI = bootstrap over apps. Counts are vote totals (descriptive).
 */
export interface PrecisionRow {
  key: string;
  label: string;
  tp: number;
  fp: number;
  unsure: number;
  n_findings: number;
  n_apps: number;
  /** findings whose tp/fp votes disagree */
  n_split: number;
  precision: number | null;
  ci: Ci | null;
  /** secondary: majority vote (ties/unsure dropped) */
  majority_precision: number | null;
  weighted: boolean;
}

export interface SecurityBlockResult {
  type: "security";
  block_id: string;
  title: string;
  checklist: {
    item_id: string;
    label: string;
    category: string;
    /** vulnerable_share = mean over apps of the app's vulnerable/(vulnerable+not_vulnerable); counts are rater-level */
    per_group: {
      group: string;
      label: string;
      n_apps: number;
      n: number;
      vulnerable: number;
      not_vulnerable: number;
      not_tested: number;
      vulnerable_share: number | null;
      ci: Ci | null;
    }[];
    agreement: Agreement;
  }[];
  reports: {
    per_app: {
      app_id: string;
      label: string;
      group: string;
      total: number;
      /** tester-assigned severities */
      by_severity: Record<string, number>;
      by_category: Record<string, number>;
    }[];
    per_group: {
      group: string;
      label: string;
      n_apps: number;
      total: number;
      /** mean over apps of (reports / submitted assignments) */
      per_rater_mean: number | null;
      ci: Ci | null;
      by_severity: Record<string, number>;
    }[];
  };
  /** Majority verdict per finding; ties and all-unsure count as unsure. */
  finding_review: {
    n_findings: number;
    n_verdicts: number;
    overall: PrecisionRow;
    per_tool: PrecisionRow[];
    /** grouped by the tool's own label ("unrated" when it printed none) */
    per_tool_severity: PrecisionRow[];
    per_group: PrecisionRow[];
    agreement: Agreement;
  } | null;
  console: {
    requests: number;
    participants_using: number;
    per_app: { app_id: string; label: string; requests: number }[];
  } | null;
}

export interface ScenariosBlockResult {
  type: "scenarios";
  block_id: string;
  title: string;
  scenarios: {
    scenario_id: string;
    title: string;
    n: number;
    success: number;
    partial: number;
    failure: number;
    not_attempted: number;
    /** pooled success / attempted (descriptive) */
    success_rate: number | null;
    median_seconds: number | null;
    ease_mean: number | null;
    /** success_rate = mean over apps of the app's success share; CI bootstrap over apps */
    per_group: {
      group: string;
      label: string;
      n_apps: number;
      n: number;
      success_rate: number | null;
      ci: Ci | null;
      median_seconds: number | null;
    }[];
  }[];
}

export interface QuestionnaireBlockResult {
  type: "questionnaire";
  block_id: string;
  title: string;
  instrument: StandardInstrument | "custom";
  /** Standard instruments: SUS 0..100, UMUX-Lite 0..100, raw TLX 0..100. */
  score: { per_app: AppScore[]; per_group: GroupScore[] } | null;
  questions: {
    question_id: string;
    label: string;
    kind: QuestionKind;
    n: number;
    mean: number | null;
    distribution: Record<string, number>;
  }[];
}

export interface BugsBlockResult {
  type: "bugs";
  block_id: string;
  title: string;
  per_app: {
    app_id: string;
    label: string;
    group: string;
    n_raters: number;
    total: number;
    per_rater_mean: number | null;
    by_severity: Record<string, number>;
    none_found: number;
  }[];
  per_group: GroupScore[];
}

export interface BtEntry {
  key: string;
  label: string;
  /** connected comparison component; strengths/ranks are only comparable within one */
  component: string;
  /** Bradley–Terry strength (geometric mean of all = 1). */
  strength: number | null;
  log_strength: number | null;
  /** Cluster bootstrap 95% on log_strength (resampling items; apps within groups for by_group). */
  ci: Ci | null;
  wins: number;
  losses: number;
  ties: number;
  comparisons: number;
  rank: number | null;
}

export interface PairwiseCriterionResult {
  /** "overall" for the overall preference */
  criterion_id: string;
  label: string;
  n_comparisons: number;
  by_group: BtEntry[];
  by_app: BtEntry[];
  /** Preference for the left (displayed A) side; p withheld while provisional. */
  position_bias: {
    left: number;
    right: number;
    ties: number;
    p_value: number | null;
  };
  agreement: Agreement;
}

export interface PairwiseBlockResult {
  type: "pairwise";
  block_id: string;
  title: string;
  criteria: PairwiseCriterionResult[];
}

export type BlockResult =
  | RatingBlockResult
  | RequirementsBlockResult
  | SecurityBlockResult
  | ScenariosBlockResult
  | QuestionnaireBlockResult
  | BugsBlockResult
  | PairwiseBlockResult;

export interface ParticipantQuality {
  participant_id: string;
  label: string;
  kind: ParticipantKind;
  excluded: boolean;
  submitted: number;
  skipped: number;
  median_seconds: number | null;
  speeding: number;
  straightlining: number;
  attention_failed: number;
  flags: string[];
}

/** A between-condition contrast against the baseline group (group_by = condition only). */
export interface Contrast {
  block_id: string;
  metric: string;
  label: string;
  group: string;
  group_label: string;
  baseline: string;
  baseline_label: string;
  /** group mean − baseline mean (higher = more of the metric) */
  diff: number | null;
  ci: Ci | null;
  /** paired on common (app_slug, repeat_index) cells when ≥ 3 exist, else unpaired */
  paired: boolean;
  n_cells: number;
  p_value: number | null;
  /** Holm-adjusted across all contrasts of the task */
  p_adjusted: number | null;
  /** the pre-registered primary metric */
  primary: boolean;
}

export interface HumanVsAutomated {
  metric: string;
  label: string;
  rho: number | null;
  ci: Ci | null;
  n: number;
  note: string;
}

export interface TaskResults {
  task_id: string;
  generated_at: string;
  /** True while the task is not closed. */
  provisional: boolean;
  p_values_withheld: boolean;
  /** Plan followed: closed once, planned sample reached, no manual exclusions after open, primary metric set. */
  confirmatory: boolean;
  /** Why the results are not confirmatory. */
  deviations: string[];
  /** exclusion rules applied (from design.analysis) and how many assignments each removed */
  rule_exclusions: Record<string, number>;
  group_by: GroupBy;
  include_excluded: boolean;
  counts: {
    participants: number;
    participants_included: number;
    assignments_submitted: number;
    assignments_included: number;
    assignments_skipped: number;
    assignments_excluded: number;
  };
  warnings: ResultWarning[];
  groups: ResultGroup[];
  apps: ResultApp[];
  blocks: BlockResult[];
  quality: ParticipantQuality[];
  contrasts: Contrast[];
  human_vs_automated: HumanVsAutomated[];
}

export type ExportKind =
  | "items"
  | "responses"
  | "ratings"
  | "requirements"
  | "security_checklist"
  | "pairwise"
  | "scenarios"
  | "questionnaires"
  | "observations"
  | "finding_verdicts"
  | "participants"
  | "assignments"
  | "apps"
  | "http_log";

// ---------------------------------------------------------------------------
// Portal API (/api/human-eval/portal/)
// ---------------------------------------------------------------------------

export interface JoinInfo {
  task_title: string;
  task_description: string;
  /** open | not_open | closed | full | expired | inactive */
  availability: string;
  /** This browser already holds a participant session for the task (POST join then answers 409 already_joined). */
  already_joined: { task_id: string; participant_label: string } | null;
  consent: ConsentConfig;
  estimated_items: number | null;
}

export interface JoinInput {
  consent: boolean;
}

export interface JoinResult {
  task_id: string;
  participant_label: string;
  /** Plaintext resume code — shown once; the participant writes it down. */
  access_code: string;
}

export interface ResumeInput {
  code: string;
}

export interface ResumeResult {
  task_id: string;
  participant_label: string;
}

export interface PortalParticipation {
  task_id: string;
  task_title: string;
  task_status: TaskStatus;
  participant_label: string;
  participant_status: ParticipantStatus;
  kind: ParticipantKind;
  submitted: number;
  target: number | null;
}

export interface PortalMe {
  /** participant: cookie session (link / code); account: logged-in user; anonymous: neither (cookie wins when both) */
  kind: "participant" | "account" | "anonymous";
  /** the cookie session's participation, if any */
  participant: { task_id: string; label: string } | null;
  user: { email: string; name: string; is_evaluator: boolean } | null;
  /** cookie participation (if any) plus every account/preview participation of the user */
  participations: PortalParticipation[];
}

export interface PortalTask {
  id: string;
  title: string;
  description: string;
  instructions: string;
  mode: TaskMode;
  status: TaskStatus;
  /** Participants may start new items right now. */
  accepting: boolean;
  participant: {
    label: string;
    kind: ParticipantKind;
    status: ParticipantStatus;
    consent_at: string | null;
    completion_code: string;
  };
  consent: ConsentConfig;
  /** attention questions are sent without `expected` */
  pre_questions: Question[];
  pre_answers: Record<string, QuestionAnswer>;
  /** Background answers can no longer change: an item was already handed out (expired ones included). */
  pre_answers_locked: boolean;
  post_questions: Question[];
  post_answers: Record<string, QuestionAnswer>;
  progress: {
    submitted: number;
    skipped: number;
    /** null = unlimited */
    limit: number | null;
    remaining_available: number;
  };
  current_assignment_id: string | null;
  blocks: { id: string; type: BlockType; title: string }[];
  allow_skip: boolean;
}

export type NextReason =
  | "assigned"
  | "resumed"
  | "done"
  | "limit_reached"
  | "no_items_available"
  | "not_accepting"
  | "consent_required"
  | "pre_questions_required";

export interface NextResult {
  assignment_id: string | null;
  reason: NextReason;
}

export interface RequirementItem {
  id: string;
  source: RequirementSource | "custom";
  text: string;
}

/** A finding as shown for TP/FP review: tool's own label only, never a normalized severity. */
export interface ReviewFinding {
  id: string;
  tool: string;
  tool_name: string;
  /** exactly as the tool printed it; "" when the tool rates nothing */
  tool_severity: string;
  category: string;
  rule_id: string;
  title: string;
  description: string;
  file_path: string;
  line: number | null;
  code_snippet: string;
  docs_url: string;
  cwe: string;
  /** DAST findings */
  url: string;
  method: string;
  param: string;
  evidence: string;
}

/** Per-block context frozen in Assignment.context; per app slot where it differs. */
export interface BlockContext {
  /** requirements: slot → items */
  requirements?: Record<string, RequirementItem[]>;
  /** security: slot → findings to review */
  findings?: Record<string, ReviewFinding[]>;
  /** questionnaire (standard instruments): fixed questions */
  questions?: Question[];
}

/** What the participant actually used, recorded when the app is first seen running. */
export interface HostingProvenance {
  container_id: string | null;
  container_name: string | null;
  image: string;
  reset_at: string | null;
  /** 1-based: how many assignments had used this app's container state before (0 after a reset) */
  prior_users: number;
  first_running_at: string | null;
}

export interface AssignmentContext {
  blocks: Record<string, BlockContext>;
  /** slot → provenance */
  hosting: Record<string, HostingProvenance>;
  instructions_sha256: string;
  /** slot → hosting state/error at skip time (skips only) */
  skip_hosting?: Record<string, { state: HostingState; error: string }>;
}

/** The participant spec view of an app: the task as the model got it. */
export interface AppSpecView {
  /** Task title. */
  name: string;
  /** Task summary. */
  description: string;
  /** The prose variant the model was given. */
  prose: string;
  frontend_requirements: string[];
  api_endpoints: {
    method: string;
    path: string;
    description: string;
    auth: string;
  }[];
  admin_api_endpoints: {
    method: string;
    path: string;
    description: string;
    auth: string;
  }[];
}

export interface PortalApp {
  slot: "A" | "B" | "single";
  /** blinded label, e.g. "App 07" */
  label: string;
  hosting: HostingInfo;
  spec: AppSpecView | null;
  /** The app's real runtime admin login; null when hidden or not available. */
  admin_credentials: { username: string; password: string } | null;
  /**
   * `available` when shown; `not_measured` when the app has no runtime credentials record (show
   * "admin login not available", never a default password); null when blinding hides it.
   */
  admin_credentials_status: "available" | "not_measured" | null;
  has_source: boolean;
}

export interface PortalAssignment {
  id: string;
  task: { id: string; title: string; mode: TaskMode; instructions: string };
  status: AssignmentStatus;
  position: number;
  started_at: string | null;
  submitted_at: string | null;
  active_seconds: number;
  /** read_only once submitted / skipped / expired or the task stopped accepting */
  read_only: boolean;
  apps: PortalApp[];
  /** Design blocks as the participant sees them (attention `expected` stripped). */
  blocks: Block[];
  context: AssignmentContext;
  responses: Record<string, { answers: BlockAnswers; updated_at: string }>;
  observations: Observation[];
  finding_verdicts: Record<
    string,
    { verdict: FindingVerdictValue; note: string }
  >;
  allow_skip: boolean;
  http_console: boolean;
}

export interface SubmitResult {
  status: AssignmentStatus;
  next: NextResult | null;
}

export interface SkipInput {
  reason: SkipReason;
  note?: string;
}

/** Sent every 30 s while the workbench is open, visible or not. */
export interface HeartbeatInput {
  /** seconds of visible-tab time since the last beat (server caps at 60) */
  active_seconds: number;
  /** whether the workbench tab is currently visible */
  visible: boolean;
  /** slots whose app pane is shown (or opened in another tab), keeps their apps from idling out */
  viewing: ("A" | "B" | "single")[];
}

export interface HeartbeatResult {
  status: AssignmentStatus;
  active_seconds: number;
  apps: { slot: "A" | "B" | "single"; hosting: HostingInfo }[];
}

export interface ConsoleRequest {
  slot: "A" | "B" | "single";
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS";
  /** must start with "/", may include a query string */
  path: string;
  headers: Record<string, string>;
  body: string;
}

export interface ConsoleResponse {
  id: string;
  status_code: number | null;
  reason: string;
  headers: Record<string, string>;
  body: string;
  truncated: boolean;
  elapsed_ms: number | null;
  error: string;
}

export interface SourceFileEntry {
  path: string;
  size: number;
}

export interface SourceTree {
  files: SourceFileEntry[];
}

export interface SourceFile {
  path: string;
  content: string;
  truncated: boolean;
}

export interface FindingVerdictInput {
  verdict: FindingVerdictValue;
  note?: string;
}

export interface WithdrawInput {
  delete_data: boolean;
}
