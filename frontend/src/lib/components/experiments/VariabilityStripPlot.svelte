<script lang="ts">
	/**
	 * VariabilityStripPlot — one row per (condition, app) cell, one dot per
	 * repeat on a shared axis and a tick at the cell mean, with mean ± SD (n)
	 * printed beside the marks. Ink on paper: dots in `chart-1`, the mean
	 * tick in ink. Missing repeats (unmeasured) are not drawn and n says so.
	 * Long designs show the first 40 rows with a "Show all" toggle.
	 *
	 * Props:
	 * - `cells`: the payload's cells (one condition after another).
	 * - `metric`: the plotted metric (key, label, unit).
	 * - `labelOf(conditionId)`: condition display label.
	 */
	import { Button } from '$lib/components/ui/button';
	import type { VariabilityCell, VariabilityMetric } from '$lib/api/variability';
	import { cellValues, domainPos, formatMetricValue, stripDomain } from './variability';

	interface Props {
		cells: VariabilityCell[];
		metric: VariabilityMetric;
		labelOf: (conditionId: number) => string;
	}

	let { cells, metric, labelOf }: Props = $props();

	const LIMIT = 40;
	let showAll = $state(false);

	const rows = $derived(cells.filter((c) => (c.values[metric.key] ?? []).some((v) => v != null)));
	const shown = $derived(showAll ? rows : rows.slice(0, LIMIT));
	const domain = $derived(stripDomain(cellValues(rows, metric.key), metric.unit));
	const ticks = $derived([domain[0], (domain[0] + domain[1]) / 2, domain[1]]);

	function describe(cell: VariabilityCell): string {
		const vals = (cell.values[metric.key] ?? []).map((v) => formatMetricValue(v, metric.unit)).join(', ');
		const s = cell.stats[metric.key];
		return `${labelOf(cell.condition_id)} on ${cell.app_slug}: repeats ${vals}; mean ${formatMetricValue(s?.mean, metric.unit)}, SD ${formatMetricValue(
			s?.sd,
			metric.unit
		)}, n ${s?.n ?? 0}`;
	}
</script>

{#if rows.length === 0}
	<p class="px-4 py-3 text-sm text-muted-foreground">No cell has a measured {metric.label.toLowerCase()}.</p>
{:else}
	<div class="divide-y divide-border/60">
		<div class="grid gap-x-4 bg-muted/40 px-4 py-1.5 sm:grid-cols-[minmax(10rem,16rem)_minmax(0,1fr)_7.5rem]">
			<span class="section-label max-sm:hidden">Condition · app</span>
			<span class="relative block h-4 font-mono text-[11px] text-muted-foreground" aria-hidden="true">
				{#each ticks as t, i (i)}
					<span
						class="absolute top-0 whitespace-nowrap {i === 0 ? '' : i === ticks.length - 1 ? '-translate-x-full' : '-translate-x-1/2'}"
						style="left: {domainPos(t, domain)}">{formatMetricValue(t, metric.unit)}</span
					>
				{/each}
			</span>
			<span class="section-label text-right max-sm:hidden">Mean ± SD (n)</span>
		</div>
		{#each shown as cell, i (`${cell.condition_id}:${cell.app_id}`)}
			{@const s = cell.stats[metric.key]}
			{@const first = i === 0 || shown[i - 1].condition_id !== cell.condition_id}
			<div class="grid items-center gap-x-4 px-4 py-1.5 sm:grid-cols-[minmax(10rem,16rem)_minmax(0,1fr)_7.5rem]">
				<span class="min-w-0 text-sm">
					<span class="block truncate {first ? 'font-medium' : 'text-muted-foreground'}" title={labelOf(cell.condition_id)}
						>{labelOf(cell.condition_id)}</span
					>
					<span class="block truncate font-mono text-xs text-muted-foreground" title={cell.app_name}>{cell.app_slug}</span>
				</span>
				<span class="relative block h-7" role="img" aria-label={describe(cell)} title={describe(cell)}>
					{#each ticks as t, k (k)}
						<span class="absolute inset-y-0 w-px bg-border/70" style="left: {domainPos(t, domain)}" aria-hidden="true"></span>
					{/each}
					{#if s?.min != null && s?.max != null}
						<span
							class="absolute top-1/2 h-px -translate-y-1/2 bg-foreground/40"
							style="left: {domainPos(s.min, domain)}; width: calc({domainPos(s.max, domain)} - {domainPos(s.min, domain)})"
							aria-hidden="true"
						></span>
					{/if}
					{#each cell.values[metric.key] ?? [] as v, k (k)}
						{#if v != null}
							<span
								class="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-card"
								style="left: {domainPos(v, domain)}; background: var(--chart-1)"
								aria-hidden="true"
							></span>
						{/if}
					{/each}
					{#if s?.mean != null}
						<span class="absolute top-1/2 h-4 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-foreground" style="left: {domainPos(s.mean, domain)}" aria-hidden="true"
						></span>
					{/if}
				</span>
				<span class="text-right font-mono text-xs tabular-nums max-sm:text-left">
					{formatMetricValue(s?.mean, metric.unit)}
					<span class="text-muted-foreground">± {formatMetricValue(s?.sd, metric.unit)} ({s?.n ?? 0})</span>
				</span>
			</div>
		{/each}
	</div>
	{#if rows.length > LIMIT}
		<div class="border-t border-border/60 px-4 py-2">
			<Button variant="ghost" size="sm" onclick={() => (showAll = !showAll)}>
				{showAll ? `Show the first ${LIMIT}` : `Show all ${rows.length} cells`}
			</Button>
		</div>
	{/if}
{/if}
