<script lang="ts">
	/**
	 * Tool metrics (e.g. complexity, duplication) aggregated over succeeded
	 * executions: numeric metrics as avg/min/max, distributions as counts.
	 */
	import type { ReportToolMetrics } from '$lib/api/reports';
	import { fmtNum, humanize } from '../format';

	interface Props {
		metrics: Record<string, ReportToolMetrics>;
	}

	let { metrics }: Props = $props();

	const tools = $derived(Object.entries(metrics ?? {}));
</script>

{#if tools.length === 0}
	<p class="text-xs text-muted-foreground">No succeeded execution reported metrics.</p>
{:else}
	<div class="grid gap-5 lg:grid-cols-2">
		{#each tools as [tool, m] (tool)}
			{@const numeric = Object.entries(m.numeric ?? {})}
			{@const dists = Object.entries(m.distributions ?? {})}
			<section class="min-w-0 space-y-2">
				<h3 class="flex items-baseline gap-2 text-sm">
					<span class="font-mono font-medium">{tool}</span>
					<span class="text-xs text-muted-foreground">
						{m.results_with_metrics} succeeded result{m.results_with_metrics === 1 ? '' : 's'}
					</span>
				</h3>
				{#if numeric.length > 0}
					<div class="table-scroll-wrapper">
						<table class="text-xs">
							<thead>
								<tr>
									<th scope="col">Metric</th>
									<th scope="col" class="text-right">Avg</th>
									<th scope="col" class="text-right">Min</th>
									<th scope="col" class="text-right">Max</th>
								</tr>
							</thead>
							<tbody>
								{#each numeric as [key, stat] (key)}
									<tr>
										<td class="capitalize">{humanize(key)}</td>
										<td class="text-right font-mono tabular-nums">{fmtNum(stat.avg, 2)}</td>
										<td class="text-right font-mono tabular-nums text-muted-foreground"
											>{fmtNum(stat.min, 2)}</td
										>
										<td class="text-right font-mono tabular-nums text-muted-foreground"
											>{fmtNum(stat.max, 2)}</td
										>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
				{#each dists as [key, buckets] (key)}
					<div class="flex flex-wrap items-center gap-1.5 text-xs">
						<span class="text-muted-foreground capitalize">{humanize(key)}:</span>
						{#each Object.entries(buckets) as [bucket, count] (bucket)}
							<span class="mono-tag">{bucket} {fmtNum(count, 2)}</span>
						{/each}
					</div>
				{/each}
			</section>
		{/each}
	</div>
{/if}
