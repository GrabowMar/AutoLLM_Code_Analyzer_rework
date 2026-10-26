<script lang="ts">
	/**
	 * Daily analysis runs by outcome (stacked bars): completed, partial (a
	 * required tool did not succeed) and failed. Status hues are reserved
	 * tokens; the legend carries the totals so identity never rides on color.
	 */
	import { BarChart } from 'layerchart';
	import type { TrendPoint } from '$lib/api/statistics';

	interface Props {
		points: TrendPoint[];
		height?: number;
	}

	let { points, height = 208 }: Props = $props();

	interface Row {
		date: string;
		completed: number;
		partial: number;
		failed: number;
		other: number;
	}

	const rows = $derived<Row[]>(
		points.map((p) => {
			const partial = p.partial ?? 0;
			const other = Math.max(0, p.total - p.completed - partial - p.failed);
			return { date: p.date, completed: p.completed, partial, failed: p.failed, other };
		})
	);

	const series = [
		{ key: 'completed', label: 'Completed', color: 'var(--success)' },
		{ key: 'partial', label: 'Partial', color: 'var(--warning)' },
		{ key: 'failed', label: 'Failed', color: 'var(--destructive)' },
		{
			key: 'other',
			label: 'Queued / running / cancelled',
			color: 'color-mix(in oklab, var(--muted-foreground) 45%, transparent)'
		}
	] as const;

	const totals = $derived(
		Object.fromEntries(
			series.map((s) => [s.key, rows.reduce((sum, r) => sum + r[s.key], 0)])
		) as Record<(typeof series)[number]['key'], number>
	);
	const visibleSeries = $derived(series.filter((s) => s.key !== 'other' || totals.other > 0));

	const dayFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
	function label(iso: string): string {
		const d = new Date(`${iso}T00:00:00`);
		return Number.isNaN(d.getTime()) ? iso : dayFormat.format(d);
	}

	// At most ~8 x labels whatever the window.
	const ticks = $derived.by(() => {
		const step = Math.max(1, Math.ceil(rows.length / 8));
		const last = rows.length - 1;
		// The last day always gets a label unless it would crowd the previous tick.
		return rows
			.filter((_, i) => (i % step === 0 && last - i >= step / 2) || i === last)
			.map((r) => r.date);
	});
</script>

<div class="w-full" style="height: {height}px">
	<BarChart
		data={rows}
		x="date"
		series={visibleSeries.map((s) => ({ ...s }))}
		seriesLayout="stack"
		bandPadding={0.25}
		padding={{ left: 28, bottom: 20, top: 8, right: 8 }}
		props={{
			grid: { class: 'stroke-[color:var(--border)]' },
			// Surface-coloured outline = the 2px gap between stacked segments
			// (stackPadding would give zero-height segments a negative height).
			bars: { stroke: 'var(--card)', strokeWidth: 2 },
			xAxis: { format: (d: string) => label(d), ticks, tickLength: 0 },
			yAxis: { tickLength: 0, format: (v: number) => (Number.isInteger(v) ? String(v) : '') }
		}}
	/>
</div>

<ul class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
	{#each visibleSeries as s (s.key)}
		<li class="inline-flex items-center gap-1.5">
			<span class="h-2.5 w-2.5 rounded-sm" style="background: {s.color}" aria-hidden="true"></span>
			{s.label}
			<span class="font-medium tabular-nums text-foreground">{totals[s.key]}</span>
		</li>
	{/each}
</ul>
