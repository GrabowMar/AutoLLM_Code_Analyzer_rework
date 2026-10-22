/**
 * LLM sampling-param vocabulary shared by LLMParamsEditor, preset summaries
 * and run summaries. Ranges mirror `LLMParamsSchema` / `validate_llm_params`.
 */
import type { LLMParams } from "$lib/api/generation";

export type LLMNumberKey =
  | "temperature"
  | "top_p"
  | "top_k"
  | "min_p"
  | "max_tokens"
  | "frequency_penalty"
  | "presence_penalty"
  | "repetition_penalty";

export interface LLMNumberField {
  key: LLMNumberKey;
  label: string;
  min: number;
  max: number;
  step: number;
  integer: boolean;
  /** 'basic' fields are always visible; 'more' ones sit behind a toggle. */
  group: "basic" | "more";
  hint?: string;
}

export const LLM_NUMBER_FIELDS: LLMNumberField[] = [
  {
    key: "temperature",
    label: "Temperature",
    min: 0,
    max: 2,
    step: 0.05,
    integer: false,
    group: "basic",
  },
  {
    key: "top_p",
    label: "Top P",
    min: 0,
    max: 1,
    step: 0.05,
    integer: false,
    group: "basic",
  },
  {
    key: "max_tokens",
    label: "Output cap (tokens)",
    min: 1,
    max: 2_000_000,
    step: 1,
    integer: true,
    group: "basic",
    hint: "Per call; clamped to each model's own output limit.",
  },
  {
    key: "top_k",
    label: "Top K",
    min: 1,
    max: 1000,
    step: 1,
    integer: true,
    group: "more",
  },
  {
    key: "min_p",
    label: "Min P",
    min: 0,
    max: 1,
    step: 0.01,
    integer: false,
    group: "more",
  },
  {
    key: "frequency_penalty",
    label: "Frequency penalty",
    min: -2,
    max: 2,
    step: 0.1,
    integer: false,
    group: "more",
  },
  {
    key: "presence_penalty",
    label: "Presence penalty",
    min: -2,
    max: 2,
    step: 0.1,
    integer: false,
    group: "more",
  },
  {
    key: "repetition_penalty",
    label: "Repetition penalty",
    min: 0,
    max: 2,
    step: 0.05,
    integer: false,
    group: "more",
  },
];

export const REASONING_EFFORTS = ["low", "medium", "high"] as const;

/** Keys every mode may set; stop / response_format / provider depend on the mode. */
export const LLM_PARAM_KEYS: (keyof LLMParams)[] = [
  "temperature",
  "top_p",
  "top_k",
  "min_p",
  "max_tokens",
  "frequency_penalty",
  "presence_penalty",
  "repetition_penalty",
  "reasoning",
  "stop",
  "response_format",
  "provider",
];

const SOURCE_LABELS: Record<string, string> = {
  mode_default: "mode default",
  max_tokens: "default cap",
  profile: "profile",
  preset: "preset",
  inline: "override",
  llm_defaults: "experiment",
  param_overrides: "condition",
};

/** Human label for a value source ("mode_default" → "mode default"). */
export function llmSourceLabel(source: string | undefined | null): string {
  if (!source) return "";
  return SOURCE_LABELS[source] ?? source.replace(/_/g, " ");
}

function isEmptyObject(value: unknown): boolean {
  return (
    !!value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === 0
  );
}

/**
 * Drop unset values so a params object only carries explicit choices:
 * null / undefined / '' / [] / {} are removed, as are empty reasoning keys.
 * An empty `provider` object is dropped on purpose: sending `{}` would
 * replace the default routing.
 */
export function cleanLLMParams(
  params: LLMParams | null | undefined,
): LLMParams {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value) && value.length === 0) continue;
    if (key === "reasoning" && value && typeof value === "object") {
      const reasoning = Object.fromEntries(
        Object.entries(value).filter(
          ([, v]) => v !== null && v !== undefined && v !== "",
        ),
      );
      if (Object.keys(reasoning).length > 0) out.reasoning = reasoning;
      continue;
    }
    if (isEmptyObject(value)) continue;
    out[key] = value;
  }
  return out as LLMParams;
}

/** Compact display of one value ("high", "0.2", "[\"END\"]"). */
export function formatLLMValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (key === "reasoning" && typeof value === "object") {
    const r = value as Record<string, unknown>;
    const parts: string[] = [];
    if (r.effort) parts.push(String(r.effort));
    if (r.max_tokens) parts.push(`${r.max_tokens} tok`);
    if (r.enabled === false) parts.push("off");
    if (r.exclude) parts.push("hidden");
    return parts.join(", ") || "on";
  }
  if (key === "response_format" && typeof value === "object") {
    return String((value as Record<string, unknown>).type ?? "custom");
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** "temperature 0 · reasoning high" — empty string for no explicit params. */
export function summarizeLLMParams(
  params: LLMParams | null | undefined,
): string {
  const clean = cleanLLMParams(params);
  return LLM_PARAM_KEYS.filter((key) => key in clean)
    .map((key) => `${key} ${formatLLMValue(key, clean[key])}`)
    .join(" · ");
}

/**
 * Value for `<Input type="number">`. The Input prop is typed as a string, but a
 * number input binds numbers; handing it the number (not `String(n)`) keeps
 * Svelte from rewriting a partial entry such as "-0" while the user types.
 */
export function numberInputValue(value: unknown): string {
  return (typeof value === "number" ? value : "") as unknown as string;
}
