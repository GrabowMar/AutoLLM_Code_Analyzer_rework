// Participant (portal) API client for the human-evaluation module
// (/api/human-eval/portal/). Unlike apiFetch it never redirects to the
// researcher login on 401 (anonymous participants have no account), it
// bootstraps the CSRF cookie before the first mutation (the participant
// cookie auth enforces CSRF) and it supports multipart uploads.
// Errors are thrown as the parsed JSON body with a non-enumerable
// `httpStatus`, exactly like apiFetch, so formatApiError / apiErrorStatus work.
import { ensureCsrfCookie, getCsrfToken } from "./core";
import type {
  AssignmentStatus,
  BlockAnswers,
  ConsoleRequest,
  ConsoleResponse,
  FindingVerdictInput,
  FindingVerdictValue,
  HeartbeatInput,
  HeartbeatResult,
  HostingInfo,
  JoinInfo,
  JoinInput,
  JoinResult,
  NextResult,
  Observation,
  ObservationAttachment,
  ObservationInput,
  PortalAssignment,
  PortalMe,
  PortalTask,
  QuestionAnswer,
  ResumeInput,
  ResumeResult,
  SkipInput,
  SourceFile,
  SourceTree,
  SubmitResult,
  WithdrawInput,
} from "./types/human-eval";

export type * from "./types/human-eval";

const PORTAL_BASE = "/api/human-eval/portal";

export interface FetchOptions {
  signal?: AbortSignal;
  /** Let the request outlive the page (flushing autosaves on tab close; body ≤ 64 KiB). */
  keepalive?: boolean;
}

export type Slot = "A" | "B" | "single";

function isSafe(method: string): boolean {
  return method === "GET" || method === "HEAD";
}

/** fetch() against the portal API; throws the parsed error body (with httpStatus). */
export async function portalFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  const isForm =
    typeof FormData !== "undefined" && options.body instanceof FormData;
  if (!isForm && options.body !== undefined && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  if (!isSafe(method)) {
    await ensureCsrfCookie();
    headers["X-CSRFToken"] = getCsrfToken();
  }
  const res = await fetch(`${PORTAL_BASE}${path}`, {
    ...options,
    method,
    headers,
    credentials: "include",
  });
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => ({}));
    const error: Record<string, unknown> =
      body && typeof body === "object" && !Array.isArray(body)
        ? (body as Record<string, unknown>)
        : { detail: body };
    if (res.status === 401) {
      // Ninja answers {"detail": "Unauthorized"}; give participants something actionable.
      error.detail =
        "Your session has ended. Enter your access code again (or sign in).";
    }
    Object.defineProperty(error, "httpStatus", { value: res.status });
    throw error;
  }
  return res;
}

function body(
  method: string,
  payload: unknown,
  options: FetchOptions = {},
): RequestInit {
  return {
    method,
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: options.signal,
    keepalive: options.keepalive,
  };
}

async function json<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- session -----------------------------------------------------------------

export async function getJoinInfo(
  token: string,
  options: FetchOptions = {},
): Promise<JoinInfo> {
  return json(
    await portalFetch(`/join/${encodeURIComponent(token)}/`, {
      signal: options.signal,
    }),
  );
}

export async function joinTask(
  token: string,
  input: JoinInput,
  options: FetchOptions = {},
): Promise<JoinResult> {
  return json(
    await portalFetch(
      `/join/${encodeURIComponent(token)}/`,
      body("POST", input, options),
    ),
  );
}

export async function resumeWithCode(
  input: ResumeInput,
  options: FetchOptions = {},
): Promise<ResumeResult> {
  return json(await portalFetch(`/resume/`, body("POST", input, options)));
}

export async function portalLogout(options: FetchOptions = {}): Promise<void> {
  await portalFetch(`/logout/`, body("POST", {}, options));
}

export async function getPortalMe(
  options: FetchOptions = {},
): Promise<PortalMe> {
  return json(await portalFetch(`/me/`, { signal: options.signal }));
}

// --- task --------------------------------------------------------------------

export async function getPortalTask(
  taskId: string,
  options: FetchOptions = {},
): Promise<PortalTask> {
  return json(
    await portalFetch(`/tasks/${taskId}/`, { signal: options.signal }),
  );
}

export async function giveConsent(
  taskId: string,
  options: FetchOptions = {},
): Promise<PortalTask> {
  return json(
    await portalFetch(
      `/tasks/${taskId}/consent/`,
      body("POST", { agree: true }, options),
    ),
  );
}

export async function savePreAnswers(
  taskId: string,
  answers: Record<string, QuestionAnswer>,
  options: FetchOptions = {},
): Promise<PortalTask> {
  return json(
    await portalFetch(
      `/tasks/${taskId}/pre-answers/`,
      body("PUT", { answers }, options),
    ),
  );
}

export async function savePostAnswers(
  taskId: string,
  answers: Record<string, QuestionAnswer>,
  options: FetchOptions = {},
): Promise<PortalTask> {
  return json(
    await portalFetch(
      `/tasks/${taskId}/post-answers/`,
      body("PUT", { answers }, options),
    ),
  );
}

export async function requestNext(
  taskId: string,
  options: FetchOptions = {},
): Promise<NextResult> {
  return json(
    await portalFetch(`/tasks/${taskId}/next/`, body("POST", {}, options)),
  );
}

