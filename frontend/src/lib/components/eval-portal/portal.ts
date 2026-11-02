// Pure helpers for the participant portal shell and flows: access-code
// formatting, error and status wording, progress numbers and portal paths.
import { apiErrorStatus, formatApiError } from "$lib/api/core";
import type {
  NextReason,
  ParticipantStatus,
  PortalParticipation,
  PortalTask,
  Question,
  QuestionAnswer,
} from "$lib/api/types/human-eval";
import type { Tone } from "$lib/constants/colors";
import { questionsComplete } from "./questions";

// --- paths -------------------------------------------------------------------

export const PORTAL_HOME = "/eval";

export function taskPath(taskId: string): string {
  return `/eval/t/${encodeURIComponent(taskId)}`;
}

export function workbenchPath(taskId: string, assignmentId: string): string {
  return `${taskPath(taskId)}/a/${encodeURIComponent(assignmentId)}`;
}

/** Researcher view of a task (shown to researchers previewing it). */
export function researcherTaskPath(taskId: string): string {
  return `/human-eval/${encodeURIComponent(taskId)}`;
}

// --- access codes ------------------------------------------------------------

/**
 * Access codes as backend/human_eval/services/access.py issues them
 * (CODE_ALPHABET, ACCESS_CODE_LENGTH): 12 characters from an alphabet without
 * look-alikes, shown in groups of four. Failed resume attempts count against a
 * throttle that a whole classroom may share, so input that cannot be a code is
 * stopped here instead of being sent.
 */
export const ACCESS_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"; // gitleaks:allow (public character set, not a secret)
export const ACCESS_CODE_LENGTH = 12;

/** Code characters of a typed access code: upper case, separators and punctuation dropped. */
export function normalizeAccessCode(raw: string): string {
  return (raw ?? "").toUpperCase().replace(/[^0-9A-Z]/g, "");
}

/** Groups of four for reading and copying, e.g. "k7qm4xt9bw2r" → "K7QM-4XT9-BW2R". */
export function formatAccessCode(raw: string): string {
  const n = normalizeAccessCode(raw);
  return n.match(/.{1,4}/g)?.join("-") ?? "";
}

/** The code split into groups of four (for the large resume-code display). */
export function accessCodeGroups(raw: string): string[] {
  return normalizeAccessCode(raw).match(/.{1,4}/g) ?? [];
}

/** As-you-type value of the access-code input (normalized, grouped, capped). */
export function formatAccessCodeInput(
  raw: string,
  maxChars = ACCESS_CODE_LENGTH,
): string {
  return formatAccessCode(normalizeAccessCode(raw).slice(0, maxChars));
}

/**
 * Caret position in `formatted` that sits after the same number of code
 * characters as `caret` did in `raw` (keeps the caret in place while the input
 * reformats itself).
 */
export function caretAfterFormat(
  raw: string,
  caret: number,
  formatted: string,
): number {
  const wanted = normalizeAccessCode(raw.slice(0, Math.max(0, caret))).length;
  if (wanted === 0) return 0;
  let seen = 0;
  for (let i = 0; i < formatted.length; i++) {
    if (formatted[i] !== "-") seen += 1;
    if (seen === wanted) return i + 1;
  }
  return formatted.length;
}

/** Characters of the input that never occur in a code (e.g. O, 0, I, 1), in order, once each. */
export function invalidAccessCodeChars(raw: string): string[] {
  const out: string[] = [];
  for (const ch of normalizeAccessCode(raw)) {
    if (!ACCESS_CODE_ALPHABET.includes(ch) && !out.includes(ch)) out.push(ch);
  }
  return out;
}

/** Why the input cannot be an access code, or "" when it is worth sending. */
export function accessCodeProblem(raw: string): string {
  const invalid = invalidAccessCodeChars(raw);
  if (invalid.length) {
    return `Codes never contain ${invalid.join(", ")}. They use the digits 2–9 and the letters A–Z except I, L, O and U.`;
  }
  const n = normalizeAccessCode(raw).length;
  if (n === 0) return "Enter your access code.";
  if (n !== ACCESS_CODE_LENGTH) {
    return `Access codes have ${ACCESS_CODE_LENGTH} characters; this has ${n}.`;
  }
  return "";
}

/** True when the input is shaped like an access code. */
export function isPlausibleAccessCode(raw: string): boolean {
  return accessCodeProblem(raw) === "";
}

// --- errors --------------------------------------------------------------------

export const RATE_LIMIT_MESSAGE =
  "Too many attempts. Wait a few minutes, then try again.";
