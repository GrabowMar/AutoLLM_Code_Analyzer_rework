/**
 * Composer drafts: the form state of each mode, persisted per tab in
 * sessionStorage so switching modes, opening the Library and reloading keep
 * the user's choices. Storage can be unavailable (private mode, blocked site
 * data), so every access is guarded and the page works without it.
 */
import { browser } from "$app/environment";
import type {
  AgentConfigOverrides,
  GenerationMode,
  GenerationProfile,
  LLMParams,
  PresetRef,
} from "$lib/api/generation";
import { cleanLLMParams } from "../llm-params";

export type ComposerMode = GenerationMode;
export const COMPOSER_MODES: ComposerMode[] = [
  "agent",
  "scaffolding",
  "custom",
];

/** App specs are graded by their hidden conformance suite; a free-form task is not. */
export type TaskKind = "specs" | "freeform";

export const DEFAULT_TRIALS = 3;
export const MAX_TRIALS = 10;
export const MAX_SEED = 2_147_483_647;
/** Larger batches ask for confirmation before they start. */
export const CONFIRM_RUNS_ABOVE = 20;

export const DEFAULT_SYSTEM_PROMPT =
  "You are an expert full-stack developer. Write clean, well-structured code with proper error handling, type safety, and following best practices.";

export interface AgentDraft {
  taskKind: TaskKind;
  appIds: number[];
  /** Free-form task, or extra context added to every app spec's brief. */
  description: string;
  stackSlug: string;
  modelIds: number[];
  harnessPresetIds: number[];
  samplingPresetId: number | null;
  llmParams: LLMParams;
  budgetPresetId: number | null;
  /** Inline budget overrides plus `sampling_policy`; never `harness`. */
  agentConfig: AgentConfigOverrides;
  trials: number | null;
  /** null = every run draws its own seed. */
  baseSeed: number | null;
}

export interface ScaffoldingDraft {
  appIds: number[];
  stackSlug: string;
  /** null = Auto (resolved per app). An exact version (pk), possibly not the latest. */
  profileId: number | null;
  /** Slug of `profileId`, so a superseded version can still be looked up. */
  profileSlug: string | null;
  modelIds: number[];
  samplingPresetId: number | null;
  llmParams: LLMParams;
  trials: number | null;
}

export interface CustomDraft {
  systemPrompt: string;
  userPrompt: string;
  modelId: number | null;
  samplingPresetId: number | null;
  llmParams: LLMParams;
  seed: number | null;
}

export interface DraftByMode {
  agent: AgentDraft;
  scaffolding: ScaffoldingDraft;
  custom: CustomDraft;
}

/** Exact preset versions a draft came from (`?from_job=`), shown as pinned. */
export interface PresetPins {
  sampling: PresetRef | null;
  budget: PresetRef | null;
  /** Scaffolding: the exact profile version (kept selectable when it is not the latest). */
  profile?: GenerationProfile | null;
  /**
   * Scaffolding: the batch whose runs name the selected profile's slug, when
   * reading it failed while preparing (the composer reads it again).
   */
  profileBatchId?: string | null;
}

export const NO_PINS: PresetPins = { sampling: null, budget: null };

/** A uniformly random base seed in the range the API accepts. */
export function randomSeed(): number {
  try {
    const buf = new Uint32Array(1);
    crypto.getRandomValues(buf);
    return buf[0] % (MAX_SEED + 1);
  } catch {
    return Math.floor(Math.random() * (MAX_SEED + 1));
  }
}

export function defaultDraft<M extends ComposerMode>(mode: M): DraftByMode[M] {
  const drafts: DraftByMode = {
    agent: {
      taskKind: "specs",
      appIds: [],
      description: "",
      stackSlug: "",
      modelIds: [],
      harnessPresetIds: [],
      samplingPresetId: null,
      llmParams: {},
      budgetPresetId: null,
      agentConfig: {},
      trials: DEFAULT_TRIALS,
      baseSeed: randomSeed(),
    },
    scaffolding: {
      appIds: [],
      stackSlug: "",
      profileId: null,
      profileSlug: null,
      modelIds: [],
      samplingPresetId: null,
      llmParams: {},
      trials: DEFAULT_TRIALS,
    },
    custom: {
      systemPrompt: DEFAULT_SYSTEM_PROMPT,
      userPrompt: "",
      modelId: null,
      samplingPresetId: null,
      llmParams: {},
      seed: null,
    },
  };
  return drafts[mode];
}

// ── sanitizing stored drafts (never trust storage contents) ────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function ids(value: unknown): number[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return [
    ...new Set(
      value.filter(
        (v): v is number => typeof v === "number" && Number.isInteger(v),
      ),
    ),
  ];
}

