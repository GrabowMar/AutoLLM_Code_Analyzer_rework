/**
 * The builder's steps (`/experiments/new`): what each step is called, which part of the design it owns,
 * whether it is complete, and which step a plan issue belongs to. Pure, so the stepper, the rail and the
 * review page agree, and unit-tested.
 *
 *   Setup → Models → Tasks (slot: Targets) → Treatments → Runs & budget → Measurement → Review
 *
 * Required: Models (≥ 1 model), Tasks / Targets (≥ 1 unit), Treatments in app mode (≥ 1 stack) and, for a
 * confirmatory experiment, an analysis plan (Measurement). The others always have a usable default.
 */
import type { Design, StudyMode } from "$lib/api/v3/types";
import { getLevels } from "./design";
import type { SummaryIssue } from "./issues";

export const STEP_IDS = [
  "setup",
  "models",
  "units",
  "treatments",
  "replication",
  "measurement",
  "review",
] as const;
export type StepId = (typeof STEP_IDS)[number];

export interface StepDef {
  id: StepId;
  /** Short name (stepper, rail). */
  label: string;
  /** Heading of the step's panel. */
  title: string;
  /** One sentence under the heading: what the step decides. */
  lead: string;
}

export function stepsFor(mode: StudyMode): StepDef[] {
  const slot = mode === "slot";
  return [
    {
      id: "setup",
      label: "Setup",
      title: "What is this experiment?",
      lead: "Name it, state the hypothesis, and choose what is generated and whether the analysis is pre-registered.",
    },
    {
      id: "models",
      label: "Models",
      title: "Which models are compared?",
      lead: "Every chosen model runs every condition. Local endpoints are listed first, then OpenRouter by vendor.",
    },
    {
      id: "units",
      label: slot ? "Targets" : "Tasks",
      title: slot
        ? "Which parts are regenerated?"
        : "What do the models build?",
      lead: slot
        ? "Slot targets grouped by parent app. Stack, starter, auth and prose come from the parents."
        : "Graded tasks. Each task is one unit, paired across every condition, so more tasks buy more power than more repeats.",
    },
    {
      id: "treatments",
      label: "Treatments",
      title: slot ? "What else varies?" : "Stack and what else varies",
      lead: "Each factor with several levels multiplies the conditions. A factor left alone uses the default shown.",
    },
    {
      id: "replication",
      label: "Runs & budget",
      title: "How many runs, and how much may they spend?",
      lead: "Repeats per condition and unit, the seeds that make them reproducible, and the limits enforced outside the model.",
    },
    {
      id: "measurement",
      label: "Measurement",
      title: "What is measured?",
      lead: "Evaluation of every sample, the pre-registered analysis (confirmatory) and what runs after the experiment.",
    },
    {
      id: "review",
      label: "Review",
      title: "Review and launch",
      lead: "The resolved plan: conditions, cells, cost and anything to fix. Launching freezes the plan so it cannot drift.",
    },
  ];
}

export function isStepId(value: string | null | undefined): value is StepId {
  return !!value && (STEP_IDS as readonly string[]).includes(value);
}

export function nextStep(id: StepId): StepId | null {
  const i = STEP_IDS.indexOf(id);
  return i >= 0 && i < STEP_IDS.length - 1 ? STEP_IDS[i + 1] : null;
}

export function prevStep(id: StepId): StepId | null {
  const i = STEP_IDS.indexOf(id);
  return i > 0 ? STEP_IDS[i - 1] : null;
}

/** One required choice that is still open (the rail's to-do list and the review's blockers). */
export interface MissingChoice {
  step: StepId;
  /** Imperative, e.g. "Choose at least one model". */
  label: string;
}

