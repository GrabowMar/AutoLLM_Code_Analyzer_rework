// An invite token kept in this browser while its holder signs up, verifies
// their e-mail and logs in, so the join can continue afterwards. Kept 24 hours
// at most and cleared on every outcome of the join page. Every storage access
// is guarded: private windows and blocked storage simply forget the invite.

const KEY = "coauthor.pendingInvite";
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** `coi_` + 43 URL-safe base64 characters (32 random bytes). */
export const INVITE_TOKEN_RE = /^coi_[A-Za-z0-9_-]{43}$/;

export function isInviteToken(value: unknown): value is string {
  return typeof value === "string" && INVITE_TOKEN_RE.test(value);
}

export function rememberInvite(token: string, now: number = Date.now()): void {
  if (!isInviteToken(token)) return;
  try {
    localStorage.setItem(KEY, JSON.stringify({ t: token, at: now }));
  } catch {
    // blocked storage
  }
}

export function clearInvite(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // blocked storage
  }
}

/** The kept token, or null (nothing kept, malformed, or older than 24 hours: then it is removed). */
export function pendingInvite(now: number = Date.now()): string | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      const { t, at } = parsed as { t?: unknown; at?: unknown };
      if (
        isInviteToken(t) &&
        typeof at === "number" &&
        Number.isFinite(at) &&
        at <= now &&
        now - at < MAX_AGE_MS
      ) {
        return t;
      }
    }
  } catch {
    // not JSON: drop it below
  }
  clearInvite();
  return null;
}

/** The token from a `#coi_…` fragment, or null for anything else. */
export function inviteFromHash(hash: string): string | null {
  const value = hash.startsWith("#") ? hash.slice(1) : hash;
  return isInviteToken(value) ? value : null;
}

/** True when this page is shown inside a frame (or the top window cannot be read). */
export function isFramed(): boolean {
  try {
    return window.top !== window.self;
  } catch {
    return true;
  }
}
