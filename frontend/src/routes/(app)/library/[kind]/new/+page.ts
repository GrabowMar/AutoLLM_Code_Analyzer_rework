import { error } from "@sveltejs/kit";
import { isLibraryApiKind, parseRef } from "$lib/api/v3/types";
import type { PageLoad } from "./$types";

/**
 * `/library/{kind}/new` — URL contract:
 * - `?from=<slug@v>`: start from a copy of that version's content (any kind the user can see);
 * - `?tier=frugal|standard|generous`: Budget presets start from that tier (default frugal);
 * - `?prefill=<JSON>`: detectors only — fields merged over the blank detector (WP-16's
 *   "Create detector from selection").
 */
export const load: PageLoad = ({ params, url }) => {
  if (!isLibraryApiKind(params.kind))
    error(404, `No library kind "${params.kind}"`);
  const from = url.searchParams.get("from");
  const tier = url.searchParams.get("tier");
  let prefill: Record<string, unknown> | null = null;
  const raw = url.searchParams.get("prefill");
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
        prefill = parsed;
    } catch {
      prefill = null;
    }
  }
  return {
    kind: params.kind,
    from: from && parseRef(from) ? from : null,
    tier: tier === "standard" || tier === "generous" ? tier : "frugal",
    prefill,
  };
};
