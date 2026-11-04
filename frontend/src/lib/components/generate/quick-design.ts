/**
 * "Generate app" (`/generate`): one app from one choice per factor. The form becomes the same `Design` the
 * experiment builder produces for those choices (`studies/builder/design.ts`): an exploratory app design with
 * one task, one condition (every chosen factor `fixed` with one level), one repeat, the Budget preset as the
 * design-level `resource_policy` (a factor only when it is varied) and the default evaluation suite. It is
 * sent to `POST /studies/adhoc/` with `quick: true`: first without `launch` (a dry run that prices it and
 * creates nothing), then with `launch` (a quick study, hidden from the experiments list by default).
 *
 * Budget text never enters the prompt (D7): the Budget preset is enforced by the gateway.
 */
import type { Design, EvalLevel, FactorKey } from "$lib/api/v3/types";
import {
  DEFAULT_POLICY,
  MODE_DEFAULTS,
  defaultDesign,
  setLevels,
} from "$lib/components/studies/builder/design";

export interface QuickAppForm {
  /** TaskSpecVersion ref `slug@v`. */
  task: string;
  /** Stack profile slug. */
  stack: string;
  style: string;
  auth_mode: string;
  prose_variant: string;
  /** `LLMModel.model_id`. */
  model: string;
  harness_preset: string;
  /** Budget preset (resource policy) slug. */
  resource_policy: string;
  /** Advanced: prompt set slug; empty = the mode default (`app-default`). */
  prompt_set: string;
  /** Advanced. */
  eval_level: EvalLevel;
  /** Advanced: the base seed; `null` = the page's random seed. */
  base_seed: number | null;
}

/** Factor key per form field (the order the builder lists them in). */
export const QUICK_FACTORS: [keyof QuickAppForm, FactorKey][] = [
  ["model", "model"],
  ["harness_preset", "harness_preset"],
  ["stack", "stack_profile"],
  ["style", "style"],
  ["auth_mode", "auth_mode"],
  ["prompt_set", "prompt_set"],
  ["prose_variant", "prose_variant"],
];

const APP = MODE_DEFAULTS.app;

/** A fresh form: the resolver's defaults pre-selected (shown as explicit choices), frugal, security. */
export function emptyQuickAppForm(
  prefill: Partial<QuickAppForm> = {},
): QuickAppForm {
  return {
    task: "",
    stack: "",
    style: APP.style ?? "",
    auth_mode: APP.auth_mode ?? "",
    prose_variant: APP.prose_variant ?? "",
    model: "",
    harness_preset: APP.harness_preset ?? "",
    resource_policy: DEFAULT_POLICY,
    prompt_set: "",
    eval_level: "security",
    base_seed: null,
    ...prefill,
  };
}

/** Field → message for what must be chosen before the form can be priced. */
export function validateQuickAppForm(
  form: QuickAppForm,
): Partial<Record<keyof QuickAppForm, string>> {
  const errors: Partial<Record<keyof QuickAppForm, string>> = {};
  if (!form.task.trim()) errors.task = "Pick the task the app implements.";
  if (!form.stack.trim()) errors.stack = "Pick a stack.";
  if (!form.model.trim()) errors.model = "Pick the model that writes the app.";
  if (!form.harness_preset.trim()) errors.harness_preset = "Pick a harness.";
  if (!form.resource_policy.trim())
    errors.resource_policy = "Pick a Budget preset.";
  if (
    form.base_seed !== null &&
    (!Number.isInteger(form.base_seed) ||
      form.base_seed < 0 ||
      form.base_seed >= 2 ** 31)
  )
    errors.base_seed = "The seed is an integer from 0 to 2147483647.";
  return errors;
}

/** The one-condition, one-unit, one-repeat app design for `POST /studies/adhoc/`. */
export function buildQuickDesign(form: QuickAppForm, seed: number): Design {
  const base = form.base_seed ?? seed;
  let design = defaultDesign("app", "exploratory", { base, order: base });
  for (const [field, key] of QUICK_FACTORS) {
    const value = String(form[field] ?? "").trim();
    design = setLevels(design, key, value ? [value] : []);
  }
  return {
    ...design,
    units: { tasks: [form.task.trim()], slot_sets: [], slot_targets: [] },
    repeats: 1,
    resource_policy: form.resource_policy.trim() || DEFAULT_POLICY,
    evaluation: { ...design.evaluation, eval_level: form.eval_level },
  };
}

/** A readable name for the quick study (its experiment record and the sample's back link). */
export function quickStudyName(form: QuickAppForm): string {
  const task = form.task.split("@")[0] || form.task;
  const model = form.model.split("/").pop() || form.model;
  return `Quick · ${task} · ${form.stack} · ${model}`.slice(0, 200);
}
