// Pure helpers of the workbench HTTP console: header rows, request checks
// that mirror the server's (services/console.py, spec §5.12), endpoint
// suggestions from the app spec, response formatting and local history.
import type {
  AppSpecView,
  ConsoleRequest,
  ConsoleResponse,
} from "$lib/api/types/human-eval";

export type ConsoleMethod = ConsoleRequest["method"];

export const CONSOLE_METHODS: ConsoleMethod[] = [
  "GET",
  "POST",
  "PUT",
  "PATCH",
  "DELETE",
  "HEAD",
  "OPTIONS",
];

/** RFC 9110 token characters, 1..64 long (the server's rule). */
export const HEADER_NAME_RE = /^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$/;
export const MAX_HEADERS = 30;
export const MAX_HEADER_VALUE = 4096;
export const MAX_PATH_LENGTH = 2048;
export const MAX_BODY_BYTES = 256 * 1024;
export const HISTORY_LIMIT = 50;

/** Headers the server drops before forwarding (host and hop-by-hop). */
export const DROPPED_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "content-length",
]);

export interface HeaderRow {
  name: string;
  value: string;
}

export interface BuiltHeaders {
  headers: Record<string, string>;
  /** Problems that block sending. */
  errors: string[];
  /** Names the server will drop (shown as a hint, not an error). */
  dropped: string[];
}

const CONTROL_RE = /[\x00-\x20\x7f]/;
const CRLF_RE = /[\r\n]/;

/** Parse one "Name: value" line (null for blank lines and lines without a name). */
export function parseHeaderLine(line: string): HeaderRow | null {
  const trimmed = line.replace(/\r$/, "");
  if (!trimmed.trim()) return null;
  const colon = trimmed.indexOf(":");
  if (colon <= 0) return null;
  const name = trimmed.slice(0, colon).trim();
  if (!name) return null;
  return { name, value: trimmed.slice(colon + 1).trim() };
}

/** Parse pasted raw headers ("Name: value" per line) into rows. */
export function parseHeaderBlock(text: string): HeaderRow[] {
  return text
    .split("\n")
    .map(parseHeaderLine)
    .filter((row): row is HeaderRow => row !== null);
}

/**
 * Turn editor rows into the request's header map. Empty rows are ignored;
 * names are checked against the server's rules; duplicates (case-insensitive)
 * are errors because the server keeps only one.
 */
export function buildHeaders(rows: HeaderRow[]): BuiltHeaders {
  const headers: Record<string, string> = {};
  const errors: string[] = [];
  const dropped: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim();
    const value = row.value.trim();
    if (!name && !value) continue;
    if (!name) {
      errors.push("A header value has no name.");
      continue;
    }
    if (!HEADER_NAME_RE.test(name)) {
      errors.push(`“${name}” is not a valid header name.`);
      continue;
    }
    if (CRLF_RE.test(row.value)) {
      errors.push(`The value of ${name} contains a line break.`);
      continue;
    }
    if (value.length > MAX_HEADER_VALUE) {
      errors.push(
        `The value of ${name} is longer than ${MAX_HEADER_VALUE} characters.`,
      );
      continue;
    }
    const lower = name.toLowerCase();
    if (seen.has(lower)) {
      errors.push(`${name} is set twice.`);
      continue;
    }
    seen.add(lower);
    if (DROPPED_HEADERS.has(lower)) {
      dropped.push(name);
      continue;
    }
    headers[name] = value;
  }
  if (Object.keys(headers).length > MAX_HEADERS) {
    errors.push(`Send at most ${MAX_HEADERS} headers.`);
  }
  return { headers, errors, dropped };
}

/** Case-insensitive header lookup. */
export function headerValue(
  headers: Record<string, string> | null | undefined,
  name: string,
): string | undefined {
  const lower = name.toLowerCase();
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (key.toLowerCase() === lower) return value;
  }
  return undefined;
}

/** Why the server would refuse this path, or null when it is acceptable. */
export function pathIssue(path: string): string | null {
  if (!path) return "Enter a path such as /api/items.";
  if (!path.startsWith("/")) return "The path must start with “/”.";
  if (path.startsWith("//")) return "The path must not start with “//”.";
  if (path.length > MAX_PATH_LENGTH)
    return `Keep the path under ${MAX_PATH_LENGTH} characters.`;
  if (CONTROL_RE.test(path))
    return "The path must not contain spaces or control characters (encode them, e.g. %20).";
  if (path.includes("#"))
    return "Fragments (#…) are never sent to the server; remove them.";
  return null;
}

/** UTF-8 byte length of a string. */
export function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Why the body cannot be sent, or null. */
export function bodyIssue(body: string): string | null {
  return utf8Length(body) > MAX_BODY_BYTES
    ? "The body is larger than 256 KiB."
    : null;
}

/** True when the text parses as JSON (an object, array, string, number, …). */
export function isJson(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  try {
    JSON.parse(trimmed);
    return true;
  } catch {
    return false;
  }
}

/**
 * Headers actually sent: adds `Content-Type: application/json` when the body
 * is JSON and no content type was given (the common case for API testing).
 */
export function withDefaultContentType(
  headers: Record<string, string>,
  body: string,
): Record<string, string> {
  if (!body.trim() || headerValue(headers, "content-type") !== undefined)
    return headers;
  return isJson(body)
    ? { ...headers, "Content-Type": "application/json" }
    : headers;
}

export interface EndpointSuggestion {
  method: ConsoleMethod;
  path: string;
  description: string;
  /** "api" or "admin_api" (from the spec's lists). */
  source: "api" | "admin_api";
}

