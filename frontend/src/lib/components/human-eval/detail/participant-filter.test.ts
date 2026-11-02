import { describe, expect, it } from "vitest";
import type { ResearcherParticipant } from "$lib/api/types/human-eval";
import {
  PARTICIPANT_SEARCH_LIMIT,
  participantMatchesNote,
  participantOption,
  selectedParticipantLabel,
} from "./participant-filter";

function participant(
  over: Partial<ResearcherParticipant> = {},
): ResearcherParticipant {
  return {
    id: "p-150",
    label: "P150",
    kind: "code",
    status: "active",
    email: null,
    invite: null,
    access_code_hint: "7Q",
    consent_at: null,
    joined_at: null,
    last_seen_at: null,
    completed_at: null,
    submitted: 0,
    skipped: 0,
    in_progress: 0,
    excluded: false,
    exclusion_reason: "",
    excluded_at: null,
    note: "",
    quality_flags: [],
    created_at: "2026-11-01T00:00:00Z",
    ...over,
  } as ResearcherParticipant;
}

describe("participant filter options", () => {
  it("labels each match by number with kind and identity underneath", () => {
    expect(participantOption(participant())).toEqual({
      value: "p-150",
      label: "P150",
      description: "Access code",
    });
    expect(
      participantOption(participant({ kind: "account", email: "ana@uni.edu" }))
        .description,
    ).toBe("Account · ana@uni.edu");
    expect(
      participantOption(
        participant({ kind: "invite", invite: { id: "i", label: "Class A" } }),
      ).description,
    ).toBe("Invite link · Class A");
    expect(
      participantOption(participant({ note: "late joiner\nsecond line" }))
        .description,
    ).toBe("Access code · late joiner");
    expect(
      participantOption(participant({ kind: "preview" })).description,
    ).toBe("Preview");
  });

  it("names a selected participant beyond the first page instead of showing 'Any participant'", () => {
    const known = new Map([["p-1", "P1"]]);
    expect(selectedParticipantLabel("", known, "P9")).toBe("");
    expect(selectedParticipantLabel("p-1", known)).toBe("P1");
    // Deep link to P150 (never returned by a search yet): the filtered rows name them.
    expect(selectedParticipantLabel("p-150", known, "P150")).toBe("P150");
    expect(selectedParticipantLabel("p-150", known)).toBe(
      "Selected participant",
    );
  });

  it("asks to narrow the search when there are more matches than shown", () => {
    expect(PARTICIPANT_SEARCH_LIMIT).toBeLessThanOrEqual(100);
    expect(participantMatchesNote(20, 20, "")).toBe("");
    expect(participantMatchesNote(20, 180, "")).toBe(
      "20 of 180; type a label (P12), email, note or invite",
    );
    expect(participantMatchesNote(20, 45, "P1")).toBe(
      "20 of 45 matches; type more to narrow",
    );
  });
});
