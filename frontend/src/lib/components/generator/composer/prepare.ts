/**
 * What the composer opens with: the mode's session draft (or defaults),
 * optionally replaced by a job's settings (`?from_job=`) or a batch's whole
 * grid (`?from_batch=`), and adjusted by the `?model=` / `?app=` deep links.
 * Each source that can't be applied adds a note instead of failing the page.
 */
import {
  getGenerationBatch,
  getGenerationBatchJobs,
  getGenerationJob,
  getGenerationProfiles,
  getProfileVersions,
  getRunPreset,
  type GenerationBatchRequest,
  type GenerationBatchSummary,
  type GenerationJob,
  type GenerationProfile,
  type HarnessPresetConfig,
  type LLMParams,
  type PresetRef,
  type RunPreset,
} from "$lib/api/generation";
import { formatApiError } from "$lib/api/core";
import { isNotFoundError } from "../batches/batch-utils";
import { findModel, loadAppSpecCatalog, loadModelCatalog } from "../catalog";
import { cleanLLMParams } from "../llm-params";
import { loadPresets } from "../presets.svelte";
import {
  DEFAULT_SYSTEM_PROMPT,
  DEFAULT_TRIALS,
  MAX_TRIALS,
  NO_PINS,
  cleanAgentOverrides,
  defaultDraft,
  loadDraft,
  randomSeed,
  stableKey,
  type ComposerMode,
  type DraftByMode,
  type PresetPins,
} from "./draft";

export type PreparedComposer = {
  [M in ComposerMode]: {
    mode: M;
    draft: DraftByMode[M];
    pins: PresetPins;
    /** Informational: what was loaded from a job / link. */
    notice: string;
    /** Something requested could not be applied. */
    warning: string;
  };
}[ComposerMode];

export interface ComposerSource {
  mode: ComposerMode;
  modelSlug: string | null;
  appSlug: string | null;
  fromJobId: string | null;
  fromBatchId?: string | null;
}

/** Keys of `job` whose values differ from `base` (what the inline layer added). */
export function diffParams(
  job: Record<string, unknown> | null | undefined,
  base: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(job ?? {})) {
    if (value === null || value === undefined) continue;
    if (stableKey(value) !== stableKey((base ?? {})[key])) out[key] = value;
  }
  return out;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function modeOf(job: GenerationJob): ComposerMode {
  return job.mode === "scaffolding" || job.mode === "custom"
    ? job.mode
    : "agent";
}

async function fetchPreset(
  id: number | null | undefined,
): Promise<RunPreset | null> {
  if (id == null) return null;
  try {
    return await getRunPreset(id);
  } catch {
    return null;
  }
}

type PresetLookup =
  | { status: "none" }
  | { status: "found"; preset: RunPreset }
  | { status: "missing" }
  | { status: "error"; message: string };

/** One preset by id, telling a 404 (gone) apart from a failed request. */
async function lookupPreset(
  id: number | null | undefined,
): Promise<PresetLookup> {
  if (id == null) return { status: "none" };
  try {
    return { status: "found", preset: await getRunPreset(id) };
  } catch (e) {
    if (isNotFoundError(e)) return { status: "missing" };
    return { status: "error", message: formatApiError(e, "request failed") };
  }
}

function foundPreset(lookup: PresetLookup): RunPreset | null {
  return lookup.status === "found" ? lookup.preset : null;
}

/**
 * The preset id a batch ran with stays in the draft whatever its lookup
 * said: the composer's preset tracking drops it only on a 404, so a
 * transient failure here never swaps the batch's preset for the default.
 */
function keptPresetId(
  id: number | null | undefined,
  lookup: PresetLookup,
  label: string,
  problems: string[],
): number | null {
  if (id == null) return null;
  if (lookup.status === "missing")
    problems.push(`its ${label} preset no longer exists`);
  else if (lookup.status === "error")
    problems.push(
      `its ${label} preset couldn't be loaded (${lookup.message}); it stays selected`,
    );
  return id;
}

function presetRef(preset: RunPreset | null): PresetRef | null {
  return preset
    ? { id: preset.id, slug: preset.slug, version: preset.version }
    : null;
}

export type ProfileLookup =
  | { status: "found"; profile: GenerationProfile }
  | { status: "missing" }
  | { status: "error"; message: string };

