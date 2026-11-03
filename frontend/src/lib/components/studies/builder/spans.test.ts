import { describe, expect, it } from "vitest";
import type { SourceSpanOut } from "$lib/api/v3/types";
import { buildOverrideRequest, owners, segmentText, spanText } from "./spans";

function span(over: Partial<SourceSpanOut>): SourceSpanOut {
  return {
    template_ref: "brief-app-default@1#aaaaaaaaaaaa",
    owner_kind: "prompt",
    owner_ref: "brief-app-default@1#aaaaaaaaaaaa",
    role: "user",
    char_start: 0,
    char_end: 0,
    ...over,
  };
}

const TEXT = "Build an app. Use Flask and React. Be secure.";
//            0         1         2         3         4
//            0123456789012345678901234567890123456789012345

describe("segmentText", () => {
  it("returns one plain segment without sources", () => {
    expect(segmentText(TEXT, [])).toEqual([
      { start: 0, end: TEXT.length, text: TEXT, source: null },
    ]);
    expect(segmentText("", [])).toEqual([]);
  });

  it("splits by the innermost owner and keeps the text whole", () => {
    const outer = span({ char_start: 0, char_end: TEXT.length });
    const stack = span({
      owner_kind: "stack",
      owner_ref: "flask-react@1#bbbbbbbbbbbb",
      template_ref: "stack-flask-react@1#cccccccccccc",
      char_start: 14,
      char_end: 34,
    });
    const segments = segmentText(TEXT, [outer, stack]);
    expect(segments.map((s) => s.text).join("")).toBe(TEXT);
    expect(segments.map((s) => [s.text, s.source?.owner_kind ?? null])).toEqual(
      [
        ["Build an app. ", "prompt"],
        ["Use Flask and React.", "stack"],
        [" Be secure.", "prompt"],
      ],
    );
  });

  it("leaves uncovered text unowned and clips out-of-range spans", () => {
    const style = span({
      owner_kind: "style",
      owner_ref: "plain@1",
      char_start: 35,
      char_end: 999,
    });
    const segments = segmentText(TEXT, [
      style,
      span({ char_start: 5, char_end: 5 }),
    ]);
    expect(segments).toEqual([
      { start: 0, end: 35, text: TEXT.slice(0, 35), source: null },
      {
        start: 35,
        end: TEXT.length,
        text: "Be secure.",
        source: expect.objectContaining({
          owner_kind: "style",
          char_end: TEXT.length,
        }),
      },
    ]);
  });

  it("merges adjacent pieces of the same span", () => {
    const a = span({ char_start: 0, char_end: 20 });
    const inner = span({
      owner_kind: "guidance",
      owner_ref: "none@1",
      char_start: 5,
      char_end: 5,
    });
    expect(
      segmentText(TEXT, [a, inner]).filter(
        (s) => s.source?.owner_kind === "prompt",
      ),
    ).toHaveLength(1);
  });
});

describe("owners and spanText", () => {
  it("lists distinct owners in text order", () => {
    const list = owners([
      span({
        owner_kind: "style",
        owner_ref: "plain@1",
        char_start: 30,
        char_end: 40,
      }),
      span({ char_start: 0, char_end: 10 }),
      span({
        owner_kind: "style",
        owner_ref: "plain@1",
        char_start: 41,
        char_end: 44,
      }),
    ]);
    expect(list.map((o) => o.kind)).toEqual(["prompt", "style"]);
  });

  it("slices a span's text", () => {
    expect(spanText(TEXT, span({ char_start: 14, char_end: 34 }))).toBe(
      "Use Flask and React.",
    );
  });
});

describe("buildOverrideRequest (source-span edit → override request)", () => {
  const stackSpan = span({
    owner_kind: "stack",
    owner_ref: "flask-react@1#bbbbbbbbbbbb",
  });

  it("sends the owner, the role and the whole edited template", () => {
    expect(
      buildOverrideRequest(
        stackSpan,
        "user",
        "Use {{ stack.backend }}.",
        "Use {{ stack.backend }} only.",
      ),
    ).toEqual({
      owner_kind: "stack",
      owner_ref: "flask-react@1#bbbbbbbbbbbb",
      role: "user",
      template: "Use {{ stack.backend }} only.",
    });
  });

  it("refuses unchanged, empty and non-editable owners", () => {
    expect(buildOverrideRequest(stackSpan, "user", "same", "same")).toBeNull();
    expect(buildOverrideRequest(stackSpan, "user", "x", "   ")).toBeNull();
    expect(
      buildOverrideRequest(
        span({ owner_kind: "task", owner_ref: "todo@1" }),
        "user",
        "a",
        "b",
      ),
    ).toBeNull();
    expect(
      buildOverrideRequest(
        span({ owner_kind: "starter", owner_ref: "s@1" }),
        null,
        "a",
        "b",
      ),
    ).toBeNull();
  });

  it("omits an empty role", () => {
    expect(buildOverrideRequest(stackSpan, "", "a", "b")?.role).toBeNull();
  });
});
