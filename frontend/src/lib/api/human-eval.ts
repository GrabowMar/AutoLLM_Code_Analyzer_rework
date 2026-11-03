// Researcher API client for the human-evaluation module (/api/human-eval/).
// Wire types: ./types/human-eval.ts (mirrors backend/human_eval).
import { apiFetch } from "./core";
import type {
  AccountsCreated,
  AccountsInput,
  AppHostingAction,
  AssignmentDetail,
  AssignmentExclusionInput,
  AssignmentQuery,
  AssignmentSummary,
  CandidateRun,
  CandidateRunQuery,
  CodesCreated,
  CreatedCode,
  DesignValidation,
  ExportKind,
  FunctionalSummary,
  GroupBy,
  HttpExchangeRecord,
  HumanEvalEnvironment,
  HumanEvalError,
  Invite,
  InviteInput,
  Item,
  ObservationQuery,
  Paginated,
  ParticipantQuery,
  ParticipantUpdateInput,
  Preset,
  PreviewSession,
  ResearcherHostingInfo,
  ResearcherObservation,
  ResearcherParticipant,
  SetupLink,
  TaskAction,
  TaskApp,
  TaskAppsAddInput,
  TaskCreateInput,
  TaskDesign,
  TaskDetail,
  TaskHosting,
  TaskHostingAction,
  TaskListItem,
  TaskMode,
  TaskProgress,
  TaskResults,
  TaskSettings,
  TaskStatus,
  TaskUpdateInput,
} from "./types/human-eval";

export type * from "./types/human-eval";

const BASE = "/human-eval";

export interface FetchOptions {
  signal?: AbortSignal;
}

type QueryValue = string | number | boolean | null | undefined;

function query(params: Record<string, QueryValue>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    q.set(key, String(value));
  }
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

function send(
  method: string,
  body: unknown,
  options: FetchOptions = {},
): RequestInit {
  return {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options.signal,
  };
}

async function json<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** The structured {detail, code, errors[]} body of a human-eval 400/409, or null. */
export function humanEvalErrorBody(error: unknown): HumanEvalError | null {
  if (!error || typeof error !== "object") return null;
  const body = error as Record<string, unknown>;
  if (typeof body.code === "string" && Array.isArray(body.errors)) {
    return body as unknown as HumanEvalError;
  }
  return null;
}

// --- catalog / environment ---------------------------------------------------

export async function listPresets(
  options: FetchOptions = {},
): Promise<Preset[]> {
  return json(await apiFetch(`${BASE}/presets/`, { signal: options.signal }));
}

export async function getEnvironment(
  options: FetchOptions = {},
): Promise<HumanEvalEnvironment> {
  return json(
    await apiFetch(`${BASE}/environment/`, { signal: options.signal }),
  );
}

export async function validateDesign(
  input: { mode: TaskMode; design: TaskDesign; settings: TaskSettings },
  options: FetchOptions = {},
): Promise<DesignValidation> {
  return json(
    await apiFetch(`${BASE}/validate-design/`, send("POST", input, options)),
  );
}

/** Completed app runs that can join a task's pool (the caller's; `study_id` narrows to one study). */
export async function listCandidateRuns(
  q: CandidateRunQuery = {},
  options: FetchOptions = {},
): Promise<Paginated<CandidateRun>> {
  return json(
    await apiFetch(`${BASE}/candidate-runs/${query({ ...q })}`, {
      signal: options.signal,
    }),
  );
}

/** Display label of an app's functional status (`not_measured` is never a pass or a 0). */
export function functionalStatusLabel(
  status: string | null | undefined,
): string {
  if (!status) return "Not measured";
  return FUNCTIONAL_STATUS_LABELS[status] ?? status.replace(/_/g, " ");
}

const FUNCTIONAL_STATUS_LABELS: Record<string, string> = {
  tested: "Tested",
  pending: "Evaluation pending",
  running: "Evaluating",
  not_measured: "Not measured",
  error: "Evaluation error",
  build_fail: "Build failed",
  start_fail: "Start failed",
  output_invalid: "Output invalid",
  infra_error: "Infrastructure error",
  excluded: "Excluded",
};

/** Where an app's functional verdict comes from; "" when there is none. */
export function functionalSourceLabel(
  source: FunctionalSummary["source"] | undefined,
): string {
  return source === "eval_suite" ? "EvalSuite" : "";
}

/** One-line description of a study in a picker: status, mode, kind and main-stage cell progress. */
export function studyOptionDescription(study: {
  status: string;
  mode: string;
  kind: string;
  cells_done: number;
  cells_total: number;
}): string {
  const cells =
    study.cells_total > 0
      ? `${study.cells_done}/${study.cells_total} cells`
      : "no cells yet";
  return [
    study.status.replace(/_/g, " "),
    `${study.mode} mode`,
    study.kind,
    cells,
  ].join(" · ");
}