export async function finishTask(
  taskId: string,
  options: FetchOptions = {},
): Promise<PortalTask> {
  return json(
    await portalFetch(`/tasks/${taskId}/finish/`, body("POST", {}, options)),
  );
}

export async function withdrawFromTask(
  taskId: string,
  input: WithdrawInput,
  options: FetchOptions = {},
): Promise<void> {
  await portalFetch(`/tasks/${taskId}/withdraw/`, body("POST", input, options));
}

// --- assignment ----------------------------------------------------------------

export async function getPortalAssignment(
  assignmentId: string,
  options: FetchOptions = {},
): Promise<PortalAssignment> {
  return json(
    await portalFetch(`/assignments/${assignmentId}/`, {
      signal: options.signal,
    }),
  );
}

export async function saveResponse(
  assignmentId: string,
  blockId: string,
  answers: BlockAnswers,
  options: FetchOptions = {},
): Promise<{ block_id: string; answers: BlockAnswers; updated_at: string }> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/responses/${encodeURIComponent(blockId)}/`,
      body("PUT", { answers }, options),
    ),
  );
}

export async function submitAssignment(
  assignmentId: string,
  options: FetchOptions = {},
): Promise<SubmitResult> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/submit/`,
      body("POST", {}, options),
    ),
  );
}

export async function skipAssignment(
  assignmentId: string,
  input: SkipInput,
  options: FetchOptions = {},
): Promise<SubmitResult> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/skip/`,
      body("POST", input, options),
    ),
  );
}

export async function sendHeartbeat(
  assignmentId: string,
  input: HeartbeatInput,
  options: FetchOptions = {},
): Promise<HeartbeatResult> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/heartbeat/`,
      body("POST", input, options),
    ),
  );
}

export async function ensureApp(
  assignmentId: string,
  slot: Slot,
  options: FetchOptions = {},
): Promise<HostingInfo> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/apps/${slot}/ensure/`,
      body("POST", {}, options),
    ),
  );
}

export async function sendConsoleRequest(
  assignmentId: string,
  input: ConsoleRequest,
  options: FetchOptions = {},
): Promise<ConsoleResponse> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/console/`,
      body("POST", input, options),
    ),
  );
}

// --- observations & attachments --------------------------------------------------

export async function listPortalObservations(
  assignmentId: string,
  options: FetchOptions = {},
): Promise<Observation[]> {
  return json(
    await portalFetch(`/assignments/${assignmentId}/observations/`, {
      signal: options.signal,
    }),
  );
}

export async function createObservation(
  assignmentId: string,
  input: ObservationInput,
  options: FetchOptions = {},
): Promise<Observation> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/observations/`,
      body("POST", input, options),
    ),
  );
}

export async function updateObservation(
  observationId: string,
  input: Partial<Omit<ObservationInput, "slot">>,
  options: FetchOptions = {},
): Promise<Observation> {
  return json(
    await portalFetch(
      `/observations/${observationId}/`,
      body("PATCH", input, options),
    ),
  );
}

export async function deleteObservation(
  observationId: string,
  options: FetchOptions = {},
): Promise<void> {
  await portalFetch(
    `/observations/${observationId}/`,
    body("DELETE", undefined, options),
  );
}

export async function uploadAttachment(
  observationId: string,
  file: Blob,
  filename = "screenshot.png",
  options: FetchOptions = {},
): Promise<ObservationAttachment> {
  const form = new FormData();
  form.append("file", file, filename);
  return json(
    await portalFetch(`/observations/${observationId}/attachments/`, {
      method: "POST",
      body: form,
      signal: options.signal,
    }),
  );
}

export async function deleteAttachment(
  attachmentId: string,
  options: FetchOptions = {},
): Promise<void> {
  await portalFetch(
    `/attachments/${attachmentId}/`,
    body("DELETE", undefined, options),
  );
}

/** Same-origin URL that serves a participant's own attachment (cookie auth). */
export function portalAttachmentUrl(attachmentId: string): string {
  return `${PORTAL_BASE}/attachments/${attachmentId}/`;
}

// --- finding review & source -------------------------------------------------------

export async function setFindingVerdict(
  assignmentId: string,
  findingId: string,
  input: FindingVerdictInput,
  options: FetchOptions = {},
): Promise<{
  finding_key: string;
  verdict: FindingVerdictValue;
  note: string;
}> {
  return json(
    await portalFetch(
      `/assignments/${assignmentId}/finding-verdicts/${findingId}/`,
      body("PUT", input, options),
    ),
  );
}

export async function getSourceTree(
  assignmentId: string,
  slot: Slot,
  options: FetchOptions = {},
): Promise<SourceTree> {
  return json(
    await portalFetch(`/assignments/${assignmentId}/source/?slot=${slot}`, {
      signal: options.signal,
    }),
  );
}

export async function getSourceFile(
  assignmentId: string,
  slot: Slot,
  path: string,
  options: FetchOptions = {},
): Promise<SourceFile> {
  const q = new URLSearchParams({ slot, path });
  return json(
    await portalFetch(`/assignments/${assignmentId}/source/file/?${q}`, {
      signal: options.signal,
    }),
  );
}

/** Statuses after which an assignment no longer accepts edits. */
export const CLOSED_ASSIGNMENT_STATUSES: AssignmentStatus[] = [
  "submitted",
  "skipped",
  "expired",
];
