<script lang="ts">
	/**
	 * RunStatusCell — a run's status for tables: the badge, and while active a
	 * thin progress bar with "3/12 tools · Semgrep" (or the phase / queue state).
	 * Finished runs with failed tools say how many.
	 *
	 * Props: `run` (list item), `toolName(slug)`.
	 */
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import type { AnalysisRunListItem } from '$lib/api/runs';
	import { RUN_STATUS_HELP, isActive, runStatusLabel } from './format';

	interface Props {
		run: AnalysisRunListItem;
		toolName?: (slug: string) => string;
	}

	let { run, toolName = (s: string) => s }: Props = $props();

	const p = $derived(run.progress);
	const total = $derived(p?.total || run.tool_slugs.length);
	const pct = $derived(p && total ? Math.round((p.done / total) * 100) : 0);
	const note = $derived.by(() => {
		if (run.status === 'pending') return 'waiting for its turn';
		if (p?.running.length) return toolName(p.running[0]);
		if (run.phase === 'waiting_for_workspace') return 'waiting for workspace';
		if (run.phase === 'preparing') return 'preparing';
		return '';
	});
	const failedTools = $derived((run.summary?.tools_failed as number | undefined) ?? 0);
</script>

<div class="min-w-[9rem] space-y-1" title={RUN_STATUS_HELP[run.status]}>
	<StatusBadge status={run.status} label={runStatusLabel(run.status)} />
	{#if isActive(run.status)}
		<div class="h-1 w-full max-w-[10rem] overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={p?.done ?? 0} aria-label="Tools finished">
			<div class="h-full rounded-full bg-info transition-[width] duration-300" style="width: {pct}%"></div>
		</div>
		<p class="truncate text-[11px] text-muted-foreground">
			<span class="font-mono tabular-nums">{p?.done ?? 0}/{total}</span> tools{note ? ` · ${note}` : ''}
		</p>
	{:else if failedTools > 0 && run.status !== 'failed'}
		<p class="cell-sub text-[11px] text-danger-fg">{failedTools} tool{failedTools === 1 ? '' : 's'} failed</p>
	{/if}
</div>
