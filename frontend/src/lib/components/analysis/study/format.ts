/**
 * Display helpers for the studies UI (wizard, detail, protocol, report).
 *
 * Research-validity rule: `null` / `undefined` is missing data and renders
 * as `NA` ("—") with a reason, never as 0 (formatters: `../format`).
 */

import type {
  Correction,
  DesignFactor,
  EligibilitySeverity,
  LaunchStatus,
  RunStatus,
  Study,
  StudyStatus,
  TargetOptions,
  TargetSpec,
} from "$lib/api/analysis";
import { hasTargetSpec } from "$lib/api/analysis";
import type { Tone } from "$lib/constants/colors";

// Number / time / hash formatting lives in the shared analysis module.
export {
  NA,
  NA_REASON,
  fmtBytes,
  fmtDate,
  fmtDateTime,
  fmtFloat,
  fmtInt,
  fmtInterval,
  fmtP,
  fmtPct,
  fmtSeconds,
  fmtUsd,
  isMissing,
  shortHash,
} from "../format";
import { NA, shortHash } from "../format";

/** `snake_case` / `kebab-case` code → "Snake case". */
export function humanize(code: string | null | undefined): string {
  if (!code) return NA;
  const text = code.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export const DESIGN_FACTOR_LABELS: Record<DesignFactor, string> = {
  model: "Model",
  condition: "Condition",
  harness: "Harness",
  mode: "Mode",
  stack: "Stack",
  app: "App",
};

export const CORRECTION_LABELS: Record<Correction, string> = {
  holm: "Holm",
  bh: "Benjamini–Hochberg",
  none: "None",
};

export function factorList(factors: DesignFactor[] | null | undefined): string {
  if (!factors || factors.length === 0) return NA;
  return factors.map((f) => DESIGN_FACTOR_LABELS[f] ?? f).join(" × ");
}

export const STUDY_STATUS_DESCRIPTIONS: Record<StudyStatus, string> = {
  draft: "Editable. Run the preflight and lock the protocol when ready.",
  locked: "Protocol frozen. Launch to submit one run per eligible target.",
  running: "Runs are being submitted or executed.",
  completed: "Every run finished. Generate the post-run report.",
  cancelled: "Cancelled. Finished runs are kept and can be reported.",
};

export const LAUNCH_STATUS_LABELS: Record<LaunchStatus, string> = {
  submitting: "Submitting runs",
  interrupted: "Submission interrupted",
  submitted: "All runs submitted",
  failed: "Submission failed",
  cancelled: "Submission cancelled",
};

/** Tone of a target eligibility reason. */
export function reasonTone(severity: EligibilitySeverity | string): Tone {
  if (severity === "exclude") return "danger";
  if (severity === "warn" || severity === "warning") return "warning";
  return "neutral";
}

/** Tone of a report deviation severity (`major` / `warn` / `info`). */
export const deviationTones: Partial<Record<string, Tone>> = {
  major: "danger",
  warn: "warning",
  warning: "warning",
  info: "info",
};

/** Run counts of a study (list row) by bucket. */
export interface RunBuckets {
  total: number;
  terminal: number;
  active: number;
  completed: number;
  partial: number;
  failed: number;
  cancelled: number;
}

export function runBuckets(
  counts: Partial<Record<RunStatus, number>> | null | undefined,
): RunBuckets {
  const c = counts ?? {};
  const completed = c.completed ?? 0;
  const partial = c.partial ?? 0;
  const failed = c.failed ?? 0;
  const cancelled = c.cancelled ?? 0;
  const active = (c.queued ?? 0) + (c.running ?? 0);
  const terminal = completed + partial + failed + cancelled;
  return {
    total: terminal + active,
    terminal,
    active,
    completed,
    partial,
    failed,
    cancelled,
  };
}

/** One-line description of a target spec ("Experiment · Security baseline · 2 conditions"). */
export function targetSpecSummary(
  spec: TargetSpec | Record<string, never> | null | undefined,
  options?: TargetOptions | null,
): string {
  if (!hasTargetSpec(spec)) return "No targets chosen";
  switch (spec.kind) {
    case "experiment": {
      const exp = options?.experiments.find((e) => e.id === spec.experiment_id);
      const parts = [
        `Experiment · ${exp?.name ?? shortHash(spec.experiment_id, 8)}`,
      ];
      if (spec.condition_ids?.length)
        parts.push(`${spec.condition_ids.length} condition(s)`);
      if (spec.app_slugs?.length)
        parts.push(`apps: ${spec.app_slugs.join(", ")}`);
      return parts.join(" · ");
    }
    case "batch": {
      const batch = options?.batches.find((b) => b.id === spec.batch_id);
      return `Batch · ${batch?.name ?? shortHash(spec.batch_id, 8)}`;
    }
    case "jobs":
      return `${spec.job_ids.length} selected application(s)`;
    case "filter": {
      const f = spec.filters ?? {};
      const parts: string[] = [];
      if (f.model_ids?.length) parts.push(`${f.model_ids.length} model(s)`);
      if (f.app_slugs?.length) parts.push(`apps: ${f.app_slugs.join(", ")}`);
      if (f.harnesses?.length) parts.push(`harness: ${f.harnesses.join(", ")}`);
      if (f.modes?.length) parts.push(`mode: ${f.modes.join(", ")}`);
      if (f.stacks?.length) parts.push(`stack: ${f.stacks.join(", ")}`);
      if (f.created_after) parts.push(`after ${f.created_after.slice(0, 10)}`);
      if (f.created_before)
        parts.push(`before ${f.created_before.slice(0, 10)}`);
      return parts.length
        ? `Filter · ${parts.join(" · ")}`
        : "Filter · every completed job";
    }
  }
}

/** `profile@vN` label. */
export function profileLabel(study: Pick<Study, "profile">): string {
  if (!study.profile) return "No profile";
  return `${study.profile.name} · v${study.profile.version}`;
}
