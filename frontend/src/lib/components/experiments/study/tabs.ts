/**
 * The experiment page's sections (`?tab=`), in paper order, and the names
 * older links used before the study layout (Overview, Design, Runs,
 * Automation), so bookmarks and links from other pages keep landing.
 */
export type ExperimentTab =
  | "summary"
  | "method"
  | "results"
  | "data"
  | "operations";

export const EXPERIMENT_TABS: { value: ExperimentTab; label: string }[] = [
  { value: "summary", label: "Abstract" },
  { value: "method", label: "Method" },
  { value: "results", label: "Results" },
  { value: "data", label: "Data" },
  { value: "operations", label: "Operations" },
];

const ALIASES: Record<string, ExperimentTab> = {
  abstract: "summary",
  overview: "summary",
  design: "method",
  runs: "data",
  automation: "operations",
};

/** `?tab=` → a section; unknown values → null (the page's default). */
export function resolveExperimentTab(
  raw: string | null | undefined,
): ExperimentTab | null {
  if (!raw) return null;
  if (EXPERIMENT_TABS.some((t) => t.value === raw)) return raw as ExperimentTab;
  return ALIASES[raw] ?? null;
}