function idOrNull(value: unknown): number | null | undefined {
  if (value === null) return null;
  return typeof value === "number" && Number.isInteger(value)
    ? value
    : undefined;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function strOrNull(value: unknown): string | null | undefined {
  if (value === null) return null;
  return typeof value === "string" && value ? value : undefined;
}

function intOrNull(
  value: unknown,
  min: number,
  max: number,
): number | null | undefined {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isInteger(value)) return undefined;
  return value >= min && value <= max ? value : undefined;
}

const AGENT_CONFIG_KEYS = [
  "wall_clock_seconds",
  "max_llm_requests",
  "max_total_tokens",
  "max_cost_usd",
  "network_policy",
  "sampling_policy",
] as const;

/** Keep only the inline keys the composer edits (drops `harness`, junk). */
export function cleanAgentOverrides(value: unknown): AgentConfigOverrides {
  if (!isRecord(value)) return {};
  const out: Record<string, unknown> = {};
  for (const key of AGENT_CONFIG_KEYS) {
    const v = value[key];
    if (key === "network_policy" || key === "sampling_policy") {
      if (typeof v === "string" && v) out[key] = v;
    } else if (typeof v === "number" && Number.isFinite(v)) {
      out[key] = v;
    }
  }
  return out as AgentConfigOverrides;
}

function params(value: unknown): LLMParams | undefined {
  return isRecord(value) ? cleanLLMParams(value as LLMParams) : undefined;
}

type Partialize<T> = { [K in keyof T]?: T[K] };

function pick<T extends object>(base: T, overrides: Partialize<T>): T {
  const out = { ...base };
  for (const [key, value] of Object.entries(overrides)) {
    if (value !== undefined) (out as Record<string, unknown>)[key] = value;
  }
  return out;
}

function sanitize<M extends ComposerMode>(
  mode: M,
  raw: unknown,
): DraftByMode[M] | null {
  if (!isRecord(raw)) return null;
  if (mode === "agent") {
    const taskKind =
      raw.taskKind === "freeform" || raw.taskKind === "specs"
        ? raw.taskKind
        : undefined;
    return pick(defaultDraft("agent"), {
      taskKind,
      appIds: ids(raw.appIds),
      description: str(raw.description),
      stackSlug: str(raw.stackSlug),
      modelIds: ids(raw.modelIds),
      harnessPresetIds: ids(raw.harnessPresetIds),
      samplingPresetId: idOrNull(raw.samplingPresetId),
      llmParams: params(raw.llmParams),
      budgetPresetId: idOrNull(raw.budgetPresetId),
      agentConfig: isRecord(raw.agentConfig)
        ? cleanAgentOverrides(raw.agentConfig)
        : undefined,
      trials: intOrNull(raw.trials, 1, MAX_TRIALS),
      baseSeed: intOrNull(raw.baseSeed, 0, MAX_SEED),
    }) as DraftByMode[M];
  }
  if (mode === "scaffolding") {
    return pick(defaultDraft("scaffolding"), {
      appIds: ids(raw.appIds),
      stackSlug: str(raw.stackSlug),
      profileId: idOrNull(raw.profileId),
      profileSlug: strOrNull(raw.profileSlug),
      modelIds: ids(raw.modelIds),
      samplingPresetId: idOrNull(raw.samplingPresetId),
      llmParams: params(raw.llmParams),
      trials: intOrNull(raw.trials, 1, MAX_TRIALS),
    }) as DraftByMode[M];
  }
  return pick(defaultDraft("custom"), {
    systemPrompt: str(raw.systemPrompt),
    userPrompt: str(raw.userPrompt),
    modelId: idOrNull(raw.modelId),
    samplingPresetId: idOrNull(raw.samplingPresetId),
    llmParams: params(raw.llmParams),
    seed: intOrNull(raw.seed, 0, MAX_SEED),
  }) as DraftByMode[M];
}

// ── storage ────────────────────────────────────────────────────────────

const STORAGE_PREFIX = "sample-generator:draft:v1:";

/** The stored draft for a mode, or null when there is none / storage is unavailable. */
export function loadDraft<M extends ComposerMode>(
  mode: M,
): DraftByMode[M] | null {
  if (!browser) return null;
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + mode);
    return raw ? sanitize(mode, JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

export function saveDraft<M extends ComposerMode>(
  mode: M,
  draft: DraftByMode[M],
): void {
  if (!browser) return;
  try {
    sessionStorage.setItem(STORAGE_PREFIX + mode, JSON.stringify(draft));
  } catch {
    // Quota or blocked storage: the draft simply isn't kept.
  }
}

/** Deterministic JSON (sorted keys): equal requests produce equal keys. */
export function stableKey(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) => {
    if (isRecord(v)) {
      return Object.fromEntries(
        Object.keys(v)
          .sort()
          .map((k) => [k, v[k]]),
      );
    }
    return v;
  });
}

export function sortedIds(values: Iterable<number>): number[] {
  return [...new Set(values)].sort((a, b) => a - b);
}
