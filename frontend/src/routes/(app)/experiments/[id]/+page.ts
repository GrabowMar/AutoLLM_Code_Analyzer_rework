import type { PageLoad } from "./$types";
import { resolveExperimentTab } from "$lib/components/experiments/study/tabs";

export const ssr = false;

/**
 * `?tab=summary|method|results|data|operations` (older names — overview,
 * design, runs, automation — still resolve; unknown values: the default).
 */
export const load: PageLoad = ({ params, url }) => ({
  id: params.id,
  tab: resolveExperimentTab(url.searchParams.get("tab")),
});
