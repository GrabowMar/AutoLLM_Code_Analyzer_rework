import type { PageLoad } from "./$types";

export const ssr = false;

export type ExperimentTab =
  | "overview"
  | "results"
  | "runs"
  | "design"
  | "automation";
const TABS: ExperimentTab[] = [
  "overview",
  "results",
  "runs",
  "design",
  "automation",
];

/** `?tab=overview|results|runs|design|automation` (unknown values: the default for the status). */
export const load: PageLoad = ({ params, url }) => {
  const raw = url.searchParams.get("tab");
  return {
    id: params.id,
    tab: TABS.includes(raw as ExperimentTab) ? (raw as ExperimentTab) : null,
  };
};
