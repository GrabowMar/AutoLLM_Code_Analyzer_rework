/**
 * Status/severity → Tailwind badge classes, one map per domain.
 *
 * Domains intentionally diverge (e.g. "running" is blue for analysis but
 * amber for generation) — never merge these maps or point a page at another
 * domain's map without checking the rendered colors stay identical.
 */

/** Findings severity badges (shared across analysis, statistics, home). */
export const severityColors: Record<string, string> = {
  critical: "bg-red-500/15 text-red-400 border-red-500/30",
  high: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  medium: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  low: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  info: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

/** Analysis runs/results. */
export const analysisStatusColors: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  running: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  pending: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  partial: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  cancelled: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  skipped: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

/** Generation jobs (running is amber here, unlike analysis). */
export const generationStatusColors: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  running: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  pending: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  cancelled: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

/** Pipeline runs / batch items. */
export const pipelineRunStatusColors: Record<string, string> = {
  pending: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  running: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  succeeded: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  completed: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  cancelled: "bg-orange-500/15 text-orange-400 border-orange-500/30",
};

/** Pipeline lifecycle (the pipeline itself, not its runs). */
export const pipelineLifecycleColors: Record<string, string> = {
  draft: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  active: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  archived: "bg-orange-500/15 text-orange-400 border-orange-500/30",
};

/** Runtime containers. */
export const containerStatusColors: Record<string, string> = {
  pending: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  building: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  running: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  stopped: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  removed: "bg-neutral-500/15 text-neutral-400 border-neutral-500/30",
};

/** Reports. */
export const reportStatusColors: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  generating: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  pending: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
};
