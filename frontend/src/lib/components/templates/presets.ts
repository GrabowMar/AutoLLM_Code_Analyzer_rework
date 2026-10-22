/**
 * Library-side helpers for run presets: slug rules mirroring
 * `backend/generation/services/presets.py`, content comparison, and the budget
 * field vocabulary shared by the preset editor and the experiment form.
 */
import {
  getRunPreset,
  type AgentBudgetKey,
  type AgentConfigInfo,
  type PresetKind,
  type RunPreset,
} from "$lib/api/generation";
import { loadPresets } from "$lib/components/generator/presets.svelte";

export const PRESET_KINDS: PresetKind[] = ["harness", "sampling", "budget"];

export const PRESET_KIND_TITLES: Record<PresetKind, string> = {
  harness: "Harness",
  sampling: "Sampling",
  budget: "Budget",
};

export function isPresetKind(value: unknown): value is PresetKind {
  return (
    typeof value === "string" && PRESET_KINDS.includes(value as PresetKind)
  );
}

const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,80}$/;
const RESERVED_SUFFIX = "-default";

/** Preset slugs are lowercase-dashed; never suggest a reserved `*-default` slug. */
export function presetSlugFromName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 81)
    .replace(/-+$/, "");
  return slug.endsWith(RESERVED_SUFFIX) ? `${slug}-copy` : slug;
}

/**
 * Why `slug` cannot name a new preset, checked against the presets this user
 * can see (the server also checks every other user's, without saying whose).
 */
export function presetSlugProblem(
  slug: string,
  takenSlugs: Iterable<string>,
): string {
  if (!slug) return "Required";
  if (!SLUG_PATTERN.test(slug)) {
    return "2–81 characters: lowercase letters, digits and dashes, starting with a letter or digit";
  }
  if (slug.endsWith(RESERVED_SUFFIX))
    return "Slugs ending in “-default” are reserved for system presets";
  for (const taken of takenSlugs) {
    if (taken === slug) return "Slug already in use";
  }
  return "";
}

/** JSON with sorted object keys, so equal configs compare equal. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value as Record<string, unknown>)
        .sort()
        .map((key) => [key, sortKeys((value as Record<string, unknown>)[key])]),
    );
  }
  return value;
}

/** Harness name a harness preset configures ("" for other kinds). */
export function presetHarness(preset: RunPreset): string {
  if (preset.kind !== "harness") return "";
  const config = preset.config as { harness?: unknown } | null;
  return typeof config?.harness === "string" ? config.harness : "";
}

export function shortHash(hash: string | null | undefined): string {
  return (hash ?? "").slice(0, 8);
}

/**
 * The preset behind a picker's id: from the cached latest-version list when
 * possible (no extra request), else fetched (older or archived versions).
 * Null when it cannot be loaded.
 */
export async function resolvePreset(
  kind: PresetKind,
  id: number,
): Promise<RunPreset | null> {
  try {
    const listed = (await loadPresets(kind)).find((p) => p.id === id);
    return listed ?? (await getRunPreset(id));
  } catch {
    try {
      return await getRunPreset(id);
    } catch {
      return null;
    }
  }
}

// ── Budget limits ─────────────────────────────────────────────────────

export interface BudgetField {
  key: AgentBudgetKey;
  label: string;
  /** Unit shown to the user; wall clock is edited in minutes, stored in seconds. */
  unit: string;
  /** stored value = shown value × scale */
  scale: number;
  integer: boolean;
  step: number;
  /** Used when /agent/config/ is unavailable (the server validates either way). */
  fallbackMin: number;
  fallbackMax: number;
}

export const BUDGET_FIELDS: BudgetField[] = [
  {
    key: "max_llm_requests",
    label: "LLM requests per run",
    unit: "",
    scale: 1,
    integer: true,
    step: 1,
    fallbackMin: 1,
    fallbackMax: 2000,
  },
  {
    key: "wall_clock_seconds",
    label: "Wall clock per run (min)",
    unit: "min",
    scale: 60,
    integer: false,
    step: 1,
    fallbackMin: 60,
    fallbackMax: 14400,
  },
  {
    key: "max_cost_usd",
    label: "Cost cap per run (USD)",
    unit: "$",
    scale: 1,
    integer: false,
    step: 0.5,
    fallbackMin: 0.001,
    fallbackMax: 100,
  },
  {
    key: "max_total_tokens",
    label: "Token cap per run",
    unit: "",
    scale: 1,
    integer: true,
    step: 1000,
    fallbackMin: 1000,
    fallbackMax: 200_000_000,
  },
];

/** Allowed range in the units the user edits (wall clock in minutes). */
export function budgetRange(
  field: BudgetField,
  config: AgentConfigInfo | null,
): { min: number; max: number } {
  const limit = config?.limits?.[field.key];
  const min = (limit?.min ?? field.fallbackMin) / field.scale;
  const max = (limit?.max ?? field.fallbackMax) / field.scale;
  return { min, max };
}

/** Platform default in display units, or null when unknown. */
export function budgetDefault(
  field: BudgetField,
  config: AgentConfigInfo | null,
): number | null {
  const value = config?.defaults?.[field.key];
  return typeof value === "number" ? value / field.scale : null;
}

/** Display value → stored value (minutes → seconds), rounded like the API expects. */
export function budgetStoredValue(field: BudgetField, shown: number): number {
  const stored = shown * field.scale;
  return field.integer || field.scale !== 1
    ? Math.round(stored)
    : Number(stored.toFixed(4));
}

/** Validation message for a shown value ('' when fine). */
export function budgetProblem(
  field: BudgetField,
  shown: number,
  config: AgentConfigInfo | null,
): string {
  if (!Number.isFinite(shown)) return "Enter a number";
  if (field.integer && !Number.isInteger(shown)) return "Whole numbers only";
  const { min, max } = budgetRange(field, config);
  if (shown < min || shown > max) {
    return `Allowed range ${formatBound(min)}–${formatBound(max)}`;
  }
  return "";
}

function formatBound(value: number): string {
  return Number.isInteger(value)
    ? value.toLocaleString()
    : String(Number(value.toFixed(3)));
}

export const NETWORK_POLICY_LABELS: Record<string, string> = {
  packages: "Package registries only",
  none: "No network",
};

export const SAMPLING_POLICY_LABELS: Record<string, string> = {
  provider_default: "Provider defaults (harness sampling stripped)",
  harness_native: "Harness native (harness sampling passed through)",
};
