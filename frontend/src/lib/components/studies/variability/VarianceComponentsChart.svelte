<script lang="ts">
	/**
	 * VarianceComponentsChart — one 100% stacked bar per metric: the shares of
	 * the total variance attributed to condition, app, condition × app and
	 * repeat (run-to-run), method-of-moments estimates. A negative estimate is
	 * shown as 0 and starred. Shares are printed under each bar; the legend
	 * names the four categorical slots. Metrics without variance (ceiling) or
	 * without repeats say so instead of drawing an empty bar.
	 *
	 * Props:
	 * - `components`: payload `components` (by metric key).
	 * - `metrics`: the payload's metrics (labels, order); only measured ones are drawn.
	 * - `selected`: metric key to emphasise (the section's metric selector).
	 */
	import { cn } from '$lib/utils';
	import type { VarianceComponents, VariabilityMetric } from '$lib/api/variability';
	import { COMPONENT_LABEL, COMPONENT_ORDER, componentColor, formatRatio, formatShare } from './variability';

	interface Props {
		components: Record<string, VarianceComponents>;
		metrics: VariabilityMetric[];
		selected?: string;
	}

	let { components, metrics, selected }: Props = $props();

	const measured = $derived(metrics.filter((m) => m.n_runs > 0 && components[m.key]).map((m) => ({ metric: m, comp: components[m.key] })));
	const rows = $derived(measured.filter((r) => r.comp.estimable));
	/** Metrics without a cell of 2+ measured repeats: one line, not a column of empty bars. */
	const skipped = $derived(measured.filter((r) => !r.comp.estimable));
	const sources = $derived(COMPONENT_ORDER.filter((s) => rows.some((r) => r.comp.components.some((c) => c.source === s))));

	function describe(label: string, comp: VarianceComponents): string {
		if (!comp.estimable) return `${label}: not estimable (${comp.reason ?? 'no repeats'})`;
		if (comp.ceiling) return `${label}: no variance (every run scored the same)`;
		const parts = comp.components.map((c) => `${COMPONENT_LABEL[c.source]} ${formatShare(c.share)}${c.truncated ? ' (negative estimate set to 0)' : ''}`);
		return `${label}: ${parts.join(', ')}; ${comp.n_runs} runs`;
	}
</script>

{#if rows.length === 0}
	<p class="px-4 py-3 text-sm text-muted-foreground">No metric has repeats to decompose.</p>
{/if}
{#if rows.length > 0}
	<div class="space-y-3 p-4">
		<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Variance sources">
			{#each sources as source (source)}
				<li class="flex items-center gap-1.5">
					<span class="size-2.5 rounded-[2px]" style="background: {componentColor(source)}" aria-hidden="true"></span>
					<span class="text-muted-foreground">{COMPONENT_LABEL[source]}</span>
				</li>
			{/each}
		</ul>
		<ol class="space-y-2.5">
			{#each rows as { metric, comp } (metric.key)}
				<li class={cn('grid gap-x-4 gap-y-1 sm:grid-cols-[10rem_minmax(0,1fr)_6rem] sm:items-center', selected === metric.key && 'rounded-sm bg-primary/5 -mx-2 px-2 py-1')}>
					<span class="min-w-0 text-sm">
						<span class="block truncate font-medium" title={metric.source}>{metric.label}</span>
						<span class="block font-mono text-[11px] text-muted-foreground">n {comp.n_runs} runs · {comp.n_apps} apps</span>
					</span>
					<span class="min-w-0">
						{#if comp.ceiling}
							<span class="text-xs text-muted-foreground">No variance — every run scored the same (ceiling / floor)</span>
						{:else}
							<span class="flex h-3.5 w-full overflow-hidden rounded-sm bg-muted" role="img" aria-label={describe(metric.label, comp)} title={describe(metric.label, comp)}>
								{#each comp.components as c (c.source)}
									{#if (c.share ?? 0) > 0}
										<span class="h-full border-r border-card last:border-r-0" style="width: {(c.share ?? 0) * 100}%; background: {componentColor(c.source)}"></span>
									{/if}
								{/each}
							</span>
							<span class="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[11px] text-muted-foreground">
								{#each comp.components as c (c.source)}
									<span title={c.truncated ? 'Negative estimate, shown as 0' : `${c.df} df`}
										>{COMPONENT_LABEL[c.source].split(' (')[0]} {formatShare(c.share)}{c.truncated ? '*' : ''}</span
									>
								{/each}
							</span>
						{/if}
					</span>
					<span class="font-mono text-xs tabular-nums sm:text-right" title="Correlation of two repeats of one (condition, app): (app + condition × app) / (app + condition × app + repeat)">
						{#if comp.estimable && !comp.ceiling}
							<span class="text-muted-foreground">ICC</span> {formatRatio(comp.icc_within_condition)}
						{/if}
					</span>
				</li>
			{/each}
		</ol>
	</div>
{/if}
{#if skipped.length > 0}
	<p class="border-t border-border/60 px-4 py-2.5 text-xs text-muted-foreground">
		Not estimable: {skipped.map((r) => `${r.metric.label} (n ${r.metric.n_runs}; ${r.comp.reason ?? 'no repeats'})`).join(', ')}.
	</p>
{/if}
