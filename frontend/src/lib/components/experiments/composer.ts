/**
 * The experiment composer's state as a plain, serialisable draft: defaults,
 * session persistence (`experiment-composer:v2`), the cross-product of
 * conditions, the create body (`ExperimentCreateV2`), reconstruction from an
 * existing experiment (clone / edit), per-endpoint selection grouping and
 * "Balance across endpoints".
 */
import { browser } from "$app/environment";
import type { AgentConfigOverrides, LLMParams } from "$lib/api/generation";
import type { LLMModelSummary } from "$lib/api/models";
import type {
  ConditionChoice,
  ExperimentCreateV2,
  ExperimentDetail,
  ExperimentPreviewV2,
  PostRunSpec,
  ScheduleSpec,
  SchedulingPolicy,
} from "$lib/api/types/experiments";
import { cleanLLMParams } from "$lib/components/generator/llm-params";
import { slugify } from "$lib/components/templates/slugify";
import { defaultSchedule } from "./cron";
import {
  MAX_SEED,
  cleanAgentOverrides,
  randomSeed,
} from "$lib/components/generator/composer/draft";

export const COMPOSER_STORAGE_KEY = "experiment-composer:v2";
export const DEFAULT_REPEATS = 3;
export const MAX_REPEATS = 20;
export { MAX_SEED, randomSeed };

export type ComposerMode = "agent" | "scaffolding";

export interface AblationDraft {
  /** Client-side identity (list keys); never sent. */
  key: string;
  model_id: number | null;
  harness_preset_id: number | null;
  profile_id: number | null;
  label: string;
  param_overrides: LLMParams;
}

export interface ExecutionDraft {
  policy: SchedulingPolicy;
  /** -10..10 (UI: Low -5 · Normal 0 · High 5; non-staff are clamped server-side). */
  priority: number;
  /** null = unlimited. */
  maxConcurrency: number | null;
  infraRetries: number;
  warmup: boolean;
  maxResidentModels: number | null;
}

export interface ExperimentDraft {
  name: string;
  /** Typed slug; ignored until `slugTouched` (then the slug follows the name). */
  slug: string;
  slugTouched: boolean;
  hypothesis: string;
  description: string;
  mode: ComposerMode;
  stackSlug: string;
  modelIds: number[];
  harnessPresetIds: number[];
  profileIds: number[];
  /** Condition key of the pre-registered baseline; null = the first condition. */
  baseline: string | null;
  appIds: number[];
  repeats: number | null;
  baseSeed: number | null;
  samplingPresetId: number | null;
  llmParams: LLMParams;
  budgetPresetId: number | null;
  budgetOverrides: AgentConfigOverrides;
  ablations: AblationDraft[];
  execution: ExecutionDraft;
  postRun: PostRunSpec;
  /** One optional schedule (more can be added on the Automation tab). */
  schedule: ScheduleSpec | null;
}

export function defaultPostRun(mode: ComposerMode): PostRunSpec {
  return {
    evaluate: mode === "scaffolding",
    analysis: { enabled: false, tool_slugs: [], only_passing: false, profile: null },
    report: { enabled: false, exclude_truncated: true },
  };
}

export function defaultExecution(): ExecutionDraft {
  return {
    policy: "auto",
    priority: 0,
    maxConcurrency: null,
    infraRetries: 2,
    warmup: true,
    maxResidentModels: null,
  };
}

/** A fresh draft. New drafts get a random seed, so seeds and run order are reproducible. */
export function defaultComposerDraft(): ExperimentDraft {
  return {
    name: "",
    slug: "",
    slugTouched: false,
    hypothesis: "",
    description: "",
    mode: "agent",
    stackSlug: "",
    modelIds: [],
    harnessPresetIds: [],
    profileIds: [],
    baseline: null,
    appIds: [],
    repeats: DEFAULT_REPEATS,
    baseSeed: randomSeed(),
    samplingPresetId: null,
    llmParams: {},
    budgetPresetId: null,
    budgetOverrides: {},
    ablations: [],
    execution: defaultExecution(),
    postRun: defaultPostRun("agent"),
    schedule: null,
  };
}

// ── slug ──────────────────────────────────────────────────────────────

/** Experiment slugs are SlugFields (50 characters). */
export const MAX_SLUG = 50;

