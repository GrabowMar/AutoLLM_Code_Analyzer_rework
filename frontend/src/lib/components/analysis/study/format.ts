/**
 * Display helpers for the studies UI (wizard, detail, protocol, report).
 *
 * Research-validity rule: `null` / `undefined` is missing data and renders
 * as {@link NA} ("—") with a reason, never as 0. Every formatter here
 * returns {@link NA} for a missing value so callers cannot forget it.
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

/** The missing-value marker. */
export const NA = "—";

/** Default tooltip for a missing value. */
export { NA_REASON } from "../run-utils";

export function isMissing(value: unknown): value is null | undefined {
  return (
    value === null ||
    value === undefined ||
    (typeof value === "number" && !Number.isFinite(value))
  );
}

/** Integer with grouping (1,234). */
export function fmtInt(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  return Math.round(value).toLocaleString();
}

/**
 * A measured number with precision by magnitude: 1234.5 → "1,235",
 * 207.7151 → "207.7", 25.51 → "25.51", 0.0909 → "0.0909".
 */
export function fmtFloat(
  value: number | null | undefined,
  digits?: number,
): string {
  if (isMissing(value)) return NA;
  if (digits !== undefined) {
    return value.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: digits,
    });
  }
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 1000) return Math.round(value).toLocaleString();
  if (abs >= 100)
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (abs >= 1)
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(Number(value.toPrecision(3)));
}

/** Share in [0, 1] as a percentage: 0.8333 → "83.3%". */
export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (isMissing(value)) return NA;
  const pct = value * 100;
  const fixed = Number(pct.toFixed(digits));
  return `${fixed.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

/** p-value: "< 0.001" below the display floor, else 3 decimals. */
export function fmtP(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  if (value < 0.001) return "< 0.001";
  return value.toFixed(3);
}

/** `[lo, hi]` interval; NA unless both ends are numbers. */
export function fmtInterval(
  value: unknown,
  fmt: (n: number) => string = (n) => fmtFloat(n),
): string {
  if (!Array.isArray(value) || value.length !== 2) return NA;
  const [lo, hi] = value as unknown[];
  if (typeof lo !== "number" || typeof hi !== "number") return NA;
  return `[${fmt(lo)}, ${fmt(hi)}]`;
}

/** US dollars with enough precision for per-call LLM costs ($0 stays "$0.00"). */
export function fmtUsd(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  const abs = Math.abs(value);
  if (abs === 0) return "$0.00";
  if (abs >= 100) return `$${value.toFixed(0)}`;
  if (abs >= 1) return `$${value.toFixed(2)}`;
  if (abs >= 0.01) return `$${value.toFixed(4)}`;
  return `$${Number(value.toPrecision(3))}`;
}

/** Seconds → "42 s" / "3 min 12 s" / "1 h 05 min". */
export function fmtSeconds(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  const s = Math.max(0, value);
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min ${Math.round(s % 60)} s`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/** Bytes → "1.4 GB". */
export function fmtBytes(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let unit = 0;
  while (Math.abs(n) >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit += 1;
  }
  return `${n.toFixed(n >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** ISO timestamp → locale date + time (NA when missing). */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** ISO timestamp → "5 Mar 2026". */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** First characters of a sha256 / uuid. */
export function shortHash(
  value: string | null | undefined,
  length = 12,
): string {
  if (!value) return NA;
  const clean = value.startsWith("sha256:") ? value.slice(7) : value;
  return clean.slice(0, length);
}

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
