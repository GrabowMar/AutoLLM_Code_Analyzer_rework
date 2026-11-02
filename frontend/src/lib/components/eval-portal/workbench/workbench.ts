// Shared workbench helpers: slot ordering, paths, labels and error messages.
import { apiErrorStatus, formatApiError } from "$lib/api/core";
import type {
  AssignmentStatus,
  HostingInfo,
  NextReason,
  PortalApp,
  SkipReason,
} from "$lib/api/types/human-eval";

export type Slot = "A" | "B" | "single";

const SLOT_RANK: Record<string, number> = { A: 0, B: 1, single: 2 };

/** Apps in display order: A, then B (the server already decided the sides). */
export function orderedApps(apps: PortalApp[]): PortalApp[] {
  return [...apps].sort(
    (a, b) => (SLOT_RANK[a.slot] ?? 9) - (SLOT_RANK[b.slot] ?? 9),
  );
}

/** "A · App 07" in pairwise mode, the label alone for a single app. */
export function slotTitle(app: Pick<PortalApp, "slot" | "label">): string {
  return app.slot === "A" || app.slot === "B"
    ? `${app.slot} · ${app.label}`
    : app.label;
}

export function taskHref(taskId: string): string {
  return `/eval/t/${encodeURIComponent(taskId)}`;
}

export function workbenchHref(taskId: string, assignmentId: string): string {
  return `${taskHref(taskId)}/a/${encodeURIComponent(assignmentId)}`;
}

/** `code` of a structured error body ({detail, code, errors}), or "". */
export function errorCode(error: unknown): string {
  if (!error || typeof error !== "object") return "";
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : "";
}

/** `errors` of a structured error body. */
export function errorItems(
  error: unknown,
): { path: string; message: string }[] {
  if (!error || typeof error !== "object") return [];
  const list = (error as { errors?: unknown }).errors;
  if (!Array.isArray(list)) return [];
  return list
    .filter(
      (e): e is { path?: unknown; message?: unknown } =>
        !!e && typeof e === "object",
    )
    .map((e) => ({
      path: typeof e.path === "string" ? e.path : "",
      message: typeof e.message === "string" ? e.message : "",
    }))
    .filter((e) => e.message);
}

/** The assignment can no longer be changed (submitted, expired, task paused…). */
export function isReadOnlyError(error: unknown): boolean {
  return apiErrorStatus(error) === 409 && errorCode(error) === "read_only";
}

/** Participant-facing message for anything the portal client throws. */
export function workbenchError(error: unknown, fallback: string): string {
  const status = apiErrorStatus(error);
  if (status === 401) {
    return "Your session has ended. Enter your access code again (or sign in) to continue.";
  }
  if (status === 403) {
    const detail = (error as { detail?: unknown })?.detail;
    if (typeof detail === "string" && /csrf/i.test(detail)) {
      return "Your browser did not send the security cookie this page needs. Allow cookies for this site and reload the page.";
    }
  }
  if (status === 429) {
    return formatApiError(
      error,
      "Too many requests. Wait a moment, then try again.",
    );
  }
  if (errorCode(error) === "quota_exceeded") {
    return formatApiError(error, "You reached the limit for this assignment.");
  }
  return formatApiError(error, fallback);
}

export const STATUS_TEXT: Record<AssignmentStatus, string> = {
  assigned: "Not started",
  in_progress: "In progress",
  submitted: "Submitted",
  skipped: "Skipped",
  expired: "Expired",
};

/** Why the assignment is read-only, for the banner. */
export function readOnlyText(status: AssignmentStatus): string {
  switch (status) {
    case "submitted":
      return "You submitted this app. Your answers are shown read-only.";
    case "skipped":
      return "You skipped this app. Your answers are shown read-only.";
    case "expired":
      return "This assignment expired after a long time without activity. Your saved answers are kept; open the study page to continue it if it is still available to you.";
    default:
      return "The study is not accepting answers right now (paused or closed). Your saved answers are shown read-only.";
  }
}

export interface SkipReasonOption {
  value: SkipReason;
  label: string;
  description: string;
}

/**
 * app_broken is about the app itself (a build or start failure is the model's
 * outcome); platform_unavailable about hosting: queue, platform, portal (R1).
 */