// --- tasks -------------------------------------------------------------------

export interface TaskListQuery {
  status?: TaskStatus | "";
  search?: string;
  page?: number;
  per_page?: number;
}

export async function listTasks(
  q: TaskListQuery = {},
  options: FetchOptions = {},
): Promise<Paginated<TaskListItem>> {
  return json(
    await apiFetch(`${BASE}/tasks/${query({ ...q })}`, {
      signal: options.signal,
    }),
  );
}

export async function createTask(
  input: TaskCreateInput,
  options: FetchOptions = {},
): Promise<TaskDetail> {
  return json(await apiFetch(`${BASE}/tasks/`, send("POST", input, options)));
}

export async function getTask(
  taskId: string,
  options: FetchOptions = {},
): Promise<TaskDetail> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/`, { signal: options.signal }),
  );
}

export async function updateTask(
  taskId: string,
  input: TaskUpdateInput,
  options: FetchOptions = {},
): Promise<TaskDetail> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/`, send("PATCH", input, options)),
  );
}

export async function deleteTask(
  taskId: string,
  options: FetchOptions = {},
): Promise<void> {
  await apiFetch(
    `${BASE}/tasks/${taskId}/`,
    send("DELETE", undefined, options),
  );
}

export async function duplicateTask(
  taskId: string,
  options: FetchOptions = {},
): Promise<TaskDetail> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/duplicate/`,
      send("POST", {}, options),
    ),
  );
}

export async function transitionTask(
  taskId: string,
  action: TaskAction,
  options: FetchOptions = {},
): Promise<TaskDetail> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/status/`,
      send("POST", { action }, options),
    ),
  );
}

export async function listItems(
  taskId: string,
  options: FetchOptions = {},
): Promise<Item[]> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/items/`, {
      signal: options.signal,
    }),
  );
}

export async function startPreview(
  taskId: string,
  options: FetchOptions = {},
): Promise<PreviewSession> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/preview/`,
      send("POST", {}, options),
    ),
  );
}

export async function resetPreview(
  taskId: string,
  options: FetchOptions = {},
): Promise<void> {
  await apiFetch(
    `${BASE}/tasks/${taskId}/preview/`,
    send("DELETE", undefined, options),
  );
}

// --- app pool & hosting --------------------------------------------------------

export async function listTaskApps(
  taskId: string,
  options: FetchOptions = {},
): Promise<TaskApp[]> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/apps/`, {
      signal: options.signal,
    }),
  );
}

export async function addTaskApps(
  taskId: string,
  input: TaskAppsAddInput,
  options: FetchOptions = {},
): Promise<TaskApp[]> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/apps/`,
      send("POST", input, options),
    ),
  );
}

export async function updateTaskApp(
  taskId: string,
  appId: string,
  input: { is_active?: boolean; label?: string },
  options: FetchOptions = {},
): Promise<TaskApp> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/apps/${appId}/`,
      send("PATCH", input, options),
    ),
  );
}

export async function removeTaskApp(
  taskId: string,
  appId: string,
  options: FetchOptions = {},
): Promise<void> {
  await apiFetch(
    `${BASE}/tasks/${taskId}/apps/${appId}/`,
    send("DELETE", undefined, options),
  );
}

export async function appHostingAction(
  taskId: string,
  appId: string,
  action: AppHostingAction,
  options: FetchOptions = {},
): Promise<ResearcherHostingInfo> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/apps/${appId}/hosting/`,
      send("POST", { action }, options),
    ),
  );
}

export async function getTaskHosting(
  taskId: string,
  options: FetchOptions = {},
): Promise<TaskHosting> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/hosting/`, {
      signal: options.signal,
    }),
  );
}

export async function taskHostingAction(
  taskId: string,
  action: TaskHostingAction,
  options: FetchOptions = {},
): Promise<TaskHosting> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/hosting/`,
      send("POST", { action }, options),
    ),
  );
}

// --- participants & invites ----------------------------------------------------

export async function listParticipants(
  taskId: string,
  q: ParticipantQuery = {},
  options: FetchOptions = {},
): Promise<Paginated<ResearcherParticipant>> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/participants/${query({ ...q })}`, {
      signal: options.signal,
    }),
  );
}

export async function createAccessCodes(
  taskId: string,
  count: number,
  options: FetchOptions = {},
): Promise<CodesCreated> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/participants/codes/`,
      send("POST", { count }, options),
    ),
  );
}

export async function addAccounts(
  taskId: string,
  input: AccountsInput,
  options: FetchOptions = {},
): Promise<AccountsCreated> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/participants/accounts/`,
      send("POST", input, options),
    ),
  );
}

export async function updateParticipant(
  taskId: string,
  participantId: string,
  input: ParticipantUpdateInput,
  options: FetchOptions = {},
): Promise<ResearcherParticipant> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/participants/${participantId}/`,
      send("PATCH", input, options),
    ),
  );
}

