import { describe, expect, it } from "vitest";
import type { PortalParticipation, Question } from "$lib/api/types/human-eval";
import {
  ACCESS_CODE_ALPHABET,
  CSRF_MESSAGE,
  RATE_LIMIT_MESSAGE,
  SESSION_ENDED_MESSAGE,
  accessCodeGroups,
  accessCodeProblem,
  availabilityNotice,
  blockTypeText,
  caretAfterFormat,
  errorCode,
  finishConfirmText,
  finishGate,
  formatAccessCode,
  formatAccessCodeInput,
  invalidAccessCodeChars,
  isPlausibleAccessCode,
  nextReasonNotice,
  normalizeAccessCode,
  participationFraction,
  participationProgressText,
  portalErrorMessage,
  preStepDone,
  progressNumbers,
  taskPath,
  workbenchPath,
} from "./portal";

/** An error shaped like portalFetch throws it: the body plus a hidden httpStatus. */
function apiError(status: number, body: Record<string, unknown>) {
  const error = { ...body };
  Object.defineProperty(error, "httpStatus", { value: status });
  return error;
}

describe("access codes", () => {
  it("normalizes case and drops separators", () => {
    expect(normalizeAccessCode(" k7qm-4xt9 bw2r ")).toBe("K7QM4XT9BW2R");
    expect(normalizeAccessCode("k7qm.4xt9_bw2r")).toBe("K7QM4XT9BW2R");
    expect(normalizeAccessCode("")).toBe("");
  });

  it("groups codes in fours for display", () => {
    expect(formatAccessCode("k7qm4xt9bw2r")).toBe("K7QM-4XT9-BW2R");
    expect(formatAccessCode("K7QM-4XT9-BW2R")).toBe("K7QM-4XT9-BW2R");
    expect(formatAccessCode("ABCDE")).toBe("ABCD-E");
    expect(accessCodeGroups("K7QM-4XT9-BW2R")).toEqual([
      "K7QM",
      "4XT9",
      "BW2R",
    ]);
  });

  it("formats the input as it is typed and caps it at the code length", () => {
    expect(formatAccessCodeInput("k7q")).toBe("K7Q");
    expect(formatAccessCodeInput("k7qm4")).toBe("K7QM-4");
    expect(formatAccessCodeInput("K7QM-")).toBe("K7QM");
    expect(formatAccessCodeInput("k7qm4xt9bw2rEXTRA")).toBe("K7QM-4XT9-BW2R");
    expect(
      formatAccessCodeInput("x".repeat(40), 20).replace(/-/g, ""),
    ).toHaveLength(20);
  });

  it("keeps the caret after the same code characters", () => {
    // typed "5" at the end of "K7QM" → "K7QM-5": caret after the 5
    expect(caretAfterFormat("K7QM5", 5, "K7QM-5")).toBe(6);
    // typed in the middle: "K7xQM" caret after x (3) → "K7XQ-M" caret after X (3)
    expect(caretAfterFormat("K7xQM", 3, "K7XQ-M")).toBe(3);
    expect(caretAfterFormat("abc", 0, "ABC")).toBe(0);
    expect(caretAfterFormat("ab--", 4, "AB")).toBe(2);
  });

  it("uses the backend's code alphabet (no 0, 1, I, L, O, U)", () => {
    for (const ch of "01ILOU")
      expect(ACCESS_CODE_ALPHABET.includes(ch)).toBe(false);
    expect(ACCESS_CODE_ALPHABET).toHaveLength(30);
    expect(invalidAccessCodeChars("K7QM-4XT9-BW2R")).toEqual([]);
    expect(invalidAccessCodeChars("k0qm-4xtO-bw1r")).toEqual(["0", "O", "1"]);
  });

  it("only sends input shaped like a code", () => {
    expect(isPlausibleAccessCode("K7QM-4XT9-BW2R")).toBe(true);
    expect(isPlausibleAccessCode("k7qm 4xt9 bw2r")).toBe(true);
    expect(accessCodeProblem("")).toMatch(/Enter/);
    expect(accessCodeProblem("K7QM-4XT9")).toMatch(/12 characters; this has 8/);
    expect(accessCodeProblem("K7QM-4XT9-BW2R-2")).toMatch(/this has 13/);
    expect(accessCodeProblem("K7QM-4XT9-BW2O")).toMatch(/never contain O/);
  });
});

