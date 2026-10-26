/**
 * Page-lifetime cache of `GET /profiles/defaults/` (default config, tool
 * catalog, reviewer options, vocabularies), shared by every profile editor
 * and picker on a page. Browser-only: on the server nothing is cached
 * (module state would be shared between users).
 */
import { browser } from "$app/environment";
import {
  getProfileDefaults,
  type ProfileDefaults,
  type ReviewerOptions,
} from "$lib/api/analysis";

let cache: Promise<ProfileDefaults> | null = null;

export function loadProfileDefaults(
  options: { force?: boolean } = {},
): Promise<ProfileDefaults> {
  if (!browser) return getProfileDefaults();
  if (!cache || options.force) {
    const request = getProfileDefaults();
    cache = request;
    // A failed load must not poison the cache for the next caller.
    request.catch(() => {
      if (cache === request) cache = null;
    });
  }
  return cache;
}

/** The reviewer options of the defaults, or null when the backend could not build them. */
export function reviewerOptionsOf(
  defaults: ProfileDefaults | null | undefined,
): ReviewerOptions | null {
  const reviewer = defaults?.reviewer;
  if (!reviewer || "error" in reviewer) return null;
  return reviewer;
}

/** Why reviewer options are missing (shown instead of the reviewer form). */
export function reviewerOptionsError(
  defaults: ProfileDefaults | null | undefined,
): string {
  const reviewer = defaults?.reviewer;
  return reviewer && "error" in reviewer ? reviewer.error : "";
}
