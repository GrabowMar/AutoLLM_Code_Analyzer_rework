/**
 * Provenance index of a post-run report: which run analysed which target,
 * so every number, status cell and target label can link back to the run
 * (and through it to the tool results) it came from.
 */

import type {
  ReportCell,
  StatusMatrixSeries,
  StudyReport,
} from "$lib/api/analysis";

export interface ReportProvenance {
  /** job id → canonical run id of the study. */
  runByJob: Map<string, string>;
  /** target label ("model · app · mode · r0 · 8chars") → job id. */
  jobByLabel: Map<string, string>;
  runIds: Set<string>;
  jobIds: Set<string>;
}

function str(value: ReportCell | undefined): string | null {
  return typeof value === "string" && value ? value : null;
}

export function buildProvenance(report: StudyReport): ReportProvenance {
  const runByJob = new Map<string, string>();
  const jobByLabel = new Map<string, string>();
  const runIds = new Set<string>(report.inputs.analysis_run_ids);
  const jobIds = new Set<string>();

  for (const section of report.sections) {
    for (const table of section.tables) {
      const hasRun = table.columns.some((c) => c.key === "run_id");
      const hasJob = table.columns.some((c) => c.key === "job_id");
      if (!hasRun || !hasJob) continue;
      for (const row of table.rows) {
        const run = str(row.run_id);
        const job = str(row.job_id);
        if (!run || !job) continue;
        runIds.add(run);
        jobIds.add(job);
        // Prefer the canonical run of the analysis set over superseded ones.
        const canonical = row.canonical !== false && row.in_analysis !== false;
        if (canonical || !runByJob.has(job)) runByJob.set(job, run);
      }
    }
    for (const series of section.series) {
      if (series.kind !== "status_matrix") continue;
      for (const row of (series as StatusMatrixSeries).rows) {
        jobByLabel.set(row.label, row.key);
        jobIds.add(row.key);
      }
    }
  }
  return { runByJob, jobByLabel, runIds, jobIds };
}

export function runHref(runId: string): string {
  return `/analysis/${runId}`;
}

/**
 * Where a targets × tools cell leads: the AI review tab for the reviewer,
 * the tool's findings for a succeeded static tool, else the run overview
 * (tool status table with the failure reason and diagnostics).
 */
export function toolCellHref(
  runId: string,
  tool: string,
  status: string,
): string {
  if (tool === "ai_review") return `${runHref(runId)}?tab=ai`;
  if (status === "succeeded") {
    return `${runHref(runId)}?tab=findings&tool=${encodeURIComponent(tool)}`;
  }
  return runHref(runId);
}

export function jobHref(jobId: string): string {
  return `/applications/${jobId}`;
}

/** Link for a target label (its run when known, else its application). */
export function labelHref(
  prov: ReportProvenance,
  label: string,
): string | null {
  const job = prov.jobByLabel.get(label);
  if (!job) return null;
  const run = prov.runByJob.get(job);
  return run ? runHref(run) : jobHref(job);
}

/** Link for an id-like cell (`run_id`, `job_id`, or a known run/job uuid). */
export function idHref(
  prov: ReportProvenance,
  columnKey: string,
  value: string,
): string | null {
  if (columnKey === "run_id" || prov.runIds.has(value)) return runHref(value);
  if (columnKey === "job_id" || prov.jobIds.has(value)) {
    const run = prov.runByJob.get(value);
    return columnKey === "job_id" || !run ? jobHref(value) : runHref(run);
  }
  return null;
}
