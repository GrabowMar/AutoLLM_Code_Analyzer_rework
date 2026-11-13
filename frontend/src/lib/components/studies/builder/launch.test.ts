import { describe, expect, it } from "vitest";
import type { PostRunOut } from "$lib/api/v3/types";
import { postRunKey, shouldAttachPostRun } from "./launch";

function postRun(
  config: Record<string, unknown>,
  over: Partial<PostRunOut> = {},
): PostRunOut {
  return {
    id: 1,
    kind: "analysis",
    trigger: "completed",
    status: "pending",
    config,
    progress: {},
    analysis_run_ids: [],
    report_id: null,
    pattern_run_id: null,
    error: "",
    created_at: "2026-11-05T00:00:00Z",
    started_at: null,
    ...over,
  } as PostRunOut;
}

describe("post-run at launch", () => {
  it("keys configs by their normalized shape", () => {
    expect(postRunKey({ tool_slugs: ["b", "a", "a"], report: true })).toBe(
      postRunKey({ tool_slugs: ["a", "b"], only_passing: false }),
    );
    expect(postRunKey({ tool_slugs: [], report: false })).not.toBe(
      postRunKey({ tool_slugs: [] }),
    );
  });

  it("attaches the report even without analyzers", () => {
    expect(
      shouldAttachPostRun(
        { tool_slugs: [], only_passing: false, report: true },
        [],
      ),
    ).toBe(true);
  });

  it("attaches nothing when there is nothing to run", () => {
    expect(
      shouldAttachPostRun(
        { tool_slugs: [], only_passing: false, report: false },
        [],
      ),
    ).toBe(false);
  });

  it("never attaches the same config twice", () => {
    const config = {
      tool_slugs: ["semgrep"],
      only_passing: false,
      report: true,
    };
    expect(shouldAttachPostRun(config, [postRun({ ...config })])).toBe(false);
    expect(
      shouldAttachPostRun(config, [postRun({ ...config, only_passing: true })]),
    ).toBe(true);
    expect(
      shouldAttachPostRun(config, [
        postRun({ ...config }, { trigger: "manual" }),
      ]),
    ).toBe(true);
  });
});
