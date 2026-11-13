import { describe, expect, it } from "vitest";
import type { LLMModelSummary } from "$lib/api/models";
import {
  ALIAS_GROUP_LABEL,
  filterByEndpointScope,
  groupModels,
  isLocalModel,
  modelLevelsFromRefs,
  modelIneligibility,
  selectionByEndpoint,
  applyFacets,
  paginateGroups,
  blendedPrice,
  facetCounts,
  highlightParts,
  isNewModel,
  matchesTokens,
  searchTokens,
  sortModels,
} from "./catalog";

let nextId = 1;
function model(overrides: Partial<LLMModelSummary> = {}): LLMModelSummary {
  const id = nextId++;
  return {
    id,
    model_id: `vendor/model-${id}`,
    canonical_slug: `vendor/model-${id}`,
    provider: "vendor",
    model_name: `Model ${id}`,
    description: "",
    is_free: false,
    context_window: 8192,
    max_output_tokens: 0,
    context_window_display: "8K",
    input_price_per_million: 1,
    output_price_per_million: 2,
    capabilities: [],
    cost_efficiency: 0,
    supports_vision: false,
    supports_function_calling: true,
    supports_streaming: true,
    supports_json_mode: false,
    endpoint: null,
    is_local: false,
    ...overrides,
  };
}

const gpu1 = {
  id: 1,
  slug: "gpu1",
  name: "Zeta box",
  kind: "ollama" as const,
  online: true,
};
const gpu2 = {
  id: 2,
  slug: "gpu2",
  name: "Alpha box",
  kind: "ollama" as const,
  online: false,
};

function local(
  endpoint: typeof gpu1,
  name: string,
  extra: Partial<LLMModelSummary> = {},
) {
  return model({
    endpoint,
    is_local: true,
    provider: endpoint.slug,
    model_name: name,
    served_model_name: name,
    model_id: `local-${endpoint.slug}/${name}`,
    is_free: true,
    ...extra,
  });
}

describe("isLocalModel", () => {
  it("uses is_local, else the endpoint kind", () => {
    expect(isLocalModel(model())).toBe(false);
    expect(isLocalModel(local(gpu1, "qwen"))).toBe(true);
    expect(isLocalModel(model({ is_local: undefined, endpoint: gpu1 }))).toBe(
      true,
    );
    expect(
      isLocalModel(
        model({
          is_local: undefined,
          endpoint: { ...gpu1, kind: "openrouter" },
        }),
      ),
    ).toBe(false);
  });
});

describe("groupModels", () => {
  const a = model({ provider: "openai", model_name: "GPT b" });
  const b = model({ provider: "anthropic", model_name: "Claude" });
  const c = model({ provider: "openai", model_name: "GPT a" });
  const l1 = local(gpu1, "qwen2.5-coder:32b");
  const l2 = local(gpu1, "llama3:8b");
  const l3 = local(gpu2, "mistral:7b");
  const all = [a, b, c, l1, l2, l3];

  it("groups by provider like the classic picker", () => {
    const groups = groupModels(all, "provider");
    expect(groups.map((g) => g.label)).toEqual([
      "anthropic",
      "gpu1",
      "gpu2",
      "openai",
    ]);
    expect(groups.every((g) => g.type === "provider")).toBe(true);
    expect(groups[3].models.map((m) => m.model_name)).toEqual([
      "GPT a",
      "GPT b",
    ]);
  });

  it("collects ~vendor aliases into one group after the vendors", () => {
    const alias1 = model({
      provider: "~anthropic",
      model_name: "Claude Opus Latest",
    });
    const alias2 = model({ provider: "~openai", model_name: "GPT Latest" });
    const groups = groupModels([alias1, a, alias2, b], "endpoint");
    expect(groups.map((g) => g.label)).toEqual([
      "anthropic",
      "openai",
      ALIAS_GROUP_LABEL,
    ]);
    expect(groups[2].models.map((m) => m.model_name)).toEqual([
      "Claude Opus Latest",
      "GPT Latest",
    ]);
  });

  it("puts local endpoints first, by name, then OpenRouter vendors", () => {
    const groups = groupModels(all, "endpoint", new Set([l1.id, l3.id, a.id]));
    expect(groups.map((g) => [g.type, g.label])).toEqual([
      ["endpoint", "Alpha box"],
      ["endpoint", "Zeta box"],
      ["provider", "anthropic"],
      ["provider", "openai"],
    ]);
    const zeta = groups[1];
    expect(zeta.key).toBe("endpoint:1");
    expect(zeta.endpoint).toEqual(gpu1);
    expect(zeta.models.map((m) => m.model_name)).toEqual([
      "llama3:8b",
      "qwen2.5-coder:32b",
    ]);
    expect(zeta.selectedCount).toBe(1);
    expect(groups[0].selectedCount).toBe(1);
    expect(groups[3].selectedCount).toBe(1);
  });

  it("does not mutate its input", () => {
    const input = [a, c];
    groupModels(input, "provider");
    expect(input).toEqual([a, c]);
  });
});

