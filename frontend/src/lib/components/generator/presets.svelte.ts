/**
 * Run-preset cache + display helpers. `invalidatePresets(kind)` bumps a
 * reactive version, so every mounted PresetPicker of that kind reloads (e.g.
 * after the composer's "New harness preset" dialog saved one).
 */
import { browser } from "$app/environment";
import {
  listRunPresets,
  type BudgetPresetConfig,
  type HarnessOptionField,
  type HarnessOptions,
  type HarnessPresetConfig,
  type PresetConfigByKind,
  type PresetKind,
  type RunPreset,
  type SamplingPresetConfig,
} from "$lib/api/generation";
import { formatTokens } from "$lib/utils/formatters";
import { summarizeLLMParams } from "./llm-params";

export const PRESET_KIND_LABELS: Record<PresetKind, string> = {
  harness: "Harness preset",
  sampling: "Sampling preset",
  budget: "Budget preset",
};

const versions = $state<Record<PresetKind, number>>({
  harness: 0,
  sampling: 0,
  budget: 0,
});
const cache = new Map<PresetKind, Promise<RunPreset[]>>();

/** Reactive: read it inside an effect/derived to reload after invalidation. */
export function presetsVersion(kind: PresetKind): number {
  return versions[kind];
}

/** Latest non-archived presets of a kind (system ∪ own), cached per page load. */
export function loadPresets<K extends PresetKind>(
  kind: K,
  options: { force?: boolean } = {},
): Promise<RunPreset<K>[]> {
  // Never cache on the server: module state is shared between requests.
  if (!browser) return listRunPresets(kind);
  let request = cache.get(kind);
  if (!request || options.force) {
    const fresh: Promise<RunPreset[]> = listRunPresets(kind);
    cache.set(kind, fresh);
    fresh.catch(() => {
      if (cache.get(kind) === fresh) cache.delete(kind);
    });
    request = fresh;
  }
  return request as Promise<RunPreset<K>[]>;
}

/** Drop cached lists (all kinds when omitted) and notify mounted pickers. */
export function invalidatePresets(kind?: PresetKind): void {
  const kinds: PresetKind[] = kind ? [kind] : ["harness", "sampling", "budget"];
  for (const k of kinds) {
    cache.delete(k);
    versions[k] += 1;
  }
}

/** Library deep link for managing presets of a kind. */
export function presetManageHref(kind: PresetKind): string {
  return `/templates?tab=presets&kind=${kind}`;
}

function formatOptionValue(
  value: unknown,
  field: HarnessOptionField | undefined,
): string {
  if (typeof value === "boolean") return value ? "on" : "off";
  if (Array.isArray(value)) {
    return value
      .map(
        (v) => field?.choices?.find((c) => c.value === v)?.label ?? String(v),
      )
      .join("+");
  }
  return String(value);
}

/** `["edit_format=whole", "map_tokens=0"]` for non-default options. */
export function harnessOptionChips(
  options: HarnessOptions | null | undefined,
  schema: HarnessOptionField[] = [],
): string[] {
  return Object.entries(options ?? {}).map(([key, value]) => {
    const field = schema.find((f) => f.key === key);
    return `${key}=${formatOptionValue(value, field)}`;
  });
}

function formatWallClock(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = seconds / 60;
  return `${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} min`;
}

/** "30 requests · 60 min · $3 · network none" — empty for platform defaults. */
export function summarizeBudget(
  config: BudgetPresetConfig | null | undefined,
): string {
  if (!config) return "";
  const parts: string[] = [];
  if (config.max_llm_requests != null)
    parts.push(`${config.max_llm_requests} requests`);
  if (config.wall_clock_seconds != null)
    parts.push(formatWallClock(config.wall_clock_seconds));
  if (config.max_cost_usd != null) parts.push(`$${config.max_cost_usd}`);
  if (config.max_total_tokens != null)
    parts.push(`${formatTokens(config.max_total_tokens)} tokens`);
  if (config.network_policy) parts.push(`network ${config.network_policy}`);
  return parts.join(" · ");
}

/** One-line description of a preset config, with a neutral text for defaults. */
export function summarizePresetConfig<K extends PresetKind>(
  kind: K,
  config: PresetConfigByKind[K] | null | undefined,
  schema: HarnessOptionField[] = [],
): string {
  if (kind === "harness") {
    const harness = config as HarnessPresetConfig | undefined;
    const chips = harnessOptionChips(harness?.options, schema);
    const name = harness?.harness ?? "harness";
    return chips.length
      ? `${name} · ${chips.join(", ")}`
      : `${name} · default options`;
  }
  if (kind === "sampling") {
    const sampling = config as SamplingPresetConfig | undefined;
    const parts = [summarizeLLMParams(sampling?.llm_params)].filter(Boolean);
    if (sampling?.sampling_policy === "harness_native")
      parts.push("harness-native sampling");
    return parts.join(" · ") || "Provider defaults";
  }
  return (
    summarizeBudget(config as BudgetPresetConfig | undefined) ||
    "Platform defaults"
  );
}

export function summarizePreset(
  preset: RunPreset,
  schema: HarnessOptionField[] = [],
): string {
  return summarizePresetConfig(preset.kind, preset.config, schema);
}
