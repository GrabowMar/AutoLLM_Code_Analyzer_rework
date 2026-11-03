// Unknown token limits: the API stores "not reported" context window / max
// output as 0 (local endpoint models). Every model-page render path must show a
// muted "—" for it, never "0", and never place it in a catalog comparison.
import { describe, expect, it, vi } from "vitest";
import { render } from "svelte/server";

vi.mock("svelte-sonner", () => ({ toast: { success: vi.fn() } }));

import type {
  LLMModelDetail,
  LLMModelSummary,
  ModelInsights,
} from "$lib/api/client";
import { knownTokenLimit } from "./helpers";
import ModelComparisonChart from "./ModelComparisonChart.svelte";
import ModelHeader from "./ModelHeader.svelte";
import ModelOverviewTab from "./ModelOverviewTab.svelte";
import ModelsTable from "./ModelsTable.svelte";

/** Shape of models 660/661 on dev1: an OpenCode Go endpoint model with no reported limits. */
function model(overrides: Partial<LLMModelDetail> = {}): LLMModelDetail {
  return {
    id: 660,
    model_id: "local-opencode-go/deepseek-v4.1-flash",
    canonical_slug: "local-opencode-go_deepseek-v4.1-flash",
    provider: "opencode-go",
    model_name: "deepseek-v4.1-flash",
    description: "",
    is_free: true,
    is_local: true,
    context_window: 0,
    max_output_tokens: 0,
    context_window_display: "0",
    input_price_per_million: 0,
    output_price_per_million: 0,
    capabilities: [],
    cost_efficiency: 1,
    supports_vision: false,
    supports_function_calling: true,
    supports_streaming: true,
    supports_json_mode: false,
    input_price_per_token: 0,
    output_price_per_token: 0,
    metadata: {},
    capabilities_json: {},
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

/** SSR body without Svelte's hydration markers (<!--[-->, <!---->). */
function html(body: string): string {
  // Repeat until stable so nested/overlapping markers cannot leave a partial "<!--" behind.
  let out = body;
  let prev: string;
  do {
    prev = out;
    out = out.replace(/<!--[\s\S]*?-->/g, "");
  } while (out !== prev);
  return out;
}

const known = {
  context_window: 131_072,
  max_output_tokens: 8_192,
  context_window_display: "131K",
};

function insights(): ModelInsights {
  const pct = (value: number | null, percentile: number | null) => ({
    value,
    percentile,
    n: 468,
    median: 262_144,
  });
  return {
    generation: { runs_total: 0 },
    ranking: null,
    catalog: {
      input_price: pct(null, null),
      output_price: pct(null, null),
      // What the API returns for an unreported limit: no value, no percentile.
      context_window: pct(null, null),
      max_output_tokens: pct(null, null),
      artificial_analysis: {
        intelligence_index: pct(null, null),
        coding_index: pct(null, null),
        agentic_index: pct(null, null),
      },
      catalog_size: 468,
      provider_models: 0,
    },
  } as unknown as ModelInsights;
}

function overview(m: LLMModelDetail): string {
  return html(
    render(ModelOverviewTab, {
      props: {
        model: m,
        slug: m.canonical_slug,
        insights: insights(),
        insightsError: "",
        onRetryInsights: () => {},
        relatedModels: [],
        onOpenTab: () => {},
      },
    }).body,
  );
}

function table(items: LLMModelSummary[]): string {
  const noop = () => {};
  return html(
    render(ModelsTable, {
      props: {
        data: { items, total: items.length, page: 1, per_page: 50, pages: 1 },
        loading: false,
        sortBy: "",
        sortDir: "asc",
        selectedModelSlugs: new Set<string>(),
        visibleModelSlugs: items.map((m) => m.canonical_slug),
        allVisibleSelected: false,
        hasActiveFilters: false,
        onToggleSort: noop,
        onToggleModelSelection: noop,
        onToggleVisibleSelection: noop,
        onChangeMobileSort: noop,
        onToggleSortDir: noop,
        onClearAllFilters: noop,
        onClearSelection: noop,
        onCompare: noop,
      },
    }).body,
  );
}

describe("knownTokenLimit", () => {
  it("treats 0, negative and missing limits as unknown", () => {
    expect(knownTokenLimit(0)).toBeNull();
    expect(knownTokenLimit(-1)).toBeNull();
    expect(knownTokenLimit(null)).toBeNull();
    expect(knownTokenLimit(undefined)).toBeNull();
    expect(knownTokenLimit(Number.NaN)).toBeNull();
    expect(knownTokenLimit(131_072)).toBe(131_072);
  });
});

describe("model header", () => {
  it("shows an unreported context window as a muted dash, not 0", () => {
    const body = html(
      render(ModelHeader, {
        props: {
          model: model(),
          slug: "x",
          efficiency: { grade: "A+", color: "" },
          refreshing: false,
        },
      }).body,
    );
    expect(body).toMatch(
      /<b class="font-normal text-muted-foreground">—<\/b> context/,
    );
    expect(body).not.toMatch(/<b[^>]*>0<\/b> context/);
    expect(body).not.toContain("max out");
  });

  it("keeps known limits", () => {
    const body = html(
      render(ModelHeader, {
        props: {
          model: model(known),
          slug: "x",
          efficiency: { grade: "A+", color: "" },
          refreshing: false,
        },
      }).body,
    );
    expect(body).toMatch(/<b>131K<\/b> context/);
    expect(body).toMatch(/<b>8K<\/b> max out/);
  });
});

describe("model overview", () => {
  it("never shows a 0-token context and leaves it out of the catalog meters", () => {
    const body = overview(model());
    expect(body).not.toContain("0 tokens");
    expect(body).toMatch(
      /<dt class="text-muted-foreground">Context<\/dt>\s*<dd class="[^"]*text-muted-foreground[^"]*">—/,
    );
    // Context window row: muted dash, no meter, no "Smaller than …" comparison.
    expect(body).toContain("Context window");
    expect(body).not.toMatch(/Context window: percentile/);
    expect(body).not.toMatch(/(Larger|Smaller) than/);
    expect(body).not.toContain("Max output");
  });

  it("keeps known limits with their catalog meters", () => {
    const m = model(known);
    const body = html(
      render(ModelOverviewTab, {
        props: {
          model: m,
          slug: m.canonical_slug,
          insights: (() => {
            const i = insights();
            i.catalog.context_window = {
              value: 131_072,
              percentile: 0.4,
              n: 468,
              median: 262_144,
            };
            i.catalog.max_output_tokens = {
              value: 8_192,
              percentile: 0.2,
              n: 460,
              median: 117_964,
            };
            return i;
          })(),
          insightsError: "",
          onRetryInsights: () => {},
          relatedModels: [],
          onOpenTab: () => {},
        },
      }).body,
    );
    expect(body).toContain("131,072 tokens");
    expect(body).toContain("8,192 tokens");
    expect(body).toContain("Context window: percentile 40 of the catalog");
    expect(body).toContain("Max output: percentile 20 of the catalog");
  });
});

describe("models table and related list", () => {
  it("renders unreported limits as muted dashes on desktop and mobile", () => {
    const body = table([model()]);
    expect(body).not.toMatch(/<td class="num">0<\/td>/);
    expect(body).toMatch(
      /<td class="num"><span class="text-muted-foreground" title="Not reported by the source">—<\/span><\/td>/,
    );
    // Mobile list: Context and Max out both a muted dash.
    expect(
      body.match(
        /tabular-nums"><span class="text-muted-foreground">—<\/span><\/dd>/g,
      ),
    ).toHaveLength(2);
  });

  it("keeps known limits in the table", () => {
    const body = table([model(known)]);
    expect(body).toMatch(/<td class="num">131K<\/td>/);
    expect(body).toMatch(/<td class="num text-muted-foreground">8K<\/td>/);
  });

  it("shows an unreported context as a muted dash in 'More from <provider>'", () => {
    const body = html(
      render(ModelComparisonChart, {
        props: {
          provider: "opencode-go",
          slug: "x",
          relatedModels: [model({ id: 661 }), model({ id: 662, ...known })],
        },
      }).body,
    );
    expect(body).not.toMatch(/<td class="num">0<\/td>/);
    expect(body).toMatch(
      /<td class="num"><span class="text-muted-foreground" title="Not reported by the source">—<\/span><\/td>/,
    );
    expect(body).toMatch(/<td class="num">131K<\/td>/);
  });
});