export const SESSION_ENDED_MESSAGE =
  "Your session has ended. Enter your access code again (or sign in) to continue.";
export const CSRF_MESSAGE =
  "Your browser did not send the security cookie this page needs. Allow cookies for this site and reload the page.";

/** `code` of a structured portal error body ({detail, code, errors}), or "". */
export function errorCode(error: unknown): string {
  if (!error || typeof error !== "object") return "";
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : "";
}

function isCsrfFailure(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const detail = (error as { detail?: unknown }).detail;
  return typeof detail === "string" && /csrf/i.test(detail);
}

/** Participant-facing message for anything the portal client throws. */
export function portalErrorMessage(error: unknown, fallback: string): string {
  const status = apiErrorStatus(error);
  if (status === 429) return RATE_LIMIT_MESSAGE;
  if (status === 401) return SESSION_ENDED_MESSAGE;
  if (status === 403 && isCsrfFailure(error)) return CSRF_MESSAGE;
  return formatApiError(error, fallback);
}

// --- join availability -----------------------------------------------------------

export interface Notice {
  title: string;
  description: string;
}

/** Why an invite link cannot be used right now (JoinInfo.availability). */
export function availabilityNotice(availability: string): Notice | null {
  switch (availability) {
    case "open":
      return null;
    case "not_open":
      return {
        title: "This evaluation has not started yet",
        description:
          "The link is valid, but the researcher has not opened the evaluation. Try again later.",
      };
    case "closed":
      return {
        title: "This evaluation has ended",
        description: "It no longer accepts new participants.",
      };
    case "full":
      return {
        title: "This invite link is full",
        description:
          "It has been used as many times as the researcher allowed. Ask them for a new link.",
      };
    case "expired":
      return {
        title: "This invite link has expired",
        description: "Ask the researcher for a new link.",
      };
    case "inactive":
      return {
        title: "This invite link is no longer active",
        description: "The researcher switched it off. Ask them for a new link.",
      };
    default:
      return {
        title: "This invite link cannot be used right now",
        description: "Ask the researcher for a new link.",
      };
  }
}

// --- next item -------------------------------------------------------------------

/** What to tell a participant when `next/` hands out nothing. */
export function nextReasonNotice(reason: NextReason): Notice | null {
  switch (reason) {
    case "assigned":
    case "resumed":
      return null;
    case "done":
      return {
        title: "You have evaluated every app available to you",
        description: "Finish the evaluation below to get your completion code.",
      };
    case "limit_reached":
      return {
        title: "You have reached the number of apps for this evaluation",
        description: "Finish the evaluation below to get your completion code.",
      };
    case "no_items_available":
      return {
        title: "No app is free right now",
        description:
          "Other participants are working on the remaining apps. Try again in a few minutes, or finish now if you are done.",
      };
    case "not_accepting":
      return {
        title: "The evaluation is not accepting work right now",
        description: "It is paused or has closed. Your saved work is kept.",
      };
    case "consent_required":
      return {
        title: "Consent needed first",
        description:
          "Read the study information and give your consent before you start.",
      };
    case "pre_questions_required":
      return {
        title: "A few questions first",
        description: "Answer the questions about yourself before you start.",
      };
    default:
      return {
        title: "No app could be assigned",
        description: "Try again in a moment.",
      };
  }
}

// --- progress --------------------------------------------------------------------

export interface ProgressNumbers {
  /** submitted + skipped */
  done: number;
  submitted: number;
  skipped: number;
  /** best estimate of how many apps this participant will do; null if unknown */
  total: number | null;
  /** done / total in 0..1, or null */
  fraction: number | null;
}

/**
 * Progress for the task page. `total` is the per-participant limit, capped by
 * what is still available (done + current + remaining_available).
 */
export function progressNumbers(
  progress: PortalTask["progress"],
  hasCurrent: boolean,
): ProgressNumbers {
  const submitted = Math.max(0, progress.submitted ?? 0);
  const skipped = Math.max(0, progress.skipped ?? 0);
  const done = submitted + skipped;
  const reachable =
    done +
    (hasCurrent ? 1 : 0) +
    Math.max(0, progress.remaining_available ?? 0);
  let total: number | null = reachable;
  if (progress.limit != null) total = Math.min(progress.limit, reachable);
  total = Math.max(total, done);
  const fraction = total > 0 ? Math.min(1, done / total) : null;
  return { done, submitted, skipped, total, fraction };
}

