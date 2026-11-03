import { describe, expect, it } from "vitest";
import type { RenderOut } from "$lib/api/v3/types";
import render from "$lib/api/v3/__fixtures__/render-app.json";
import {
  OWNER_LABEL,
  ownerHref,
  segmentStimulus,
  stimulusOwners,
} from "./spans";

const OUT = render as unknown as RenderOut;

describe("stimulus source spans (prompt playground with server output)", () => {
  it("segments the rendered brief without losing or reordering text", () => {
    for (const stimulus of OUT.stimuli) {
      const segments = segmentStimulus(stimulus);
      expect(segments.map((s) => s.text).join("")).toBe(stimulus.text);
      for (const seg of segments) {
        if (!seg.source) continue;
        expect(seg.source.char_start).toBeLessThanOrEqual(seg.start);
        expect(seg.source.char_end).toBeGreaterThanOrEqual(seg.end);
      }
    }
  });

  it("attributes the task's prose, the stack, the style and the auth contract to their owners", () => {
    const brief = OUT.stimuli.find((s) => s.role === "brief")!;
    const kinds = new Set(stimulusOwners(brief).map((o) => o.owner_kind));
    for (const kind of ["prompt", "task", "stack", "style"] as const)
      expect(kinds).toContain(kind);
    const chars = stimulusOwners(brief).reduce((n, o) => n + o.chars, 0);
    expect(chars).toBeLessThanOrEqual(brief.chars);
    const taskSeg = segmentStimulus(brief).find(
      (s) => s.source?.owner_kind === "task",
    )!;
    expect(brief.text.slice(taskSeg.start, taskSeg.end)).toBe(taskSeg.text);
    expect(OWNER_LABEL[taskSeg.source!.owner_kind]).toBe("Task");
  });

  it("gives nested spans to the innermost owner", () => {
    const segments = segmentStimulus({
      text: "Brief: use Flask. End.",
      sources: [
        {
          template_ref: "brief@1#aaaaaaaaaaaa",
          owner_kind: "prompt",
          owner_ref: "brief@1#aaaaaaaaaaaa",
          role: "brief",
          char_start: 0,
          char_end: 22,
        },
        {
          template_ref: "stack-flask@1#bbbbbbbbbbbb",
          owner_kind: "stack",
          owner_ref: "flask-react@1#cccccccccccc",
          role: null,
          char_start: 7,
          char_end: 17,
        },
      ],
    });
    expect(segments.map((s) => [s.text, s.source?.owner_kind])).toEqual([
      ["Brief: ", "prompt"],
      ["use Flask.", "stack"],
      [" End.", "prompt"],
    ]);
  });

  it("links owners to their Library version", () => {
    expect(
      ownerHref({
        owner_kind: "stack",
        owner_ref: "flask-react@1#cccccccccccc",
        template_ref: "x",
      }),
    ).toBe("/library/stacks/flask-react?v=1");
    expect(
      ownerHref({
        owner_kind: "auth_fragment",
        owner_ref: "app-default@1",
        template_ref: "auth-contract-cookie_session@1#dddddddddddd",
      }),
    ).toBe("/library/prompts/auth-contract-cookie_session?v=1");
    expect(
      ownerHref({
        owner_kind: "prompt",
        owner_ref: "draft:brief",
        template_ref: "draft:brief",
      }),
    ).toBeNull();
  });

  it("renders the prompt hash the server computed", () => {
    expect(OUT.prompt_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(OUT.issues).toEqual([]);
  });
});
