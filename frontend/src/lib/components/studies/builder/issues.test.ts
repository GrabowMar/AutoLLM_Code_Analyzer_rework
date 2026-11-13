import { describe, expect, it } from "vitest";
import type { Issue } from "$lib/api/v3/types";
import {
  fromIssues,
  groupIssues,
  hasErrors,
  issueTitle,
  parseFix,
} from "./issues";

function issue(over: Partial<Issue>): Issue {
  return {
    code: "estimate_exceeds_cap",
    severity: "warn",
    message: "",
    cells: [],
    path: null,
    data: {},
    fix: [],
    ...over,
  };
}

describe("fromIssues", () => {
  it("maps server issues and merges repeats of one message", () => {
    const rows = fromIssues([
      issue({
        code: "starter_missing",
        severity: "error",
        message: "no full starter",
        cells: ["c0-u0-r0-main"],
      }),
      issue({
        code: "starter_missing",
        severity: "error",
        message: "no full starter",
        cells: ["c1-u0-r0-main"],
      }),
      issue({ code: "aliasing", severity: "warn", message: "D aliased" }),
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      code: "starter_missing",
      severity: "error",
      cells: ["c0-u0-r0-main", "c1-u0-r0-main"],
      subject: "2 cells",
    });
    expect(rows[1].detail).toBe("D aliased");
  });

  it("labels an issue on several cells with the count and falls back to the title", () => {
    const [row] = fromIssues([issue({ message: "", cells: ["a", "b", "c"] })]);
    expect(row.subject).toBe("3 cells");
    expect(row.detail).toBe(issueTitle("estimate_exceeds_cap"));
  });

  it("treats unknown severities as notes and handles null input", () => {
    expect(
      fromIssues([issue({ severity: "fatal" as never })])[0].severity,
    ).toBe("info");
    expect(fromIssues(null)).toEqual([]);
  });
});

describe("groupIssues / hasErrors / issueTitle", () => {
  it("groups errors, warnings, notes in that order", () => {
    const rows = fromIssues([
      issue({ severity: "info", message: "a" }),
      issue({ severity: "error", message: "b" }),
      issue({ message: "c" }),
    ]);
    expect(groupIssues(rows).map((g) => g.severity)).toEqual([
      "error",
      "warn",
      "info",
    ]);
    expect(hasErrors(rows)).toBe(true);
    expect(hasErrors(rows.filter((r) => r.severity !== "error"))).toBe(false);
  });

  it("humanizes codes without a title and prefers built-in titles over registry summaries", () => {
    expect(issueTitle("some_new_code")).toBe("Some new code");
    expect(
      issueTitle("some_new_code", { some_new_code: "From the registry" }),
    ).toBe("From the registry");
    expect(issueTitle("aliasing", { aliasing: "x" })).toBe(
      "Aliased main effects",
    );
  });
});

describe("parseFix", () => {
  it("parses the server's quick fixes", () => {
    expect(
      parseFix({
        label: "reduce repeats to 3",
        action: "set_repeats",
        params: { repeats: 3 },
      }),
    ).toEqual({ kind: "set_repeats", repeats: 3 });
    expect(
      parseFix({
        label: "raise cap",
        action: "set_study_cap",
        params: { study_cap: 12.5 },
      }),
    ).toEqual({ kind: "set_study_cap", cap: 12.5 });
    expect(
      parseFix({
        label: "raise call cap",
        action: "set_knob",
        params: { "call.max_input_tokens": 9000 },
      }),
    ).toEqual({
      kind: "set_knob",
      values: { "call.max_input_tokens": 9000 },
    });
    expect(
      parseFix({ label: "move", action: "move_prose_variant", params: {} }),
    ).toEqual({ kind: "move_prose_variant" });
  });

  it("rejects malformed params and unknown actions", () => {
    expect(
      parseFix({ label: "x", action: "set_repeats", params: { repeats: -1 } })
        .kind,
    ).toBe("unknown");
    expect(
      parseFix({ label: "x", action: "set_study_cap", params: {} }).kind,
    ).toBe("unknown");
    expect(parseFix({ label: "x", action: "teleport", params: {} })).toEqual({
      kind: "unknown",
      action: "teleport",
    });
  });
});