export function draftSlug(
  draft: Pick<ExperimentDraft, "name" | "slug" | "slugTouched">,
): string {
  if (draft.slugTouched) return draft.slug.trim();
  return slugify(draft.name).slice(0, MAX_SLUG).replace(/-+$/g, "");
}

// ── conditions ────────────────────────────────────────────────────────

export interface CrossCondition extends ConditionChoice {
  key: string;
}

/** Stable identity of one cross condition: "m12|h5" (agent) or "m12|p3" (scaffolding). */
export function conditionKey(choice: ConditionChoice): string {
  const axis =
    choice.harness_preset_id != null
      ? `h${choice.harness_preset_id}`
      : choice.profile_id != null
        ? `p${choice.profile_id}`
        : choice.harness
          ? `n${choice.harness}`
          : "-";
  return `m${choice.model_id}|${axis}`;
}

/** The cross product in display order: models outer, harness presets / profiles inner. */
export function crossConditions(
  draft: Pick<
    ExperimentDraft,
    "mode" | "modelIds" | "harnessPresetIds" | "profileIds"
  >,
): CrossCondition[] {
  const out: CrossCondition[] = [];
  const axis =
    draft.mode === "agent" ? draft.harnessPresetIds : draft.profileIds;
  for (const model_id of draft.modelIds) {
    for (const id of axis) {
      const choice: ConditionChoice =
        draft.mode === "agent"
          ? { model_id, harness_preset_id: id }
          : { model_id, profile_id: id };
      out.push({ ...choice, key: conditionKey(choice) });
    }
  }
  return out;
}

/** The baseline condition actually sent: the chosen one while it exists, else the first. */
export function effectiveBaseline(
  draft: Pick<
    ExperimentDraft,
    "mode" | "modelIds" | "harnessPresetIds" | "profileIds" | "baseline"
  >,
): CrossCondition | null {
  const cross = crossConditions(draft);
  return cross.find((c) => c.key === draft.baseline) ?? cross[0] ?? null;
}

export function ablationReady(
  ablation: AblationDraft,
  mode: ComposerMode,
): boolean {
  if (ablation.model_id == null) return false;
  return mode === "agent"
    ? ablation.harness_preset_id != null
    : ablation.profile_id != null;
}

// ── create body ───────────────────────────────────────────────────────

export interface BodyOptions {
  /** Scaffolding with a no-op system sampling preset sends "no preset" (see SamplingSettings). */
  samplingNoop?: boolean;
}

function agentSafeParams(params: LLMParams, mode: ComposerMode): LLMParams {
  const out = cleanLLMParams(params);
  if (mode === "agent") {
    // The gateway drops these for agent runs; never store them silently.
    delete out.stop;
    delete out.response_format;
  }
  return out;
}

export function buildCreateBody(
  draft: ExperimentDraft,
  options: BodyOptions = {},
): ExperimentCreateV2 {
  const agent = draft.mode === "agent";
  const baseline = effectiveBaseline(draft);
  const extra = draft.ablations
    .filter((a) => ablationReady(a, draft.mode))
    .map((a) => {
      const item: NonNullable<
        ExperimentCreateV2["conditions"]["extra"]
      >[number] = {
        model_id: a.model_id!,
        param_overrides: agentSafeParams(a.param_overrides, draft.mode),
      };
      if (agent) item.harness_preset_id = a.harness_preset_id!;
      else item.profile_id = a.profile_id!;
      if (a.label.trim()) item.label = a.label.trim();
      return item;
    });
  const body: ExperimentCreateV2 = {
    name: draft.name.trim(),
    slug: draftSlug(draft),
    description: draft.description.trim(),
    hypothesis: draft.hypothesis.trim(),
    app_requirement_ids: [...draft.appIds].sort((a, b) => a - b),
    repeats: draft.repeats ?? DEFAULT_REPEATS,
    base_seed: draft.baseSeed,
    mode: draft.mode,
    stack_slug: draft.stackSlug || null,
    sampling_preset_id: options.samplingNoop ? null : draft.samplingPresetId,
    budget_preset_id: agent ? draft.budgetPresetId : null,
    agent_config: agent ? cleanAgentOverrides(draft.budgetOverrides) : {},
    llm_defaults: agentSafeParams(draft.llmParams, draft.mode),
    conditions: {
      cross: agent
        ? {
            model_ids: [...draft.modelIds],
            harness_preset_ids: [...draft.harnessPresetIds],
          }
        : {
            model_ids: [...draft.modelIds],
            profile_ids: [...draft.profileIds],
          },
      ...(extra.length ? { extra } : {}),
      ...(baseline ? { baseline: stripKey(baseline) } : {}),
    },
    execution: {
      scheduling_policy: draft.execution.policy,
      priority: draft.execution.priority,
      max_concurrency: draft.execution.maxConcurrency,
      infra_retries: draft.execution.infraRetries,
      options: {
        warmup: draft.execution.warmup,
        max_resident_models: draft.execution.maxResidentModels,
      },
    },
    post_run: {
      ...draft.postRun,
      evaluate: agent ? false : draft.postRun.evaluate,
    },
    schedules: draft.schedule ? [draft.schedule] : [],
  };
  return body;
}

