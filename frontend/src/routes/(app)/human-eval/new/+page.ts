import type { PageLoad } from "./$types";

// Client-only: the composer keeps its draft in sessionStorage.
export const ssr = false;

/** `?preset=<slug>` preselects a starting point. */
export const load: PageLoad = ({ url }) => ({
  preset: url.searchParams.get("preset") ?? "",
});