/** "3 of 10 apps" / "3 apps" for a portal home card. */
export function participationProgressText(p: PortalParticipation): string {
  const noun = (n: number) => (n === 1 ? "app" : "apps");
  if (p.target != null && p.target > 0) {
    return `${p.submitted} of ${p.target} ${noun(p.target)} evaluated`;
  }
  return `${p.submitted} ${noun(p.submitted)} evaluated`;
}

/** 0..1 for a home card meter, or null when the target is unknown. */
export function participationFraction(p: PortalParticipation): number | null {
  if (p.target == null || p.target <= 0) return null;
  return Math.min(1, Math.max(0, p.submitted / p.target));
}

// --- task page steps ---------------------------------------------------------------

/**
 * The "about you" step is done: there are no pre-questions, the participant
 * already started, or the answers were saved and every required one is given.
 * "Saved" is `savedNow` (saved during this visit) or stored answers: the server
 * drops blank answers, so an all-optional questionnaire left blank is stored
 * as {} — yet the server's gate (required questions only) lets it through.
 */
export function preStepDone(state: {
  questions: Question[];
  answers: Record<string, QuestionAnswer> | null | undefined;
  started: boolean;
  savedNow: boolean;
}): boolean {
  if (state.questions.length === 0 || state.started) return true;
  const answers = state.answers ?? {};
  const saved = state.savedNow || Object.keys(answers).length > 0;
  return saved && questionsComplete(state.questions, answers);
}

export interface FinishGate {
  disabled: boolean;
  /** One sentence next to the Finish button. */
  hint: string;
}

const RELEASE_TEXT =
  "If you finish now, the app you were given is released, as long as you have not saved anything on it. If you have, submit or skip it first.";

/**
 * Whether Finish can be pressed and what to say next to it. An open
 * assignment does not block it: finishing releases an app with no saved work,
 * and the server refuses (409 assignment_open) only when work was saved.
 */
export function finishGate(state: {
  stepsPending: boolean;
  hasCurrent: boolean;
  prominent: boolean;
}): FinishGate {
  if (state.stepsPending)
    return { disabled: true, hint: "Complete the steps above first." };
  if (state.hasCurrent) return { disabled: false, hint: RELEASE_TEXT };
  if (state.prominent) {
    return {
      disabled: false,
      hint: "You are done with the apps. Finish to get your completion code.",
    };
  }
  return {
    disabled: false,
    hint: "Finish when you do not want to evaluate more apps. You get a completion code, and cannot start new apps afterwards.",
  };
}

/** Text of the "Finish the evaluation?" confirmation. */
export function finishConfirmText(state: {
  started: boolean;
  workLeft: boolean;
  hasCurrent: boolean;
}): string {
  if (state.hasCurrent) {
    return `${RELEASE_TEXT} After finishing you cannot start new apps; you get your completion code.`;
  }
  if (!state.started) {
    return "You have not evaluated any app yet. After finishing you cannot start new ones; you get your completion code.";
  }
  if (state.workLeft) {
    return "There are still apps you could evaluate. After finishing you cannot start new ones; you get your completion code.";
  }
  return "You get your completion code next. After finishing you cannot start new apps.";
}

// --- statuses ----------------------------------------------------------------------

/** StatusBadge tones for portal statuses (task and participant). */
export const PORTAL_TONES: Partial<Record<string, Tone>> = {
  draft: "neutral",
  open: "success",
  paused: "warning",
  closed: "neutral",
  archived: "neutral",
  invited: "neutral",
  active: "info",
  completed: "success",
  withdrawn: "neutral",
};

const PARTICIPANT_STATUS_TEXT: Record<ParticipantStatus, string> = {
  invited: "Not started",
  active: "In progress",
  completed: "Completed",
  withdrawn: "Withdrawn",
};

export function participantStatusText(status: ParticipantStatus): string {
  return PARTICIPANT_STATUS_TEXT[status] ?? status;
}

// --- dates -------------------------------------------------------------------------

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

// --- blocks --------------------------------------------------------------------------

const BLOCK_TYPE_TEXT: Record<string, string> = {
  rating: "Rate the app",
  requirements: "Check the requirements",
  security: "Security testing",
  scenarios: "Usability tasks",
  questionnaire: "Questionnaire",
  bugs: "Report bugs",
  pairwise: "Compare two apps",
};

/** What a block asks of the participant, in a few words. */
export function blockTypeText(type: string): string {
  return BLOCK_TYPE_TEXT[type] ?? type.replace(/_/g, " ");
}
