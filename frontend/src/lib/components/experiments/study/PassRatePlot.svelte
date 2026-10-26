<script lang="ts">
	/**
	 * PassRatePlot — dot-and-whisker figure: every condition's mean app-level
	 * pass rate with its cluster-bootstrap 95% interval (shown from
	 * MIN_APPS_FOR_CI apps), ranked, on one 0–100 % axis. A single series, so
	 * one mark colour (chart slot 1) and no legend; the baseline is a hollow
	 * diamond and a hairline through every row at its mean. Every value is
	 * printed beside its mark, so the native tooltip only repeats them.
	 *
	 * Props:
	 * - `results`: `GET /results/`.
	 * - `limit`: show only the first N ranks (the Abstract), with a "+N more" row
	 *   linking to `moreHref`.
	 * Phones get a stacked list (label and estimate over a full-width plot row).
	 */
	import { cn } from '$lib/utils';
	import { tableFrame } from '$lib/components/table';
	import type { ExperimentResults, LeaderboardRow } from '$lib/api/types/experiments';
	import { MIN_APPS_FOR_CI, formatPercent } from '../format';

	interface Props {
		results: ExperimentResults;
		limit?: number;
		moreHref?: string;
	}

	let { results, limit, moreHref }: Props = $props();

	const TICKS = [0, 0.25, 0.5, 0.75, 1];

	const ranked = $derived([...results.leaderboard].sort((a, b) => a.rank - b.rank));
	const rows = $derived(limit ? ranked.slice(0, limit) : ranked);
	const hidden = $derived(ranked.length - rows.length);
	const baselineId = $derived(results.baseline_condition_id);
	const baselineMean = $derived(ranked.find((r) => r.condition_id === baselineId)?.pass_rate_mean ?? null);

	const pct = (v: number) => `${Math.min(100, Math.max(0, v * 100))}%`;
	const ciOf = (row: LeaderboardRow) => (row.n_apps >= MIN_APPS_FOR_CI ? row.pass_rate_ci95 : null);

	function describe(row: LeaderboardRow): string {
		if (row.pass_rate_mean == null) return `${row.label}: not graded yet`;
		const ci = ciOf(row);
		return `${row.label}: ${formatPercent(row.pass_rate_mean, 1)}${
			ci ? `, 95% CI ${formatPercent(ci[0], 1)} to ${formatPercent(ci[1], 1)}` : ', no interval'
		}; ${row.k_passed}/${row.n_graded} cells passed over ${row.n_apps} app${row.n_apps === 1 ? '' : 's'}`;
	}
</script>

