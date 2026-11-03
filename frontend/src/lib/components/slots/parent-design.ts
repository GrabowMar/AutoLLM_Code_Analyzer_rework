/**
 * "Generate a parent app" (spec 10.5 step 0): a compact form → a one-cell, exploratory app design for
 * `POST /studies/adhoc/` (9.4). One repeat, the frugal budget preset by default, evaluation at `security` so
 * the run can become a tested parent. Every factor is `fixed` with one level; factors left empty are not sent
 * and take the resolver's defaults (`study/plan.py:APP_DEFAULTS`, e.g. harness `opencode-native`, style
 * `model-choice`, auth `contract_bearer`), which the form shows as placeholders.
 *
 * Budget text never enters the prompt (D7): the budget preset is a factor, enforced by the gateway.
 */

export interface ParentAppForm {
  /** TaskSpecVersion ref `slug@v` */
  task: string;
  /** stack profile ref (slug or `slug@v`) */
  stack: string;
  /** `LLMModel.model_id` */
  model: string;
  harness_preset: string;
  style: string;
  auth_mode: string;
  prose_variant: string;
  /** budget preset (resource policy) ref; the study-level policy */
  resource_policy: string;
  /** EvalSuite ref (required by `Design.evaluation`) */
  eval_suite: string;
  eval_level: "build" | "functional" | "security" | "full";
  base_seed: number | null;
}

/** The resolver's defaults for factors a design leaves out (shown as placeholders). */
export const PARENT_DEFAULTS = {
  harness_preset: "opencode-native",
  style: "model-choice",
  auth_mode: "contract_bearer",
  prose_variant: "standard",
  resource_policy: "frugal",
} as const;

export function emptyParentAppForm(task = ""): ParentAppForm {
  return {
    task,
    stack: "",
    model: "",
    harness_preset: "",
    style: "",
    auth_mode: "",
    prose_variant: "",
    resource_policy: PARENT_DEFAULTS.resource_policy,
    eval_suite: "",
    eval_level: "security",
    base_seed: null,
  };
}

export function validateParentAppForm(
  form: ParentAppForm,
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.task.trim())
    errors.task = "Pick the task the parent app implements.";
  if (!form.stack.trim()) errors.stack = "Pick a stack.";
  if (!form.model.trim())
    errors.model = "Pick the model that writes the parent.";
  if (!form.resource_policy.trim())
    errors.resource_policy = "Pick a budget preset.";
  if (!form.eval_suite.trim())
    errors.eval_suite = "Pick an evaluation suite: a parent must be tested.";
  if (
    form.base_seed !== null &&
    (!Number.isInteger(form.base_seed) || form.base_seed < 0)
  )
    errors.base_seed = "The seed is a non-negative integer.";
  return errors;
}

type Factor = { key: string; role: "fixed"; levels: string[] };

/** The `Design` body (as JSON) for `POST /studies/adhoc/`. */
export function buildParentDesign(
  form: ParentAppForm,
  seed: number,
): Record<string, unknown> {
  const factors: Factor[] = [];
  const add = (key: string, value: string) => {
    const level = value.trim();
    if (level) factors.push({ key, role: "fixed", levels: [level] });
  };
  add("model", form.model);
  add("stack_profile", form.stack);
  add("harness_preset", form.harness_preset);
  add("style", form.style);
  add("auth_mode", form.auth_mode);
  add("prose_variant", form.prose_variant);
  add("resource_policy", form.resource_policy);
  const base = form.base_seed ?? seed;
  return {
    mode: "app",
    kind: "exploratory",
    factors,
    units: { tasks: [form.task.trim()], slot_sets: [], slot_targets: [] },
    repeats: 1,
    grid: { type: "full", fraction: null, exclude: [] },
    base_seed: base,
    seed_rule: "crn_unit_repeat",
    scheduling: "auto",
    order_seed: base,
    resource_policy: form.resource_policy.trim(),
    evaluation: {
      eval_suite: form.eval_suite.trim(),
      eval_level: form.eval_level,
    },
    parent_selection: null,
    slot_primary_features: [],
    analysis_plan: null,
  };
}

/** A readable name for the ad-hoc study. */
export function parentStudyName(form: ParentAppForm): string {
  const model = form.model.split("/").pop() || form.model;
  return `Parent · ${form.task} · ${form.stack} · ${model}`.slice(0, 200);
}

/** The cost quote of a launch the budget preset wants confirmed (400 `confirmation_required`, 9.4). */
export interface LaunchQuote {
  p90_total: number;
  expected_total: number | null;
  threshold: number | null;
}

/**
 * The quote carried by a `confirmation_required` error from `POST /studies/adhoc/` (`{detail, code, data:
 * {p90_total, expected_total, threshold}}`), or null for any other error. The re-sent launch confirms
 * `confirm_usd = p90_total`; nothing launches until the researcher has seen this.
 */
export function launchQuote(error: unknown): LaunchQuote | null {
  if (!error || typeof error !== "object") return null;
  const body = error as { code?: unknown; data?: Record<string, unknown> };
  if (
    body.code !== "confirmation_required" ||
    !body.data ||
    typeof body.data !== "object"
  )
    return null;
  const num = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  const p90 = num(body.data.p90_total);
  if (p90 === null) return null;
  return {
    p90_total: p90,
    expected_total: num(body.data.expected_total),
    threshold: num(body.data.threshold),
  };
}
