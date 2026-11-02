/**
 * Display helpers shared by the experiments pages: lifecycle tones and
 * labels, progress-count mapping, ETA / hours formats, scheduling-policy and
 * blocked-reason vocabulary.
 */
import type { GenerationBatchCounts } from "$lib/api/generation";
import type {
  AnalysisPlan,
  ExperimentResults,
  ExperimentStatusV2,
  PairedDeviation,
  PlanMetric,
  ProgressCounts,
  SchedulingPolicy,
} from "$lib/api/types/experiments";
import type { Tone } from "$lib/constants/colors";

export const EMPTY_PROGRESS: ProgressCounts = {
  pending: 0,
  running: 0,
  eval_queued: 0,
  evaluating: 0,
  completed: 0,
  failed: 0,
  cancelled: 0,
};

/** Counts with every key present (partial payloads tolerated). */
export function progressCounts(
  counts: Partial<ProgressCounts> | null | undefined,
): ProgressCounts {
  return { ...EMPTY_PROGRESS, ...(counts ?? {}) };
}

/**
 * Experiment progress as `BatchProgress` counts: `eval_queued` waits like a
 * queued run, `evaluating` is work in flight (the batch counts' rule, §B.3).
 */
export function toBatchCounts(
  counts: Partial<ProgressCounts> | null | undefined,
): GenerationBatchCounts {
  const c = progressCounts(counts);
  return {
    pending: c.pending,
    queued: c.eval_queued,
    running: c.running + c.evaluating,
    completed: c.completed,
    failed: c.failed,
    cancelled: c.cancelled,
  };
}

export function finishedCells(
  counts: Partial<ProgressCounts> | null | undefined,
): number {
  const c = progressCounts(counts);
  return c.completed + c.failed + c.cancelled;
}

export function activeCells(
  counts: Partial<ProgressCounts> | null | undefined,
): number {
  const c = progressCounts(counts);
  return c.pending + c.running + c.eval_queued + c.evaluating;
}

// ── lifecycle ─────────────────────────────────────────────────────────

/** StatusBadge `tones` for experiment statuses. */
export const experimentTones: Partial<Record<string, Tone>> = {
  draft: "neutral",
  running: "info",
  paused: "warning",
  completed: "success",
  cancelled: "neutral",
  archived: "neutral",
};

export function isTerminal(status: ExperimentStatusV2 | string): boolean {
  return (
    status === "completed" || status === "cancelled" || status === "archived"
  );
}

/** Running or paused: the experiment still owns queued or running cells. */
export function isActive(status: ExperimentStatusV2 | string): boolean {
  return status === "running" || status === "paused";
}

export const PAUSE_REASON_LABELS: Record<string, string> = {
  user: "paused by you",
  digest_changed: "a model's weights changed",
  endpoint_removed: "an endpoint was removed",
};

export function pauseReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "";
  return PAUSE_REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

export const MODE_LABELS: Record<string, string> = {
  agent: "Agent",
  scaffolding: "Scaffolding",
};

// ── priority ──────────────────────────────────────────────────────────

/**
 * The UI's three priority levels (Low −5 · Normal 0 · High 5) for a stored
 * priority (−10..10; staff may set any value through the API).
 */
export function priorityBucket(priority: number | null | undefined): number {
  if (!priority) return 0;
  return priority > 0 ? 5 : -5;
}

// ── scheduling policy ─────────────────────────────────────────────────

export const POLICY_LABELS: Record<SchedulingPolicy, string> = {
  auto: "Auto",
  interleaved: "Interleaved",
  blocked: "Per repeat",
  grouped: "Grouped",
};

export const POLICY_DESCRIPTIONS: Record<SchedulingPolicy, string> = {
  auto: "Per repeat on local endpoints that hold few models at once, interleaved elsewhere.",
  interleaved:
    "Randomised complete blocks: every model's runs are spread over the whole experiment, so time-of-day effects cancel out. Most model swaps.",
  blocked:
    "Within each repeat a model's runs are contiguous; model order is re-shuffled per repeat. Few swaps, drift still balanced across repeats.",
  grouped:
    "Model by model. Fewest swaps and fastest on one GPU, but each model runs at a different time: comparisons are time-confounded.",
};

export function policyLabel(policy: string | null | undefined): string {
  if (!policy) return "—";
  return POLICY_LABELS[policy as SchedulingPolicy] ?? policy;
}

/**
 * The effective policy as one short label: a single policy, or
 * "Per repeat · gpu-1, Interleaved · openrouter" when lanes differ.
 */
export function effectivePolicyLabel(
  effective: Record<string, string> | string | null | undefined,
  fallback?: string | null,
): string {
  if (!effective) return policyLabel(fallback);
  if (typeof effective === "string") return policyLabel(effective);
  const entries = Object.entries(effective);
  if (entries.length === 0) return policyLabel(fallback);
  const bySlug = new Map<string, string[]>();
  for (const [slug, p] of entries)
    bySlug.set(p, [...(bySlug.get(p) ?? []), slug]);
  if (bySlug.size === 1) return policyLabel(entries[0][1]);
  return [...bySlug.entries()]
    .map(([p, slugs]) => `${policyLabel(p)} (${slugs.join(", ")})`)
    .join(" · ");
}