export function missingChoices(design: Design): MissingChoice[] {
  const out: MissingChoice[] = [];
  if (getLevels(design, "model").length === 0)
    out.push({ step: "models", label: "Choose at least one model" });
  if (design.mode === "app") {
    if (design.units.tasks.length === 0)
      out.push({ step: "units", label: "Choose at least one task" });
    if (getLevels(design, "stack_profile").length === 0)
      out.push({ step: "treatments", label: "Choose a stack" });
  } else if (design.units.slot_targets.length === 0) {
    out.push({ step: "units", label: "Choose at least one slot target" });
  }
  if (design.kind === "confirmatory" && !design.analysis_plan)
    out.push({ step: "measurement", label: "Write the analysis plan" });
  if (design.grid.type === "fractional" && !design.grid.fraction?.generator)
    out.push({ step: "treatments", label: "Give the fraction a generator" });
  return out;
}

/** Steps that hold a required choice (marked "required" in the stepper until complete). */
export function requiredSteps(design: Design): Set<StepId> {
  const steps = new Set<StepId>(["models", "units"]);
  if (design.mode === "app") steps.add("treatments");
  if (design.kind === "confirmatory") steps.add("measurement");
  return steps;
}

/** Client checks that `missingChoices` already lists as to-dos (never shown again as red errors). */
export const TODO_CODES: ReadonlySet<string> = new Set([
  "no_models",
  "no_tasks",
  "no_stack",
  "no_targets",
  "no_analysis_plan",
  "no_generator",
]);

const CODE_STEP: Record<string, StepId> = {
  no_models: "models",
  free_model_rate_limit: "models",
  model_alias_in_confirmatory: "models",
  models_without_tools: "models",
  no_tasks: "units",
  no_targets: "units",
  few_clusters: "units",
  prose_variant_in_unit: "units",
  mixed_parents: "units",
  oracle_missing: "units",
  provider_unpinned: "treatments",
  starter_missing: "treatments",
  no_stack: "treatments",
  incompatible_style: "treatments",
  combination_pruned: "treatments",
  unbalanced_grid: "treatments",
  aliasing: "treatments",
  no_generator: "treatments",
  harness_not_enabled: "treatments",
  harness_exploratory_only: "treatments",
  estimate_exceeds_cap: "replication",
  cap_admits_fewer_blocks: "replication",
  no_analysis_plan: "measurement",
  primary_feature_missing: "measurement",
  adaptive_stop_in_confirmatory: "measurement",
};

/** The step whose choices an issue is about: its `path` first (`factors.model`, `units.tasks`…), then its code. */
export function stepOfIssue(
  issue: Pick<SummaryIssue, "code" | "path">,
): StepId {
  const path = issue.path ?? "";
  if (path) {
    if (path.startsWith("factors.model")) return "models";
    if (path.startsWith("units") || path.startsWith("parent_selection"))
      return "units";
    if (path.startsWith("factors") || path.startsWith("grid"))
      return "treatments";
    if (path.startsWith("repeats") || path.startsWith("resource_policy"))
      return "replication";
    if (
      path.startsWith("analysis_plan") ||
      path.startsWith("evaluation") ||
      path.startsWith("slot_primary_features")
    )
      return "measurement";
  }
  return CODE_STEP[issue.code] ?? "review";
}

/** Issue counts per step (errors and warnings; notes are not counted). */
export function issuesByStep(
  issues: readonly SummaryIssue[],
): Record<StepId, { errors: number; warnings: number }> {
  const out = Object.fromEntries(
    STEP_IDS.map((id) => [id, { errors: 0, warnings: 0 }]),
  ) as Record<StepId, { errors: number; warnings: number }>;
  for (const issue of issues) {
    if (TODO_CODES.has(issue.code)) continue;
    const step = stepOfIssue(issue);
    if (issue.severity === "error") out[step].errors++;
    else if (issue.severity === "warn") out[step].warnings++;
  }
  return out;
}

/**
 * Conditions the client can vouch for before the server answers: 0 while a required factor is missing
 * (the grid would otherwise count a stack-only design as one condition).
 */
export function guardConditions(
  design: Design,
  expanded: number | null,
): number | null {
  if (getLevels(design, "model").length === 0) return 0;
  if (design.mode === "app" && getLevels(design, "stack_profile").length === 0)
    return 0;
  return expanded;
}
