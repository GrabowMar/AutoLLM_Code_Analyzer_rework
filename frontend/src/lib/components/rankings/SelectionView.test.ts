// Server-side renders of the MSS v2 study-selection view and the rankings row breakdown
// with recorded payloads: catches template runtime errors and pins the rules
// (picks grouped by tier, blocked models with reasons, robustness badges, missing
// inputs as "no data" never 0, the infeasibility diagnosis).
import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import type { RankingRow, SelectionPayload } from "$lib/api/rankings";
import SelectionView from "./SelectionView.svelte";
import MssBreakdown from "./MssBreakdown.svelte";
import feasibleJson from "./fixtures/selection-feasible.json";
import infeasibleJson from "./fixtures/selection-infeasible.json";

const feasible = feasibleJson as unknown as SelectionPayload;
const infeasible = infeasibleJson as unknown as SelectionPayload;

/** Visible text: tags dropped, whitespace collapsed (Svelte's hydration comments included). */
function text(html: string): string {
  return html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ");
}

describe("SelectionView (feasible)", () => {
  const { body } = render(SelectionView, { props: { payload: feasible } });
  const t = text(body);

  it("groups the picks by tier with the A1 quotas", () => {
    expect(t).toMatch(/Frontier · 3 of 17/);
    expect(t).toMatch(/Mid · 3 of 17/);
    expect(t).toMatch(/Budget · 4 of 17/);
    expect(t.indexOf("Frontier · 3")).toBeLessThan(t.indexOf("Mid · 3"));
    expect(t.indexOf("Mid · 3")).toBeLessThan(t.indexOf("Budget · 4"));
  });

  it("renders every pick with its route", () => {
    for (const p of feasible.selection!.picks)
      expect(body).toContain(`title="${p.canonical_id}"`);
    expect(t).toContain("Claude Opus 5.5");
    expect(t).toContain("Anthropic key");
    expect(t).toContain("OpenCode Go");
  });

  it("shows robustness badges, including the borderline pick", () => {
    expect(t).toMatch(/borderline 48%/);
    expect(t).toMatch(/robust 100%/);
    expect(t).toMatch(/contender/);
  });

  it("shows missing capability as no data, never 0", () => {
    // claude-opus-5.5 has no capability score in the fixture.
    expect(feasible.selection!.picks[0].scores.capability).toBeNull();
    expect(t).toContain("no data");
  });

  it("lists the higher-ranked models that were not picked, with readable reasons", () => {
    expect(t).toContain("Higher-ranked but not picked");
    expect(t).toContain("Quality floor");
    expect(t).toContain("capability 0.392 < 0.407");
    expect(t).toContain("OpenRouter budget");
  });

  it("prints the capability percentile in the tier as p0–p100", () => {
    expect(t).toContain("p80");
    expect(t).not.toMatch(/\bp1\b/);
  });

  it("shows budget use per route and Go cap use", () => {
    expect(t).toContain("Budget use per route");
    expect(t).toContain("$122.40");
    expect(t).toContain("$63.23");
    expect(t).toMatch(/63% of \$15/);
    expect(t).toMatch(/2 wk · 11\/5h/);
  });

  it("shows constraints, alternates, sensitivity and sources", () => {
    expect(t).toContain("binding");
    expect(t).toContain("Alternates");
    expect(t).toContain("Gemini 4 Argon");
    expect(t).toContain("Preview: draws capped");
    expect(t).toContain("OpenRouter Data API rankings-daily");
    expect(t).toContain("2026-11-02 – 2026-11-08");
  });
});

describe("SelectionView (infeasible)", () => {
  const { body } = render(SelectionView, { props: { payload: infeasible } });
  const t = text(body);

  it("explains why no set is feasible and what would fix it", () => {
    expect(t).toContain("Infeasible");
    expect(t).toContain("No feasible selection");
    expect(t).toContain("$59.10");
    expect(t).toMatch(/feasible with 3/);
    expect(t).not.toContain("Higher-ranked but not picked");
  });
});