/**
 * One exact profile version, by pk and slug. `GET /profiles/` lists only the
 * latest version of each slug, so an older one is found in the slug's
 * version list. Missing: deleted, archived or not visible.
 */
export async function lookupProfileVersion(
  id: number,
  slug: string | null | undefined,
): Promise<ProfileLookup> {
  if (!slug) {
    // Without the slug only latest versions can be checked; not finding the id
    // there says nothing about an older version, so keep it rather than drop it.
    try {
      const row = (await getGenerationProfiles()).find((p) => p.id === id);
      return row
        ? { status: "found", profile: row }
        : { status: "error", message: "its version could not be identified" };
    } catch (e) {
      return { status: "error", message: formatApiError(e, "request failed") };
    }
  }
  try {
    const row = (await getProfileVersions(slug)).find((p) => p.id === id);
    return row ? { status: "found", profile: row } : { status: "missing" };
  } catch (e) {
    if (isNotFoundError(e)) return { status: "missing" };
    return { status: "error", message: formatApiError(e, "request failed") };
  }
}

export type SlugLookup =
  | { status: "found"; slug: string }
  | { status: "none" }
  | { status: "error"; message: string };

/**
 * The slug of the profile a batch's runs used ("slug@v" on the runs): an
 * older version can only be looked up by it. `none`: no run records one;
 * `error`: the runs couldn't be loaded, which says nothing about the profile.
 */
export async function batchProfileSlug(batchId: string): Promise<SlugLookup> {
  try {
    const ref = (await getGenerationBatchJobs(batchId)).find(
      (j) => j.profile_ref,
    )?.profile_ref;
    if (!ref) return { status: "none" };
    const at = ref.lastIndexOf("@");
    return { status: "found", slug: at > 0 ? ref.slice(0, at) : ref };
  } catch (e) {
    return { status: "error", message: formatApiError(e, "request failed") };
  }
}

/**
 * {@link lookupProfileVersion} for a profile whose slug may be unknown: the
 * slug is then read from `batchId`'s runs. A failure to read them is an
 * error (the profile stays selected), not "missing". Returns the slug found.
 */
export async function lookupBatchProfileVersion(
  id: number,
  slug: string | null,
  batchId: string | null,
): Promise<{ lookup: ProfileLookup; slug: string | null }> {
  if (slug || !batchId)
    return { lookup: await lookupProfileVersion(id, slug), slug };
  const fromRuns = await batchProfileSlug(batchId);
  if (fromRuns.status === "error")
    return {
      lookup: { status: "error", message: fromRuns.message },
      slug: null,
    };
  const found = fromRuns.status === "found" ? fromRuns.slug : null;
  return { lookup: await lookupProfileVersion(id, found), slug: found };
}

function warningFrom(problems: string[]): string {
  return problems.length
    ? `Couldn't carry everything over: ${problems.join("; ")}.`
    : "";
}

/**
 * The harness a pre-preset agent job ran: its agent_config names it, or (for
 * jobs that ran the then-default harness) only the snapshot does.
 */
function legacyHarness(
  job: GenerationJob,
  snapshot: Record<string, unknown>,
): string | null {
  const agent = isRecord(snapshot.agent) ? snapshot.agent : {};
  const harness = isRecord(snapshot.harness) ? snapshot.harness : {};
  const name = job.agent_config?.harness ?? agent.harness ?? harness.name;
  return typeof name === "string" && name ? name : null;
}