describe("portalErrorMessage", () => {
  it("explains rate limiting, ended sessions and CSRF failures", () => {
    expect(
      portalErrorMessage(apiError(429, { detail: "rate_limited" }), "x"),
    ).toBe(RATE_LIMIT_MESSAGE);
    expect(
      portalErrorMessage(apiError(401, { detail: "Unauthorized" }), "x"),
    ).toBe(SESSION_ENDED_MESSAGE);
    expect(
      portalErrorMessage(apiError(403, { detail: "CSRF check failed" }), "x"),
    ).toBe(CSRF_MESSAGE);
  });

  it("uses the server's detail otherwise", () => {
    expect(
      portalErrorMessage(
        apiError(404, {
          detail: "Code not recognised",
          code: "not_found",
          errors: [],
        }),
        "x",
      ),
    ).toBe("Code not recognised");
    expect(
      portalErrorMessage(
        apiError(403, {
          detail: "Evaluator accounts can only use the portal.",
        }),
        "x",
      ),
    ).toBe("Evaluator accounts can only use the portal.");
    expect(portalErrorMessage({}, "Fallback")).toBe("Fallback");
  });

  it("reads structured error codes", () => {
    expect(
      errorCode(apiError(409, { detail: "x", code: "already_joined" })),
    ).toBe("already_joined");
    expect(errorCode({ detail: "x" })).toBe("");
    expect(errorCode(null)).toBe("");
  });
});

describe("notices", () => {
  it("has nothing to say about an open invite or a handed-out item", () => {
    expect(availabilityNotice("open")).toBeNull();
    expect(nextReasonNotice("assigned")).toBeNull();
    expect(nextReasonNotice("resumed")).toBeNull();
  });

  it("explains every other availability and next reason", () => {
    for (const a of [
      "not_open",
      "closed",
      "full",
      "expired",
      "inactive",
      "something_new",
    ]) {
      expect(availabilityNotice(a)?.title).toBeTruthy();
    }
    for (const r of [
      "done",
      "limit_reached",
      "no_items_available",
      "not_accepting",
      "consent_required",
      "pre_questions_required",
    ] as const) {
      expect(nextReasonNotice(r)?.description).toBeTruthy();
    }
  });

  it("names block types in plain words", () => {
    expect(blockTypeText("pairwise")).toBe("Compare two apps");
    expect(blockTypeText("custom_thing")).toBe("custom thing");
  });
});

describe("progress", () => {
  it("caps the total by the per-participant limit", () => {
    expect(
      progressNumbers(
        { submitted: 3, skipped: 1, limit: 10, remaining_available: 20 },
        false,
      ),
    ).toEqual({
      done: 4,
      submitted: 3,
      skipped: 1,
      total: 10,
      fraction: 0.4,
    });
  });

  it("caps the total by what is still available and counts the current app", () => {
    const p = progressNumbers(
      { submitted: 2, skipped: 0, limit: 10, remaining_available: 1 },
      true,
    );
    expect(p.total).toBe(4);
    expect(p.fraction).toBe(0.5);
  });

  it("without a limit, the total is everything reachable; never below done", () => {
    expect(
      progressNumbers(
        { submitted: 0, skipped: 0, limit: null, remaining_available: 0 },
        false,
      ),
    ).toMatchObject({
      total: 0,
      fraction: null,
    });
    expect(
      progressNumbers(
        { submitted: 5, skipped: 0, limit: 3, remaining_available: 0 },
        false,
      ),
    ).toMatchObject({
      total: 5,
      fraction: 1,
    });
  });

  it("describes a participation for the home page", () => {
    const base: PortalParticipation = {
      task_id: "t",
      task_title: "T",
      task_status: "open",
      participant_label: "P001",
      participant_status: "active",
      kind: "code",
      submitted: 1,
      target: 4,
    };
    expect(participationProgressText(base)).toBe("1 of 4 apps evaluated");
    expect(participationFraction(base)).toBe(0.25);
    expect(
      participationProgressText({ ...base, target: null, submitted: 1 }),
    ).toBe("1 app evaluated");
    expect(participationFraction({ ...base, target: null })).toBeNull();
    expect(participationFraction({ ...base, target: 0 })).toBeNull();
  });
});

