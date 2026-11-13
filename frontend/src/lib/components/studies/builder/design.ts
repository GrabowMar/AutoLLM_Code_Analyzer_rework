/**
 * The builder's design vocabulary and pure draft operations (spec 9.4, 10.2). Everything the builder
 * changes in a `Design` goes through these functions so the rules live in one place and are unit-tested:
 *
 * - which factors exist per mode and where their levels come from (models, library kinds, enums);
 * - roles follow the level count: one level → `fixed`, several → `crossed` (a `fraction` stays a fraction);
 * - an empty factor is dropped (the resolver then applies the mode default, shown as "default: …");
 * - slot studies fix stack, starter, auth and prose by their parents (those factors are not offered);
 * - budget text never enters the design's prompts: the Budget preset is a separate ref (`resource_policy`).
 */
import type {
  Design,
  EvalLevel,
  EvaluationSpec,
  FactorKey,
  FactorRole,
  FactorSpec,
  LibraryKind,
  StudyKind,
  StudyMode,
} from "$lib/api/v3/types";

export type LevelSource =
  | { type: "model" }
  | { type: "library"; kind: LibraryKind }
  | { type: "enum"; values: { value: string; label: string }[] };

export interface FactorDef {
  key: FactorKey;
  label: string;
  /** One line under the factor name. */
  help: string;
  source: LevelSource;
  modes: StudyMode[];
}

export const FACTOR_DEFS: FactorDef[] = [
  {
    key: "model",
    label: "Model",
    help: "The LLM that writes the code (OpenRouter or a local endpoint).",
    source: { type: "model" },
    modes: ["app", "slot"],
  },
  {
    key: "harness_preset",
    label: "Harness",
    help: "Agent harness and its options (slot: agent executor only).",
    source: { type: "library", kind: "harness_preset" },
    modes: ["app", "slot"],
  },
  {
    key: "stack_profile",
    label: "Stack",
    help: "Backend and frontend framework, database and run contract.",
    source: { type: "library", kind: "stack" },
    modes: ["app"],
  },
  {
    key: "starter_level",
    label: "Starter",
    help: "How much scaffold the workspace starts with.",
    source: {
      type: "enum",
      values: [
        { value: "bare", label: "Bare" },
        { value: "minimal", label: "Minimal" },
        { value: "full", label: "Full" },
      ],
    },
    modes: ["app"],
  },
  {
    key: "style",
    label: "Style",
    help: "UI styling directive (must fit the stack's frontend).",
    source: { type: "library", kind: "style" },
    modes: ["app", "slot"],
  },
  {
    key: "auth_mode",
    label: "Auth",
    help: "Auth contract the app must implement (its text is a hashed fragment).",
    source: {
      type: "enum",
      values: [
        { value: "contract_bearer", label: "Bearer token contract" },
        { value: "cookie_session", label: "Cookie session contract" },
        { value: "spec_defined", label: "Task-defined flow" },
      ],
    },
    modes: ["app"],
  },
  {
    key: "guidance",
    label: "Guidance",
    help: "Security or library guidance added to the brief.",
    source: { type: "library", kind: "guidance" },
    modes: ["app", "slot"],
  },
  {
    key: "prompt_set",
    label: "Prompts",
    help: "Prompt set (role templates and fragments); each level can be edited.",
    source: { type: "library", kind: "prompt_set" },
    modes: ["app", "slot"],
  },
  {
    key: "prose_variant",
    label: "Prose variant",
    help: "How much of the task the brief states (checks are marked stated / unstated).",
    source: {
      type: "enum",
      values: [
        { value: "minimal", label: "Minimal" },
        { value: "standard", label: "Standard" },
        { value: "exhaustive", label: "Exhaustive" },
      ],
    },
    modes: ["app"],
  },
  {
    key: "sampling_preset",
    label: "Sampling",
    help: "Sampling parameters (identical for every model).",
    source: { type: "library", kind: "sampling" },
    modes: ["app", "slot"],
  },
  {
    key: "resource_policy",
    label: "Budget preset",
    help: "Vary the Budget preset as a factor (class C/R knobs change the treatment).",
    source: { type: "library", kind: "resource_policy" },
    modes: ["app", "slot"],
  },
  {
    key: "network_policy",
    label: "Network",
    help: "Package installs through the read-only caches, or no network.",
    source: {
      type: "enum",
      values: [
        { value: "packages", label: "Packages only" },
        { value: "none", label: "None" },
      ],
    },
    modes: ["app"],
  },
  {
    key: "slot_executor",
    label: "Executor",
    help: "Single-shot strict JSON, or a restricted agent.",
    source: {
      type: "enum",
      values: [
        { value: "single_shot", label: "Single shot" },
        { value: "restricted_agent", label: "Restricted agent" },
      ],
    },
    modes: ["slot"],
  },
  {
    key: "hole_mode",
    label: "Hole",
    help: "What replaces the part before regeneration.",
    source: {
      type: "enum",
      values: [
        { value: "stub", label: "Stub" },
        { value: "remove", label: "Remove" },
        { value: "rewrite", label: "Rewrite" },
      ],
    },
    modes: ["slot"],
  },
  {
    key: "keep_docstring",
    label: "Docstring",
    help: "Keep the part's docstring or comment in the hole.",
    source: {
      type: "enum",
      values: [
        { value: "false", label: "Removed" },
        { value: "true", label: "Kept" },
      ],
    },
    modes: ["slot"],
  },
  {
    key: "context_policy",
    label: "Context",
    help: "Which files the model sees around the hole.",
    source: {
      type: "enum",
      values: [
        { value: "slot_file_only", label: "Slot file only" },
        { value: "related_files", label: "Related files" },
        { value: "full_app", label: "Full app" },
        { value: "interface_only", label: "Interfaces only" },
      ],
    },
    modes: ["slot"],
  },
];