describe("filterByEndpointScope", () => {
  const remote = model();
  const loc = local(gpu1, "qwen");
  it("filters by source", () => {
    expect(filterByEndpointScope([remote, loc], "all")).toEqual([remote, loc]);
    expect(filterByEndpointScope([remote, loc], "local")).toEqual([loc]);
    expect(filterByEndpointScope([remote, loc], "openrouter")).toEqual([
      remote,
    ]);
  });
});

describe("selectionByEndpoint", () => {
  it("counts per endpoint with OpenRouter last", () => {
    const r1 = model();
    const r2 = model();
    const l1 = local(gpu1, "a");
    const l2 = local(gpu2, "b");
    const l3 = local(gpu2, "c");
    const counts = selectionByEndpoint(
      [r1, r2, l1, l2, l3],
      [r1.id, l1.id, l2.id, l3.id, 9999],
    );
    expect(counts).toEqual([
      { key: "endpoint:2", label: "Alpha box", count: 2 },
      { key: "endpoint:1", label: "Zeta box", count: 1 },
      { key: "openrouter", label: "OpenRouter", count: 1 },
    ]);
  });
});

describe("modelIneligibility", () => {
  it("explains missing, unavailable and tool-less models", () => {
    expect(modelIneligibility(undefined, {})).toMatch(/catalog/);
    expect(
      modelIneligibility(local(gpu1, "x", { is_available: false }), {}),
    ).toBe("No longer served by Zeta box");
    expect(
      modelIneligibility(
        local(gpu1, "x", { supports_function_calling: false }),
        {
          requireTools: true,
        },
      ),
    ).toMatch(/Endpoint reports no tool calling/);
    expect(
      modelIneligibility(model({ supports_function_calling: false }), {
        requireTools: true,
      }),
    ).toMatch(/^No tool calling/);
  });
  it("lets an offline endpoint's model through", () => {
    expect(
      modelIneligibility(local(gpu2, "x"), { requireTools: true }),
    ).toBeNull();
  });
});

describe("modelLevelsFromRefs", () => {
  it("resolves pks, slugs and ids in link order, without duplicates", () => {
    const a = model({
      model_id: "local-box/qwen2.5-coder:7b",
      canonical_slug: "local-box/qwen2.5-coder-7b",
    });
    const b = model({
      model_id: "openai/gpt-5",
      canonical_slug: "openai/gpt-5-2025",
    });
    expect(
      modelLevelsFromRefs(
        [a, b],
        [String(b.id), "LOCAL-BOX/QWEN2.5-CODER-7B", "openai/gpt-5"],
      ),
    ).toEqual({
      levels: ["openai/gpt-5", "local-box/qwen2.5-coder:7b"],
      unknown: [],
    });
  });

  it("keeps an unknown model id for the lint and drops an unknown pk", () => {
    expect(modelLevelsFromRefs([], ["vendor/gone", "99999"])).toEqual({
      levels: ["vendor/gone"],
      unknown: ["99999"],
    });
  });
});

