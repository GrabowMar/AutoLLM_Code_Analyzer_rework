import { describe, expect, it } from "vitest";
import type { LLMModelSummary } from "$lib/api/models";
import type { ExperimentDetail } from "$lib/api/types/experiments";
import {
  balanceAcrossEndpoints,
  buildCreateBody,
  conditionKey,
  crossConditions,
  defaultComposerDraft,
  draftFromExperiment,
  draftSlug,
  effectiveBaseline,
  loadInfoFromPreview,
  sanitizeDraft,
  selectionByEndpoint,
  type ExperimentDraft,
} from "./composer";

function draft(over: Partial<ExperimentDraft> = {}): ExperimentDraft {
  return {
    ...defaultComposerDraft(),
    name: "Harness study",
    baseSeed: 42,
    ...over,
  };
}

function model(
  id: number,
  over: Partial<LLMModelSummary> = {},
): LLMModelSummary {
  return {
    id,
    model_id: `m${id}`,
    canonical_slug: `m${id}`,
    provider: "local",
    model_name: `Model ${id}`,
    description: "",
    is_free: true,
    context_window: 32768,
    max_output_tokens: 4096,
    context_window_display: "32K",
    input_price_per_million: 0,
    output_price_per_million: 0,
    capabilities: [],
    cost_efficiency: 0,
    supports_vision: false,
    supports_function_calling: true,
    supports_streaming: true,
    supports_json_mode: false,
    ...over,
  };
}

const ep = (slug: string) => ({
  id: slug.length,
  slug,
  name: slug,
  kind: "ollama" as const,
  online: true,
});

describe("conditions", () => {
  it("builds the cross product, models outer", () => {
    const d = draft({ modelIds: [1, 2], harnessPresetIds: [5, 6] });
    expect(crossConditions(d).map((c) => c.key)).toEqual([
      "m1|h5",
      "m1|h6",
      "m2|h5",
      "m2|h6",
    ]);
    const s = draft({
      mode: "scaffolding",
      modelIds: [1],
      profileIds: [3],
      harnessPresetIds: [9],
    });
    expect(crossConditions(s)).toEqual([
      { model_id: 1, profile_id: 3, key: "m1|p3" },
    ]);
  });

  it("falls back to the first condition when the chosen baseline disappears", () => {
    const d = draft({
      modelIds: [1, 2],
      harnessPresetIds: [5],
      baseline: "m2|h5",
    });
    expect(effectiveBaseline(d)?.key).toBe("m2|h5");
    expect(effectiveBaseline({ ...d, modelIds: [1] })?.key).toBe("m1|h5");
    expect(effectiveBaseline({ ...d, modelIds: [] })).toBeNull();
  });

  it("keys conditions stably", () => {
    expect(conditionKey({ model_id: 3, harness_preset_id: 4 })).toBe("m3|h4");
    expect(conditionKey({ model_id: 3, profile_id: 4 })).toBe("m3|p4");
  });
});

