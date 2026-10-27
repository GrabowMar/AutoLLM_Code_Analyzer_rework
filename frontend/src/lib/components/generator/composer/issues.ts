/**
 * Run-summary issues: preview warnings (agent) and client-side checks
 * (scaffolding / single prompt) share one shape and one renderer.
 */
import type { AgentPreviewWarning, PreviewSeverity } from "$lib/api/generation";

export interface SummaryIssue {
  code: string;
  severity: PreviewSeverity;
  detail: string;
  /** Prefix naming the model the issue is about. */
  subject?: string;
}

/** One "before you start" item of a Run summary. */
export interface ChecklistItem {
  label: string;
  done: boolean;
  /** Shown after the label when done (e.g. "3 selected"). */
  detail?: string;
  /** Anchor of the section that completes the item. */
  href?: string;
}

/** A quick fix shown under an issue (a link or a button). */
export interface IssueAction {
  label: string;
  href?: string;
  onclick?: () => void;
}

const TITLES: Record<string, string> = {
  no_tasks: "No task",
  no_models: "No models",
  models_without_tools: "Model without tool calling",
  missing_api_key: "No OpenRouter key",
  duplicate_harness_config: "Duplicate harness configuration",
  invalid_preset: "Invalid preset",
  reasoning_exceeds_output_cap: "Reasoning budget above the output cap",
  resolution_failed: "A run could not be resolved",
  params_dropped: "Parameters dropped",
  provider_unpinned: "Upstream provider not pinned",
  harness_native_sampling: "Harness-native sampling",
  context_window_fallback: "Context window unknown",
  cost_cap_may_bind: "Cost cap may end runs early",
  wall_clock_may_bind: "Wall clock may end runs early",
  whole_file_small_output: "Whole-file edits with a small output cap",
  large_batch: "Large batch",
  ungraded_task: "Not graded",
  non_default_harness_options: "Non-default harness options",
  budget_changes_brief: "Budget is stated in the brief",
  single_trial: "Single trial",
  random_seeds: "Random seeds",
  profile_stack_mismatch: "Profile and stack disagree",
  auto_profile: "Profile chosen per app",
  no_profile_for_stack: "No prompt profile for this stack",
  llm_preview_failed: "Effective parameters unavailable",
  support_unknown: "Parameter support unknown",
  preset_lookup_failed: "Selected preset not loaded",
  profile_unavailable: "Profile version unavailable",
  profile_lookup_failed: "Profile version not loaded",
};

/**
 * The warning shown while a selected preset could not be looked up (an
 * outage, not a 404): the selection is kept, and its action retries.
 */
export function presetLookupIssue(errors: string[]): SummaryIssue | null {
  const messages = [...new Set(errors.filter(Boolean))];
  if (messages.length === 0) return null;
  return {
    code: "preset_lookup_failed",
    severity: "warn",
    detail: `A selected preset couldn't be loaded (${messages.join("; ")}). It stays selected; retry to show its settings.`,
  };
}

export function issueTitle(code: string): string {
  return TITLES[code] ?? code.replace(/_/g, " ");
}

export const SEVERITY_ORDER: PreviewSeverity[] = ["error", "warn", "info"];

export const SEVERITY_LABELS: Record<PreviewSeverity, string> = {
  error: "Errors",
  warn: "Warnings",
  info: "Notes",
};

export function groupIssues(
  issues: SummaryIssue[],
): { severity: PreviewSeverity; items: SummaryIssue[] }[] {
  return SEVERITY_ORDER.map((severity) => ({
    severity,
    items: issues.filter((issue) => issue.severity === severity),
  })).filter((group) => group.items.length > 0);
}

export function previewIssues(
  warnings: AgentPreviewWarning[],
  modelName: (id: number) => string | undefined,
): SummaryIssue[] {
  return warnings.map((w) => ({
    code: w.code,
    severity: SEVERITY_ORDER.includes(w.severity) ? w.severity : "info",
    detail: w.detail,
    subject: w.model_id != null ? modelName(w.model_id) : undefined,
  }));
}
