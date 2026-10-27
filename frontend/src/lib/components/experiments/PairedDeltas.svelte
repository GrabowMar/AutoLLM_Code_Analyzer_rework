<script lang="ts">
	/**
	 * PairedDeltas — each condition against the baseline as a forest plot:
	 * the mean app-level Δ (square) with its paired cluster-bootstrap 95% CI
	 * (whiskers) around a zero line, wins / losses / ties over apps, n and the
	 * p adjusted as the frozen analysis plan says, else Holm (hidden while the experiment is interim). Ink marks only:
	 * a filled square's interval excludes 0, a hollow one's includes it — the
	 * sign is read from the axis, never from a red/green hue. Renders the plot
	 * table only; the page's StudyFigure supplies the title and caption.
	 *
	 * Props:
	 * - `results`: `GET /results/`.
	 * - `labelOf(conditionId)`: display label.
	 */
	import { cn } from '$lib/utils';
	import { tableFrame } from '$lib/components/table';
	import type { ExperimentResults, PairedRow } from '$lib/api/types/experiments';
	import { CORRECTION_LABEL, MIN_APPS_FOR_CI, formatDeltaPp, formatP } from './format';

	interface Props {
		results: ExperimentResults;
		labelOf: (conditionId: number) => string;
	}

	let { results, labelOf }: Props = $props();

	const baselineId = $derived(results.paired.baseline_condition_id);
	const rows = $derived(
		[...results.paired.rows]
			.filter((r) => r.condition_id !== baselineId)
			.sort((a, b) => (b.mean_delta ?? -Infinity) - (a.mean_delta ?? -Infinity))
	);
	// Symmetric axis around 0, at least ±10 pp, rounded up to 10 pp.
	const extent = $derived.by(() => {
		let max = 0.1;
		for (const r of rows) {
			for (const v of [r.mean_delta, r.ci95?.[0], r.ci95?.[1]]) if (v != null) max = Math.max(max, Math.abs(v));
		}
		return Math.min(1, Math.ceil(max * 10) / 10);
	});
	const ticks = $derived([-extent, -extent / 2, 0, extent / 2, extent]);
	const pos = (v: number) => `${((Math.max(-extent, Math.min(extent, v)) + extent) / (2 * extent)) * 100}%`;
	const ciOf = (row: PairedRow) => (row.n_pairs >= MIN_APPS_FOR_CI ? row.ci95 : null);
	const excludesZero = (ci: [number, number] | null) => !!ci && (ci[0] > 0 || ci[1] < 0);

	function describe(row: PairedRow): string {
		const ci = ciOf(row);
		if (row.mean_delta == null) return `${labelOf(row.condition_id)}: no paired apps`;
		return `${labelOf(row.condition_id)}: Δ ${formatDeltaPp(row.mean_delta)}${
			ci ? `, 95% CI ${formatDeltaPp(ci[0])} to ${formatDeltaPp(ci[1])}` : ', no interval'
		}; ${row.wins} wins, ${row.losses} losses, ${row.ties} ties over ${row.n_pairs} apps`;
	}
</script>

