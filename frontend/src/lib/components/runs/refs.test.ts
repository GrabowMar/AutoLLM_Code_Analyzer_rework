import { describe, expect, it } from "vitest";
import { kindForPath, libraryHref } from "./refs";
import { segmentBySources } from "./spans";

describe("manifest ref links", () => {
  it("links pinned refs to the exact library version", () => {
    expect(libraryHref("style", "tailwind@2#abcdef012345")).toBe(
      "/library/styles/tailwind?v=2",
    );
    expect(libraryHref("harness_preset", "opencode-native@1")).toBe(
      "/library/harness-presets/opencode-native?v=1",
    );
    expect(libraryHref("style", "not a ref")).toBeNull();
    expect(libraryHref("model", "openai@1")).toBeNull();
  });

  it("finds the library kind of a ref from its JSON path", () => {
    expect(kindForPath(["factors", "style", "ref"])).toBe("style");
    expect(kindForPath(["harness", "preset_ref"])).toBe("harness_preset");
    expect(kindForPath(["planned_evaluation", "eval_suite", "ref"])).toBe(
      "eval_suite",
    );
    expect(kindForPath(["stimuli", 0, "template_ref"])).toBe("prompt");
    expect(kindForPath(["identity", "task_hash"])).toBeNull();
  });
});

describe("stimulus source spans", () => {
  const span = (owner: string, a: number, b: number) => ({
    template_ref: "app-user@1",
    owner_kind: "style" as const,
    owner_ref: owner,
    role: "user",
    char_start: a,
    char_end: b,
  });

  it("splits a text into owned and unowned segments", () => {
    const parts = segmentBySources("Build an app. Use Tailwind. Done.", [
      span("tailwind@1", 14, 27),
    ]);
    expect(parts.map((p) => [p.text, p.source?.owner_ref ?? null])).toEqual([
      ["Build an app. ", null],
      ["Use Tailwind.", "tailwind@1"],
      [" Done.", null],
    ]);
  });

  it("gives nested spans to the innermost owner and keeps the full text", () => {
    const text = "0123456789";
    const parts = segmentBySources(text, [
      span("outer", 0, 10),
      span("inner", 3, 5),
    ]);
    expect(parts.map((p) => p.text).join("")).toBe(text);
    expect(parts.map((p) => p.source?.owner_ref)).toEqual([
      "outer",
      "inner",
      "outer",
    ]);
    expect(segmentBySources("abc", [])).toEqual([
      { text: "abc", source: null },
    ]);
  });
});
