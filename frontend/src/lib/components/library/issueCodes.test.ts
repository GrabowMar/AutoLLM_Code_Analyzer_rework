import { describe, expect, it } from "vitest";
import config from "$lib/api/v3/__fixtures__/config.json";
import { issueSummaries } from "$lib/api/v3/harnesses";
import type { GenerationConfigOut, IssueCodeOut } from "$lib/api/v3/types";

describe("GenerationConfigOut.issue_codes", () => {
  const rows = (config as unknown as GenerationConfigOut)
    .issue_codes as IssueCodeOut[];

  it("carries the registry rows with per-kind severities", () => {
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(Object.keys(row).sort()).toEqual([
        "alternatives",
        "code",
        "confirmatory",
        "exploratory",
        "owner",
        "summary",
      ]);
      expect([null, "error", "warn", "info"]).toContain(row.exploratory);
      expect([null, "error", "warn", "info"]).toContain(row.confirmatory);
    }
  });

  it("maps codes to summaries for SummaryIssues, empty when the config lacks the registry", () => {
    const summaries = issueSummaries(config as unknown as GenerationConfigOut);
    expect(Object.keys(summaries)).toHaveLength(rows.length);
    expect(summaries[rows[0].code]).toBe(rows[0].summary);
    expect(issueSummaries({})).toEqual({});
    expect(issueSummaries(null)).toEqual({});
  });
});