{#snippet axis()}
	<span class="relative block h-4 font-mono text-[11px] normal-case tracking-normal text-muted-foreground" aria-hidden="true">
		{#each TICKS as t (t)}
			<span class={cn('absolute top-0', t === 0 ? '' : t === 1 ? '-translate-x-full' : '-translate-x-1/2')} style="left: {pct(t)}">{t * 100}%</span>
		{/each}
	</span>
{/snippet}

{#snippet marks(row: LeaderboardRow)}
	{@const isBaseline = row.condition_id === baselineId}
	{@const ci = ciOf(row)}
	{@const mean = row.pass_rate_mean}
	<span class="relative block h-8" role="img" aria-label={describe(row)} title={describe(row)}>
		{#each TICKS as t (t)}
			<span class="absolute inset-y-0 w-px bg-border/70" style="left: {pct(t)}" aria-hidden="true"></span>
		{/each}
		{#if baselineMean != null}
			<span class="absolute inset-y-0 w-px bg-foreground/35" style="left: {pct(baselineMean)}" aria-hidden="true"></span>
		{/if}
		{#if ci}
			<span
				class="absolute top-1/2 h-px -translate-y-1/2 bg-foreground/60"
				style="left: {pct(ci[0])}; width: {pct(Math.max(0.002, ci[1] - ci[0]))}"
				aria-hidden="true"
			></span>
			{#each [ci[0], ci[1]] as end, i (i)}
				<span class="absolute top-1/2 h-2.5 w-px -translate-y-1/2 bg-foreground/60" style="left: {pct(end)}" aria-hidden="true"></span>
			{/each}
		{/if}
		{#if mean != null}
			<span
				class={cn(
					'absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 ring-2 ring-card',
					isBaseline ? 'rotate-45 border-2 border-foreground bg-card' : 'rounded-full bg-chart-1'
				)}
				style="left: {pct(mean)}"
				aria-hidden="true"
			></span>
		{/if}
	</span>
{/snippet}

{#snippet estimate(row: LeaderboardRow)}
	{@const ci = ciOf(row)}
	{#if row.pass_rate_mean == null}
		<span class="text-muted-foreground">not graded</span>
	{:else}
		<span class="font-semibold text-foreground">{formatPercent(row.pass_rate_mean, 1)}</span>
		{#if ci}
			<span class="text-muted-foreground">[{formatPercent(ci[0])}, {formatPercent(ci[1])}]</span>
		{:else}
			<span class="text-muted-foreground" title="An interval needs at least {MIN_APPS_FOR_CI} apps">[n={row.n_apps}]</span>
		{/if}
	{/if}
{/snippet}

<!-- Phones: one stacked row per condition (label and estimate, then the plot at full width). -->
<div class="sm:hidden">
	<div class="border-b border-border bg-muted/40 px-4 py-1.5">{@render axis()}</div>
	<ol class="divide-y divide-border/60">
		{#each rows as row (row.condition_id)}
			<li class="px-4 pt-2">
				<div class="flex items-baseline gap-2 text-sm">
					<span class="font-mono text-xs text-muted-foreground">{row.rank}</span>
					<span class="min-w-0 flex-1 break-words font-medium">{row.label}</span>
					<span class="shrink-0 font-mono text-xs">{@render estimate(row)}</span>
				</div>
				{#if row.condition_id === baselineId}<p class="pl-4 font-mono text-xs text-muted-foreground">baseline</p>{/if}
				{@render marks(row)}
			</li>
		{/each}
	</ol>
	{#if hidden > 0}
		<p class="border-t border-border/60 px-4 py-2 text-xs text-muted-foreground">
			+{hidden} more condition{hidden === 1 ? '' : 's'}{#if moreHref} — <a class="settings-link" href={moreHref}>see Table 1</a>{/if}
		</p>
	{/if}
</div>

<div class="table-frame max-sm:hidden" {@attach tableFrame}>
	<table class="min-w-[38rem]">
		<thead>
			<tr>
				<th scope="col" class="w-8 text-right">#</th>
				<th scope="col" class="col-sticky">Condition</th>
				<th scope="col" class="w-[48%] min-w-60">
					{@render axis()}
					<span class="sr-only">Mean pass rate with 95% interval</span>
				</th>
				<th scope="col" class="num" title="Mean of app-level pass rates [cluster-bootstrap 95% CI]">Mean [95% CI]</th>
				<th scope="col" class="num" title="Cells that passed every check / graded cells">k/n</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.condition_id)}
				<tr>
					<td class="text-right font-mono text-xs tabular-nums text-muted-foreground">{row.rank}</td>
					<th
						scope="row"
						class="col-sticky top-auto z-[1] min-w-44 max-w-64 bg-card text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground backdrop-blur-none"
					>
						<span class="line-clamp-2 break-words font-medium" title={row.label}>{row.label}</span>
						{#if row.condition_id === baselineId}<span class="block font-mono text-xs text-muted-foreground">baseline</span>{/if}
					</th>
					<td class="py-0">{@render marks(row)}</td>
					<td class="num text-xs">{@render estimate(row)}</td>
					<td class="num text-xs text-muted-foreground">{row.k_passed}/{row.n_graded}</td>
				</tr>
			{/each}
			{#if hidden > 0}
				<tr>
					<td></td>
					<td colspan="4" class="text-xs text-muted-foreground">
						+{hidden} more condition{hidden === 1 ? '' : 's'}{#if moreHref} — <a class="settings-link" href={moreHref}>see Table 1</a>{/if}
					</td>
				</tr>
			{/if}
		</tbody>
	</table>
</div>
