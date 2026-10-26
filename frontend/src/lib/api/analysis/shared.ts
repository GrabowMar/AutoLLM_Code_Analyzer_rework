import { saveBlob } from "$lib/utils/download";
import { apiFetch } from "../core";

/** Path prefix for `apiFetch` (which adds `/api` itself). */
export const ANALYSIS_PATH = "/analysis";
/** Root-relative URL prefix for links and downloads (`<a href>`, `window.open`). */
export const ANALYSIS_URL = `/api${ANALYSIS_PATH}`;

/** Optional request knobs shared by the analysis client functions. */
export interface AnalysisRequestOptions {
  /** Abort a superseded request (`isAbortError` recognizes the rejection). */
  signal?: AbortSignal;
}

type QueryScalar = string | number | boolean;
export type QueryValue =
  QueryScalar | readonly QueryScalar[] | null | undefined;

/**
 * `?a=1&b=x,y` from a params object: `undefined`, `null`, `""` and empty
 * lists are left out; lists are sent comma-separated (the API's CSV filters).
 */
export function query(params: object): string {
  const usp = new URLSearchParams();
  for (const [key, raw] of Object.entries(params) as [string, QueryValue][]) {
    if (raw === undefined || raw === null || raw === "") continue;
    if (Array.isArray(raw)) {
      if (raw.length > 0) usp.set(key, raw.join(","));
      continue;
    }
    usp.set(key, String(raw));
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}

/** One path segment, encoded (ids are UUIDs, slugs may not be). */
export function segment(value: string): string {
  return encodeURIComponent(value);
}

export async function getJson<T>(
  path: string,
  options: AnalysisRequestOptions = {},
): Promise<T> {
  const res = await apiFetch(`${ANALYSIS_PATH}${path}`, {
    signal: options.signal,
  });
  return res.json() as Promise<T>;
}

export async function getText(
  path: string,
  options: AnalysisRequestOptions = {},
): Promise<string> {
  const res = await apiFetch(`${ANALYSIS_PATH}${path}`, {
    signal: options.signal,
  });
  return res.text();
}

/** A mutating request; `body` undefined sends none (action endpoints accept an empty body). */
export async function sendJson<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
  options: AnalysisRequestOptions = {},
): Promise<T> {
  const res = await apiFetch(`${ANALYSIS_PATH}${path}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options.signal,
  });
  return res.json() as Promise<T>;
}

/** Filename from `Content-Disposition: attachment; filename="x"` (RFC 5987 aware). */
function dispositionFilename(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (extended?.[1]) {
    try {
      return decodeURIComponent(extended[1].trim().replace(/^"|"$/g, ""));
    } catch {
      // Fall through to the plain parameter.
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain?.[1]?.trim() || null;
}

/**
 * Download a file of the analysis API (`/studies/{id}/export/tables.zip`,
 * `/studies/{id}/report.md`, ...) through `apiFetch` and save it. Resolves
 * with the saved filename; a refusal (409 / 404 `{detail, ...}`) is thrown
 * like any other API error, so it can be shown with `formatApiError` instead
 * of being saved as the file (which a plain `<a href download>` would do).
 */
export async function downloadAnalysisFile(
  path: string,
  fallbackName: string,
  options: AnalysisRequestOptions = {},
): Promise<string> {
  const res = await apiFetch(`${ANALYSIS_PATH}${path}`, {
    signal: options.signal,
  });
  const blob = await res.blob();
  const name =
    dispositionFilename(res.headers.get("Content-Disposition")) ?? fallbackName;
  saveBlob(blob, name);
  return name;
}

/** Resolves after `ms`, or rejects with an `AbortError` when `signal` aborts. */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const aborted = () =>
      new DOMException("The operation was aborted.", "AbortError");
    if (signal?.aborted) {
      reject(aborted());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(aborted());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
