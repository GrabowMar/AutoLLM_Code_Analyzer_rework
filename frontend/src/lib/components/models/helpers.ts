import type { LLMModelDetail } from "$lib/api/client";
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
}

/** OpenRouter `reasoning` block of the catalog payload. */
export interface ReasoningInfo {
  mandatory?: boolean;
  default_effort?: string;
  supported_efforts?: string[];
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

/** Parameter count → "685B" / "7.2B" / "350M". */
export function fmtParams(n: number | null | undefined): string {
  if (!n) return "—";
  if (n >= 1e12) return `${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(n >= 1e11 ? 0 : 1)}B`;
  return `${Math.round(n / 1e6)}M`;
}