describe("MssBreakdown", () => {
  const base: RankingRow = {
    model_id: "z-ai/glm-5.3",
    model_name: "Z.ai: GLM 5.3",
    provider: "z-ai",
    is_free: false,
    context_length: 200000,
    price_per_million_input: 1,
    price_per_million_output: 3,
    apps: 0,
    apps_completed: 0,
    avg_duration: null,
    findings: null,
    ai_findings: null,
    n_trials: 0,
    functional_pass_rate: null,
    variance: null,
    canonical_id: "z-ai/glm-5.3",
    vendor: "z-ai",
    origin: "CN",
    open_weights: true,
    release_date: "2026-08-14",
    pinned_version: null,
    routes: {
      go: {
        model_id: "local-opencode-go/glm-5.3",
        available: true,
        paid: true,
        prerelease: false,
        served_name: "glm-5.3",
        permaslug: null,
      },
    },
    preferred_route: "go",
    eligible: true,
    exclusion: null,
    popularity_score: 0.71,
    price_score: 0.48,
    capability_score: null,
    recency_score: 0.76,
    mss_score: 0.61,
    mss_coverage: 0.8,
    mss_missing: ["capability"],
    mss_flags: ["capability_missing"],
    mss_inherited_from: null,
    run_cost_usd: 0.5976,
    planning_cost_usd: 27.555,
    price_tier: "mid",
    popularity: {
      source: "openrouter",
      volume_share: 0.01737,
      value_share: 0.04814,
      days_available: 28,
      days_listed: 28,
      days_censored: 0,
      censored: false,
      short_exposure: false,
      window: { start: "2026-10-12", end: "2026-11-08", days: 28 },
      as_of: "2026-11-09T09:22:19Z",
    },
    webdev_rating: null,
    list_price: {
      input: 1.2,
      output: 4,
      cache_read: 0.24,
      cache_write: null,
      source: "models.dev:zai/glm-5.3",
      fetched_at: "2026-11-09T09:16:00+00:00",
    },
    empirical_quality_score: null,
    composite_score: 0.61,
  };

  it("shows every input with its source and a missing one as no data", () => {
    const t = text(render(MssBreakdown, { props: { row: base } }).body);
    expect(t).toContain("1.74%");
    expect(t).toContain("2026-10-12 – 2026-11-08");
    expect(t).toContain("models.dev:zai/glm-5.3");
    expect(t).toContain("$27.55");
    expect(t).toContain("no data");
    expect(t).toContain("No LMArena WebDev rating");
    expect(t).toContain("OpenCode Go");
    expect(t).not.toMatch(/Capability\s+0\.0/);
  });

  it("says why an excluded model has no MSS", () => {
    const t = text(
      render(MssBreakdown, {
        props: {
          row: {
            ...base,
            eligible: false,
            mss_score: null,
            exclusion: {
              step: "E7",
              code: "superseded_by:z-ai/glm-5.4",
              detail: null,
            },
          },
        },
      }).body,
    );
    expect(t).toContain("E7");
    expect(t).toContain("Superseded by z-ai/glm-5.4");
  });

  it("recognises the LMArena popularity proxy", () => {
    const t = text(
      render(MssBreakdown, {
        props: {
          row: {
            ...base,
            mss_flags: ["lmarena_proxy"],
            popularity: { source: "lmarena_proxy" } as never,
          },
        },
      }).body,
    );
    expect(t).toContain("Proxied from LMArena interactions");
    expect(t).not.toContain("no share recorded");
  });
});

describe("SelectionView (study months, floor sensitivity)", () => {
  it("scales the Go tone by the study months and marks floor-sensitive picks", () => {
    const picks = feasible.selection!.picks.map((p, i) => ({
      ...p,
      floor_sensitive: i === 0,
    }));
    const go = feasible.selection!.budget_use.go.map((g) => ({
      ...g,
      share_of_monthly_limit: 1.5,
    }));
    const payload = {
      ...feasible,
      params: { ...feasible.params, study_months: 3 },
      selection: {
        ...feasible.selection!,
        picks,
        budget_use: { ...feasible.selection!.budget_use, go },
      },
    } as SelectionPayload;
    const { body } = render(SelectionView, { props: { payload } });
    // 150% of a monthly limit over 3 months is 59% of the B-go cap: not "bad".
    const goCell = body.slice(
      body.lastIndexOf("fig-cell", body.indexOf("OpenCode Go")),
      body.indexOf("OpenCode Go"),
    );
    expect(goCell).not.toContain('data-tone="bad"');
    expect(text(body)).toContain("▲ floor");
  });
});
