/**
 * What the composer opens with: the mode's session draft (or defaults),
 * optionally replaced by a job's settings (`?from_job=`) and adjusted by the
 * `?model=` / `?app=` deep links. Each source that can't be applied adds a
 * note instead of failing the page.
 */
import {
  getGenerationJob,
  getRunPreset,
  type GenerationJob,
  type HarnessPresetConfig,
  type LLMParams,
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

async function fetchPreset(id: number | undefined): Promise<RunPreset | null> {
  if (id == null) return null;
  try {
    return await getRunPreset(id);
  } catch {
    return null;
  }
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
  const app = isRecord(snapshot.app_requirement)
    ? snapshot.app_requirement
    : null;

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
      warning: problems.length
        ? `Couldn't carry everything over: ${problems.join("; ")}.`
        : "",
    };
  }

  if (mode === "scaffolding") {
    const draft: DraftByMode["scaffolding"] = {
      appIds,
      stackSlug: job.stack_slug ?? "",
      profileId: typeof job.profile === "number" ? job.profile : null,
      modelIds: model ? [model.id] : [],
      samplingPresetId: samplingPin?.id ?? null,
      llmParams: inlineLLM,
      trials: DEFAULT_TRIALS,
    };
    return {
      mode,
      draft,
      pins: { ...NO_PINS, sampling: samplingPin },
      notice: `Task, model, stack, profile and settings loaded from job ${shortId} (${label}). Trials reset to ${DEFAULT_TRIALS}.`,
      warning: problems.length
        ? `Couldn't carry everything over: ${problems.join("; ")}.`
        : "",
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
    warning: problems.length
      ? `Couldn't carry everything over: ${problems.join("; ")}.`
      : "",
  };
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
  if (source.fromJobId) {
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
