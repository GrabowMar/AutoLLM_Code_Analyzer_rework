// The analysis run header's setup line and target label (v3: the analyzed GenerationRun's study
// cell and treatment, read through the backend's RunView). Pure functions (unit-tested).

import { modelPathSegment } from "$lib/api/models";
import type { RunSetup, RunTarget } from "$lib/api/runs";
import { shortModelName } from "$lib/components/table";

/** One part of a run's setup line; `href` links it, `title` explains it. */
export interface SetupItem {
  label: string;
  href?: string;
  title?: string;
}

/** "repeat 2/5", "repeat 7 (beyond the 5 planned)", "repeat 3" (no plan). */
export function repeatLabel(
  repeatIndex: number | null,
  planned: number | null,
): string | null {
  if (repeatIndex == null) return null;
  const k = repeatIndex + 1;
  if (planned == null) return `repeat ${k}`;
  return k > planned
    ? `repeat ${k} (beyond the ${planned} planned)`
    : `repeat ${k}/${planned}`;
}

/**
 * The design cell (study › condition › pilot › repeat k/N) and the treatment (model · harness ·
 * task) of the analyzed run. Parts the run does not have are left out (an ad-hoc run has no
 * study cell).
 */
export function runSetupItems(job: RunSetup): {
  cell: SetupItem[];
  method: SetupItem[];
} {
  const cell: SetupItem[] = [];
  if (job.study_id) {
    cell.push({
      label: job.study_name || "Experiment",
      href: `/experiments/${job.study_id}`,
      title: "Open the experiment",
    });
  }
  if (job.condition_label) {
    cell.push({ label: job.condition_label, title: "Experiment condition" });
  }
  if (job.stage === "pilot") {
    cell.push({
      label: "pilot",
      title: "A pilot-stage run: never pooled with the main stage",
    });
  }
  const repeat = repeatLabel(job.repeat_index, job.planned_repeats);
  if (repeat) {
    cell.push({
      label: repeat,
      title:
        "Repeat of this (condition, task) cell / repeats planned by the experiment",
    });
  }
  const method: SetupItem[] = [];
  if (job.model_id || job.model_name) {
    method.push({
      label: shortModelName(job.model_name || job.model_id),
      href: `/models/${job.model_slug || modelPathSegment(job.model_id)}`,
      title: job.model_id,
    });
  }
  if (job.harness) {
    method.push({
      label: job.harness_version
        ? `${job.harness} ${job.harness_version}`
        : job.harness,
      title: job.config_fingerprint
        ? `Coding-agent harness · config fingerprint ${job.config_fingerprint.slice(0, 12)}`
        : "Coding-agent harness",
    });
  }
  const task = job.task_title || job.task_slug;
  method.push({
    label: task
      ? `${task}${job.prose_variant ? ` (${job.prose_variant})` : ""}`
      : job.stack_ref || "App",
    href: `/samples/${job.id}`,
    title:
      [job.task_ref, job.stack_ref].filter(Boolean).join(" · ") ||
      "Open the sample",
  });
  return { cell, method };
}

/** The header's title and subtitle for an analysis target (a run, pasted code or a deleted app). */
export function runTargetLines(target: RunTarget | null | undefined): {
  title: string;
  sub: string;
} {
  if (!target) return { title: "—", sub: "" };
  if (target.kind !== "run") return { title: target.label, sub: "" };
  const title =
    target.task_title || target.stack_ref || target.label || "Generated app";
  const sub = [
    shortModelName(target.model_name),
    target.task_title ? target.stack_ref : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return { title, sub };
}
