<script lang="ts">
	/**
	 * RunFindingsCell — a run's findings for tables. A finished run shows its
	 * code findings, with dependency advisories and dynamic tools' (runtime)
	 * findings counted apart underneath (they are not code issues); a run
	 * stored before that split, or still active, shows its total (so far).
	 * Up to three chips give levels exactly as the tools reported them, over
	 * all of the run's findings; findings of tools that rate nothing are left
	 * out of the chips, never rated here. A finished run in which no tool
	 * measured anything, or no code tool measured the code, shows "—", not 0.
	 *
	 * Props: `run` (list item).
	 */
	import type { AnalysisRunListItem } from '$lib/api/runs';
	import LevelBadge from './LevelBadge.svelte';
	import { isActive, nonCodeText, reportedLevels, runScopeCounts, runToolCounts } from './format';

	let { run }: { run: AnalysisRunListItem } = $props();

	const active = $derived(isActive(run.status));
	const total = $derived(
		active ? (run.progress?.findings ?? 0) : (run.summary?.total_findings as number | undefined)
	);
	const scopes = $derived(active ? null : runScopeCounts(run.summary));
	const extra = $derived(nonCodeText(scopes));
	const levels = $derived(reportedLevels(run.summary).slice(0, 3));
	// Failed tools can keep findings: only an empty count from a run that measured nothing is hidden.
	const nothingMeasured = $derived(!active && !total && !!runToolCounts(run)?.nothingMeasured);
</script>

{#if nothingMeasured}
	<span class="text-muted-foreground" title="No tool measured the code (all skipped or failed): there is no finding count">—</span>
{:else if total == null}
	<span class="text-muted-foreground">—</span>
{:else}
	<div class="flex flex-col items-end gap-0.5">
		<div class="flex flex-wrap items-center justify-end gap-1.5">
			{#if !scopes}
				<span class="font-mono tabular-nums">{total}</span>
			{:else if scopes.code == null}
				<span class="text-muted-foreground" title="No code-analysis tool measured this run: there is no code finding count">—</span>
			{:else}
				<span class="font-mono tabular-nums" title="Code findings; dependency advisories and runtime findings are counted apart">{scopes.code}</span>
			{/if}
			{#if active}<span class="text-[11px] text-muted-foreground">so far</span>{/if}
			{#if !extra}{@render chips()}{/if}
		</div>
		{#if extra}
			<span class="cell-sub text-[11px] text-muted-foreground" title="Known-vulnerability advisories (package versions) and dynamic tools' findings: not code issues">+ {extra}</span>
			<!-- The levels cover every finding, advisories included: never next to the code count. -->
			{#if levels.length > 0}
				<div class="cell-sub flex items-center justify-end gap-1.5 whitespace-nowrap">
					<span class="text-[11px] text-muted-foreground" title="Levels as the tools reported them, over all {total} findings">all:</span>{@render chips()}
				</div>
			{/if}
		{/if}
	</div>
{/if}

{#snippet chips()}
	{#each levels as [label, n] (label)}
		<span class="cell-sub inline-flex items-center gap-0.5" title="Level as the tools reported it, over all {total} findings"><LevelBadge {label} /><span class="font-mono text-[11px] text-muted-foreground tabular-nums">{n}</span></span>
	{/each}
{/snippet}
