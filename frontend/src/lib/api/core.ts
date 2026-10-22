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

/** True for an aborted fetch (timeouts, or a superseded request cancelled via AbortController). */
export function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") return true;
  if (error instanceof Error && /aborted/i.test(error.message)) return true;
  return false;
}

/** Request locations Ninja/Pydantic put first in a validation error `loc`. */
const ERROR_LOCATIONS = new Set([
  "body",
  "query",
  "path",
  "header",
  "cookie",
  "form",
  "file",
]);

/** `["body", "payload", "llm_params", "temperature"]` → `"llm_params.temperature"`. */
function validationFieldPath(loc: unknown): string {
  if (!Array.isArray(loc)) return "";
  const parts = loc.map((part) => String(part));
  const location = parts[0];
  if (location !== undefined && ERROR_LOCATIONS.has(location)) {
    parts.shift();
    // Ninja nests body fields under the view's argument name ("payload", "data").
    if (location === "body" && parts.length > 0) parts.shift();
  }
  return parts.join(".");
}

function validationMessage(msg: unknown): string {
  return typeof msg === "string" ? msg.replace(/^Value error, /, "") : "";
}

/** Ninja 422 `detail: [{loc, msg}]` entries as `{field, message}` pairs. */
function validationErrors(
  detail: unknown,
): { field: string; message: string }[] {
  if (!Array.isArray(detail)) return [];
  return detail
    .map((item) => {
      if (typeof item === "string") return { field: "", message: item };
      if (!item || typeof item !== "object") return { field: "", message: "" };
      const entry = item as Record<string, unknown>;
      return {
        field: validationFieldPath(entry.loc),
        message: validationMessage(entry.msg ?? entry.message),
      };
    })
    .filter((entry) => entry.message.length > 0);
}

/**
 * Per-field messages from a 422 validation error (first message per field),
 * keyed by dotted path without the request location, e.g. `trials` or
 * `llm_params.temperature`. Empty for every other error shape.
 */
export function apiFieldErrors(error: unknown): Record<string, string> {
  if (!error || typeof error !== "object") return {};
  const result: Record<string, string> = {};
  for (const { field, message } of validationErrors(
    (error as Record<string, unknown>).detail,
  )) {
    if (field && !(field in result)) result[field] = message;
  }
  return result;
}

/**
 * Human-readable message for anything `apiFetch` (or fetch itself) throws:
 * `{detail: str}`, Ninja 422 `{detail: [{loc, msg}]}`, `{message}`,
 * `{detail, remediation}` (API-key preflight), allauth `{errors: [...]}`,
 * `Error`s and strings.
 */
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
  } else if (Array.isArray(detail)) {
    message = validationErrors(detail)
      .map(({ field, message: msg }) => (field ? `${field}: ${msg}` : msg))
      .join("; ");
  } else if (detail && typeof detail === "object") {
    const nested = detail as Record<string, unknown>;
    message = typeof nested.detail === "string" ? nested.detail : "";
  }
  if (!message && typeof body.message === "string") {
    message = body.message;
  } else if (!message && Array.isArray(body.errors)) {
    const messages = body.errors
      .map((item) =>
        item && typeof item === "object"
          ? (item as Record<string, unknown>).message
          : item,
      )
      .filter(
        (item): item is string => typeof item === "string" && item.length > 0,
      );
    // Join as sentences without doubling the messages' own full stops.
    message = messages
      .map((item, i) =>
        i < messages.length - 1 && !/[.!?]$/.test(item) ? `${item}.` : item,
      )
      .join(" ");
  }

  return [message || fallback, remediation].filter(Boolean).join(" ");
}

export async function allauthFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    await ensureCsrfCookie();
  }
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
      if (typeof window !== "undefined") {
        window.location.href = "/auth/login";
      }
      throw { status: 401, message: "Unauthorized" };
    }
    const body: unknown = await res.json().catch(() => ({}));
    const error: Record<string, unknown> =
      body && typeof body === "object" && !Array.isArray(body)
        ? (body as Record<string, unknown>)
        : { detail: body };
    // Non-enumerable: the thrown value stays the response body for every
    // existing reader, while `apiErrorStatus` can tell a 404 from an outage.
    Object.defineProperty(error, "httpStatus", { value: res.status });
    throw error;
  }
  return res;
}

/** HTTP status of an error thrown by `apiFetch`, or null (network failure, abort, other errors). */
export function apiErrorStatus(error: unknown): number | null {
  if (!error || typeof error !== "object") return null;
  const status = (error as { httpStatus?: unknown }).httpStatus;
  return typeof status === "number" ? status : null;
}
