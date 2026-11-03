/**
 * The study record's sections (`?tab=`), in paper order (spec 10.3), and the names older links used
 * (v2 experiment tabs and the builder's step names), so bookmarks keep landing: `design`, `plan` →
 * Method; `monitor`, `budget` → Operations. Patterns exists for slot studies only.
 */
import type { StudyMode } from "$lib/api/v3/types";

export type StudyTab =
  | "summary"
  | "method"
  | "results"
  | "patterns"
  | "data"
  | "operations";

export const STUDY_TABS: {
  value: StudyTab;
  label: string;
  modes: StudyMode[];
}[] = [
  { value: "summary", label: "Abstract", modes: ["app", "slot"] },
  { value: "method", label: "Method", modes: ["app", "slot"] },
  { value: "results", label: "Results", modes: ["app", "slot"] },
  { value: "patterns", label: "Patterns", modes: ["slot"] },
  { value: "data", label: "Data", modes: ["app", "slot"] },
  { value: "operations", label: "Operations", modes: ["app", "slot"] },
];

const ALIASES: Record<string, StudyTab> = {
  abstract: "summary",
  overview: "summary",
  design: "method",
  plan: "method",
  runs: "data",
  monitor: "operations",
  budget: "operations",
  automation: "operations",
};

/** The tabs shown for a study mode, numbered from what is shown (no gaps). */
export function tabsFor(
  mode: StudyMode,
): { value: StudyTab; label: string; number: number }[] {
  return STUDY_TABS.filter((t) => t.modes.includes(mode)).map((t, i) => ({
    value: t.value,
    label: t.label,
    number: i + 1,
  }));
}

/** `?tab=` → a section; unknown values (and Patterns on an app study) → null (the page's default). */
export function resolveStudyTab(
  raw: string | null | undefined,
  mode: StudyMode = "slot",
): StudyTab | null {
  if (!raw) return null;
  const tab = (STUDY_TABS.some((t) => t.value === raw) ? raw : ALIASES[raw]) as
    | StudyTab
    | undefined;
  if (!tab) return null;
  return tabsFor(mode).some((t) => t.value === tab) ? tab : null;
}
