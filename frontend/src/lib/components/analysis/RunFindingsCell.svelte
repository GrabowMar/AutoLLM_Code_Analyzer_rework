<script lang="ts">
	/**
	 * RunFindingsCell — a run's findings for tables: the total (so far, while
	 * active) and up to three levels exactly as the tools reported them; findings
	 * of tools that rate nothing are left out of the chips, never rated here.
	 *
	 * Props: `run` (list item).
	 */
	import type { AnalysisRunListItem } from '$lib/api/runs';
	import LevelBadge from './LevelBadge.svelte';
	import { isActive, reportedLevels } from './format';

	let { run }: { run: AnalysisRunListItem } = $props();

	const active = $derived(isActive(run.status));
	const total = $derived(
		active ? (run.progress?.findings ?? 0) : (run.summary?.total_findings as number | undefined)
	);
	const levels = $derived(reportedLevels(run.summary).slice(0, 3));
</script>

{#if total == null}
	<span class="text-muted-foreground">—</span>
{:else}
	<div class="flex flex-wrap items-center justify-end gap-1.5">
		<span class="font-mono tabular-nums">{total}</span>
		{#if active}<span class="text-[11px] text-muted-foreground">so far</span>{/if}
		{#each levels as [label, n] (label)}
			<span class="cell-sub inline-flex items-center gap-0.5"><LevelBadge {label} /><span class="font-mono text-[11px] text-muted-foreground tabular-nums">{n}</span></span>
		{/each}
	</div>
{/if}