export const SKIP_REASONS: SkipReasonOption[] = [
  {
    value: "app_broken",
    label: "The app itself is broken",
    description:
      "It fails to build or start, or it opens but crashes, shows an error or a blank page, or its main pages don't work.",
  },
  {
    value: "platform_unavailable",
    label: "The platform could not show the app",
    description:
      "It stayed in the queue, the hosting platform was unavailable, or the portal could not show it.",
  },
  {
    value: "cannot_evaluate",
    label: "I can't evaluate this app",
    description:
      "For example, the task needs knowledge or tools you don't have.",
  },
  {
    value: "conflict_of_interest",
    label: "Conflict of interest",
    description: "For example, you worked on this app or know who made it.",
  },
  {
    value: "other",
    label: "Something else",
    description: "Say what happened in the note.",
  },
];

/**
 * The participant-facing error the server sends for a `failed` app when the
 * hosting platform itself (Docker) failed; every other failure is the app's
 * own (hosting.PARTICIPANT_ERROR_PLATFORM / PARTICIPANT_ERROR_APP).
 */
export const PLATFORM_FAILURE_ERROR =
  "The hosting platform is unavailable right now.";

/**
 * Reason the skip dialog starts with, from the apps' hosting states: an app
 * that failed to build or start is broken (R1: the model's outcome, counted as
 * unevaluable); queued, unavailable or a platform failure is the platform's
 * problem; anything else starts with no reason picked.
 */
export function defaultSkipReason(
  slots: readonly string[],
  hosting: Partial<Record<string, Pick<HostingInfo, "state" | "error">>>,
): SkipReason | "" {
  const infos = slots.map((slot) => hosting[slot]).filter((h) => !!h);
  const platformFailure = (h: Pick<HostingInfo, "state" | "error">) =>
    h.state === "failed" && (h.error ?? "").trim() === PLATFORM_FAILURE_ERROR;
  if (infos.some((h) => h.state === "failed" && !platformFailure(h)))
    return "app_broken";
  if (
    infos.some(
      (h) =>
        h.state === "queued" || h.state === "unavailable" || platformFailure(h),
    )
  ) {
    return "platform_unavailable";
  }
  return "";
}

/** Sentence shown after submit/skip when no next app was handed out. */
export function afterSubmitText(reason: NextReason | undefined): string {
  switch (reason) {
    case "done":
      return "You have evaluated every app available to you.";
    case "limit_reached":
      return "You reached the number of apps for this study.";
    case "no_items_available":
      return "No other app is available right now. Check back later.";
    case "not_accepting":
      return "The study is not accepting new work right now.";
    default:
      return "";
  }
}

/** HH:MM of a timestamp in the participant's locale. */
export function clockTime(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** mm:ss (or h:mm:ss) for a number of seconds. */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0");
  const ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Read a localStorage value; null when storage is unavailable. */
export function readStorage(key: string): string | null {
  try {
    return typeof localStorage === "undefined"
      ? null
      : localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Write (or remove, with null) a localStorage value; failures are ignored. */
export function writeStorage(key: string, value: string | null): void {
  try {
    if (typeof localStorage === "undefined") return;
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Private mode / quota: the value simply isn't remembered.
  }
}

/** Prefix of everything the HTTP console keeps in this browser. */
export const CONSOLE_STORAGE_PREFIX = "heval.console.";

/** localStorage key of the console's per-assignment data (`history`, `auth`, `rememberAuth`). */
export function consoleStorageKey(
  kind: "history" | "auth" | "rememberAuth",
  assignmentId: string,
): string {
  return `${CONSOLE_STORAGE_PREFIX}${kind}.${assignmentId}`;
}

type KeyedStorage = Pick<Storage, "length" | "key" | "removeItem">;

function browserStorage(): KeyedStorage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/**
 * Forget the HTTP console's history, remembered Authorization headers and
 * opt-ins in this browser (sign out, leave, withdraw), so the next person on a
 * shared computer finds none of it. Failures are ignored.
 */
export function clearConsoleStorage(
  storage: KeyedStorage | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    for (let i = storage.length - 1; i >= 0; i--) {
      const key = storage.key(i);
      if (key?.startsWith(CONSOLE_STORAGE_PREFIX)) storage.removeItem(key);
    }
  } catch {
    // Storage blocked: nothing was stored either.
  }
}

/**
 * Send with `keepalive` while the page is being hidden or closed, so the
 * request outlives the document. Browsers cap keepalive bodies (about 64 KiB
 * in flight) and reject over the cap with a TypeError: then it is sent again
 * as a plain request.
 */
export async function sendMaybeKeepalive<R>(
  leaving: boolean,
  send: (options: { keepalive?: boolean }) => Promise<R>,
): Promise<R> {
  if (!leaving) return send({});
  try {
    return await send({ keepalive: true });
  } catch (error) {
    if (error instanceof TypeError) return send({});
    throw error;
  }
}