{#snippet axis()}
	<span class="relative block h-4 font-mono text-[11px] normal-case tracking-normal text-muted-foreground" aria-hidden="true">
		{#each ticks as t, i (i)}
			<span
				class={cn('absolute top-0 whitespace-nowrap', i === 0 ? '' : i === ticks.length - 1 ? '-translate-x-full' : '-translate-x-1/2')}
				style="left: {pos(t)}"
			>{t === 0 ? '0' : formatDeltaPp(t, 0).replace(' pp', '')}</span>
		{/each}
	</span>
{/snippet}

{#snippet marks(row: PairedRow)}
	{@const d = row.mean_delta}
	{@const ci = ciOf(row)}
	<span class="relative block h-8" role="img" aria-label={describe(row)} title={describe(row)}>
		{#each ticks as t, i (i)}
			<span class={cn('absolute inset-y-0 w-px', t === 0 ? 'bg-foreground/50' : 'bg-border/70')} style="left: {pos(t)}" aria-hidden="true"></span>
		{/each}
		{#if ci}
			<span
				class="absolute top-1/2 h-px -translate-y-1/2 bg-foreground/70"
				style="left: {pos(ci[0])}; width: calc({pos(ci[1])} - {pos(ci[0])})"
				aria-hidden="true"
			></span>
			{#each [ci[0], ci[1]] as end, i (i)}
				<span class="absolute top-1/2 h-2.5 w-px -translate-y-1/2 bg-foreground/70" style="left: {pos(end)}" aria-hidden="true"></span>
			{/each}
		{/if}
		{#if d != null}
			<span
				class={cn(
					'absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 border-2 border-foreground ring-2 ring-card',
					excludesZero(ci) ? 'bg-foreground' : 'bg-card'
				)}
				style="left: {pos(d)}"
				aria-hidden="true"
			></span>
		{/if}
	</span>
{/snippet}

{#snippet pValue(row: PairedRow)}
	{#if results.interim}
		<span class="text-muted-foreground" title="Hidden while conditions are at unequal progress">—</span>
	{:else if row.p_adjusted == null}
		<span class="text-muted-foreground">—</span>
	{:else}
		<span
			class={row.significant ? 'font-semibold text-foreground' : ''}
			title="{results.paired.status} · {CORRECTION_LABEL[results.paired.correction] ?? results.paired.correction}, α = {results.paired.alpha}, {results.paired.alternative}"
			>{formatP(row.p_adjusted)}</span
		>
	{/if}
{/snippet}

{#if rows.length === 0}
	<p class="px-4 py-3 text-sm text-muted-foreground">
		{results.leaderboard.length <= 1
			? 'Only one condition — nothing to compare against the baseline.'
			: baselineId == null
				? 'No baseline is set, so there is nothing to pair against.'
				: 'No condition has apps graded alongside the baseline yet.'}
	</p>
{:else}
	<!-- Phones: one stacked row per condition (label and Δ, the plot, then the counts). -->
	<div class="sm:hidden">
		<div class="border-b border-border bg-muted/40 px-4 py-1.5">{@render axis()}</div>
		<ol class="divide-y divide-border/60">
			{#each rows as row (row.condition_id)}
				{@const ci = ciOf(row)}
				<li class="px-4 py-2">
					<div class="flex items-baseline gap-2 text-sm">
						<span class="min-w-0 flex-1 break-words font-medium">{labelOf(row.condition_id)}</span>
						<span class="shrink-0 font-mono text-xs font-semibold">{row.mean_delta != null ? formatDeltaPp(row.mean_delta) : '—'}</span>
					</div>
					{@render marks(row)}
					<p class="flex flex-wrap gap-x-3 font-mono text-xs text-muted-foreground">
						{#if ci}<span>[{formatDeltaPp(ci[0])}, {formatDeltaPp(ci[1])}]</span>{/if}
						<span>W/L/T {row.wins}/{row.losses}/{row.ties}</span>
						<span>n {row.n_pairs}</span>
						<span>p {@render pValue(row)}</span>
					</p>
				</li>
			{/each}
		</ol>
	</div>

	<div class="table-frame max-sm:hidden" {@attach tableFrame}>
		<table class="min-w-[44rem]">
			<thead>
				<tr>
					<th scope="col" class="col-sticky">Condition</th>
					<th scope="col" class="w-[42%] min-w-60">
						{@render axis()}
						<span class="sr-only">Difference from the baseline, percentage points</span>
					</th>
					<th scope="col" class="num">Δ [95% CI]</th>
					<th scope="col" class="num" title="Apps where the condition beat / lost to / tied the baseline">W/L/T</th>
					<th scope="col" class="num" title="Apps with a mean for both conditions">n</th>
					<th
						scope="col"
						class="num"
						title="Wilcoxon signed-rank ({results.paired.alternative}), {CORRECTION_LABEL[results.paired.correction] ??
							results.paired.correction} across conditions; {results.paired.status}"
						>p ({CORRECTION_LABEL[results.paired.correction] ?? results.paired.correction})</th
					>
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.condition_id)}
					{@const d = row.mean_delta}
					{@const ci = ciOf(row)}
					<tr>
						<th
							scope="row"
							class="col-sticky top-auto z-[1] min-w-44 max-w-64 bg-card text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground backdrop-blur-none"
						>
							<span class="line-clamp-2 break-words font-medium" title={labelOf(row.condition_id)}>{labelOf(row.condition_id)}</span>
						</th>
						<td class="py-0">{@render marks(row)}</td>
						<td class="num text-xs">
							{#if d != null}
								<span class="font-semibold text-foreground">{formatDeltaPp(d)}</span>
								{#if ci}<span class="block text-muted-foreground">[{formatDeltaPp(ci[0])}, {formatDeltaPp(ci[1])}]</span>{/if}
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						</td>
						<td class="num text-xs">{row.wins}/{row.losses}/{row.ties}</td>
						<td class="num text-xs">{row.n_pairs}</td>
						<td class="num text-xs">{@render pValue(row)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
