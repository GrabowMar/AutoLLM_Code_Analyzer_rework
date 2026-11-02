<script lang="ts">
	/**
	 * DotCiPlot — dot-and-whisker figure (after experiments/study/PassRatePlot):
	 * one row per group, the estimate as a dot in chart slot 1, its 95%
	 * interval as a whisker with end caps, an optional sensitivity estimate as
	 * a hollow ring (legend shown when present), and an optional reference
	 * hairline (e.g. 0 for log strengths). Every value is printed beside its
	 * mark; groups without an interval show their n instead. Phones get a
	 * stacked list rather than a sideways scroll.
	 *
	 * Props: `rows` (PlotRow[]), `domain`, `ticks` (default from the domain),
	 * `tickKind` ('score' | 'share' | 'log') or `tickFormat`, `format` (estimate text),
	 * `labelHeader`, `estimateHeader`, `nHeader`, `reference` (value),
	 * `secondaryLabel` (legend text for hollow marks), `ranked` (rank column),
	 * `minCiNote` (shown instead of an interval).
	 */
	import { cn } from '$lib/utils';
	import { tableFrame } from '$lib/components/table';
	import { axisTicks, formatCi, plotPercent, tickText, type Domain } from '../../format';
	import type { PlotRow } from './types';

	interface Props {
		rows: PlotRow[];
		domain: Domain;
		ticks?: number[];
		tickKind?: 'score' | 'share' | 'log';
		tickFormat?: (v: number) => string;
		format: (v: number) => string;
		labelHeader?: string;
		estimateHeader?: string;
		nHeader?: string;
		reference?: number | null;
		secondaryLabel?: string;
		ranked?: boolean;
		minCiNote?: string;
	}

	let {
		rows,
		domain,
		ticks,
		tickKind = 'score',
		tickFormat,
		format,
		labelHeader = 'Group',
		estimateHeader = 'Estimate [95% CI]',
		nHeader = 'n',
		reference = null,
		secondaryLabel = '',
		ranked = false,
		minCiNote = 'An interval needs at least 3 apps',
	}: Props = $props();

	const tickValues = $derived(ticks ?? axisTicks(domain));
	const hasSecondary = $derived(rows.some((r) => r.secondary != null));
	const pct = (v: number) => `${plotPercent(v, domain)}%`;
	const ciOf = (row: PlotRow) => (row.ci && row.ci.low != null && row.ci.high != null ? { low: row.ci.low, high: row.ci.high } : null);

	function describe(row: PlotRow): string {
		if (row.value == null) return `${row.label}: no estimate`;
		const ci = ciOf(row);
		return `${row.label}: ${format(row.value)}${ci ? `, 95% CI ${format(ci.low)} to ${format(ci.high)}` : ', no interval'}${
			row.secondary != null ? `; ${secondaryLabel || 'sensitivity'} ${format(row.secondary)}` : ''
		}${row.n ? `; ${row.n}` : ''}`;
	}
</script>