export async function deleteParticipant(
  taskId: string,
  participantId: string,
  options: FetchOptions = {},
): Promise<void> {
  await apiFetch(
    `${BASE}/tasks/${taskId}/participants/${participantId}/`,
    send("DELETE", undefined, options),
  );
}

export async function regenerateAccessCode(
  taskId: string,
  participantId: string,
  options: FetchOptions = {},
): Promise<CreatedCode> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/participants/${participantId}/regenerate-code/`,
      send("POST", {}, options),
    ),
  );
}

/** New set-password link for an account you created that has never logged in. */
export async function issueSetupLink(
  taskId: string,
  participantId: string,
  options: FetchOptions = {},
): Promise<SetupLink> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/participants/${participantId}/setup-link/`,
      send("POST", {}, options),
    ),
  );
}

export async function listInvites(
  taskId: string,
  options: FetchOptions = {},
): Promise<Invite[]> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/invites/`, {
      signal: options.signal,
    }),
  );
}

export async function createInvite(
  taskId: string,
  input: InviteInput,
  options: FetchOptions = {},
): Promise<Invite> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/invites/`,
      send("POST", input, options),
    ),
  );
}

export async function updateInvite(
  taskId: string,
  inviteId: string,
  input: InviteInput,
  options: FetchOptions = {},
): Promise<Invite> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/invites/${inviteId}/`,
      send("PATCH", input, options),
    ),
  );
}

export async function deleteInvite(
  taskId: string,
  inviteId: string,
  options: FetchOptions = {},
): Promise<void> {
  await apiFetch(
    `${BASE}/tasks/${taskId}/invites/${inviteId}/`,
    send("DELETE", undefined, options),
  );
}

// --- progress, responses, results ------------------------------------------------

export async function getProgress(
  taskId: string,
  options: FetchOptions = {},
): Promise<TaskProgress> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/progress/`, {
      signal: options.signal,
    }),
  );
}

export async function listAssignments(
  taskId: string,
  q: AssignmentQuery = {},
  options: FetchOptions = {},
): Promise<Paginated<AssignmentSummary>> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/assignments/${query({ ...q })}`, {
      signal: options.signal,
    }),
  );
}

export async function getAssignment(
  taskId: string,
  assignmentId: string,
  options: FetchOptions = {},
): Promise<AssignmentDetail> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/assignments/${assignmentId}/`, {
      signal: options.signal,
    }),
  );
}

export async function setAssignmentExclusion(
  taskId: string,
  assignmentId: string,
  input: AssignmentExclusionInput,
  options: FetchOptions = {},
): Promise<AssignmentSummary> {
  return json(
    await apiFetch(
      `${BASE}/tasks/${taskId}/assignments/${assignmentId}/`,
      send("PATCH", input, options),
    ),
  );
}

export async function listObservations(
  taskId: string,
  q: ObservationQuery = {},
  options: FetchOptions = {},
): Promise<Paginated<ResearcherObservation>> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/observations/${query({ ...q })}`, {
      signal: options.signal,
    }),
  );
}

export async function listHttpLog(
  taskId: string,
  q: {
    participant_id?: string;
    app_id?: string;
    /** One assignment's exchanges (newest first), e.g. for the assignment sheet. */
    assignment_id?: string;
    page?: number;
    per_page?: number;
  } = {},
  options: FetchOptions = {},
): Promise<Paginated<HttpExchangeRecord>> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/http-log/${query({ ...q })}`, {
      signal: options.signal,
    }),
  );
}

export async function getResults(
  taskId: string,
  q: { group_by?: GroupBy; include_excluded?: boolean } = {},
  options: FetchOptions = {},
): Promise<TaskResults> {
  return json(
    await apiFetch(`${BASE}/tasks/${taskId}/results/${query({ ...q })}`, {
      signal: options.signal,
    }),
  );
}

/** Same-origin download URLs (session cookie auth; use as <a href download>). */
export function exportCsvUrl(
  taskId: string,
  kind: ExportKind,
  includeExcluded = false,
): string {
  return `/api${BASE}/tasks/${taskId}/export/${kind}.csv${query({ include_excluded: includeExcluded || undefined })}`;
}

export function exportBundleUrl(taskId: string): string {
  return `/api${BASE}/tasks/${taskId}/export/bundle.zip`;
}

export function exportResultsJsonUrl(taskId: string): string {
  return `/api${BASE}/tasks/${taskId}/export/results.json`;
}

/** Researcher-side attachment URL (the payloads already carry `url`). */
export function researcherAttachmentUrl(
  taskId: string,
  attachmentId: string,
): string {
  return `/api${BASE}/tasks/${taskId}/attachments/${attachmentId}/`;
}
