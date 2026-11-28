// Keeps every open tab on the workspace the session works in.
//
// The workspace is chosen per session (the server keeps it in the session
// cookie), so a switch in one tab changes the identity of every other tab of
// the same browser at once, while their pages and banners still show the old
// one. Two lines of defence:
//
// 1. The tab that changes the workspace announces it (BroadcastChannel, with a
//    localStorage write as fallback); the other tabs reload under the new
//    identity (`onWorkspaceChange`).
// 2. Every changing request carries `X-Workspace: <coauthorship id | own>`, the
//    workspace this tab shows (`workspaceClaim`). The server refuses a request
//    whose claim differs from the session's choice with 409
//    `workspace_changed`, and apiFetch reloads the tab. This covers a missed
//    message (another browser profile, a sleeping tab, blocked storage).
//
// This module has no imports, so `core.ts` can use it without a cycle.

const CHANNEL_NAME = "coauthor-workspace";
const STORAGE_KEY = "coauthor.workspace.changed";
/** The value of the `X-Workspace` header for the person's own account. */
export const OWN_WORKSPACE = "own";

let channel: BroadcastChannel | null | undefined;
let claim: string | null = null;

function getChannel(): BroadcastChannel | null {
  if (channel !== undefined) return channel;
  try {
    channel =
      typeof BroadcastChannel === "undefined"
        ? null
        : new BroadcastChannel(CHANNEL_NAME);
    // Outside a browser (Node test runs) an open channel would keep the process alive.
    (channel as { unref?: () => void } | null)?.unref?.();
  } catch {
    channel = null;
  }
  return channel;
}

/** The workspace this tab shows, sent as `X-Workspace` on changing requests; `null` sends nothing. */
export function workspaceClaim(): string | null {
  return claim;
}

/** Set by the workspace store once it knows which workspace this tab shows (`null` while unknown). */
export function setWorkspaceClaim(coauthorshipId: number | null | undefined) {
  claim =
    coauthorshipId === undefined
      ? null
      : coauthorshipId === null
        ? OWN_WORKSPACE
        : String(coauthorshipId);
}

/** Tell the other tabs that this session now works in another workspace. Never throws. */
export function announceWorkspaceChange() {
  try {
    getChannel()?.postMessage({ type: "changed", at: Date.now() });
  } catch {
    // closed channel: the storage event below still reaches the other tabs
  }
  try {
    localStorage.setItem(STORAGE_KEY, String(Date.now()));
  } catch {
    // blocked storage: the X-Workspace check still catches a stale tab
  }
}

/** Call `handler` when another tab announces a workspace change; returns the unsubscribe function. */
export function onWorkspaceChange(handler: () => void): () => void {
  const ch = getChannel();
  // Both transports usually deliver the same change: react once.
  let fired = false;
  const fire = () => {
    if (fired) return;
    fired = true;
    handler();
  };
  const onMessage = () => fire();
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) fire();
  };
  ch?.addEventListener("message", onMessage);
  const win = typeof window !== "undefined" ? window : null;
  win?.addEventListener?.("storage", onStorage);
  return () => {
    ch?.removeEventListener("message", onMessage);
    win?.removeEventListener?.("storage", onStorage);
  };
}

/** Load the current page again, so it runs as the session's current identity. */
export function reloadHere() {
  const { pathname, search } = window.location;
  window.location.assign(`${pathname || "/"}${search || ""}`);
}
