import type { LLMModelDetail, ModelTwin } from "$lib/api/client";
import { toast } from "svelte-sonner";

export { formatPrice, formatTokens } from "$lib/utils/formatters";

export function formatNumber(n: number): string {
  return n.toLocaleString();
}

export function costEfficiencyGrade(score: number): {
  grade: string;
  color: string;
} {
  if (score >= 0.9) return { grade: "A+", color: "text-success-fg" };
  if (score >= 0.8) return { grade: "A", color: "text-success-fg" };
  if (score >= 0.7) return { grade: "A-", color: "text-success-fg" };
  if (score >= 0.6) return { grade: "B+", color: "text-info-fg" };
  if (score >= 0.5) return { grade: "B", color: "text-info-fg" };
  if (score >= 0.4) return { grade: "B-", color: "text-info-fg" };
  if (score >= 0.3) return { grade: "C+", color: "text-warning-fg" };
  if (score >= 0.2) return { grade: "C", color: "text-warning-fg" };
  return { grade: "D", color: "text-danger-fg" };
}

export function getMeta(m: LLMModelDetail): Record<string, unknown> {
  return (m.metadata || {}) as Record<string, unknown>;
}

export function getCaps(m: LLMModelDetail): Record<string, unknown> {
  return (m.capabilities_json || {}) as Record<string, unknown>;
}

export function getArchitecture(m: LLMModelDetail): Record<string, unknown> {
  const caps = getCaps(m);
  return (caps.architecture || {}) as Record<string, unknown>;
}

export function getSupportedParameters(m: LLMModelDetail): string[] {
  const caps = getCaps(m);
  const meta = getMeta(m);
  const params =
    (caps.supported_parameters as string[]) ||
    (meta.openrouter_supported_parameters as string[]) ||
    [];
  return params;
}

export function getPerRequestLimits(m: LLMModelDetail): Record<string, number> {
  const caps = getCaps(m);
  return (caps.per_request_limits || {}) as Record<string, number>;
}

export function getDefaultParameters(
  m: LLMModelDetail,
): Record<string, unknown> {
  const caps = getCaps(m);
  return (caps.default_parameters || {}) as Record<string, unknown>;
}

export async function copyToClipboard(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
  toast.success("Copied to clipboard");
}