export const FACTOR_BY_KEY: Record<FactorKey, FactorDef> = Object.fromEntries(
  FACTOR_DEFS.map((d) => [d.key, d]),
) as Record<FactorKey, FactorDef>;

/** Levels the resolver applies when a factor is left out (`study/plan.py:APP_DEFAULTS` / `SLOT_DEFAULTS`). */
export const MODE_DEFAULTS: Record<
  StudyMode,
  Partial<Record<FactorKey, string>>
> = {
  app: {
    harness_preset: "opencode-native",
    starter_level: "minimal",
    style: "model-choice",
    auth_mode: "contract_bearer",
    guidance: "none",
    prompt_set: "app-default",
    prose_variant: "standard",
    sampling_preset: "provider-default",
  },
  slot: {
    slot_executor: "single_shot",
    guidance: "none",
    // Slot studies never use provider-default sampling (section 7.10, WP-04).
    sampling_preset: "t07",
    hole_mode: "stub",
    keep_docstring: "false",
    context_policy: "related_files",
  },
};

/** Factors fixed by the parents in slot mode (shown read-only, never varied). */
export const PARENT_FIXED: FactorKey[] = [
  "stack_profile",
  "starter_level",
  "auth_mode",
  "prose_variant",
];

export const REPEAT_LIMITS: Record<
  StudyMode,
  { min: number; max: number; default: number }
> = {
  app: { min: 1, max: 20, default: 5 },
  slot: { min: 1, max: 200, default: 30 },
};

export const DEFAULT_POLICY = "frugal";
export const DEFAULT_EVAL_SUITE = "eval-v2";

export function factorsForMode(mode: StudyMode): FactorDef[] {
  return FACTOR_DEFS.filter((d) => d.modes.includes(mode));
}

export function defaultEvaluation(mode: StudyMode): EvaluationSpec {
  return {
    eval_suite: DEFAULT_EVAL_SUITE,
    eval_level: "security",
    overrides: {},
    slot: mode === "slot" ? { mode: "full", regression: false } : null,
    retest_fraction: 0.05,
  };
}

