import type { PageLoad } from "./$types";
import { resolveTaskTab } from "$lib/components/human-eval/format";

export const ssr = false;

/** `?tab=overview|design|apps|participants|responses|results|export` (unknown values: the default). */
export const load: PageLoad = ({ params, url }) => ({
  id: params.id,
  tab: resolveTaskTab(url.searchParams.get("tab")),
});