function normalizeMethod(method: string): ConsoleMethod {
  const upper = (method || "").trim().toUpperCase();
  return (CONSOLE_METHODS as string[]).includes(upper)
    ? (upper as ConsoleMethod)
    : "GET";
}

/** Endpoints listed in the app spec, filtered by what the participant typed. */
export function endpointSuggestions(
  spec: AppSpecView | null | undefined,
  query = "",
  limit = 30,
): EndpointSuggestion[] {
  if (!spec) return [];
  const all: EndpointSuggestion[] = [];
  const seen = new Set<string>();
  const add = (
    list: AppSpecView["api_endpoints"] | undefined,
    source: EndpointSuggestion["source"],
  ) => {
    for (const ep of list ?? []) {
      const path = (ep.path || "").trim();
      if (!path.startsWith("/")) continue;
      const method = normalizeMethod(ep.method);
      const key = `${method} ${path}`;
      if (seen.has(key)) continue;
      seen.add(key);
      all.push({ method, path, description: ep.description || "", source });
    }
  };
  add(spec.api_endpoints, "api");
  add(spec.admin_api_endpoints, "admin_api");
  const q = query.trim().toLowerCase();
  const matches = q
    ? all.filter((s) =>
        `${s.method} ${s.path} ${s.description}`.toLowerCase().includes(q),
      )
    : all;
  return matches.slice(0, limit);
}

/** Response body for display: pretty-printed when it is JSON. */
export function prettyBody(
  body: string,
  headers: Record<string, string> | null | undefined,
): { text: string; json: boolean } {
  const type = (headerValue(headers, "content-type") ?? "").toLowerCase();
  const looksJson = type.includes("json") || /^\s*[[{]/.test(body);
  if (looksJson && body.trim()) {
    try {
      return { text: JSON.stringify(JSON.parse(body), null, 2), json: true };
    } catch {
      // Truncated or invalid JSON: show it as sent.
    }
  }
  return { text: body, json: false };
}

/** Tone of a status code for the response badge. */
export function statusTone(
  code: number | null,
): "success" | "info" | "warning" | "danger" | "neutral" {
  if (code === null) return "danger";
  if (code >= 500) return "danger";
  if (code >= 400) return "warning";
  if (code >= 300) return "info";
  if (code >= 200) return "success";
  return "neutral";
}

export interface HistoryEntry {
  /** Server id of the logged exchange (or a local id for failed sends). */
  id: string;
  at: string;
  slot: ConsoleRequest["slot"];
  method: ConsoleMethod;
  path: string;
  headers: Record<string, string>;
  body: string;
  status_code: number | null;
  elapsed_ms: number | null;
  error: string;
}

/** Strip the Authorization header unless the participant asked to remember it. */
export function redactHeaders(
  headers: Record<string, string>,
  keepAuthorization: boolean,
): Record<string, string> {
  if (keepAuthorization) return { ...headers };
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === "authorization" || key.toLowerCase() === "cookie")
      continue;
    out[key] = value;
  }
  return out;
}

/** History entry for a sent request and its response. */
export function historyEntry(
  request: ConsoleRequest,
  response: Pick<
    ConsoleResponse,
    "id" | "status_code" | "elapsed_ms" | "error"
  >,
  keepAuthorization: boolean,
  at: Date = new Date(),
): HistoryEntry {
  return {
    id: response.id,
    at: at.toISOString(),
    slot: request.slot,
    method: request.method,
    path: request.path,
    headers: redactHeaders(request.headers, keepAuthorization),
    body:
      request.body.length > 16_384
        ? request.body.slice(0, 16_384)
        : request.body,
    status_code: response.status_code,
    elapsed_ms: response.elapsed_ms,
    error: response.error,
  };
}

/** Newest first, capped. */
export function pushHistory(
  list: HistoryEntry[],
  entry: HistoryEntry,
  limit: number = HISTORY_LIMIT,
): HistoryEntry[] {
  return [entry, ...list.filter((e) => e.id !== entry.id)].slice(0, limit);
}

/** Parse stored history defensively (localStorage may hold anything). */
export function parseHistory(raw: string | null): HistoryEntry[] {
  if (!raw) return [];
  try {
    const data: unknown = JSON.parse(raw);
    if (!Array.isArray(data)) return [];
    const seen = new Set<string>();
    return data
      .filter(
        (e): e is HistoryEntry =>
          !!e &&
          typeof e === "object" &&
          typeof (e as HistoryEntry).id === "string" &&
          typeof (e as HistoryEntry).path === "string" &&
          typeof (e as HistoryEntry).method === "string",
      )
      .filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true)))
      .map((e) => ({
        ...e,
        method: normalizeMethod(e.method),
        headers: e.headers && typeof e.headers === "object" ? e.headers : {},
        body: typeof e.body === "string" ? e.body : "",
      }))
      .slice(0, HISTORY_LIMIT);
  } catch {
    return [];
  }
}

/** Message for a failed console call (rate limits get a specific explanation). */
export function consoleErrorMessage(
  status: number | null,
  body: { detail?: unknown; code?: unknown } | null | undefined,
  fallback = "The request could not be sent.",
): string {
  const detail = typeof body?.detail === "string" ? body.detail : "";
  const code = typeof body?.code === "string" ? body.code : "";
  if (status === 429) {
    if (code === "rate_limited") {
      return "Too many requests: the console allows 30 requests per minute. Wait a moment, then send again.";
    }
    return detail
      ? `${detail} Wait for your other requests to finish, then try again.`
      : "Another request is still running. Wait for it to finish, then send again.";
  }
  if (code === "quota_exceeded") {
    return (
      detail || "This assignment reached its limit of logged console requests."
    );
  }
  return detail || fallback;
}