/** Build a draft that reproduces `job`'s task, model, stack and exact preset versions. */
export async function draftFromJob(
  job: GenerationJob,
): Promise<PreparedComposer> {
  const mode = modeOf(job);
  const problems: string[] = [];
  const snapshot = isRecord(job.resolved_bundle) ? job.resolved_bundle : {};
  // A job whose snapshot failed has none: its own app spec fields remain.
  const snapshotApp = isRecord(snapshot.app_requirement)
    ? snapshot.app_requirement
    : null;
  const app: Record<string, unknown> | null =
    snapshotApp ??
    (job.app_requirement_id != null || job.app_slug
      ? {
          id: job.app_requirement_id ?? undefined,
          slug: job.app_slug ?? undefined,
        }
      : null);

  const [models, specs, sampling] = await Promise.all([
    loadModelCatalog().catch(() => []),
    mode === "custom" || !app
      ? Promise.resolve([])
      : loadAppSpecCatalog().catch(() => []),
    fetchPreset(job.sampling_preset?.id),
  ]);

  const model =
    (job.model_pk != null
      ? models.find((m) => m.id === job.model_pk)
      : undefined) ??
    (job.model_id_str ? findModel(models, job.model_id_str) : undefined);
  if (job.model_id_str && !model)
    problems.push(`model ${job.model_id_str} is no longer in the catalog`);

  const appIds: number[] = [];
  if (app) {
    const spec =
      specs.find((s) => s.id === app.id) ??
      specs.find((s) => s.slug === app.slug);
    if (spec) appIds.push(spec.id);
    else
      problems.push(`app spec ${String(app.slug ?? app.id)} no longer exists`);
  }
  if (
    mode !== "custom" &&
    !app &&
    !(mode === "agent" && job.task_description?.trim())
  )
    problems.push("the job's task (app spec) wasn't recorded");

  // Preset effects were applied into the job's fields; what differs from the
  // pinned preset is the inline layer.
  const samplingParams =
    (sampling?.config as { llm_params?: Record<string, unknown> } | undefined)
      ?.llm_params ?? {};
  const inlineLLM = cleanLLMParams(
    diffParams(
      job.llm_params as Record<string, unknown>,
      samplingParams,
    ) as LLMParams,
  );
  const samplingPin = job.sampling_preset
    ? {
        id: job.sampling_preset.id,
        slug: job.sampling_preset.slug,
        version: job.sampling_preset.version,
      }
    : null;
  const label =
    model?.model_name ??
    job.model_name ??
    job.model_id_str ??
    "the job's model";
  const shortId = job.id.slice(0, 8);

  if (mode === "custom") {
    const draft: DraftByMode["custom"] = {
      systemPrompt: job.custom_system_prompt || DEFAULT_SYSTEM_PROMPT,
      userPrompt: job.custom_user_prompt ?? "",
      modelId: model?.id ?? null,
      samplingPresetId: samplingPin?.id ?? null,
      llmParams: inlineLLM,
      seed: null,
    };
    return {
      mode,
      draft,
      pins: { ...NO_PINS, sampling: samplingPin },
      notice: `Prompt, model and settings loaded from job ${shortId} (${label}). No seed is set.`,
      warning: warningFrom(problems),
    };
  }

  if (mode === "scaffolding") {
    // The exact profile version the job used, even when a newer one exists.
    let profileId = typeof job.profile === "number" ? job.profile : null;
    const profileSlug =
      job.bundle_slug ??
      (typeof snapshot.bundle_slug === "string" ? snapshot.bundle_slug : null);
    let pinnedProfile: GenerationProfile | null = null;
    if (profileId != null) {
      const found = await lookupProfileVersion(profileId, profileSlug);
      const version =
        typeof snapshot.bundle_version === "number"
          ? `@v${snapshot.bundle_version}`
          : "";
      if (found.status === "found") pinnedProfile = found.profile;
      else if (found.status === "missing") {
        problems.push(
          `profile ${profileSlug ?? `#${profileId}`}${version} no longer exists, so Auto is used`,
        );
        profileId = null;
      } else {
        problems.push(
          `profile ${profileSlug ?? `#${profileId}`}${version} couldn't be loaded (${found.message}); it stays selected`,
        );
      }
    }
    const draft: DraftByMode["scaffolding"] = {
      appIds,
      stackSlug: job.stack_slug ?? "",
      profileId,
      profileSlug: profileId != null ? profileSlug : null,
      modelIds: model ? [model.id] : [],
      samplingPresetId: samplingPin?.id ?? null,
      llmParams: inlineLLM,
      trials: DEFAULT_TRIALS,
    };
    const loaded =
      profileId != null ? "Task, model, stack, profile" : "Task, model, stack";
    return {
      mode,
      draft,
      pins: { ...NO_PINS, sampling: samplingPin, profile: pinnedProfile },
      notice: `${loaded} and settings loaded from job ${shortId} (${label}). Trials reset to ${DEFAULT_TRIALS}.`,
      warning: warningFrom(problems),
    };
  }

  // Agent: budget preset + sampling policy are the layers below the inline agent config.
  const budget = await fetchPreset(job.budget_preset?.id);
  const agentBase: Record<string, unknown> = {
    ...((budget?.config as Record<string, unknown>) ?? {}),
  };
  const policy = (sampling?.config as { sampling_policy?: string } | undefined)
    ?.sampling_policy;
  if (policy) agentBase.sampling_policy = policy;
  const agentConfig = cleanAgentOverrides(
    diffParams(job.agent_config as Record<string, unknown>, agentBase),
  );

  let harnessPresetIds: number[] = [];
  let harnessText = "";
  const legacyName = legacyHarness(job, snapshot);
  if (job.harness_preset) {
    harnessPresetIds = [job.harness_preset.id];
    harnessText = `${job.harness_preset.name} v${job.harness_preset.version}`;
  } else if (legacyName) {
    // Jobs from before presets: that harness's system preset.
    const name = legacyName;
    const list = await loadPresets("harness").catch(
      () => [] as RunPreset<"harness">[],
    );
    const fallback =
      list.find(
        (p) =>
          p.is_system &&
          p.is_default &&
          (p.config as HarnessPresetConfig).harness === name,
      ) ??
      list.find(
        (p) =>
          p.is_system && (p.config as HarnessPresetConfig).harness === name,
      );
    if (fallback) {
      harnessPresetIds = [fallback.id];
      harnessText = fallback.name;
    } else {
      problems.push(`no preset for the ${name} harness`);
    }
    if (job.harness_options && Object.keys(job.harness_options).length > 0) {
      problems.push("harness options recorded without a preset");
    }
  }

  // stop / response_format don't apply to agents and the agent editor hides them.
  const { stop: _stop, response_format: _format, ...agentLLM } = inlineLLM;
  const draft: DraftByMode["agent"] = {
    taskKind: app || !job.task_description?.trim() ? "specs" : "freeform",
    appIds,
    description: job.task_description ?? "",
    stackSlug: job.stack_slug ?? "",
    modelIds: model ? [model.id] : [],
    harnessPresetIds,
    samplingPresetId: samplingPin?.id ?? null,
    llmParams: agentLLM,
    budgetPresetId: job.budget_preset?.id ?? null,
    agentConfig,
    trials: DEFAULT_TRIALS,
    baseSeed: randomSeed(),
  };
  return {
    mode,
    draft,
    pins: {
      sampling: samplingPin,
      budget: job.budget_preset
        ? {
            id: job.budget_preset.id,
            slug: job.budget_preset.slug,
            version: job.budget_preset.version,
          }
        : null,
    },
    notice:
      `Task, model, stack and presets loaded from job ${shortId} (${label}${harnessText ? `, ${harnessText}` : ""}). ` +
      `New base seed; trials reset to ${DEFAULT_TRIALS}. For an exact reproduction use Replay on the job page.`,
    warning: warningFrom(problems),
  };
}

