/**
 * Display helpers shared by the insights pages (rankings, statistics,
 * reports). Missing data renders through `NaValue` / `MaybeValue` ("—" +
 * reason), never as 0: these wrappers of the shared analysis formatters
 * (`$lib/components/analysis/format`) return `null` for missing input so
 * callers must decide.
 */
import type { OutcomeKey } from "$lib/api/analysis";
import * as core from "$lib/components/analysis/format";
import { OUTCOME_LABELS as RUN_OUTCOME_LABELS } from "$lib/components/analysis/run-utils";

export const DASH = core.NA;

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Integer with locale grouping, or null. */
export function fmtInt(v: number | null | undefined): string | null {
  return isNum(v) ? core.fmtInt(v) : null;
}

/** Number with at most `digits` fraction digits, or null. */
export function fmtNum(
  v: number | null | undefined,
  digits = 2,
): string | null {
  return isNum(v) ? core.fmtNum(v, digits) : null;
}

/** A 0..1 share as a percentage ("91.2%"), or null. */
export function fmtShare(
  v: number | null | undefined,
  digits = 1,
): string | null {
  return isNum(v) ? core.fmtPct(v, digits) : null;
}

/** A 0..1 score on the 0..100 display scale ("91.2"), or null. */
export function fmtScore(v: number | null | undefined): string | null {
  return isNum(v) ? (v * 100).toFixed(1) : null;
}

/** USD with enough precision for sub-cent LLM costs, or null. */
export function fmtUsd(v: number | null | undefined): string | null {
  return isNum(v) ? core.fmtUsd(v) : null;
}

/** Seconds → "42 s" / "3 min 12 s", or null. */
export function fmtSeconds(v: number | null | undefined): string | null {
  return isNum(v) ? core.fmtSeconds(v) : null;
}

/** Signed number ("+2.5" / "−1.2"), or null. */
export function fmtSigned(
  v: number | null | undefined,
  digits = 2,
): string | null {
  if (!isNum(v)) return null;
  const abs = core.fmtNum(Math.abs(v), digits);
  if (v > 0) return `+${abs}`;
  if (v < 0) return `−${abs}`;
  return "0";
}

/** First characters of a plan/sample hash for display ("" when missing, for inline labels). */
export function shortHash(hash: string | null | undefined, n = 10): string {
  return hash ? core.shortHash(hash, n) : "";
}

/** `excluded_reasons` → "truncated" etc. */
export function humanize(key: string): string {
  return key.replace(/_/g, " ");
}

/** ISO timestamp → locale date + time ("—" when missing). */
export const formatTimestamp = core.fmtDateTime;

/** Run detail page of a canonical run. */
export function runHref(runId: string): string {
  return `/analysis/${encodeURIComponent(runId)}`;
}

/** Generated application (generation job) page. */
export function jobHref(jobId: string): string {
  return `/applications/${encodeURIComponent(jobId)}`;
}

/** Runs list filtered to one analysis plan. */
export function planRunsHref(planHash: string): string {
  return `/analysis?plan_hash=${encodeURIComponent(planHash)}`;
}

export interface OutcomeLabel {
  label: string;
  ai: boolean;
  digits: number;
  /** Render the value as a percentage of 0..1. */
  share?: boolean;
}

const OUTCOME_DIGITS: Record<string, number> = {
  density: 2,
  count: 1,
  binary: 1,
  rate: 1,
  metric: 2,
  percent: 2,
};

/**
 * Display meta of a `measurement.OUTCOMES` key, derived from the one
 * outcome vocabulary (`OUTCOME_LABELS` in analysis/run-utils); unknown keys
 * fall back to the key. Counts get one decimal: report rows are means.
 */
export function outcomeLabel(key: string): OutcomeLabel {
  const meta = RUN_OUTCOME_LABELS[key as OutcomeKey];
  if (!meta) {
    return { label: humanize(key), ai: key.startsWith("ai_"), digits: 2 };
  }
  return {
    label: meta.label,
    ai: meta.group === "ai",
    digits: key === "measured_sloc" ? 0 : (OUTCOME_DIGITS[meta.format] ?? 2),
    share: meta.format === "rate" || meta.format === "binary",
  };
}

/** Human labels of exclusion reason codes. */
export const EXCLUSION_LABELS: Record<string, string> = {
  truncated: "generation truncated",
  invalid: "agent marked invalid",
  empty_output: "nothing written by the model",
  below_min_sloc: "below the minimum SLOC",
  functional_missing: "no functional result",
  no_measurement: "no stored measurement",
  unsupported_measurement: "unsupported measurement schema",
  measurement_error: "measurement could not be read",
};

export function exclusionLabel(code: string): string {
  return EXCLUSION_LABELS[code] ?? humanize(code);
}
