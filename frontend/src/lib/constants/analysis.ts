/** Status → Tailwind class mappings for analysis tasks and results */
export const statusColors: Record<string, string> = {
	completed: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
	running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
	failed: 'bg-red-500/15 text-red-400 border-red-500/30',
	pending: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
	partial: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
	cancelled: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
	skipped: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
};

/** Severity → Tailwind class mappings for findings */
export const severityColors: Record<string, string> = {
	critical: 'bg-red-500/15 text-red-400 border-red-500/30',
	high: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
	medium: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
	low: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
	info: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
};

/** Analyzer type labels */
export const analyzerTypeLabels: Record<string, string> = {
	static: 'Static Analysis',
	dynamic: 'Dynamic Analysis',
	performance: 'Performance',
	ai: 'AI Review',
};

/** Severity sort order (lower = more severe) */
export const severityOrder: Record<string, number> = {
	critical: 0,
	high: 1,
	medium: 2,
	low: 3,
	info: 4,
};
