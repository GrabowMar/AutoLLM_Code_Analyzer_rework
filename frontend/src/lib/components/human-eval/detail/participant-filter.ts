// Options for the Responses tab's participant filter (ParticipantFilter.svelte), which
// searches participants on the server instead of listing the first page of them.

import type { ResearcherParticipant } from "$lib/api/types/human-eval";
import { PARTICIPANT_KIND_LABELS } from "../format";

export interface ParticipantOption {
  value: string;
  label: string;
  description?: string;
}

/** Matches per search: the list is a picker, not a table (type more to narrow). */
export const PARTICIPANT_SEARCH_LIMIT = 20;

/** "P12" with "Account · ana@uni.edu" / "Invite link · Class A" / "Preview" underneath. */
export function participantOption(p: ResearcherParticipant): ParticipantOption {
  const kind = PARTICIPANT_KIND_LABELS[p.kind] ?? p.kind;
  const detail =
    p.email || p.invite?.label || p.note.split("\n")[0]?.slice(0, 60) || "";
  return {
    value: p.id,
    label: p.label,
    description: detail ? `${kind} · ${detail}` : kind,
  };
}

/**
 * Label of the selected participant: from a search result or an earlier pick (`known`),
 * else from rows that name them (`fallback`, e.g. a filtered assignment's participant),
 * else a neutral placeholder — never "Any participant" while a filter is active.
 */
export function selectedParticipantLabel(
  id: string,
  known: ReadonlyMap<string, string>,
  fallback = "",
): string {
  if (!id) return "";
  return known.get(id) || fallback || "Selected participant";
}

/** Footer under the matches: how many there are beyond the ones shown. */
export function participantMatchesNote(
  shown: number,
  total: number,
  search: string,
): string {
  if (total <= shown) return "";
  return search.trim()
    ? `${shown} of ${total} matches; type more to narrow`
    : `${shown} of ${total}; type a label (P12), email, note or invite`;
}
