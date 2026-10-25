<script lang="ts">
	/**
	 * PairedDeltas — each condition against the baseline as a forest plot:
	 * mean app-level Δ with its paired cluster-bootstrap 95% CI around a zero
	 * line, wins / losses / ties over apps and the Holm-adjusted p (hidden
	 * while the experiment is interim).
	 *
	 * Props:
	 * - `results`: `GET /results/`.
	 * - `labelOf(conditionId)`: display label.
	 */
	import { cn } from '$lib/utils';
	import type { ExperimentResults } from '$lib/api/types/experiments';
	import { formatDeltaPp, formatP } from './format';

	interface Props {
		results: ExperimentResults;
		labelOf: (conditionId: number) => string;
	}

	let { results, labelOf }: Props = $props();

	const rows = $derived(
		[...results.paired.rows].sort((a, b) => (b.mean_delta ?? -Infinity) - (a.mean_delta ?? -Infinity))
	);
	// Symmetric axis around 0, at least ±10 pp, rounded up to 10 pp.
	const extent = $derived.by(() => {
		let max = 0.1;
		for (const r of rows) {
			for (const v of [r.mean_delta, r.ci95?.[0], r.ci95?.[1]]) if (v != null) max = Math.max(max, Math.abs(v));
		}
		return Math.min(1, Math.ceil(max * 10) / 10);
	});
	const pos = (v: number) => ((v + extent) / (2 * extent)) * 100;
</script>

<div class="space-y-2">
	<div class="overflow-x-auto rounded-md border border-border">
		<table class="w-full min-w-[40rem] text-xs">
			<thead>
				<tr>
					<th scope="col">Condition vs baseline</th>
					<th scope="col" class="w-[40%]">
						<span class="flex justify-between font-mono font-normal normal-case tracking-normal">
							<span>{formatDeltaPp(-extent, 0)}</span><span>0</span><span>{formatDeltaPp(extent, 0)}</span>
						</span>
					</th>
					<th scope="col" class="text-right">Δ</th>
					<th scope="col" class="text-right" title="Apps where the condition beat / lost to / tied the baseline">W/L/T</th>
					<th scope="col" class="text-right" title="Apps with a mean for both conditions">n</th>
					<th scope="col" class="text-right" title="Wilcoxon signed-rank, Holm-adjusted across conditions">p (Holm)</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.condition_id)}
					{@const d = row.mean_delta}
					<tr>
						<td class="max-w-64"><span class="block truncate" title={labelOf(row.condition_id)}>{labelOf(row.condition_id)}</span></td>
						<td>
							<span
								class="relative block h-4"
								role="img"
								aria-label={d != null
									? `Δ ${formatDeltaPp(d)}${row.ci95 ? `, 95% CI ${formatDeltaPp(row.ci95[0])} to ${formatDeltaPp(row.ci95[1])}` : ''}`
									: 'No paired apps'}
							>
								<span class="absolute inset-y-0 left-1/2 w-px bg-border"></span>
								{#if row.ci95}
									<span
										class="absolute top-1/2 h-0.5 -translate-y-1/2 bg-foreground/40"
										style="left: {pos(row.ci95[0])}%; width: {Math.max(0.5, pos(row.ci95[1]) - pos(row.ci95[0]))}%"
									></span>
								{/if}
								{#if d != null}
									<span
										class={cn(
											'absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card',
											d > 0 ? 'bg-success' : d < 0 ? 'bg-destructive' : 'bg-muted-foreground'
										)}
										style="left: {pos(d)}%"
									></span>
								{/if}
							</span>
						</td>
						<td class="text-right font-mono tabular-nums">{formatDeltaPp(d)}</td>
						<td class="text-right font-mono tabular-nums">{row.wins}/{row.losses}/{row.ties}</td>
						<td class="text-right font-mono tabular-nums">{row.n_pairs}</td>
						<td class="text-right font-mono tabular-nums">
							{#if results.interim}
								<span class="text-muted-foreground" title="Hidden while conditions are at unequal progress">—</span>
							{:else}
								<span class={row.p_holm != null && row.p_holm < 0.05 ? 'font-semibold text-foreground' : ''}>{formatP(row.p_holm)}</span>
							{/if}
						</td>
					</tr>
				{:else}
					<tr><td colspan="6" class="text-muted-foreground">No other condition to compare.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
	<p class="text-[11px] text-muted-foreground">
		Paired by app (repeats averaged per app); n = number of apps. Whiskers: paired cluster-bootstrap 95% interval.
	</p>
</div>