describe("paths", () => {
  it("builds portal paths", () => {
    expect(taskPath("abc")).toBe("/eval/t/abc");
    expect(workbenchPath("abc", "def")).toBe("/eval/t/abc/a/def");
  });
});

// C07/C24: an all-optional questionnaire left blank is stored as {} — saving it
// must still complete the step (the server's gate only needs required answers).
describe("preStepDone", () => {
  const optional: Question = {
    id: "years",
    kind: "text",
    label: "Years (optional)",
    required: false,
  };
  const required: Question = {
    id: "role",
    kind: "text",
    label: "Role",
    required: true,
  };

  it("completes an all-optional questionnaire saved blank", () => {
    expect(
      preStepDone({
        questions: [optional],
        answers: {},
        started: false,
        savedNow: false,
      }),
    ).toBe(false);
    expect(
      preStepDone({
        questions: [optional],
        answers: {},
        started: false,
        savedNow: true,
      }),
    ).toBe(true);
  });

  it("still needs every required answer", () => {
    expect(
      preStepDone({
        questions: [required, optional],
        answers: {},
        started: false,
        savedNow: true,
      }),
    ).toBe(false);
    expect(
      preStepDone({
        questions: [required, optional],
        answers: { role: "student" },
        started: false,
        savedNow: false,
      }),
    ).toBe(true);
  });

  it("is done without questions or once started", () => {
    expect(
      preStepDone({
        questions: [],
        answers: null,
        started: false,
        savedNow: false,
      }),
    ).toBe(true);
    expect(
      preStepDone({
        questions: [required],
        answers: {},
        started: true,
        savedNow: false,
      }),
    ).toBe(true);
  });
});

// C02: an open (possibly untouched) assignment must not disable Finish.
describe("finishGate", () => {
  it("keeps Finish open while an app is assigned and explains the release", () => {
    const gate = finishGate({
      stepsPending: false,
      hasCurrent: true,
      prominent: false,
    });
    expect(gate.disabled).toBe(false);
    expect(gate.hint).toMatch(/released/);
    expect(gate.hint).toMatch(/submit or skip it first/);
    expect(
      finishConfirmText({ started: true, workLeft: true, hasCurrent: true }),
    ).toMatch(/released/);
  });

  it("is closed only while consent or the questions about you are pending", () => {
    expect(
      finishGate({ stepsPending: true, hasCurrent: false, prominent: false })
        .disabled,
    ).toBe(true);
    expect(
      finishGate({ stepsPending: false, hasCurrent: false, prominent: true })
        .hint,
    ).toMatch(/done with the apps/);
    expect(
      finishGate({ stepsPending: false, hasCurrent: false, prominent: false })
        .disabled,
    ).toBe(false);
  });

  it("words the confirmation by progress", () => {
    expect(
      finishConfirmText({ started: false, workLeft: true, hasCurrent: false }),
    ).toMatch(/not evaluated any app/);
    expect(
      finishConfirmText({ started: true, workLeft: true, hasCurrent: false }),
    ).toMatch(/still apps/);
    expect(
      finishConfirmText({ started: true, workLeft: false, hasCurrent: false }),
    ).toMatch(/completion code next/);
  });
});
