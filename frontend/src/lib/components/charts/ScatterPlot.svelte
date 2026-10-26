<script lang="ts" module>
	export interface ScatterPoint {
		label: string;
		x: number | null;
		y: number | null;
	}
</script>

<script lang="ts">
	/**
	 * ScatterPlot — labelled points (e.g. one per group: cost vs outcome).
	 * Points with a missing coordinate are listed as "not plotted (NA)"
	 * instead of being drawn at 0.
	 *
	 * Props: `points`, `xLabel`, `yLabel`, `formatX`, `formatY`, `height`
	 * (default 240), `label`.
	 */
	import { seriesColor } from './theme';
	import { formatTick, linearScale, niceTicks } from './scale';
	import ChartEmpty from './ChartEmpty.svelte';
	import ChartLegend from './ChartLegend.svelte';

	interface Props {
		points: ScatterPoint[];
		xLabel: string;
		yLabel: string;
		formatX?: (v: number) => string;
		formatY?: (v: number) => string;
		/** Axis tick labels (default: compact numbers). */
		tickX?: (v: number) => string;
		tickY?: (v: number) => string;
		height?: number;
		label?: string;
	}

	let {
		points,
		xLabel,
		yLabel,
		formatX = (v: number) => formatTick(v),
		formatY = (v: number) => formatTick(v),
		tickX = (v: number) => formatTick(v),
		tickY = (v: number) => formatTick(v),
		height = 240,
		label = 'Scatter plot'
	}: Props = $props();

	const M = { top: 12, right: 16, bottom: 34, left: 48 };
	let width = $state(0);

	function num(v: number | null | undefined): v is number {
		return typeof v === 'number' && Number.isFinite(v);
	}

	const plotted = $derived(
		points
			.map((p, i) => ({ ...p, i }))
			.filter((p): p is typeof p & { x: number; y: number } => num(p.x) && num(p.y))
	);
	const missing = $derived(points.filter((p) => !num(p.x) || !num(p.y)));
	const xs = $derived(
		niceTicks(0, Math.max(...plotted.map((p) => p.x), 0), Math.max(2, Math.floor(width / 100)))
	);
	const ys = $derived(niceTicks(0, Math.max(...plotted.map((p) => p.y), 0), 4));
	const x = $derived(
		linearScale([xs.min, xs.max], [M.left, Math.max(M.left + 1, width - M.right)])
	);
	const y = $derived(linearScale([ys.min, ys.max], [height - M.bottom, M.top]));
	const colorFor = (i: number) => (points.length <= 5 ? seriesColor(i + 1) : 'var(--chart-2)');
</script>

{#if plotted.length === 0}
	<ChartEmpty
		message="No point has both coordinates (missing data is not plotted as 0)."
		height={120}
	/>
{:else}
	<figure class="space-y-2" aria-label={label}>
		<div class="w-full" bind:clientWidth={width}>
			{#if width > 0}
				<svg {width} {height} role="img" aria-label={label} class="block overflow-visible">
					<title>{label}</title>
					<desc>
						{plotted
							.map((p) => `${p.label}: ${xLabel} ${formatX(p.x)}, ${yLabel} ${formatY(p.y)}`)
							.join('. ')}
					</desc>
					{#each ys.ticks as t (t)}
						<line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} stroke="var(--border)" />
						<text
							x={M.left - 6}
							y={y(t) + 4}
							text-anchor="end"
							class="fill-muted-foreground text-[11px] tabular-nums"
						>
							{tickY(t)}
						</text>
					{/each}
					{#each xs.ticks as t (t)}
						<text
							x={x(t)}
							y={height - M.bottom + 16}
							text-anchor="middle"
							class="fill-muted-foreground text-[11px] tabular-nums">{tickX(t)}</text
						>
					{/each}
					<text
						x={(M.left + width - M.right) / 2}
						y={height - 2}
						text-anchor="middle"
						class="fill-muted-foreground text-[11px]"
					>
						{xLabel}
					</text>
					<text
						x={12}
						y={(M.top + height - M.bottom) / 2}
						text-anchor="middle"
						transform="rotate(-90 12 {(M.top + height - M.bottom) / 2})"
						class="fill-muted-foreground text-[11px]">{yLabel}</text
					>
					{#each plotted as p (p.i)}
						<g>
							<title
								>{p.label}: {xLabel}
								{formatX(p.x)}, {yLabel}
								{formatY(p.y)}</title
							>
							<circle
								cx={x(p.x)}
								cy={y(p.y)}
								r="6"
								fill={colorFor(p.i)}
								stroke="var(--card)"
								stroke-width="1.5"
							/>
						</g>
					{/each}
				</svg>
			{:else}
				<div style="height: {height}px"></div>
			{/if}
		</div>
		<figcaption class="space-y-1">
			<ChartLegend
				label="Points"
				items={plotted.map((p) => ({
					label: p.label,
					color: colorFor(p.i),
					value: `${formatX(p.x)} · ${formatY(p.y)}`
				}))}
			/>
			{#if missing.length > 0}
				<p class="text-[11px] text-muted-foreground">
					Not plotted (missing value, NA): {missing.map((p) => p.label).join(', ')}
				</p>
			{/if}
		</figcaption>
	</figure>
{/if}