/** A random 31-bit seed (`crypto.getRandomValues` when available). */
export function randomSeed(): number {
  const g = globalThis as {
    crypto?: { getRandomValues?: (a: Uint32Array) => Uint32Array };
  };
  if (g.crypto?.getRandomValues)
    // Drop the top bit: uniform over [0, 2^31) with no modulo.
    return g.crypto.getRandomValues(new Uint32Array(1))[0] >>> 1;
  return Math.floor(Math.random() * 2 ** 31);
}

export function defaultDesign(
  mode: StudyMode,
  kind: StudyKind = "exploratory",
  seeds?: { base: number; order: number },
): Design {
  return {
    mode,
    kind,
    factors: [],
    units: { tasks: [], slot_sets: [], slot_targets: [] },
    repeats: REPEAT_LIMITS[mode].default,
    grid: { type: "full", fraction: null, exclude: [] },
    base_seed: seeds?.base ?? randomSeed(),
    seed_rule: "crn_unit_repeat",
    scheduling: "auto",
    order_seed: seeds?.order ?? randomSeed(),
    resource_policy: DEFAULT_POLICY,
    evaluation: defaultEvaluation(mode),
    parent_selection: null,
    slot_primary_features: [],
    analysis_plan: null,
  };
}

export function getLevels(design: Design, key: FactorKey): string[] {
  return design.factors.find((f) => f.key === key)?.levels ?? [];
}

export function getFactor(
  design: Design,
  key: FactorKey,
): FactorSpec | undefined {
  return design.factors.find((f) => f.key === key);
}

function roleFor(
  levels: string[],
  current: FactorRole | undefined,
): FactorRole {
  if (current === "fraction" && levels.length === 2) return "fraction";
  return levels.length > 1 ? "crossed" : "fixed";
}

/**
 * `factors` with `key`'s levels replaced (deduplicated, order kept). Empty levels drop the factor; the role
 * follows the count. Factors keep their position; a new factor goes in vocabulary order.
 */
export function factorsWithLevels(
  factors: FactorSpec[],
  key: FactorKey,
  levels: string[],
): FactorSpec[] {
  const unique = [...new Set(levels.map((l) => l.trim()).filter(Boolean))];
  const existing = factors.find((f) => f.key === key);
  if (unique.length === 0) return factors.filter((f) => f.key !== key);
  if (existing)
    return factors.map((f) =>
      f.key === key
        ? { key, role: roleFor(unique, f.role), levels: unique }
        : f,
    );
  const order = FACTOR_DEFS.map((d) => d.key);
  return [
    ...factors,
    { key, role: roleFor(unique, undefined), levels: unique },
  ].sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
}

/** `factors` with `key`'s role set (`fixed` needs one level, `fraction` two; only one fraction factor). */
export function factorsWithRole(
  factors: FactorSpec[],
  key: FactorKey,
  role: FactorRole,
): FactorSpec[] {
  return factors.map((f) => {
    if (f.key !== key)
      return role === "fraction" && f.role === "fraction"
        ? { ...f, role: roleFor(f.levels, undefined) }
        : f;
    if (role === "fixed" && f.levels.length !== 1) return f;
    if (role === "fraction" && f.levels.length !== 2) return f;
    return { ...f, role };
  });
}

/** The design with new factors; the grid follows (fractional iff a fraction factor, stale exclusions dropped). */
export function withFactors(design: Design, factors: FactorSpec[]): Design {
  return { ...design, factors, grid: cleanGrid(design, factors) };
}

export function setLevels(
  design: Design,
  key: FactorKey,
  levels: string[],
): Design {
  return withFactors(design, factorsWithLevels(design.factors, key, levels));
}

export function setRole(
  design: Design,
  key: FactorKey,
  role: FactorRole,
): Design {
  return withFactors(design, factorsWithRole(design.factors, key, role));
}

/** Drop exclusions that name factors or levels no longer in the design. */
function cleanGrid(design: Design, factors: FactorSpec[]): Design["grid"] {
  const levels = new Map(factors.map((f) => [f.key, new Set(f.levels)]));
  const exclude = (design.grid.exclude ?? []).filter((ex) =>
    Object.entries(ex).every(([key, level]) =>
      levels.get(key as FactorKey)?.has(level),
    ),
  );
  const fractional = factors.some((f) => f.role === "fraction");
  return {
    type: fractional ? "fractional" : "full",
    fraction: fractional ? (design.grid.fraction ?? { generator: "" }) : null,
    exclude,
  };
}

