const ALLAUTH_BASE = "/_allauth/browser/v1";
const API_BASE = "/api";

export function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export function formatApiError(
  error: unknown,
  fallback = "Request failed.",
): string {
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
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    headers["X-CSRFToken"] = getCsrfToken();
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