{#snippet axis()}
	<span class="relative block h-4 font-mono text-[11px] normal-case tracking-normal text-muted-foreground" aria-hidden="true">
		{#each tickValues as t (t)}
			{@const p = plotPercent(t, domain)}
			<span class={cn('absolute top-0', p <= 0 ? '' : p >= 100 ? '-translate-x-full' : '-translate-x-1/2')} style="left: {p}%">{tickFormat ? tickFormat(t) : tickText(t, tickKind)}</span>
		{/each}
	</span>
{/snippet}

{#snippet marks(row: PlotRow)}
	{@const ci = ciOf(row)}
	<span class="relative block h-8" role="img" aria-label={describe(row)} title={describe(row)}>
		{#each tickValues as t (t)}
			<span class="absolute inset-y-0 w-px bg-border/70" style="left: {pct(t)}" aria-hidden="true"></span>
		{/each}
		{#if reference != null}
			<span class="absolute inset-y-0 w-px bg-foreground/35" style="left: {pct(reference)}" aria-hidden="true"></span>
		{/if}
		{#if ci}
			<span
				class="absolute top-1/2 h-px -translate-y-1/2 bg-foreground/60"
				style="left: {pct(ci.low)}; width: {Math.max(0.2, plotPercent(ci.high, domain) - plotPercent(ci.low, domain))}%"
				aria-hidden="true"
			></span>
			{#each [ci.low, ci.high] as end, i (i)}
				<span class="absolute top-1/2 h-2.5 w-px -translate-y-1/2 bg-foreground/60" style="left: {pct(end)}" aria-hidden="true"></span>
			{/each}
		{/if}
		{#if row.secondary != null}
			<span
				class="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-foreground/70 bg-card"
				style="left: {pct(row.secondary)}"
				aria-hidden="true"
			></span>
		{/if}
		{#if row.value != null}
			<span class="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-chart-1 ring-2 ring-card" style="left: {pct(row.value)}" aria-hidden="true"></span>
		{/if}
	</span>
{/snippet}

{#snippet estimate(row: PlotRow)}
	{@const ci = ciOf(row)}
	{#if row.value == null}
		<span class="text-muted-foreground">—</span>
	{:else}
		<span class="font-semibold text-foreground">{format(row.value)}</span>
		{#if ci}
			<span class="text-muted-foreground">{formatCi(ci, format)}</span>
		{:else}
			<span class="text-muted-foreground" title={minCiNote}>[no CI]</span>
		{/if}
		{#if row.secondary != null}
			<span class="block text-[11px] text-muted-foreground" title={secondaryLabel}>○ {format(row.secondary)}</span>
		{/if}
	{/if}
{/snippet}

{#if hasSecondary}
	<p class="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pb-1 pt-2.5 text-xs text-muted-foreground">
		<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-full bg-chart-1" aria-hidden="true"></span>Estimate (95% CI)</span>
		<span class="inline-flex items-center gap-1.5"><span class="size-2.5 rounded-full border-2 border-foreground/70 bg-card" aria-hidden="true"></span>{secondaryLabel || 'Sensitivity'}</span>
	</p>
{/if}

<!-- Phones: stacked rows. -->
<div class="sm:hidden">
	<div class="border-b border-border bg-muted/40 px-4 py-1.5">{@render axis()}</div>
	<ol class="divide-y divide-border/60">
		{#each rows as row (row.key)}
			<li class="px-4 pt-2">
				<div class="flex items-baseline gap-2 text-sm">
					{#if ranked}<span class="font-mono text-xs text-muted-foreground">{row.rank ?? '—'}</span>{/if}
					<span class="min-w-0 flex-1 break-words font-medium">{row.label}</span>
					<span class="shrink-0 font-mono text-xs">{@render estimate(row)}</span>
				</div>
				{#if row.sub || row.n}<p class="font-mono text-[11px] text-muted-foreground">{[row.sub, row.n].filter(Boolean).join(' · ')}</p>{/if}
				{@render marks(row)}
			</li>
		{/each}
	</ol>
</div>

<div class="table-frame max-sm:hidden" {@attach tableFrame}>
	<table class="min-w-[38rem]">
		<thead>
			<tr>
				{#if ranked}<th scope="col" class="w-8 text-right">#</th>{/if}
				<th scope="col" class="col-sticky">{labelHeader}</th>
				<th scope="col" class="w-[46%] min-w-60">
					{@render axis()}
					<span class="sr-only">Estimate with 95% interval</span>
				</th>
				<th scope="col" class="num">{estimateHeader}</th>
				<th scope="col" class="num">{nHeader}</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.key)}
				<tr>
					{#if ranked}<td class="text-right font-mono text-xs tabular-nums text-muted-foreground">{row.rank ?? '—'}</td>{/if}
					<th
						scope="row"
						class="col-sticky top-auto z-[1] min-w-40 max-w-64 bg-card text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground backdrop-blur-none"
					>
						<span class="line-clamp-2 break-words font-medium" title={row.label}>{row.label}</span>
						{#if row.sub}<span class="block font-mono text-xs text-muted-foreground">{row.sub}</span>{/if}
					</th>
					<td class="py-0">{@render marks(row)}</td>
					<td class="num text-xs">{@render estimate(row)}</td>
					<td class="num text-xs text-muted-foreground">{row.n ?? ''}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