function stripKey(c: CrossCondition): ConditionChoice {
  const { key: _key, ...choice } = c;
  return choice;
}

// ── persistence ───────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function ids(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return [
    ...new Set(value.filter((v): v is number => Number.isInteger(v) && v > 0)),
  ];
}

function num(value: unknown, fallback: number | null): number | null {
  if (value === null) return null;
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** Re-shapes stored JSON into a valid draft (unknown keys dropped, bad values defaulted). */
export function sanitizeDraft(raw: unknown): ExperimentDraft | null {
  if (!isRecord(raw)) return null;
  const base = defaultComposerDraft();
  const mode: ComposerMode =
    raw.mode === "scaffolding" ? "scaffolding" : "agent";
  const exec = isRecord(raw.execution) ? raw.execution : {};
  const policies: SchedulingPolicy[] = [
    "auto",
    "interleaved",
    "blocked",
    "grouped",
  ];
  const post = isRecord(raw.postRun) ? raw.postRun : {};
  const analysis = isRecord(post.analysis) ? post.analysis : {};
  const report = isRecord(post.report) ? post.report : {};
  const str = (v: unknown, d = "") => (typeof v === "string" ? v : d);
  const bool = (v: unknown, d: boolean) => (typeof v === "boolean" ? v : d);
  return {
    ...base,
    name: str(raw.name),
    slug: str(raw.slug),
    slugTouched: bool(raw.slugTouched, false),
    hypothesis: str(raw.hypothesis),
    description: str(raw.description),
    mode,
    stackSlug: str(raw.stackSlug),
    modelIds: ids(raw.modelIds),
    harnessPresetIds: ids(raw.harnessPresetIds),
    profileIds: ids(raw.profileIds),
    baseline: typeof raw.baseline === "string" ? raw.baseline : null,
    appIds: ids(raw.appIds),
    repeats: num(raw.repeats, DEFAULT_REPEATS),
    baseSeed: num(raw.baseSeed, base.baseSeed),
    samplingPresetId: num(raw.samplingPresetId, null),
    llmParams: isRecord(raw.llmParams)
      ? cleanLLMParams(raw.llmParams as LLMParams)
      : {},
    budgetPresetId: num(raw.budgetPresetId, null),
    budgetOverrides: cleanAgentOverrides(raw.budgetOverrides),
    ablations: Array.isArray(raw.ablations)
      ? raw.ablations.filter(isRecord).map((a, i) => ({
          key: str(a.key, `ablation-${i}`),
          model_id: num(a.model_id, null),
          harness_preset_id: num(a.harness_preset_id, null),
          profile_id: num(a.profile_id, null),
          label: str(a.label),
          param_overrides: isRecord(a.param_overrides)
            ? cleanLLMParams(a.param_overrides as LLMParams)
            : {},
        }))
      : [],
    execution: {
      policy: policies.includes(exec.policy as SchedulingPolicy)
        ? (exec.policy as SchedulingPolicy)
        : "auto",
      priority: num(exec.priority, 0) ?? 0,
      maxConcurrency: num(exec.maxConcurrency, null),
      infraRetries: num(exec.infraRetries, 2) ?? 2,
      warmup: bool(exec.warmup, true),
      maxResidentModels: num(exec.maxResidentModels, null),
    },
    postRun: {
      evaluate: bool(post.evaluate, mode === "scaffolding"),
      analysis: {
        enabled: bool(analysis.enabled, false),
        tool_slugs: Array.isArray(analysis.tool_slugs)
          ? analysis.tool_slugs.filter(
              (s): s is string => typeof s === "string",
            )
          : [],
        only_passing: bool(analysis.only_passing, false),
        profile:
          typeof analysis.profile === "string" && analysis.profile
            ? analysis.profile
            : null,
      },
      report: {
        enabled: bool(report.enabled, false),
        exclude_truncated: bool(report.exclude_truncated, true),
      },
    },
    schedule: isRecord(raw.schedule) ? sanitizeSchedule(raw.schedule) : null,
  };
}

const SCHEDULE_ACTIONS = new Set(["top_up", "add_replicates", "clone_and_run"]);

/** A stored schedule with every field typed (defaults fill the gaps). */
function sanitizeSchedule(raw: Record<string, unknown>): ScheduleSpec {
  const base = defaultSchedule();
  const params = isRecord(raw.params) ? raw.params : {};
  return {
    action: SCHEDULE_ACTIONS.has(raw.action as string)
      ? (raw.action as ScheduleSpec["action"])
      : base.action,
    cron: typeof raw.cron === "string" ? raw.cron : base.cron,
    timezone:
      typeof raw.timezone === "string" && raw.timezone
        ? raw.timezone
        : base.timezone,
    params: {
      ...(typeof params.replicates === "number"
        ? { replicates: params.replicates }
        : {}),
      ...(typeof params.name_template === "string"
        ? { name_template: params.name_template }
        : {}),
    },
    skip_if_active:
      typeof raw.skip_if_active === "boolean"
        ? raw.skip_if_active
        : base.skip_if_active,
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : base.enabled,
  };
}

export function loadComposerDraft(): ExperimentDraft | null {
  if (!browser) return null;
  try {
    const raw = sessionStorage.getItem(COMPOSER_STORAGE_KEY);
    return raw ? sanitizeDraft(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

export function saveComposerDraft(draft: ExperimentDraft): void {
  if (!browser) return;
  try {
    sessionStorage.setItem(COMPOSER_STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // storage full or blocked: the composer works without persistence
  }
}

export function clearComposerDraft(): void {
  if (!browser) return;
  try {
    sessionStorage.removeItem(COMPOSER_STORAGE_KEY);
  } catch {
    // ignore
  }
}

// ── from an existing experiment ───────────────────────────────────────

function isEmpty(params: LLMParams | null | undefined): boolean {
  return Object.keys(cleanLLMParams(params ?? {})).length === 0;
}

/**
 * A draft carrying an experiment's design. Conditions without overrides form
 * the cross product (models × harness presets / profiles); conditions with
 * overrides become ablations. `clone` names it "Clone of …" with a fresh seed.
 */
export function draftFromExperiment(
  exp: ExperimentDetail,
  options: { clone: boolean },
): ExperimentDraft {
  const agent = exp.mode === "agent";
  const main = exp.conditions.filter((c) => isEmpty(c.param_overrides));
  const extras = exp.conditions.filter((c) => !isEmpty(c.param_overrides));
  const uniq = (values: (number | null | undefined)[]) => [
    ...new Set(values.filter((v): v is number => v != null)),
  ];
  const modelIds = uniq(main.map((c) => c.model));
  const harnessPresetIds = agent ? uniq(main.map((c) => c.harness_preset)) : [];
  const profileIds = agent ? [] : uniq(main.map((c) => c.profile));
  const baselineCondition = exp.conditions.find(
    (c) => c.id === exp.baseline_condition_id,
  );
  const baseline =
    baselineCondition && isEmpty(baselineCondition.param_overrides)
      ? conditionKey(
          agent
            ? {
                model_id: baselineCondition.model,
                harness_preset_id:
                  baselineCondition.harness_preset ?? undefined,
              }
            : {
                model_id: baselineCondition.model,
                profile_id: baselineCondition.profile ?? undefined,
              },
        )
      : null;
  const config = (exp.agent_config ?? {}) as AgentConfigOverrides;
  const post = exp.post_run ?? {};
  const name = options.clone ? `Clone of ${exp.name}` : exp.name;
  return {
    ...defaultComposerDraft(),
    name,
    slug: options.clone ? "" : exp.slug,
    slugTouched: !options.clone,
    hypothesis: exp.hypothesis ?? "",
    description: exp.description ?? "",
    mode: agent ? "agent" : "scaffolding",
    stackSlug: exp.stack_slug ?? "",
    modelIds,
    harnessPresetIds,
    profileIds,
    baseline,
    appIds: [...exp.app_requirement_ids],
    repeats: exp.repeats,
    baseSeed: options.clone ? randomSeed() : exp.base_seed,
    samplingPresetId: exp.sampling_preset_ref?.id ?? null,
    llmParams: cleanLLMParams(exp.llm_defaults ?? {}),
    budgetPresetId: agent ? (exp.budget_preset_ref?.id ?? null) : null,
    budgetOverrides: cleanAgentOverrides(config),
    ablations: extras.map((c, i) => ({
      key: `ablation-${c.id}-${i}`,
      model_id: c.model,
      harness_preset_id: agent ? (c.harness_preset ?? null) : null,
      profile_id: agent ? null : c.profile,
      label: c.label ?? "",
      param_overrides: cleanLLMParams(c.param_overrides),
    })),
    execution: {
      policy: exp.scheduling_policy ?? "auto",
      priority: exp.priority ?? 0,
      maxConcurrency: exp.max_concurrency ?? null,
      infraRetries: exp.infra_retries ?? 2,
      warmup: exp.execution_options?.warmup ?? true,
      maxResidentModels: exp.execution_options?.max_resident_models ?? null,
    },
    postRun: {
      ...defaultPostRun(agent ? "agent" : "scaffolding"),
      ...post,
      analysis: {
        ...defaultPostRun("agent").analysis,
        ...(post.analysis ?? {}),
      },
      report: { ...defaultPostRun("agent").report, ...(post.report ?? {}) },
    },
    schedule: null,
  };
}

// ── selection grouped by endpoint ─────────────────────────────────────

export const OPENROUTER_KEY = "openrouter";

export function isLocal(model: LLMModelSummary | undefined): boolean {
  if (!model) return false;
  if (model.is_local != null) return model.is_local;
  return model.endpoint != null && model.endpoint.kind !== "openrouter";
}

/** Endpoint slug of a local model, or "openrouter". */
export function endpointKeyOf(model: LLMModelSummary | undefined): string {
  return isLocal(model) && model?.endpoint
    ? model.endpoint.slug
    : OPENROUTER_KEY;
}

export interface SelectionGroup {
  key: string;
  label: string;
  online: boolean | null;
  ids: number[];
}

/** Selected models per endpoint (local endpoints by name, OpenRouter last). */
export function selectionByEndpoint(
  selected: readonly number[],
  byId: ReadonlyMap<number, LLMModelSummary>,
): SelectionGroup[] {
  const groups = new Map<string, SelectionGroup>();
  for (const id of selected) {
    const model = byId.get(id);
    const key = endpointKeyOf(model);
    let group = groups.get(key);
    if (!group) {
      const local = isLocal(model);
      group = {
        key,
        label: local ? (model?.endpoint?.name ?? key) : "OpenRouter",
        online: local ? (model?.endpoint?.online ?? null) : null,
        ids: [],
      };
      groups.set(key, group);
    }
    group.ids.push(id);
  }
  return [...groups.values()].sort((a, b) => {
    if (a.key === OPENROUTER_KEY) return 1;
    if (b.key === OPENROUTER_KEY) return -1;
    return a.label.localeCompare(b.label);
  });
}

// ── balance across endpoints ──────────────────────────────────────────

export interface EndpointLoadInfo {
  /** Endpoint slug. */
  slug: string;
  /** Concurrent jobs the endpoint runs (job cap); ≥ 1. */
  capacity: number;
  /** From the preview, when the endpoint is in the current plan. */
  estHours?: number | null;
  /** Selected models on it in that preview. */
  models?: number;
}

export interface BalanceResult {
  ids: number[];
  /** Selected models moved to another endpoint (or merged as duplicates). */
  changed: number;
  /** Per endpoint, the estimated hours after balancing (relative units when unknown). */
  load: Record<string, number>;
}

/**
 * For models whose weights (digest) are served by several endpoints, picks one
 * endpoint per digest so the busiest endpoint finishes as early as possible
 * (greedy longest-processing-time). Models without alternatives stay. One
 * model row per digest is kept: the same weights on two endpoints would be two
 * conditions.
 *
 * Work per model is estimated from the preview as `est_hours × capacity /
 * models` (slot-hours) and divided by a target endpoint's capacity; without a
 * preview every model counts as one unit.
 */
export function balanceAcrossEndpoints(
  selected: readonly number[],
  catalog: readonly LLMModelSummary[],
  endpoints: readonly EndpointLoadInfo[],
): BalanceResult {
  const byId = new Map(catalog.map((m) => [m.id, m]));
  const info = new Map(endpoints.map((e) => [e.slug, e]));
  const capacity = (slug: string) => Math.max(1, info.get(slug)?.capacity ?? 1);

  const slotHours: number[] = [];
  for (const e of endpoints) {
    if (e.estHours != null && e.models && e.models > 0) {
      slotHours.push((e.estHours * Math.max(1, e.capacity)) / e.models);
    }
  }
  const work = slotHours.length
    ? slotHours.reduce((a, b) => a + b, 0) / slotHours.length
    : 1;

  const replicas = new Map<string, LLMModelSummary[]>();
  for (const m of catalog) {
    if (!isLocal(m) || !m.digest || m.is_available === false || !m.endpoint)
      continue;
    const list = replicas.get(m.digest);
    if (list) list.push(m);
    else replicas.set(m.digest, [m]);
  }

  const load: Record<string, number> = {};
  const add = (slug: string) => {
    load[slug] = (load[slug] ?? 0) + work / capacity(slug);
  };

  const fixed: number[] = [];
  const movable = new Map<string, number[]>(); // digest → selected ids
  for (const id of selected) {
    const model = byId.get(id);
    const digest = isLocal(model) ? model?.digest : undefined;
    if (!model || !digest || (replicas.get(digest)?.length ?? 0) < 2) {
      fixed.push(id);
      add(endpointKeyOf(model));
      continue;
    }
    const list = movable.get(digest);
    if (list) list.push(id);
    else movable.set(digest, [id]);
  }

  const result = [...fixed];
  let changed = 0;
  // Every digest costs the same work, so the most constrained groups (fewest
  // alternatives) are placed first; digest order keeps the result stable.
  const groups = [...movable.entries()].sort(
    ([a], [b]) =>
      replicas.get(a)!.length - replicas.get(b)!.length || a.localeCompare(b),
  );
  for (const [digest, chosen] of groups) {
    const options = replicas.get(digest)!;
    const currentSlugs = new Set(
      chosen.map((id) => endpointKeyOf(byId.get(id))),
    );
    const ranked = options
      .map((option) => {
        const slug = option.endpoint!.slug;
        return {
          option,
          // Rounded so equal sums compare equal despite float noise.
          finish: Math.round(((load[slug] ?? 0) + work / capacity(slug)) * 1e9),
          current: currentSlugs.has(slug),
        };
      })
      .sort(
        (a, b) =>
          a.finish - b.finish ||
          Number(b.current) - Number(a.current) ||
          a.option.endpoint!.slug.localeCompare(b.option.endpoint!.slug),
      );
    const pick = ranked[0].option;
    add(pick.endpoint!.slug);
    result.push(pick.id);
    changed += chosen.filter((id) => id !== pick.id).length;
  }
  return { ids: result, changed, load };
}

/** Endpoint load inputs from a preview plus the capacities of every visible endpoint. */
export function loadInfoFromPreview(
  preview: Pick<ExperimentPreviewV2, "endpoints"> | null,
  capacities: ReadonlyMap<string, number>,
): EndpointLoadInfo[] {
  const out = new Map<string, EndpointLoadInfo>();
  for (const [slug, cap] of capacities) out.set(slug, { slug, capacity: cap });
  for (const e of preview?.endpoints ?? []) {
    out.set(e.slug, {
      slug: e.slug,
      capacity: Math.max(
        1,
        Math.min(
          e.job_cap || e.max_concurrency || 1,
          e.max_concurrency || e.job_cap || 1,
        ),
      ),
      estHours: e.est_hours,
      models: e.models.length,
    });
  }
  return [...out.values()];
}
