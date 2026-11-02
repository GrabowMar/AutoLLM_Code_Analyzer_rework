// App hosting as the participant sees it: whether an app URL may be framed
// (spec R2 "Iframe"), when to keep polling, and state labels.
import type { Tone } from "$lib/constants/colors";
import type { HostingInfo, HostingState } from "$lib/api/types/human-eval";

/** Poll hosting this often while an app is not running yet. */
export const HOSTING_POLL_MS = 3000;

/** No top navigation; everything else a generated web app needs. */
export const IFRAME_SANDBOX =
  "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads";

/** Returns the URL when it is an absolute http(s) URL, else null. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export interface FrameDecision {
  /** May be shown in the sandboxed iframe. */
  canFrame: boolean;
  /** May be offered as an "open in new tab" link. */
  canOpen: boolean;
  /** The URL to use (normalized), or null. */
  url: string | null;
  /** Why it is not framed (empty when framed or there is no URL). */
  reason: string;
}

/**
 * Frame only https URLs on another origin than the portal: a same-origin app
 * with allow-scripts + allow-same-origin could reach the portal's cookies and
 * API. Anything else (plain http, same origin) only opens in a new tab.
 */
export function frameDecision(
  rawUrl: string | null | undefined,
  pageOrigin: string,
): FrameDecision {
  const url = safeHttpUrl(rawUrl);
  if (!url) return { canFrame: false, canOpen: false, url: null, reason: "" };
  const parsed = new URL(url);
  if (parsed.protocol !== "https:") {
    return {
      canFrame: false,
      canOpen: true,
      url,
      reason:
        "This app is served over plain HTTP, so it can only open in a new tab.",
    };
  }
  if (parsed.origin === pageOrigin) {
    return {
      canFrame: false,
      canOpen: true,
      url,
      reason:
        "This app runs on the portal's own address, so it can only open in a new tab.",
    };
  }
  return { canFrame: true, canOpen: true, url, reason: "" };
}

/**
 * Not running yet and still worth asking about: everything but running, failed
 * and unavailable. The workbench asks once (ensure) for these when it opens.
 */
export function shouldPollHosting(
  state: HostingState | null | undefined,
): boolean {
  return state !== "running" && state !== "failed" && state !== "unavailable";
}

/**
 * States the workbench keeps polling (ensure every few seconds, visible tab
 * only): the app is on its way, or on its way down. An `idle` app is not
 * started again by the poll — the participant presses Start — so a tab left
 * open does not keep restarting a stopped app or keep its assignment alive
 * (R3.11).
 */
export function shouldAutoEnsure(
  state: HostingState | null | undefined,
): boolean {
  return (
    state === "queued" ||
    state === "building" ||
    state === "starting" ||
    state === "stopping"
  );
}

/** Slots one poll tick asks about: none while the tab is hidden. */
export function slotsToPoll<S extends string>(
  slots: readonly S[],
  hosting: Partial<Record<string, HostingInfo>>,
  visible: boolean,
): S[] {
  if (!visible) return [];
  return slots.filter((slot) => shouldAutoEnsure(hosting[slot]?.state));
}

/** States in which the participant can ask for a (re)start. */
export function canRequestStart(
  state: HostingState | null | undefined,
): boolean {
  return state === "idle" || state === "failed";
}

/** One word each: StatusBadge capitalizes every word. */
export const HOSTING_LABELS: Record<HostingState, string> = {
  idle: "Stopped",
  queued: "Queued",
  building: "Building",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  failed: "Failed",
  unavailable: "Unavailable",
};

/** StatusBadge tone overrides for hosting states. */
export const hostingTones: Record<HostingState, Tone> = {
  idle: "neutral",
  queued: "neutral",
  building: "info",
  starting: "info",
  running: "success",
  stopping: "warning",
  failed: "danger",
  unavailable: "warning",
};

/** A placeholder used before the first hosting answer arrives. */
export function idleHosting(message = ""): HostingInfo {
  return { state: "idle", url: null, progress: null, message, error: "" };
}

/** Default one-line explanation when the server sends no message. */
export function hostingSentence(info: HostingInfo): string {
  if (info.message) return info.message;
  switch (info.state) {
    case "queued":
      return "Waiting for a free app slot. It starts automatically.";
    case "building":
      return "Building the app. This can take a few minutes the first time.";
    case "starting":
      return "Starting the app…";
    case "stopping":
      return "The app is stopping. Once it has stopped, you can start it again.";
    case "failed":
      return info.error || "The app could not be started.";
    case "unavailable":
      return "Apps cannot be opened from the portal right now.";
    case "running":
      return "";
    default:
      return "The app is not running.";
  }
}