// ── blocked reasons (scheduler admission, §C.4) ───────────────────────

// One vocabulary with the endpoint pages.
export { blockedReasonText } from "$lib/components/inference/endpoint-display";

/** How to relieve a bottleneck (shown as a `title`). */
export const BOTTLENECK_REMEDIATION: Record<string, string> = {
  sandbox:
    "Sandbox slots are full: raise apps-dind memory and the sandbox slot count (Endpoints → Capacity).",
  eval: "Evaluation slots are full: raise the eval slot count (Endpoints → Capacity).",
  llm: "LLM job threads are busy: raise the LLM job thread count (Endpoints → Capacity).",
  endpoint:
    "An endpoint is at capacity: raise its slots (match OLLAMA_NUM_PARALLEL), or balance models across endpoints.",
  endpoint_offline:
    "An endpoint with queued cells is offline: check the relay (and Ollama) on that machine; cells resume when it reconnects.",
  endpoint_full:
    "An endpoint is at capacity: raise its slots (match OLLAMA_NUM_PARALLEL), or balance models across endpoints.",
  circuit_open:
    "An endpoint's circuit breaker is open after repeated failures: check the endpoint; it retries automatically.",
};

export function bottleneckLabel(bottleneck: string | null | undefined): {
  label: string;
  title: string;
} | null {
  if (!bottleneck) return null;
  if (bottleneck.startsWith("endpoint_offline:")) {
    const slug = bottleneck.slice("endpoint_offline:".length);
    return {
      label: `Endpoint ${slug} offline`,
      title: BOTTLENECK_REMEDIATION.endpoint_offline,
    };
  }
  if (bottleneck.startsWith("endpoint:")) {
    const slug = bottleneck.slice("endpoint:".length);
    return {
      label: `Endpoint ${slug}`,
      title: BOTTLENECK_REMEDIATION.endpoint,
    };
  }
  const known: Record<string, string> = {
    sandbox: "Sandbox slots",
    eval: "Evaluation slots",
    llm: "LLM job threads",
    endpoint_offline: "Endpoint offline",
    endpoint_full: "Endpoint at capacity",
  };
  return {
    label: known[bottleneck] ?? bottleneck.replace(/_/g, " "),
    title: BOTTLENECK_REMEDIATION[bottleneck] ?? "",
  };
}

// ── numbers ───────────────────────────────────────────────────────────

/** Seconds → "2h 05m" / "14m" / "<1m"; null → "—". */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return "<1m";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours < 48) return `${hours}h ${String(rest).padStart(2, "0")}m`;
  return `${Math.round(hours / 24)}d ${hours % 24}h`;
}

/** Estimated hours → "≈ 2.4 h" / "≈ 25 min"; null → "—". */
export function formatHours(hours: number | null | undefined): string {
  if (hours == null || !Number.isFinite(hours)) return "—";
  if (hours < 1) return `≈ ${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 10) return `≈ ${hours.toFixed(1)} h`;
  return `≈ ${Math.round(hours)} h`;
}

/**
 * Fewest apps for which a cluster-bootstrap interval over apps is shown: with
 * one or two apps the resampled means barely vary, so the "interval" would
 * look far more certain than the data is.
 */
export const MIN_APPS_FOR_CI = 3;

/** Multiple-comparison corrections of the paired tests (analysis plan `correction`). */
export const CORRECTION_LABEL: Record<string, string> = {
  holm: "Holm",
  bh: "BH",
  none: "unadjusted",
};

/** 0..1 → "87%"; null → "—". */
export function formatPercent(
  value: number | null | undefined,
  digits = 0,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** Signed percentage points: +0.123 → "+12.3 pp". */
export function formatDeltaPp(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const pp = value * 100;
  const sign = pp > 0 ? "+" : pp < 0 ? "−" : "±";
  return `${sign}${Math.abs(pp).toFixed(digits)} pp`;
}

/** p-value → "0.003" / "<0.001"; null → "—". */
export function formatP(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "—";
  if (p < 0.001) return "<0.001";
  return p.toFixed(3);
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** "3 s ago" / "4 min ago" / "2 h ago" from an ISO time. */
export function timeAgo(
  iso: string | null | undefined,
  now = Date.now(),
): string {
  if (!iso) return "—";
  const seconds = Math.max(
    0,
    Math.round((now - new Date(iso).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

/** Time until an ISO instant: "in 4 h" / "in 12 min"; past → "due". */
export function timeUntil(
  iso: string | null | undefined,
  now = Date.now(),
): string {
  if (!iso) return "—";
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  if (seconds <= 0) return "due";
  if (seconds < 3600) return `in ${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 172_800) return `in ${Math.round(seconds / 3600)} h`;
  return `in ${Math.round(seconds / 86_400)} d`;
}

