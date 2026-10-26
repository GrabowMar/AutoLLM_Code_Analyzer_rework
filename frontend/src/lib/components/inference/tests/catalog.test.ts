import { describe, expect, it } from "vitest";
import type { LLMModelSummary } from "$lib/api/models";
import {
  ALIAS_GROUP_LABEL,
  filterByEndpointScope,
  groupModels,
  isLocalModel,
  modelIneligibility,
  selectionByEndpoint,
} from "$lib/components/generator/catalog";

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