describe("buildCreateBody", () => {
  it("sends the agent cross product, baseline and execution", () => {
    const body = buildCreateBody(
      draft({
        modelIds: [1, 2],
        harnessPresetIds: [5],
        appIds: [9, 3],
        baseline: "m2|h5",
        llmParams: { temperature: 0.2, stop: ["x"] },
        budgetOverrides: { max_llm_requests: 40 },
        execution: {
          policy: "blocked",
          priority: 5,
          maxConcurrency: 4,
          infraRetries: 1,
          warmup: false,
          maxResidentModels: 2,
        },
        postRun: {
          evaluate: true,
          analysis: { enabled: false, tool_slugs: [], only_passing: false },
          report: { enabled: true, exclude_truncated: true },
        },
      }),
    );
    expect(body.slug).toBe("harness-study");
    expect(body.app_requirement_ids).toEqual([3, 9]);
    expect(body.conditions.cross).toEqual({
      model_ids: [1, 2],
      harness_preset_ids: [5],
    });
    expect(body.conditions.baseline).toEqual({
      model_id: 2,
      harness_preset_id: 5,
    });
    expect(body.llm_defaults).toEqual({ temperature: 0.2 });
    expect(body.agent_config).toEqual({ max_llm_requests: 40 });
    expect(body.execution).toEqual({
      scheduling_policy: "blocked",
      priority: 5,
      max_concurrency: 4,
      infra_retries: 1,
      options: { warmup: false, max_resident_models: 2 },
    });
    // Agent runs evaluate inline; post_run.evaluate is scaffolding-only.
    expect(body.post_run?.evaluate).toBe(false);
    expect(body.post_run?.report.enabled).toBe(true);
    expect(body.schedules).toEqual([]);
  });

  it("sends scaffolding profiles, ablations and the no-op sampling rule", () => {
    const body = buildCreateBody(
      draft({
        mode: "scaffolding",
        modelIds: [1],
        profileIds: [7],
        samplingPresetId: 11,
        budgetPresetId: 4,
        ablations: [
          {
            key: "a",
            model_id: 1,
            harness_preset_id: null,
            profile_id: 7,
            label: " hot ",
            param_overrides: { temperature: 1 },
          },
          {
            key: "b",
            model_id: null,
            harness_preset_id: null,
            profile_id: 7,
            label: "",
            param_overrides: {},
          },
        ],
      }),
      { samplingNoop: true },
    );
    expect(body.conditions.cross).toEqual({ model_ids: [1], profile_ids: [7] });
    expect(body.conditions.extra).toEqual([
      {
        model_id: 1,
        profile_id: 7,
        label: "hot",
        param_overrides: { temperature: 1 },
      },
    ]);
    expect(body.sampling_preset_id).toBeNull();
    expect(body.budget_preset_id).toBeNull();
    expect(body.agent_config).toEqual({});
  });

  it("keeps a typed slug, else derives one from the name", () => {
    expect(
      draftSlug({ name: "Ä Big Test!", slug: "", slugTouched: false }),
    ).toBe("big-test");
    expect(draftSlug({ name: "x", slug: " mine ", slugTouched: true })).toBe(
      "mine",
    );
    expect(
      draftSlug({ name: "a".repeat(80), slug: "", slugTouched: false }),
    ).toHaveLength(50);
  });
});

describe("persistence", () => {
  it("sanitises stored drafts", () => {
    const d = sanitizeDraft({
      mode: "scaffolding",
      modelIds: [1, 1, -2, "x"],
      repeats: "3",
      execution: { policy: "bogus" },
    });
    expect(d?.mode).toBe("scaffolding");
    expect(d?.modelIds).toEqual([1]);
    expect(d?.repeats).toBe(3); // default
    expect(d?.execution.policy).toBe("auto");
    expect(d?.postRun.evaluate).toBe(true);
    expect(sanitizeDraft(null)).toBeNull();
  });

  it("types a stored schedule field by field", () => {
    const d = sanitizeDraft({
      schedule: { action: "explode", cron: 5, params: { replicates: 2, x: 1 } },
    });
    expect(d?.schedule).toMatchObject({
      action: "top_up",
      cron: "0 2 * * *",
      params: { replicates: 2 },
      skip_if_active: true,
      enabled: true,
    });
    expect(d?.schedule?.timezone).toEqual(expect.any(String));
  });

  it("new drafts carry a random seed", () => {
    expect(defaultComposerDraft().baseSeed).toEqual(expect.any(Number));
  });
});

describe("draftFromExperiment", () => {
  const exp = {
    id: "e1",
    name: "Study",
    slug: "study",
    mode: "agent",
    description: "",
    hypothesis: "h",
    stack_slug: "flask",
    app_requirement_ids: [3],
    repeats: 2,
    base_seed: 7,
    scheduling_policy: "blocked",
    priority: 0,
    max_concurrency: null,
    infra_retries: 2,
    execution_options: { warmup: true },
    agent_config: { max_llm_requests: 30, harness: "aider" },
    llm_defaults: {},
    sampling_preset_ref: { id: 4, slug: "s", version: 1, name: "S" },
    budget_preset_ref: null,
    post_run: { evaluate: false },
    baseline_condition_id: 21,
    conditions: [
      {
        id: 20,
        model: 1,
        harness_preset: 5,
        profile: null,
        param_overrides: {},
        label: "",
      },
      {
        id: 21,
        model: 2,
        harness_preset: 5,
        profile: null,
        param_overrides: {},
        label: "",
      },
      {
        id: 22,
        model: 2,
        harness_preset: 5,
        profile: null,
        param_overrides: { temperature: 1 },
        label: "hot",
      },
    ],
  } as unknown as ExperimentDetail;

  it("splits the cross product from ablations and keeps the baseline", () => {
    const d = draftFromExperiment(exp, { clone: false });
    expect(d.modelIds).toEqual([1, 2]);
    expect(d.harnessPresetIds).toEqual([5]);
    expect(d.baseline).toBe("m2|h5");
    expect(d.ablations).toHaveLength(1);
    expect(d.ablations[0]).toMatchObject({
      model_id: 2,
      harness_preset_id: 5,
      label: "hot",
    });
    expect(d.slug).toBe("study");
    expect(d.baseSeed).toBe(7);
    expect(d.budgetOverrides).toEqual({ max_llm_requests: 30 });
    expect(d.samplingPresetId).toBe(4);
  });

  it("names a clone and re-rolls its seed", () => {
    const d = draftFromExperiment(exp, { clone: true });
    expect(d.name).toBe("Clone of Study");
    expect(d.slugTouched).toBe(false);
    expect(d.baseSeed).toEqual(expect.any(Number));
  });
});

