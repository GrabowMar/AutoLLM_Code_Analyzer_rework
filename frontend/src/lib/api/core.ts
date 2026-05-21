export const ALLAUTH_BASE = "/_allauth/browser/v1";
const API_BASE = "/api";

const CSRF_COOKIE_NAMES = ["csrftoken", "__Secure-csrftoken"] as const;

function getCookieValue(name: string): string {
  if (typeof document === "undefined") return "";
  const cookies = document.cookie.split(";");
  for (const cookie of cookies) {
    const trimmed = cookie.trim();
    if (trimmed.startsWith(`${name}=`)) {
      return decodeURIComponent(trimmed.slice(name.length + 1));
    }
  }
  return "";
}

/** Read CSRF token from document cookies (local + production cookie names). */
export function getCsrfToken(): string {
  for (const name of CSRF_COOKIE_NAMES) {
    const value = getCookieValue(name);
    if (value) return value;
  }
  return "";
}

let csrfBootstrap: Promise<void> | null = null;

/** Fetch allauth config so Django sets the CSRF cookie before mutating requests. */
export async function ensureCsrfCookie(): Promise<void> {
  if (typeof document === "undefined") return;
  if (getCsrfToken()) return;

  if (csrfBootstrap) return csrfBootstrap;

  csrfBootstrap = (async () => {
    const res = await fetch(`${ALLAUTH_BASE}/config`, {
      credentials: "include",
    });
    if (!res.ok && import.meta.env.DEV) {
      console.warn(`allauth config returned HTTP ${res.status}`);
    }
    if (!getCsrfToken()) {
      throw new Error(
        "Could not load security token (CSRF). Enable cookies and open the app at the same host as in your email (e.g. http://localhost:8000, not 127.0.0.1).",
      );
    }
  })();

  try {
    await csrfBootstrap;
  } finally {
    csrfBootstrap = null;
  }
}

/** django-allauth returns 401 with login/signup flows after successful password reset / verify. */
export function hasAllauthPendingFlows(body: Record<string, unknown>): boolean {
  const data = body.data;
  if (!data || typeof data !== "object") return false;
  const flows = (data as { flows?: unknown }).flows;
  return Array.isArray(flows) && flows.length > 0;
}

export function isAllauthMutationSuccess(
  res: Response,
  body: Record<string, unknown>,
): boolean {
  if (res.ok) return true;
  if (res.status === 401 && hasAllauthPendingFlows(body)) return true;
  return false;
}

export async function parseAllauthJson(
  res: Response,
): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export type AllauthFlow = { id: string; is_pending?: boolean };

export type ParsedAllauthAuth = {
  ok: boolean;
  user?: Record<string, unknown>;
  pendingFlow?: string;
  error?: string;
};

/** Parse django-allauth headless login/signup-style responses (including 401 + flows). */
export function parseAllauthAuthResponse(
  res: Response,
  body: Record<string, unknown>,
  errorFallback: string,
): ParsedAllauthAuth {
  if (body.data && typeof body.data === "object") {
    const data = body.data as {
      user?: Record<string, unknown>;
      flows?: AllauthFlow[];
    };
    if (res.ok && data.user) {
      return { ok: true, user: data.user };
    }
    const pending = data.flows?.find((f) => f.is_pending);
    if (pending) {
      return { ok: false, pendingFlow: pending.id };
    }
  }
  return {
    ok: false,
    error: formatApiError(body, errorFallback),
  };
}

function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") return true;
  if (error instanceof Error && /aborted/i.test(error.message)) return true;
  return false;
}

export function formatApiError(
  error: unknown,
  fallback = "Request failed.",
): string {
  if (isAbortError(error)) {
    return "The request timed out. Check that the backend is running (Docker: django service) and reload the page.";
  }
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  if (!error || typeof error !== "object") return fallback;

  const body = error as Record<string, unknown>;
  const detail = body.detail;
  const remediation =
    typeof body.remediation === "string" ? body.remediation : "";
  let message = "";

  if (typeof detail === "string") {
    message = detail;
  } else if (detail && typeof detail === "object") {
    const nested = detail as Record<string, unknown>;
    message = typeof nested.detail === "string" ? nested.detail : "";
  } else if (typeof body.message === "string") {
    message = body.message;
  } else if (Array.isArray(body.errors)) {
    message = body.errors
      .map((item) =>
        item && typeof item === "object"
          ? (item as Record<string, unknown>).message
          : item,
      )
      .filter(
        (item): item is string => typeof item === "string" && item.length > 0,
      )
      .join(". ");
  }

  return [message || fallback, remediation].filter(Boolean).join(" ");
}

export async function allauthFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const method = (options.method || "GET").toUpperCase();
  await ensureCsrfCookie();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (method !== "GET" && method !== "HEAD") {
    const csrf = getCsrfToken();
    if (!csrf) {
      throw new Error(
        "Missing CSRF token. Hard-refresh the page (Ctrl+F5) and try again.",
      );
    }
    headers["X-CSRFToken"] = csrf;
  }
  const res = await fetch(`${ALLAUTH_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  return res;
}

export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    headers["X-CSRFToken"] = getCsrfToken();
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  if (!res.ok) {
    if (res.status === 401) {
      window.location.href = "/auth/login";
      return new Promise<Response>(() => {});
    }
    const body = await res.json().catch(() => ({}));
    throw body;
  }
  return res;
}