/** Downloads a Blob under `filename` (export buttons). */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** A condition's name: its label, or the server's "model / method" fallback. */
export function conditionName(c: {
  id: number;
  label: string;
  display_label?: string;
}): string {
  return c.label || c.display_label || `Condition #${c.id}`;
}

// ── analysis plan and the confirmatory gate ───────────────────────────

/** The experiment's analysis plan, frozen or not (a draft's), or null when it has none ({}). */
export function experimentPlan(e: {
  analysis_plan?: AnalysisPlan | Record<string, never>;
}): AnalysisPlan | null {
  const plan = e.analysis_plan;
  return plan && "primary_metric" in plan ? (plan as AnalysisPlan) : null;
}

/** The experiment's frozen analysis plan, or null (none, or not frozen yet: a draft's plan). */
export function frozenPlan(e: {
  analysis_plan?: AnalysisPlan | Record<string, never>;
  analysis_plan_frozen_at?: string | null;
}): AnalysisPlan | null {
  return e.analysis_plan_frozen_at ? experimentPlan(e) : null;
}

/**
 * The truncation rule the experiment report applies: the plan's (a draft's
 * plan freezes at launch, before any report), else null (the report's setting).
 */
export function planTruncationRule(e: {
  analysis_plan?: AnalysisPlan | Record<string, never>;
}): boolean | null {
  const plan = experimentPlan(e);
  return plan ? (plan.exclusions?.truncated ?? true) : null;
}

/** Plan metrics as the study report names them (backend `analysis_plan.METRICS`). */
export const PLAN_METRIC_LABEL: Record<PlanMetric, string> = {
  deploy_rate: "Deploy rate",
  pass_rate: "Pass rate",
  functional_passed: "Fully correct",
  unique_issues_per_app: "Unique issues / app",
  issues_per_kloc: "Code issues / kLOC",
  security_per_app: "Security issues / app",
  cost_per_app: "Cost / app (USD)",
};

export function planMetricLabel(metric: string): string {
  return PLAN_METRIC_LABEL[metric as PlanMetric] ?? metric;
}

/** The plan's metrics that it makes confirmatory (primary, secondary, research questions). */
export function declaredMetrics(plan: AnalysisPlan): PlanMetric[] {
  return [
    ...new Set([
      plan.primary_metric,
      ...(plan.secondary_metrics ?? []),
      ...(plan.research_questions ?? []).map(
        (q) => q.metric ?? plan.primary_metric,
      ),
    ]),
  ];
}

/** Why a Results view is not the plan's confirmatory analysis (backend `experiment_results.DEVIATIONS`). */
export const DEVIATION_LABEL: Record<PairedDeviation, string> = {
  exclusions_differ: "truncation rule differs from the plan's",
  invalid_excluded: "invalid runs excluded (not one of the plan's exclusions)",
  common_cells_off: "common cells off",
  fingerprint_filter: "fingerprint filter applied",
  design_extended: "repeats added after launch are counted",
  stopped_early: "planned cells were cancelled (stopped early)",
  multiplicity_family:
    "the plan corrects across several metrics (see the study report)",
};

export interface AnalysisStatus {
  /**
   * confirmatory: the server's verdict (`paired.status`); plan: the view is the
   * plan's analysis but its p-values are exploratory here (the plan's
   * correction family spans metrics the Results tab does not test);
   * exploratory: anything else.
   */
  kind: "confirmatory" | "plan" | "exploratory";
  label: string;
  /** Why the view is exploratory (empty when confirmatory). */
  reasons: string[];
}

/** The Results view's status, from the server's verdict and its deviations, never from the URL. */
export function analysisStatus(
  results: Pick<ExperimentResults, "baseline_source" | "paired">,
  plan: AnalysisPlan | null,
): AnalysisStatus {
  if (results.paired.status === "confirmatory") {
    return { kind: "confirmatory", label: "Pre-registered", reasons: [] };
  }
  const deviations = results.paired.deviations ?? [];
  const reasons: string[] = [];
  if (!plan) reasons.push("no frozen analysis plan");
  if (results.baseline_source === "post_hoc") {
    reasons.push("baseline chosen post hoc");
  }
  reasons.push(...deviations.map((d) => DEVIATION_LABEL[d] ?? d));
  // Stays true after any reset: the plan never tests pass rate, so no view of it is the plan's analysis.
  const declaresPassRate =
    !!plan && declaredMetrics(plan).includes("pass_rate");
  if (plan && !declaresPassRate) {
    reasons.push("the plan does not declare pass rate");
  }
  if (
    plan &&
    declaresPassRate &&
    results.baseline_source === "preregistered" &&
    deviations.length === 1 &&
    deviations[0] === "multiplicity_family"
  ) {
    return {
      kind: "plan",
      label: "Plan’s analysis",
      reasons: [
        "p-values exploratory here: the plan’s correction spans several metrics (see the study report)",
      ],
    };
  }
  return { kind: "exploratory", label: "Exploratory", reasons };
}