function numberIds(value: unknown): number[] {
  return Array.isArray(value)
    ? [...new Set(value.filter((v): v is number => Number.isInteger(v)))]
    : [];
}

/**
 * Build a draft that sets up `batch`'s whole grid again from its stored
 * request: every app spec (or the free-form task), model, harness preset,
 * the sampling / budget presets, the inline overrides, the stack and the
 * trials, with a new base seed (none when the batch drew random seeds).
 */
export async function draftFromBatch(
  batch: GenerationBatchSummary,
): Promise<PreparedComposer> {
  const request: GenerationBatchRequest = isRecord(batch.request)
    ? batch.request
    : {};
  const mode: ComposerMode =
    batch.mode === "scaffolding" ? "scaffolding" : "agent";
  const problems: string[] = [];
  const shortId = batch.id.slice(0, 8);
  const wantedApps = numberIds(request.app_requirement_ids);
  const wantedModels = numberIds(request.model_ids);

  const [models, specs, samplingLookup, budgetLookup] = await Promise.all([
    loadModelCatalog().catch(() => null),
    wantedApps.length
      ? loadAppSpecCatalog().catch(() => null)
      : Promise.resolve([]),
    lookupPreset(request.sampling_preset_id),
    mode === "agent"
      ? lookupPreset(request.budget_preset_id)
      : Promise.resolve<PresetLookup>({ status: "none" }),
  ]);
  const sampling = foundPreset(samplingLookup);
  const budget = foundPreset(budgetLookup);

  let modelIds = wantedModels;
  if (!models) problems.push("the model catalog didn't load");
  else {
    modelIds = wantedModels.filter((id) => models.some((m) => m.id === id));
    const gone = wantedModels.length - modelIds.length;
    if (gone > 0)
      problems.push(
        `${gone} model${gone === 1 ? " is" : "s are"} no longer in the catalog`,
      );
  }
  let appIds = wantedApps;
  if (!specs) problems.push("app specs didn't load");
  else if (wantedApps.length) {
    appIds = wantedApps.filter((id) => specs.some((a) => a.id === id));
    const gone = wantedApps.length - appIds.length;
    if (gone > 0)
      problems.push(
        `${gone} app spec${gone === 1 ? " no longer exists" : "s no longer exist"}`,
      );
  }
  const samplingPresetId = keptPresetId(
    request.sampling_preset_id,
    samplingLookup,
    "sampling",
    problems,
  );

  const trials =
    typeof request.trials === "number" &&
    Number.isInteger(request.trials) &&
    request.trials >= 1 &&
    request.trials <= MAX_TRIALS
      ? request.trials
      : DEFAULT_TRIALS;
  const llmParams = cleanLLMParams((request.llm_params ?? {}) as LLMParams);
  const stackSlug = request.stack_slug ?? batch.stack_slug ?? "";

  if (mode === "scaffolding") {
    let profileId = request.profile_id ?? null;
    let profileSlug: string | null = null;
    let pinnedProfile: GenerationProfile | null = null;
    // Set when the slug couldn't be read: the composer reads it from the runs again.
    let profileBatchId: string | null = null;
    if (profileId != null) {
      const id = profileId;
      const latest = await getGenerationProfilesSafe();
      pinnedProfile = latest?.find((p) => p.id === id) ?? null;
      if (!pinnedProfile) {
        // An older version: its slug is on the batch's runs ("slug@v").
        const { lookup, slug } = await lookupBatchProfileVersion(
          id,
          null,
          batch.id,
        );
        profileSlug = slug;
        if (lookup.status === "found") pinnedProfile = lookup.profile;
        else if (lookup.status === "missing") {
          problems.push(
            `its prompt profile ${slug ?? `#${id}`} no longer exists, so Auto is used`,
          );
          profileId = null;
          profileSlug = null;
        } else {
          problems.push(
            `its prompt profile ${slug ?? `#${id}`} couldn't be loaded (${lookup.message}); it stays selected`,
          );
          if (!slug) profileBatchId = batch.id;
        }
      }
    }
    // The mode-default columns were sent as-is; a different value was an override.
    if (
      llmParams.temperature == null &&
      typeof request.temperature === "number" &&
      request.temperature !== 0.3
    )
      llmParams.temperature = request.temperature;
    const draft: DraftByMode["scaffolding"] = {
      appIds,
      stackSlug,
      profileId,
      profileSlug: pinnedProfile?.slug ?? profileSlug,
      modelIds,
      samplingPresetId,
      llmParams,
      trials,
    };
    return {
      mode,
      draft,
      pins: {
        ...NO_PINS,
        sampling: presetRef(sampling),
        profile: pinnedProfile,
        profileBatchId,
      },
      notice: `The grid of batch ${shortId} is loaded: ${appIds.length} app spec${appIds.length === 1 ? "" : "s"} × ${modelIds.length} model${modelIds.length === 1 ? "" : "s"} × ${trials} trial${trials === 1 ? "" : "s"}, with its stack, profile and settings.`,
      warning: warningFrom(problems),
    };
  }

  const harnessPresetIds = numberIds(request.harness_preset_ids);
  if (harnessPresetIds.length === 0 && (request.harnesses ?? []).length > 0)
    problems.push(
      "its harnesses predate presets; the default harness preset is used",
    );
  if (request.profile_id != null)
    problems.push("its prompt profile isn't part of the agent composer");
  const { stop: _stop, response_format: _format, ...agentLLM } = llmParams;
  if (agentLLM.max_tokens == null && typeof request.max_tokens === "number")
    agentLLM.max_tokens = request.max_tokens;
  const description = (request.description ?? "").trim();
  const draft: DraftByMode["agent"] = {
    taskKind: appIds.length === 0 && description ? "freeform" : "specs",
    appIds,
    description: request.description ?? "",
    stackSlug,
    modelIds,
    harnessPresetIds,
    samplingPresetId,
    llmParams: agentLLM,
    budgetPresetId: keptPresetId(
      request.budget_preset_id,
      budgetLookup,
      "budget",
      problems,
    ),
    agentConfig: cleanAgentOverrides(request.agent_config),
    trials,
    baseSeed: request.base_seed == null ? null : randomSeed(),
  };
  const runs =
    (appIds.length || (description ? 1 : 0)) *
    modelIds.length *
    harnessPresetIds.length *
    trials;
  return {
    mode,
    draft,
    pins: { sampling: presetRef(sampling), budget: presetRef(budget) },
    notice:
      `The grid of batch ${shortId} is loaded (${runs} run${runs === 1 ? "" : "s"}: tasks, models, harness presets, presets, overrides, stack and trials). ` +
      (request.base_seed == null
        ? "Like the original, every run draws its own seed."
        : "New base seed: the runs are new trials, not replays."),
    warning: warningFrom(problems),
  };
}

