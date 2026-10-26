<script lang="ts" module>
	export interface BarSeriesDef {
		key: string;
		label: string;
		/** CSS color token (`var(--chart-n)` or a reserved severity hue). */
		color: string;
	}
</script>

<script lang="ts">
	/**
	 * GroupedBarChart — LayerChart `BarChart` with side-by-side bars per
	 * category (e.g. issue severities per group), an HTML legend with totals
	 * and an empty state. Values are counts (numbers); a missing category
	 * must be left out by the caller, never sent as 0.
	 *
	 * Props: `data` (rows with `x` and one numeric key per series), `x`,
	 * `series: BarSeriesDef[]`, `height` (default 240), `label`.
	 */
	import { BarChart } from 'layerchart';
	import ChartEmpty from './ChartEmpty.svelte';
	import ChartLegend from './ChartLegend.svelte';

	interface Props {
		data: Record<string, string | number>[];
		x: string;
		series: BarSeriesDef[];
		height?: number;
		label?: string;
	}

	let { data, x, series, height = 240, label = 'Grouped bar chart' }: Props = $props();

	const totals = $derived(
		series.map((s) => ({
			...s,
			total: data.reduce(
				(sum, row) => sum + (typeof row[s.key] === 'number' ? (row[s.key] as number) : 0),
				0
			)
		}))
	);
	const visible = $derived(totals.filter((s) => s.total > 0));
	const empty = $derived(data.length === 0 || visible.length === 0);
</script>

{#if empty}
	<ChartEmpty message="Nothing counted in any group." height={Math.min(height, 140)} />
{:else}
	<figure class="space-y-2" aria-label={label}>
		<div class="w-full" style="height: {height}px" role="img" aria-label={label}>
			<BarChart
				{data}
				{x}
				series={visible.map((s) => ({
					key: s.key,
					label: s.label,
					color: s.color
				}))}
				seriesLayout="group"
				bandPadding={0.25}
				groupPadding={0.08}
				padding={{ left: 32, bottom: 24, top: 8, right: 8 }}
				props={{
					grid: { class: 'stroke-[color:var(--border)]' },
					xAxis: { tickLength: 0 },
					yAxis: { tickLength: 0 }
				}}
			/>
		</div>
		<figcaption>
			<ChartLegend
				label="Series"
				items={totals.map((s) => ({
					label: s.label,
					color: s.color,
					shape: 'square' as const,
					value: s.total.toLocaleString()
				}))}
			/>
		</figcaption>
	</figure>
{/if}
