<script lang="ts">
	import { AreaChart } from 'layerchart';
	import type { TrendPoint } from '$lib/api/client';
	import { statusSeriesColors } from './theme';

	interface Props {
		points: TrendPoint[];
		height?: number;
	}

	let { points, height = 208 }: Props = $props();

	interface ChartPoint {
		date: Date;
		completed: number;
		failed: number;
	}

	const data = $derived<ChartPoint[]>(
		points.map((p) => ({
			date: new Date(p.date + 'T00:00:00'),
			completed: p.completed,
			failed: p.failed,
		}))
	);

	const series = [
		{ key: 'completed', label: 'Completed', color: statusSeriesColors.completed },
		{ key: 'failed', label: 'Failed', color: statusSeriesColors.failed },
	];

	const dateFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
</script>

<div class="w-full" style="height: {height}px">
	<AreaChart
		{data}
		x="date"
		{series}
		padding={{ left: 24, bottom: 20, top: 8, right: 8 }}
		props={{
			area: { fillOpacity: 0.12, line: { strokeWidth: 2 } },
			grid: { class: 'stroke-[color:var(--border)]' },
			xAxis: { format: (d: Date) => dateFormat.format(d), tickLength: 0 },
			yAxis: { tickLength: 0 },
			highlight: { points: { r: 4, strokeWidth: 2, stroke: 'var(--card)' } },
		}}
	/>
</div>

<!-- Legend: identity never rides on color alone -->
<div class="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
	{#each series as s (s.key)}
		<span class="inline-flex items-center gap-1.5">
			<span class="h-0.5 w-4 rounded-full" style="background: {s.color}"></span>
			{s.label}
		</span>
	{/each}
</div>
