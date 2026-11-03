/**
 * Pure helpers of the sample view (`/samples/[id]`, spec 10.4): outcome taxonomy text (6.12), file-origin
 * filtering with the parent overlay of slot variants, the file tree, and number formats.
 *
 * Validity rules carried here: a missing measurement is shown as "not measured" (never 0 or "clean"); an
 * evaluation that did not measure is never a pass rate; origin is provenance, never a status colour.
 */
import type { Tone } from "$lib/constants/colors";
import type {
  EvalCheckResult,
  EvalProbeResult,
  FileOriginValue,
  FileScope,
  RunEvaluationOut,
  RunFileOut,
  RunKind,
} from "$lib/api/v3/types";

// ── Outcomes (6.12) ────────────────────────────────────────────────────────

export const OUTCOME_TEXT: Record<string, { label: string; explain: string }> =
  {
    tested: {
      label: "Tested",
      explain:
        "The app built, started and the tester ran its checks. The pass rate may still be 0.",
    },
    output_invalid: {
      label: "Output invalid",
      explain:
        "The model produced no usable code (empty or skeleton-only workspace, workspace too large, or for a slot: a parse failure, an empty replacement or an edit violation under edit_policy=fail). Model-caused: analysed as data.",
    },
    build_fail: {
      label: "Build failed",
      explain:
        "The lock or image build failed for model-attributable reasons. Model-caused: analysed as data.",
    },
    start_fail: {
      label: "Start failed",
      explain:
        "The app built but did not become healthy in time. Model-caused: analysed as data.",
    },
    infra_error: {
      label: "Infra error",
      explain:
        "Retries were exhausted on an infrastructure problem (transport, docker, tester or gateway crash, provider errors). Not usable; a retry keeps the manifest and seed.",
    },
    excluded: {
      label: "Excluded",
      explain:
        "Excluded by policy or configuration (budget, cancellation, a stop rule, resource limit, …). Counted per condition, never hidden.",
    },
  };

export const OUTCOME_TONES: Partial<Record<string, Tone>> = {
  tested: "success",
  output_invalid: "warning",
  build_fail: "danger",
  start_fail: "danger",
  infra_error: "warning",
  excluded: "neutral",
};

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return "No outcome yet";
  return OUTCOME_TEXT[outcome]?.label ?? outcome.replace(/_/g, " ");
}

export const KIND_LABELS: Record<RunKind | string, string> = {
  app_agentic: "Agentic app",
  slot_single_shot: "Slot · single shot",
  slot_agent: "Slot · agent",
  reference: "Reference",
};

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}

export const TERMINATION_TEXT: Record<string, string> = {
  completed: "The harness finished on its own.",
  wall_clock:
    "Stopped at the wall-clock limit (truncated; included in ITT, excluded in the sensitivity analysis).",
  idle_timeout: "Stopped by the idle timer (truncated).",
  budget_exhausted: "Stopped when the run budget was used up (truncated).",
  max_turns: "Stopped at the turn limit (truncated).",
  harness_error:
    "The harness exited with an error; its output was still evaluated.",
  harness_crash: "The harness crashed; retried once, then evaluated.",
  llm_error: "Provider errors after upstream retries (infra error).",
  infra_interrupted: "Interrupted by an infrastructure failure (infra error).",
  cancelled: "Cancelled.",
  oom_killed:
    "Killed for exceeding its memory limit (excluded: resource_limit).",
};

/** A usable, completed agentic app is a valid slot parent (the backend re-checks `tested`). */
export function canBeParent(run: {
  kind: string;
  status: string;
  usable: boolean | null;
  outcome: string;
}): boolean {
  return (
    run.kind === "app_agentic" &&
    run.status === "completed" &&
    run.usable === true &&
    run.outcome === "tested"
  );
}

export function isActiveStatus(status: string | null | undefined): boolean {
  return status === "pending" || status === "running";
}

// ── Numbers ────────────────────────────────────────────────────────────────

const DASH = "—";