export function exportJson(m: LLMModelDetail): void {
  const data = {
    model: m,
    capabilities_json: m.capabilities_json,
    metadata: m.metadata,
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${m.canonical_slug}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function stripMarkdown(text: string): string {
  return text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_]{1,2}([^*_]+)[*_]{1,2}/g, "$1")
    .replace(/#{1,6}\s/g, "")
    .replace(/`([^`]+)`/g, "$1");
}

// ── Enrichment payloads (see backend/llm_models/services/enrichment.py) ──

export interface ModelsDevCost {
  input?: number;
  output?: number;
  cache_read?: number;
  cache_write?: number;
}

/** models.dev record stored in `metadata.models_dev` (all fields optional). */
export interface ModelsDevInfo {
  id?: string;
  family?: string;
  knowledge?: string;
  release_date?: string;
  last_updated?: string;
  open_weights?: boolean;
  reasoning?: boolean;
  reasoning_options?: {
    type: string;
    values?: string[];
    min?: number;
    max?: number;
  }[];
  tool_call?: boolean;
  structured_output?: boolean;
  temperature?: boolean;
  attachment?: boolean;
  modalities?: { input?: string[]; output?: string[] };
  limit?: { context?: number; output?: number; input?: number };
  cost?: ModelsDevCost & {
    tiers?: (ModelsDevCost & { tier?: { type?: string; size?: number } })[];
  };
  status?: string;
  fetched_at?: string;
}

/** Hugging Face Hub record stored in `metadata.huggingface`. */
export interface HuggingFaceInfo {
  id: string;
  author: string;
  license: string;
  license_name: string;
  pipeline_tag: string;
  library_name: string;
  downloads: number;
  downloads_all_time: number | null;
  likes: number;
  parameters: number | null;
  parameters_by_dtype: Record<string, number>;
  architectures: string[];
  model_type: string;
  base_models: string[];
  languages: string[];
  arxiv: string[];
  gated: boolean;
  created_at: string | null;
  last_modified: string | null;
  spaces_count: number;
  fetched_at: string;
  // ── expand[] / repo-file fields (absent on records fetched before 2026-11) ──
  sha?: string | null;
  used_storage_bytes?: number | null;
  trending_score?: number | null;
  children?: Record<
    "adapter" | "merge" | "quantized" | "finetune",
    number
  > | null;
  base_model_relation?: string | null;
  base_models_expanded?: string[];
  inference_providers?: HfInferenceProvider[] | null;
  native_quant_method?: string | null;
  /** The Hub truncates the Spaces list at 100: `spaces_count` is then a floor. */
  spaces_count_capped?: boolean;
  eval_results?: HfEvalResult[];
  /** Scores published before the 60-row storage cap (absent on older records). */
  eval_results_total?: number;
  architecture?: HfArchitecture | null;
  generation_config?: HfGenerationConfig | null;
  tokenizer?: HfTokenizer | null;
  files?: HfFiles | null;
  papers?: HfPaper[];
  /** Per sub-fetch: ok / not_found / gated_inaccessible / rate_limited / error. */
  fetch_status?: Record<string, string>;
  gated_inaccessible?: boolean;
  revision?: string;
  /** Whether the fetch used a Hugging Face token (never the token itself). */
  authenticated?: boolean;
  schema?: number;
}

export interface HfInferenceProvider {
  provider: string;
  status: string | null;
  task: string | null;
  provider_model_id: string | null;
}

/** A benchmark score published in the model repo: self-reported, never verified here. */
export interface HfEvalResult {
  task: string | null;
  dataset: string | null;
  metric: string | null;
  value: number;
  date: string | null;
  source: string;
  source_name: string | null;
  source_url: string | null;
  notes: string | null;
  verified: boolean;
}

/** Structural facts parsed from the repo's `config.json` (text part of multimodal configs). */
export interface HfArchitecture {
  model_type: string | null;
  text_model_type: string | null;
  architectures: string[];
  multimodal: boolean;
  num_hidden_layers: number | null;
  hidden_size: number | null;
  num_attention_heads: number | null;
  num_key_value_heads: number | null;
  head_dim: number | null;
  intermediate_size: number | null;
  vocab_size: number | null;
  max_position_embeddings: number | null;
  /** Pre-extension context when rope scaling stretches it, else max_position_embeddings. */
  native_context: number | null;
  rope: {
    theta: number | null;
    scaling_type: string | null;
    factor: number | null;
    original_max_position_embeddings: number | null;
  } | null;
  attention: {
    variant: string | null;
    kv_lora_rank: number | null;
    q_lora_rank: number | null;
    sliding_window: number | null;
    layer_types: Record<string, number> | null;
  } | null;
  tie_word_embeddings: boolean | null;
  torch_dtype: string | null;
  quantization_config: {
    quant_method?: string;
    bits?: number;
    fmt?: string;
    weight_block_size?: number[];
    expert_dtype?: string;
  } | null;
  is_moe: boolean;
  /** Any `*expert*` key with a real value (nested blocks included): "dense" is not claimed when true. */
  has_expert_keys?: boolean;
  moe: {
    num_experts: number;
    experts_per_token: number | null;
    num_shared_experts: number | null;
    moe_intermediate_size: number | null;
    first_k_dense_replace: number | null;
    num_nextn_predict_layers: number | null;
  } | null;
  uncovered_features?: string[];
}

/** The authors' recommended sampling (`generation_config.json`) — never what a provider used. */
export interface HfGenerationConfig {
  temperature?: number;
  top_p?: number;
  top_k?: number;
  min_p?: number;
  repetition_penalty?: number;
  do_sample?: boolean;
  max_new_tokens?: number;
  /** Written by transformers from the model config, not tuned by the authors. */
  from_model_config: boolean;
  commit: string | null;
}

export interface HfTokenizer {
  tokenizer_class: string | null;
  model_max_length: number | null;
  has_chat_template: boolean;
  chat_template_sha256: string | null;
  chat_template_source: string | null;
  bos_token: string | null;
  eos_token: string | null;
}

export interface HfFiles {
  weight_format: string | null;
  weight_files: number;
  weights_bytes: number | null;
  shard_count: number | null;
  other_weight_formats: string[];
  repo_files: number;
  file_oids: Record<string, string>;
}

export interface HfPaper {
  id: string;
  title: string | null;
  published_at: string | null;
  upvotes: number | null;
  github_repo: string | null;
}

/** `metadata.params`: checkpoint vs reported vs active parameters, each with its source. */
export interface ModelParams {
  checkpoint_total: number | null;
  checkpoint_note: string | null;
  reported_total: number | null;
  active: number | null;
  /** `model_card_manual` | `name_pattern` | `dense` | `config_estimate` | null. */
  active_source: string | null;
  active_note: string | null;
  /** Config-based estimate kept as a cross-check (labelled an estimate wherever shown). */
  config_estimate: number | null;
  is_moe: boolean | null;
  hf_id: string | null;
  mapping_note?: string;
}

/** OpenRouter `reasoning` block of the catalog payload. */
export interface ReasoningInfo {
  mandatory?: boolean;
  /** Whether a request without reasoning settings reasons (what a provider_default run gets). */
  default_enabled?: boolean;
  default_effort?: string;
  supported_efforts?: string[];
  supports_max_tokens?: boolean;
}

export interface DesignArenaEntry {
  arena: string;
  category: string;
  elo: number;
  rank: number;
  win_rate: number;
}

export interface ArtificialAnalysisIndices {
  intelligence_index?: number;
  coding_index?: number;
  agentic_index?: number;
}

export function getModelsDev(m: LLMModelDetail): ModelsDevInfo | null {
  return (getMeta(m).models_dev as ModelsDevInfo | undefined) ?? null;
}

export function getHuggingFace(m: LLMModelDetail): HuggingFaceInfo | null {
  return (getMeta(m).huggingface as HuggingFaceInfo | undefined) ?? null;
}

export function getReasoning(m: LLMModelDetail): ReasoningInfo | null {
  return (getCaps(m).reasoning as ReasoningInfo | undefined) ?? null;
}

export function getDesignArena(m: LLMModelDetail): DesignArenaEntry[] {
  const b = (getCaps(m).benchmarks || {}) as Record<string, unknown>;
  return Array.isArray(b.design_arena)
    ? (b.design_arena as DesignArenaEntry[])
    : [];
}

export function getArtificialAnalysis(
  m: LLMModelDetail,
): ArtificialAnalysisIndices | null {
  const b = (getCaps(m).benchmarks || {}) as Record<string, unknown>;
  return (
    (b.artificial_analysis as ArtificialAnalysisIndices | undefined) ?? null
  );
}

/** Unix seconds (OpenRouter `created`) or ISO date → "2026-02-17". */
export function isoDay(value: unknown): string | null {
  if (typeof value === "number" && value > 0)
    return new Date(value * 1000).toISOString().slice(0, 10);
  if (typeof value === "string" && value) return value.slice(0, 10);
  return null;
}

/** 0–1 rate → "83%" ("—" when null). */
export function fmtRate(rate: number | null | undefined, digits = 0): string {
  return rate == null ? "—" : `${(rate * 100).toFixed(digits)}%`;
}

/** Wilson interval → "55–95%" ("" when unknown). */
export function fmtCI(
  low: number | null | undefined,
  high: number | null | undefined,
): string {
  if (low == null || high == null) return "";
  return `${Math.round(low * 100)}–${Math.round(high * 100)}%`;
}

/** Compact count → "1.4M" / "4.5K" / "12". */
export function compactNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

/** USD amount with precision that scales down for small sums. */
export function fmtUsd(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 100) return `$${n.toFixed(2)}`;
  return `$${Math.round(n).toLocaleString()}`;
}

/**
 * A context window / max-output limit, or null when unknown. The API stores
 * "not reported" as 0 (local endpoint models, some catalog entries); it must
 * render as "—" and never enter a catalog comparison as a real 0-token limit.
 */
export function knownTokenLimit(n: number | null | undefined): number | null {
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? n : null;
}

/** Parameter count → "685B" / "7.2B" / "350M". */
export function fmtParams(n: number | null | undefined): string {
  if (!n) return "—";
  if (n >= 1e12) return `${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(n >= 1e11 ? 0 : 1)}B`;
  return `${Math.round(n / 1e6)}M`;
}

/** Byte count → "510.3 GB" (decimal units, as the Hub reports them). */
export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export const PARAM_SOURCE_LABELS: Record<string, string> = {
  model_card_manual: "model card",
  name_pattern: "name",
  dense: "dense",
  config_estimate: "estimate",
};

/** Active parameters with their provenance → "16B · model card" ("—" when unknown). */
export function fmtActiveParams(p: ModelParams | null | undefined): string {
  if (!p || p.active == null) return "—";
  const approx = p.active_source === "config_estimate" ? "~" : "";
  const src = p.active_source
    ? (PARAM_SOURCE_LABELS[p.active_source] ?? p.active_source)
    : "";
  return `${approx}${fmtParams(p.active)}${src ? ` · ${src}` : ""}`;
}

/** HF Spaces count; "100+" when the Hub truncated the list. */
export function fmtSpaces(hf: HuggingFaceInfo | null | undefined): string {
  if (!hf) return "—";
  return hf.spaces_count_capped || hf.spaces_count >= 100
    ? "100+"
    : compactNumber(hf.spaces_count);
}

export function getParams(m: LLMModelDetail): ModelParams | null {
  return (getMeta(m).params as ModelParams | undefined) ?? null;
}

/**
 * Where the model page reads external metadata from. A local endpoint model
 * has none of its own: it shows its catalog twin's, flagged `inheritedFrom`
 * (an assumption: same name ⇒ same weights). Platform measurements never
 * come from the twin.
 */
export interface MetadataView {
  hf: HuggingFaceInfo | null;
  md: ModelsDevInfo | null;
  params: ModelParams | null;
  /** Metadata object for panels reading other keys (epoch, lmarena). */
  meta: Record<string, unknown>;
  /** Catalog payload subset (hugging_face_id, reasoning, default_parameters, benchmarks). */
  caps: Record<string, unknown>;
  /** Twin model id when the values above are inherited, else null. */
  inheritedFrom: string | null;
  inheritedSlug: string | null;
  twin: ModelTwin | null;
}

export function metadataView(m: LLMModelDetail): MetadataView {
  const own: MetadataView = {
    hf: getHuggingFace(m),
    md: getModelsDev(m),
    params: getParams(m),
    meta: getMeta(m),
    caps: getCaps(m),
    inheritedFrom: null,
    inheritedSlug: null,
    twin: m.twin ?? null,
  };
  const twin = m.twin;
  if (!m.is_local || !twin?.model) return own;
  const tm = (twin.metadata ?? {}) as Record<string, unknown>;
  return {
    hf: (tm.huggingface as HuggingFaceInfo | undefined) ?? null,
    md: (tm.models_dev as ModelsDevInfo | undefined) ?? null,
    params: (tm.params as ModelParams | undefined) ?? null,
    meta: tm,
    caps: (twin.capabilities ?? {}) as Record<string, unknown>,
    inheritedFrom: twin.model.model_id,
    inheritedSlug: twin.model.canonical_slug,
    twin,
  };
}

/** Short git sha for display. */
export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : "—";
}