describe("search, facets and sorting", () => {
  const now = Date.parse("2026-11-05T00:00:00Z");

  it("matches every token anywhere (name, id, endpoint)", () => {
    const m = model({
      model_name: "Qwen: Qwen3 235B A22B",
      model_id: "qwen/qwen3-235b-a22b",
    });
    expect(matchesTokens(m, searchTokens("qwen 235b"))).toBe(true);
    expect(matchesTokens(m, searchTokens("235B  QWEN"))).toBe(true);
    expect(matchesTokens(m, searchTokens("qwen 14b"))).toBe(false);
    expect(
      matchesTokens(local(gpu1, "llama3"), searchTokens("zeta llama")),
    ).toBe(true);
    expect(searchTokens("   ")).toEqual([]);
  });

  it("narrows by every active facet and counts what each would keep", () => {
    const a = model({
      supports_reasoning: true,
      open_weights: true,
      released_at: "2026-10-01",
    });
    const b = model({
      supports_reasoning: true,
      supports_vision: true,
      released_at: "2025-01-01",
    });
    const c = model({ is_free: true });
    const d = local(gpu1, "llama3");
    const all = [a, b, c, d];
    expect(applyFacets(all, new Set(["reasoning"]), now)).toEqual([a, b]);
    expect(applyFacets(all, new Set(["reasoning", "vision"]), now)).toEqual([
      b,
    ]);
    expect(applyFacets(all, new Set(["open_weights"]), now)).toEqual([a, d]); // local weights are open
    const counts = facetCounts(all, new Set(["reasoning"]), now);
    expect(counts.reasoning).toBe(2);
    expect(counts.vision).toBe(1);
    expect(counts.new).toBe(1);
    expect(isNewModel(a, now)).toBe(true);
    expect(isNewModel(model({ released_at: "2027-01-01" }), now)).toBe(false); // future date is not "new"
  });

  it("sorts by blended price, context and release date without mutating", () => {
    const cheap = model({
      model_name: "Cheap",
      input_price_per_million: 0.1,
      output_price_per_million: 0.4,
    });
    const dear = model({
      model_name: "Dear",
      input_price_per_million: 3,
      output_price_per_million: 15,
      context_window: 200000,
      released_at: "2026-06-01",
    });
    const loc = local(gpu1, "Local");
    const list = [dear, cheap, loc];
    expect(sortModels(list, "price").map((m) => m.model_name)).toEqual([
      "Local",
      "Cheap",
      "Dear",
    ]);
    expect(sortModels(list, "context")[0]).toBe(dear);
    expect(sortModels(list, "newest")[0]).toBe(dear);
    expect(list[0]).toBe(dear);
    expect(blendedPrice(dear)).toBeCloseTo((3 * 3 + 15) / 4);
    const router = model({
      model_name: "Auto Router",
      input_price_per_million: -1,
      output_price_per_million: -1,
    });
    expect(
      sortModels([router, dear, cheap], "price").map((m) => m.model_name),
    ).toEqual(["Cheap", "Dear", "Auto Router"]);
  });

  it("pages grouped rows across group boundaries", () => {
    const groups = groupModels(
      [
        ...Array.from({ length: 30 }, () => model({ provider: "a" })),
        ...Array.from({ length: 5 }, () => model({ provider: "b" })),
        model({ provider: "c" }),
      ],
      "provider",
    );
    const page = paginateGroups(groups, 33);
    expect(page.groups.map((g) => [g.key, g.shown.length])).toEqual([
      ["provider:a", 30],
      ["provider:b", 3],
    ]);
    expect(page.remaining).toBe(3);
    expect(paginateGroups(groups, 100).remaining).toBe(0);
  });

  it("splits text around the search tokens for highlighting", () => {
    expect(highlightParts("Qwen3 235B", ["235b", "qwen"])).toEqual([
      { text: "Qwen", match: true },
      { text: "3 ", match: false },
      { text: "235B", match: true },
    ]);
    expect(highlightParts("a.b", ["."])).toEqual([
      { text: "a", match: false },
      { text: ".", match: true },
      { text: "b", match: false },
    ]);
    expect(highlightParts("plain", [])).toEqual([
      { text: "plain", match: false },
    ]);
  });
});
