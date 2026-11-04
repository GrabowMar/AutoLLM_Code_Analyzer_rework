import { apiFetch } from "./core";

/**
 * Per-call LLM telemetry (`GenerationArtifact.telemetry` of GenerationRuns): provider performance
 * per model and the per-call CSV dataset. Descriptive only; every value that
 * was not measured is `null` (shown as "—"/"n/a"), never 0.
 */

/** Median (and optionally p90) of a per-call measure over `n` calls. */
export interface CallQuantiles {
  n: number;
  median: number | null;
  p90?: number | null;
}

/** A pooled ratio (sum/sum) over `n` calls that reported both parts. */
export interface CallRatio {
  value: number | null;
  n: number;
}

export interface CallCoverage {
  /** Calls with an OpenRouter `/generation` record. */
  openrouter_ok: number;
  /** Calls whose record OpenRouter no longer has (404). */
  openrouter_unavailable: number;
  /** Calls with an OpenRouter `gen-` id (the only ones that can have a record). */
  openrouter_eligible: number;
  share: number | null;
  share_of_eligible: number | null;
}

export interface CallSummary {
  n_calls: number;
  n_runs: number;
  /** Calls per run kind (`app_agentic`, `slot_single_shot`, ...). */
  kinds: Record<string, number>;
  /** OpenRouter-measured time to first token (ms), incl. provider queueing/prefill. */
  ttft_ms: CallQuantiles;
  generation_time_ms: CallQuantiles;
  /** completion tokens / (generation time - TTFT), OpenRouter calls only; null below a 250 ms span. */
  decode_tps: CallQuantiles;
  /** Platform-measured call duration (s); retried calls and calls without a recorded retry count excluded. */
  duration_s: CallQuantiles;
  /** completion tokens / call duration (includes prefill and queueing). */
  e2e_output_tps: CallQuantiles;
  cache_hit_ratio: CallRatio;
  reasoning_share: CallRatio;
  retry_rate: CallRatio;
  error_rate: CallRatio;
  fallback_rate: CallRatio;
  finish_reasons: Record<string, number>;
  served_models: Record<string, number>;
  model_permaslugs: Record<string, number>;
  coverage: CallCoverage;
}

export interface ProviderCallSummary extends CallSummary {
  provider: string;
}

export interface ModelCallTelemetry {
  model_id: number;
  model: string;
  /** The run-kind filter that was applied, or null for every kind. */
  kind: string | null;
  overall: CallSummary;
  providers: ProviderCallSummary[];
  notes: string[];
}

export async function getModelCallTelemetry(
  modelId: number,
  opts: { kind?: string; signal?: AbortSignal } = {},
): Promise<ModelCallTelemetry> {
  const query = opts.kind ? `?kind=${encodeURIComponent(opts.kind)}` : "";
  const res = await apiFetch(
    `/generation/models/${modelId}/call-telemetry/${query}`,
    { signal: opts.signal },
  );
  return res.json();
}

/**
 * Download URL of the per-call CSV (one row per LLM call; never message content). Filters
 * combine; at least one is required.
 */
export function callsCsvUrl(filter: {
  study?: string;
  run?: string;
  model?: number;
}): string {
  const params = new URLSearchParams();
  if (filter.study) params.set("study", filter.study);
  if (filter.run) params.set("run", filter.run);
  if (filter.model !== undefined) params.set("model", String(filter.model));
  return `/api/generation/calls.csv?${params.toString()}`;
}