describe("selection by endpoint", () => {
  it("groups local endpoints by name and OpenRouter last", () => {
    const byId = new Map([
      [1, model(1, { is_local: true, endpoint: ep("gpu-2") })],
      [2, model(2, { endpoint: null, is_local: false })],
      [3, model(3, { is_local: true, endpoint: ep("gpu-1") })],
      [4, model(4, { is_local: true, endpoint: ep("gpu-1") })],
    ]);
    const groups = selectionByEndpoint([1, 2, 3, 4], byId);
    expect(groups.map((g) => [g.key, g.ids])).toEqual([
      ["gpu-1", [3, 4]],
      ["gpu-2", [1]],
      ["openrouter", [2]],
    ]);
  });
});

describe("balanceAcrossEndpoints", () => {
  // Digest A and B exist on both GPUs; C only on gpu-1.
  const catalog = [
    model(1, { is_local: true, endpoint: ep("gpu-1"), digest: "A" }),
    model(2, { is_local: true, endpoint: ep("gpu-1"), digest: "B" }),
    model(3, { is_local: true, endpoint: ep("gpu-1"), digest: "C" }),
    model(11, { is_local: true, endpoint: ep("gpu-2"), digest: "A" }),
    model(12, { is_local: true, endpoint: ep("gpu-2"), digest: "B" }),
    model(20, { endpoint: null, is_local: false }),
  ];

  it("moves replicated models off the busy endpoint", () => {
    const result = balanceAcrossEndpoints([1, 2, 3, 20], catalog, [
      { slug: "gpu-1", capacity: 1, estHours: 3, models: 3 },
      { slug: "gpu-2", capacity: 1 },
    ]);
    // C stays on gpu-1 (load 1); A goes to gpu-2 (1), B can go either way: tie → keeps gpu-1.
    expect(result.ids.sort((a, b) => a - b)).toEqual([2, 3, 11, 20]);
    expect(result.changed).toBe(1);
    expect(result.load["gpu-1"]).toBeCloseTo(2);
    expect(result.load["gpu-2"]).toBeCloseTo(1);
  });

  it("keeps one row per digest", () => {
    const result = balanceAcrossEndpoints([1, 11], catalog, [
      { slug: "gpu-1", capacity: 1 },
      { slug: "gpu-2", capacity: 2 },
    ]);
    expect(result.ids).toEqual([11]);
    expect(result.changed).toBe(1);
  });

  it("prefers the faster endpoint", () => {
    const result = balanceAcrossEndpoints([1, 2], catalog, [
      { slug: "gpu-1", capacity: 1 },
      { slug: "gpu-2", capacity: 4 },
    ]);
    expect(result.ids.sort((a, b) => a - b)).toEqual([11, 12]);
  });

  it("derives load inputs from a preview", () => {
    const info = loadInfoFromPreview(
      {
        endpoints: [
          {
            id: 1,
            slug: "gpu-1",
            name: "gpu-1",
            kind: "ollama",
            connection: "relay",
            online: true,
            sessions: 1,
            max_concurrency: 2,
            job_cap: 2,
            resident_limit: 1,
            models: [],
            cells: 10,
            est_run_s_p50: 60,
            est_hours: 2,
            issues: [],
          },
        ],
      },
      new Map([
        ["gpu-1", 1],
        ["gpu-2", 3],
      ]),
    );
    expect(info).toEqual([
      { slug: "gpu-1", capacity: 2, estHours: 2, models: 0 },
      { slug: "gpu-2", capacity: 3 },
    ]);
  });
});