/**
 * Switch mode (agentic app ↔ slot): keeps the factors allowed in the new mode, resets the units and the
 * repeats default, and the evaluation's slot block. Seeds, kind and the Budget preset are kept.
 */
export function switchMode(design: Design, mode: StudyMode): Design {
  if (design.mode === mode) return design;
  const allowed = new Set(factorsForMode(mode).map((d) => d.key));
  const factors = design.factors.filter((f) => allowed.has(f.key));
  const repeats = REPEAT_LIMITS[mode].default;
  return {
    ...design,
    mode,
    factors,
    grid: cleanGrid(design, factors),
    units: { tasks: [], slot_sets: [], slot_targets: [] },
    repeats,
    evaluation: {
      ...design.evaluation,
      slot:
        mode === "slot"
          ? (design.evaluation.slot ?? { mode: "full", regression: false })
          : null,
    },
    parent_selection: mode === "slot" ? design.parent_selection : null,
    slot_primary_features: mode === "slot" ? design.slot_primary_features : [],
  };
}

export function setKind(design: Design, kind: StudyKind): Design {
  return {
    ...design,
    kind,
    analysis_plan:
      kind === "confirmatory"
        ? (design.analysis_plan ?? defaultAnalysisPlan(design.mode))
        : null,
  };
}

export function clampRepeats(
  mode: StudyMode,
  repeats: number | null | undefined,
): number {
  const { min, max, default: fallback } = REPEAT_LIMITS[mode];
  if (repeats === null || repeats === undefined || !Number.isFinite(repeats))
    return fallback;
  return Math.min(max, Math.max(min, Math.round(repeats)));
}

export function setEvalLevel(design: Design, level: EvalLevel): Design {
  return { ...design, evaluation: { ...design.evaluation, eval_level: level } };
}

/** Outcomes an analysis plan may name (`study/analysis_plan.py:METRICS`). */
export const PLAN_METRICS: {
  value: string;
  label: string;
  modes: StudyMode[];
}[] = [
  {
    value: "pass_rate",
    label: "Mean agent pass rate (ITT)",
    modes: ["app", "slot"],
  },
  {
    value: "deploy_rate",
    label: "Deploy rate (builds and answers health)",
    modes: ["app", "slot"],
  },
  {
    value: "functional_passed",
    label: "All functional checks pass",
    modes: ["app", "slot"],
  },
  {
    value: "stated_pass_rate",
    label: "Pass rate on stated checks",
    modes: ["app"],
  },
  {
    value: "unstated_pass_rate",
    label: "Pass rate on unstated checks",
    modes: ["app"],
  },
  {
    value: "probe_exploit_rate",
    label: "Probe exploit rate",
    modes: ["app", "slot"],
  },
  {
    value: "unique_issues_per_app",
    label: "Static issues per sample",
    modes: ["app", "slot"],
  },
  {
    value: "issues_per_kloc",
    label: "Code issues per KLOC (model scope)",
    modes: ["app", "slot"],
  },
  {
    value: "security_per_app",
    label: "Security issues per sample",
    modes: ["app", "slot"],
  },
  {
    value: "cost_per_app",
    label: "Generation cost per sample",
    modes: ["app", "slot"],
  },
  {
    value: "pattern_prevalence",
    label: "Primary pattern prevalence",
    modes: ["slot"],
  },
];
export const PLAN_CORRECTIONS = ["holm", "bh", "none"] as const;
export const PLAN_MULTIPLICITY = ["all", "per_metric"] as const;
export const PLAN_ESTIMANDS = ["marginal", "conditional_on_parent"] as const;
export const PLAN_RANDOM_EFFECTS = [
  "unit",
  "parent",
  "target",
  "repeat",
  "provider",
] as const;
export const PLAN_SENSITIVITY = [
  "exclude_truncated",
  "exclude_infra_retried",
  "majority_fingerprint",
  "complete_blocks_only",
] as const;