export function fmtUsd(
  value: number | null | undefined,
  digits?: number,
): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return DASH;
  const d =
    digits ?? (Math.abs(value) >= 1 ? 2 : Math.abs(value) >= 0.01 ? 3 : 4);
  return `$${value.toFixed(d)}`;
}

export function fmtSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return DASH;
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  if (minutes < 60) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return DASH;
  return Math.round(value).toLocaleString("en-US");
}

/** A 0–1 rate as a percentage; `null` is "not measured", never 0 %. */
export function fmtRate(
  value: number | null | undefined,
  notMeasured = "not measured",
): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return notMeasured;
  return `${(value * 100).toFixed(value === 0 || value === 1 ? 0 : 1)}%`;
}

export function shortSha(sha: string | null | undefined, n = 12): string {
  return sha ? sha.slice(0, n) : DASH;
}

// ── Files: origin filter with parent overlay ───────────────────────────────

export const ORIGIN_LABELS: Record<string, string> = {
  authored: "authored",
  modified: "modified",
  slot: "slot",
  starter: "starter",
  parent: "parent",
  deleted: "deleted",
  artifact: "artifact",
};

export const ORIGIN_HELP: Record<string, string> = {
  authored: "Written by the model (new file).",
  modified: "A starter file the model changed.",
  slot: "The regenerated part of a slot variant.",
  starter: "Seeded by the platform and left unchanged.",
  parent:
    "A file of the frozen parent app, overlaid for this variant (not written by the variant's model).",
  deleted: "A starter file the model deleted.",
  artifact: "Build output or dependency directory (excluded from analysis).",
};

/** Origins that make up the model scope (Appendix A #4). */
export const MODEL_ORIGINS: FileOriginValue[] = [
  "authored",
  "modified",
  "slot",
];

export interface FileFilter {
  /** empty = every origin */
  origins: string[];
  /** hide the parent rows a slot variant overlays in app scope */
  hideParent: boolean;
  showTests: boolean;
  showExcluded: boolean;
  query: string;
}

export function defaultFileFilter(): FileFilter {
  return {
    origins: [],
    hideParent: false,
    showTests: true,
    showExcluded: false,
    query: "",
  };
}

/** Filter a run's file rows; deleted rows have no content and are listed only when asked for by origin. */
export function filterFiles(
  files: RunFileOut[],
  filter: FileFilter,
): RunFileOut[] {
  const query = filter.query.trim().toLowerCase();
  const origins = new Set(filter.origins);
  return files.filter((file) => {
    if (origins.size > 0 && !origins.has(file.origin)) return false;
    if (origins.size === 0 && file.origin === "deleted") return false;
    if (filter.hideParent && file.origin === "parent") return false;
    if (!filter.showTests && file.is_test) return false;
    if (!filter.showExcluded && file.excluded && !origins.has(file.origin))
      return false;
    if (query && !file.path.toLowerCase().includes(query)) return false;
    return true;
  });
}

export function originCounts(files: RunFileOut[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const file of files)
    counts[file.origin] = (counts[file.origin] ?? 0) + 1;
  return counts;
}

/** Origins present in `files`, in the canonical display order. */
export function presentOrigins(files: RunFileOut[]): string[] {
  const counts = originCounts(files);
  const order = Object.keys(ORIGIN_LABELS);
  return Object.keys(counts).sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
}

/** Which scopes a run kind offers: slot variants have a slot scope and the parent overlay in app scope. */
export function scopesFor(kind: string): FileScope[] {
  return kind === "slot_single_shot" || kind === "slot_agent"
    ? ["slot", "app", "model"]
    : ["model", "app"];
}

export const SCOPE_HELP: Record<FileScope, string> = {
  model:
    "Files the model wrote or changed (authored, modified, slot). This is what model-scope statistics count.",
  app: "The whole app as built and evaluated: the model's files plus untouched starter files (and, for slot variants, the frozen parent's files).",
  slot: "Only the regenerated part of a slot variant.",
};

// ── File tree ──────────────────────────────────────────────────────────────

