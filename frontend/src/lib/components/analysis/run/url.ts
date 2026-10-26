/**
 * URL state of the run detail page. The active tab and every list filter
 * live in the query string, so views are linkable and survive reloads and
 * back/forward; the tabs read them through `page.url` and write them with
 * {@link replaceParams} (replaceState, focus and scroll kept).
 */

import { goto } from "$app/navigation";

export const RUN_TABS = [
  "overview",
  "findings",
  "issues",
  "ai",
  "files",
  "provenance",
] as const;
export type RunTab = (typeof RUN_TABS)[number];

/** Findings tab filters (`fpage` is the findings page). */
export const FINDING_PARAMS = [
  "kind",
  "sev",
  "tool",
  "cat",
  "origin",
  "counted",
  "consensus",
  "grounding",
  "sample",
  "file",
  "rule",
  "issue",
  "q",
  "order",
  "fpage",
] as const;

/** Issues tab filters. */
export const ISSUE_PARAMS = ["ikind", "isev", "icat", "ipage"] as const;

export type ParamUpdates = Record<string, string | number | null | undefined>;

/** A copy of `url` with `updates` applied (null/""/undefined delete) after clearing `clear`. */
export function withParams(
  url: URL,
  updates: ParamUpdates,
  clear: readonly string[] = [],
): URL {
  const next = new URL(url);
  for (const key of clear) next.searchParams.delete(key);
  for (const [key, value] of Object.entries(updates)) {
    if (value === null || value === undefined || value === "") {
      next.searchParams.delete(key);
    } else {
      next.searchParams.set(key, String(value));
    }
  }
  return next;
}

/** Apply `updates` to the current URL in place (no history entry, no scroll). */
export function replaceParams(
  url: URL,
  updates: ParamUpdates,
  clear: readonly string[] = [],
): Promise<void> {
  return goto(withParams(url, updates, clear), {
    replaceState: true,
    keepFocus: true,
    noScroll: true,
  });
}

/** Comma-separated list param → values (empty when missing). */
export function csvParam(value: string | null): string[] {
  return value
    ? value
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean)
    : [];
}

export function intParam(value: string | null, fallback: number): number {
  const n = value ? Number.parseInt(value, 10) : Number.NaN;
  return Number.isFinite(n) && n > 0 ? n : fallback;
}
