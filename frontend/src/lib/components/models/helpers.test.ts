import { describe, expect, it, vi } from "vitest";

vi.mock("svelte-sonner", () => ({ toast: { success: vi.fn() } }));

import type { LLMModelDetail } from "$lib/api/client";
import {
  fmtActiveParams,
  fmtBytes,
  fmtSpaces,
  metadataView,
  type HuggingFaceInfo,
  type ModelParams,
} from "./helpers";

function hf(overrides: Partial<HuggingFaceInfo> = {}): HuggingFaceInfo {
  return {
    id: "acme/Coder",
    author: "acme",
    license: "mit",
    license_name: "",
    pipeline_tag: "",
    library_name: "",
    downloads: 1,
    downloads_all_time: null,
    likes: 0,
    parameters: null,
    parameters_by_dtype: {},
    architectures: [],
    model_type: "",
    base_models: [],
    languages: [],
    arxiv: [],
    gated: false,
    created_at: null,
    last_modified: null,
    spaces_count: 3,
    fetched_at: "2026-11-02T00:00:00Z",
    ...overrides,
  };
}

function params(overrides: Partial<ModelParams> = {}): ModelParams {
  return {
    checkpoint_total: 30_532_122_624,
    checkpoint_note: null,
    reported_total: null,
    active: 3_353_032_704,
    active_source: "config_estimate",
    active_note: null,
    config_estimate: 3_353_032_704,
    is_moe: true,
    hf_id: "acme/Coder",
    ...overrides,
  };
}

function model(overrides: Partial<LLMModelDetail> = {}): LLMModelDetail {
  return {
    id: 1,
    model_id: "local-opencode-go/deepseek-v4-pro",
    canonical_slug: "local-opencode-go_deepseek-v4-pro",
    provider: "opencode-go",
    model_name: "deepseek-v4-pro",
    description: "",
    is_free: true,
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

describe("model page formatters", () => {
  it("formats bytes in decimal units", () => {
    expect(fmtBytes(510_296_708_312)).toBe("510 GB");
    expect(fmtBytes(61_066_575_656)).toBe("61.1 GB");
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(null)).toBe("—");
  });

  it("labels active parameters with their source and marks estimates", () => {
    expect(fmtActiveParams(params())).toBe("~3.4B · estimate");
    expect(
      fmtActiveParams(
        params({ active: 16e9, active_source: "model_card_manual" }),
      ),
    ).toBe("16.0B · model card");
    expect(fmtActiveParams(params({ active: null, active_source: null }))).toBe(
      "—",
    );
    expect(fmtActiveParams(null)).toBe("—");
  });

  it("shows a capped Spaces count as 100+", () => {
    expect(
      fmtSpaces(hf({ spaces_count: 100, spaces_count_capped: true })),
    ).toBe("100+");
    expect(fmtSpaces(hf({ spaces_count: 3 }))).toBe("3");
    expect(fmtSpaces(null)).toBe("—");
  });
});

describe("metadataView", () => {
  it("uses a catalog model's own metadata", () => {
    const own = hf({ id: "deepseek-ai/DeepSeek-V4-Pro" });
    const view = metadataView(
      model({
        is_local: false,
        metadata: { huggingface: own, params: params() },
      }),
    );
    expect(view.hf).toBe(own);
    expect(view.inheritedFrom).toBeNull();
  });

  it("shows a local model its twin's metadata, flagged as inherited", () => {
    const twinHf = hf({ id: "deepseek-ai/DeepSeek-V4-Pro" });
    const view = metadataView(
      model({
        is_local: true,
        metadata: { ollama: {} },
        twin: {
          link: {
            model_id: "deepseek/deepseek-v4-pro",
            source: "models.dev:opencode-go",
          },
          model: {
            id: 207,
            model_id: "deepseek/deepseek-v4-pro",
            canonical_slug: "deepseek_deepseek-v4-pro-20260423",
            model_name: "DeepSeek V4 Pro",
            provider: "deepseek",
            context_window: 1_048_576,
            max_output_tokens: 0,
          },
          metadata: {
            huggingface: twinHf,
            params: params({
              active: 49e9,
              active_source: "model_card_manual",
            }),
          },
          capabilities: { hugging_face_id: "deepseek-ai/DeepSeek-V4-Pro" },
        },
      }),
    );
    expect(view.hf).toBe(twinHf);
    expect(view.params?.active).toBe(49e9);
    expect(view.inheritedFrom).toBe("deepseek/deepseek-v4-pro");
    expect(view.inheritedSlug).toBe("deepseek_deepseek-v4-pro-20260423");
    expect(view.caps.hugging_face_id).toBe("deepseek-ai/DeepSeek-V4-Pro");
  });

  it("leaves a local model without a twin empty instead of guessing", () => {
    const view = metadataView(
      model({ is_local: true, metadata: { ollama: {} }, twin: null }),
    );
    expect(view.hf).toBeNull();
    expect(view.params).toBeNull();
    expect(view.inheritedFrom).toBeNull();
  });
});