async function getGenerationProfilesSafe(): Promise<
  GenerationProfile[] | null
> {
  try {
    return await getGenerationProfiles();
  } catch {
    return null;
  }
}

/** Apply `?model=` / `?app=`: they replace the draft's selection with exactly that item. */
async function applyLinks(
  prepared: PreparedComposer,
  source: ComposerSource,
): Promise<PreparedComposer> {
  const notes: string[] = [];
  const problems: string[] = [];
  if (source.modelSlug) {
    const models = await loadModelCatalog().catch(() => null);
    const model = models ? findModel(models, source.modelSlug) : undefined;
    if (!models)
      problems.push(
        `the model catalog didn't load, so ${source.modelSlug} isn't preselected`,
      );
    else if (!model)
      problems.push(`model ${source.modelSlug} isn't in the catalog`);
    else {
      if (prepared.mode === "custom") prepared.draft.modelId = model.id;
      else prepared.draft.modelIds = [model.id];
      notes.push(model.model_name);
    }
  }
  if (source.appSlug && prepared.mode !== "custom") {
    const specs = await loadAppSpecCatalog().catch(() => null);
    const spec = specs?.find((s) => s.slug === source.appSlug);
    if (!specs)
      problems.push(
        `app specs didn't load, so ${source.appSlug} isn't preselected`,
      );
    else if (!spec) problems.push(`app spec ${source.appSlug} doesn't exist`);
    else {
      prepared.draft.appIds = [spec.id];
      if (prepared.mode === "agent") prepared.draft.taskKind = "specs";
      notes.push(spec.name);
    }
  }
  const notice = notes.length ? `Preselected ${notes.join(" and ")}.` : "";
  return {
    ...prepared,
    notice: [prepared.notice, notice].filter(Boolean).join(" "),
    warning: [
      prepared.warning,
      problems.length ? `Couldn't apply the link: ${problems.join("; ")}.` : "",
    ]
      .filter(Boolean)
      .join(" "),
  } as PreparedComposer;
}

