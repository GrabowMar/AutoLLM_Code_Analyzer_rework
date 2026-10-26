// At-a-glance status a settings section reports to the page's bookmark rail
// ("2 active", "connected"). Outside the settings page there is no provider and
// reporting is a no-op, so the same components work elsewhere.
import { getContext, setContext } from "svelte";
import type { Tone } from "$lib/constants/colors";

export interface SectionStatus {
  tone: Tone;
  label: string;
}

const KEY = Symbol("settings-section-status");

export function provideSectionStatus(): Record<string, SectionStatus> {
  const statuses = $state<Record<string, SectionStatus>>({});
  setContext(KEY, statuses);
  return statuses;
}

/** Call during component init; returns a setter usable from async code. */
export function useSectionStatus(
  id: string,
): (status: SectionStatus | null) => void {
  const statuses = getContext<Record<string, SectionStatus> | undefined>(KEY);
  return (status) => {
    if (!statuses) return;
    if (status) statuses[id] = status;
    else delete statuses[id];
  };
}