/** The ported analysis-plan defaults: intention-to-treat; excluding truncated runs is a sensitivity analysis. */
export function defaultAnalysisPlan(
  mode: StudyMode = "app",
): Record<string, unknown> {
  return {
    primary_metric: mode === "slot" ? "pattern_prevalence" : "pass_rate",
    secondary_metrics: [],
    research_questions: [],
    alpha: 0.05,
    correction: "holm",
    multiplicity: "all",
    estimand: mode === "slot" ? "conditional_on_parent" : "marginal",
    random_effects: mode === "slot" ? ["target"] : ["unit"],
    itt: true,
    sensitivity: ["exclude_truncated"],
    exclusions: {
      truncated: false,
      exclusion_rate: 0.1,
      differential_attrition: 0.05,
    },
    sequential: null,
    patterns: null,
    min_apps_per_condition: 0,
    notes: "",
  };
}

/**
 * Apply the `move_prose_variant` quick fix: legacy units `task@1:minimal` → unit `task@1` plus the
 * variant as a level of the `prose_variant` factor.
 */
export function moveProseVariants(design: Design): Design {
  const re = /[:/](minimal|standard|exhaustive)$/;
  const variants = new Set<string>(getLevels(design, "prose_variant"));
  const tasks: string[] = [];
  for (const unit of design.units.tasks) {
    const m = re.exec(unit);
    if (m) variants.add(m[1]);
    const task = m ? unit.slice(0, m.index) : unit;
    if (!tasks.includes(task)) tasks.push(task);
  }
  const withUnits = { ...design, units: { ...design.units, tasks } };
  return variants.size
    ? setLevels(withUnits, "prose_variant", [...variants])
    : withUnits;
}

/** Client-side checks shown before the server has resolved (neutral wording; the server is authoritative). */
export function localChecks(
  design: Design,
): { code: string; severity: "error" | "warn" | "info"; detail: string }[] {
  const out: {
    code: string;
    severity: "error" | "warn" | "info";
    detail: string;
  }[] = [];
  if (getLevels(design, "model").length === 0)
    out.push({
      code: "no_models",
      severity: "error",
      detail: "Pick at least one model.",
    });
  if (design.mode === "app") {
    if (design.units.tasks.length === 0)
      out.push({
        code: "no_tasks",
        severity: "error",
        detail: "Pick at least one task.",
      });
    if (getLevels(design, "stack_profile").length === 0) {
      out.push({
        code: "no_stack",
        severity: "error",
        detail: "Pick at least one stack.",
      });
    }
  } else if (design.units.slot_targets.length === 0) {
    out.push({
      code: "no_targets",
      severity: "error",
      detail: "Pick at least one slot target.",
    });
  }
  if (design.kind === "confirmatory" && !design.analysis_plan) {
    out.push({
      code: "no_analysis_plan",
      severity: "error",
      detail: "A confirmatory experiment needs an analysis plan.",
    });
  }
  if (design.grid.type === "fractional" && !design.grid.fraction?.generator) {
    out.push({
      code: "no_generator",
      severity: "error",
      detail: "A fractional grid needs a generator such as D=ABC.",
    });
  }
  return out;
}

/** "2 models × 3 tasks × 5 repeats" style factor counts for the RunFormula (crossed factors only). */
export function crossedCounts(
  design: Design,
): { key: FactorKey; label: string; count: number }[] {
  return design.factors
    .filter((f) => f.role !== "fixed" && f.levels.length > 1)
    .map((f) => ({
      key: f.key,
      label: FACTOR_BY_KEY[f.key]?.label ?? f.key,
      count: f.levels.length,
    }));
}

/** A design is ready to be saved server-side when the server's structural checks can pass. */
export function canCreate(design: Design): boolean {
  return localChecks(design).every(
    (c) => c.severity !== "error" || c.code === "no_generator",
  );
}