/** The mode's stored draft, or a fresh default one. */
export function storedComposer(mode: ComposerMode): PreparedComposer {
  return {
    mode,
    draft: loadDraft(mode) ?? defaultDraft(mode),
    pins: NO_PINS,
    notice: "",
    warning: "",
  } as PreparedComposer;
}

export async function prepareComposer(
  source: ComposerSource,
): Promise<PreparedComposer> {
  let prepared = storedComposer(source.mode);
  if (source.fromBatchId) {
    try {
      prepared = await draftFromBatch(
        await getGenerationBatch(source.fromBatchId),
      );
    } catch (e) {
      prepared = {
        ...prepared,
        warning: `Couldn't load batch ${source.fromBatchId.slice(0, 8)}: ${
          isNotFoundError(e)
            ? "it doesn't exist or isn't visible to you."
            : formatApiError(e, "request failed")
        }`,
      } as PreparedComposer;
    }
  } else if (source.fromJobId) {
    try {
      prepared = await draftFromJob(await getGenerationJob(source.fromJobId));
    } catch (e) {
      prepared = {
        ...prepared,
        warning: `Couldn't load job ${source.fromJobId.slice(0, 8)}: ${
          isNotFoundError(e)
            ? "it doesn't exist or isn't visible to you."
            : formatApiError(e, "request failed")
        }`,
      } as PreparedComposer;
    }
  }
  if (source.modelSlug || source.appSlug)
    prepared = await applyLinks(prepared, source);
  return prepared;
}