export interface TreeNode {
  name: string;
  path: string;
  /** set on leaves */
  file?: RunFileOut;
  children: TreeNode[];
}

/** A directory tree of `files` (directories first, then names); single-child directories are collapsed. */
export function buildFileTree(files: RunFileOut[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", children: [] };
  for (const file of files) {
    const parts = file.path.split("/").filter(Boolean);
    let node = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      const leaf = i === parts.length - 1;
      let child = node.children.find(
        (c) => c.name === part && !!c.file === leaf,
      );
      if (!child) {
        child = { name: part, path, children: [], ...(leaf ? { file } : {}) };
        node.children.push(child);
      }
      node = child;
    });
  }
  const sortRec = (nodes: TreeNode[]) => {
    nodes.sort(
      (a, b) =>
        Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name),
    );
    for (const n of nodes) sortRec(n.children);
  };
  const collapse = (nodes: TreeNode[]): TreeNode[] =>
    nodes.map((n) => {
      let node = n;
      while (
        !node.file &&
        node.children.length === 1 &&
        !node.children[0].file
      ) {
        const only = node.children[0];
        node = { ...only, name: `${node.name}/${only.name}` };
      }
      return { ...node, children: collapse(node.children) };
    });
  sortRec(root.children);
  return collapse(root.children);
}

// ── Evaluation tables ──────────────────────────────────────────────────────

/** Infra checks (and flaky ones) never count in a pass rate; they are listed apart. */
export function isInfraCheck(
  check: Pick<EvalCheckResult, "category">,
): boolean {
  return check.category === "infra";
}

export interface CheckGroup {
  category: string;
  checks: EvalCheckResult[];
  passed: number;
  counted: number;
}

/** Agent checks grouped by category (infra separated; flaky checks excluded from the counts). */
export function groupChecks(
  checks: EvalCheckResult[],
  flaky: string[] = [],
): { agent: CheckGroup[]; infra: EvalCheckResult[] } {
  const flakySet = new Set(flaky);
  const groups = new Map<string, CheckGroup>();
  const infra: EvalCheckResult[] = [];
  for (const check of checks) {
    if (isInfraCheck(check)) {
      infra.push(check);
      continue;
    }
    let group = groups.get(check.category);
    if (!group) {
      group = { category: check.category, checks: [], passed: 0, counted: 0 };
      groups.set(check.category, group);
    }
    group.checks.push(check);
    if (!flakySet.has(check.id) && check.status !== "skipped") {
      group.counted += 1;
      if (check.status === "pass") group.passed += 1;
    }
  }
  return {
    agent: [...groups.values()].sort((a, b) =>
      a.category.localeCompare(b.category),
    ),
    infra,
  };
}

export const PROBE_STATUS_TEXT: Record<EvalProbeResult["status"], string> = {
  exploited: "exploited",
  not_exploited: "not exploited",
  error: "error",
  skipped: "skipped",
};

export const PROBE_TONES: Partial<Record<string, Tone>> = {
  exploited: "danger",
  not_exploited: "success",
  error: "warning",
  skipped: "neutral",
};

export const SKIP_REASON_TEXT: Record<string, string> = {
  precondition_failed:
    "precondition failed (the endpoint it needs does not work)",
  positive_control_failed: "positive control failed",
  disabled: "disabled",
  level: "not at this evaluation level",
};

/** One line for the evaluation status; "not measured" carries its reason. */
export function evaluationSummary(evaluation: RunEvaluationOut | null): string {
  if (!evaluation) return "Not evaluated yet";
  if (evaluation.status === "measured" && evaluation.result?.measured) {
    return `Measured · ${fmtRate(evaluation.result.metrics.agent_pass_rate)} of agent checks passed`;
  }
  if (evaluation.status === "not_measured") {
    return `Not measured${evaluation.result?.not_measured_reason ? ` · ${evaluation.result.not_measured_reason}` : ""}`;
  }
  if (evaluation.status === "error")
    return "Evaluation error (not a 0 pass rate)";
  return `Evaluation ${evaluation.status}`;
}
