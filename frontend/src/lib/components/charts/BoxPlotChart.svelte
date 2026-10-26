<script lang="ts" module>
	/** One group's distribution (structurally the report's `BoxGroup`). */
	export interface BoxPlotGroup {
		group: string;
		n: number;
		n_missing?: number;
		median: number | null;
		q1: number | null;
		q3: number | null;
		mean?: number | null;
		sd?: number | null;
		min?: number | null;
		max?: number | null;
		whisker_low: number | null;
		whisker_high: number | null;
		outliers?: number[];
		values?: number[];
	}
</script>

<script lang="ts">
	/**
	 * BoxPlotChart — horizontal box plots, one row per group, drawn in SVG
	 * from precomputed statistics (median, IQR box, Tukey whiskers, mean ◇,
	 * outliers ○ and every observation as a jittered dot when n is small).
	 *
	 * A group without data (median null) shows "no data" — never a box at 0.
	 *
	 * Props:
	 * - `groups: BoxPlotGroup[]`; `unit` (axis caption); `higherIsBetter`
	 *   (adds a direction hint); `domain` (fixed axis, e.g. [0, 1]);
	 *   `format(n)` for values; `showPoints` (default true, ≤ 300 points).
	 * - `label`: accessible name of the figure.
	 */
	import { seriesColor } from './theme';
	import { formatTick, jitter, linearScale, niceTicks } from './scale';
	import ChartEmpty from './ChartEmpty.svelte';
	import ChartLegend from './ChartLegend.svelte';

	interface Props {
		groups: BoxPlotGroup[];
		unit?: string | null;
		higherIsBetter?: boolean | null;
		domain?: [number, number];
		format?: (value: number) => string;
		showPoints?: boolean;
		label?: string;
	}

	let {
		groups,
		unit = null,
		higherIsBetter = null,
		domain,
		format = (v: number) => formatTick(v),
		showPoints = true,
		label = 'Distribution by group'
	}: Props = $props();

	const ROW = 40;
	const AXIS = 26;
	const PAD = 10;

	let width = $state(0);
	let hovered = $state<number | null>(null);

	function num(v: number | null | undefined): v is number {
		return typeof v === 'number' && Number.isFinite(v);
	}

	const observed = $derived(
		groups.flatMap((g) =>
			[
				g.whisker_low,
				g.whisker_high,
				g.min,
				g.max,
				g.q1,
				g.q3,
				g.median,
				g.mean,
				...(g.outliers ?? []),
				...(g.values ?? [])
			].filter(num)
		)
	);

	const scaleInfo = $derived.by(() => {
		if (domain) return niceTicks(domain[0], domain[1], Math.max(2, Math.floor(width / 90)));
		if (observed.length === 0) return niceTicks(0, 1);
		const lo = Math.min(...observed);
		const hi = Math.max(...observed);
		// Densities and counts read against zero.
		return niceTicks(lo >= 0 ? 0 : lo, hi, Math.max(2, Math.floor(width / 90)));
	});

	const x = $derived(
		linearScale([scaleInfo.min, scaleInfo.max], [PAD, Math.max(PAD + 1, width - PAD)])
	);
	const height = $derived(groups.length * ROW + AXIS);
	const colorFor = (i: number) => (groups.length <= 5 ? seriesColor(i + 1) : 'var(--chart-2)');
	const pointCount = $derived(groups.reduce((sum, g) => sum + (g.values?.length ?? 0), 0));
	const drawPoints = $derived(showPoints && pointCount <= 300);

	function summary(g: BoxPlotGroup): string {
		if (!num(g.median)) {
			return `${g.group}: no data (n = ${g.n}${g.n_missing ? `, ${g.n_missing} missing` : ''})`;
		}
		const parts = [
			`n = ${g.n}${g.n_missing ? ` (+${g.n_missing} missing)` : ''}`,
			`median ${format(g.median)}`
		];
		if (num(g.q1) && num(g.q3)) parts.push(`IQR ${format(g.q1)}–${format(g.q3)}`);
		if (num(g.mean)) parts.push(`mean ${format(g.mean)}`);
		if (num(g.min) && num(g.max)) parts.push(`range ${format(g.min)}–${format(g.max)}`);
		return `${g.group}: ${parts.join(' · ')}`;
	}

	const tooltip = $derived.by(() => {
		if (hovered === null) return null;
		const g = groups[hovered];
		if (!g) return null;
		const anchor = num(g.median) ? x(g.median) : width / 2;
		return {
			text: summary(g),
			left: Math.min(Math.max(anchor, 120), Math.max(120, width - 120)),
			top: hovered * ROW
		};
	});

	const direction = $derived(
		higherIsBetter === true ? 'Higher is better' : higherIsBetter === false ? 'Lower is better' : ''
	);
</script>

{#if groups.length === 0 || observed.length === 0}
	<ChartEmpty message="No measured values to plot (every group is missing data)." height={120} />
{:else}
	<figure class="space-y-2" aria-label={label}>
		<div class="grid grid-cols-[minmax(5.5rem,11rem)_minmax(0,1fr)_auto] items-start gap-x-3">
			<!-- Group labels (direct labels: identity never rides on color alone) -->
			<div aria-hidden="true">
				{#each groups as g, i (g.group)}
					<div class="flex items-center gap-1.5 text-xs" style="height: {ROW}px">
						<span class="h-2 w-2 shrink-0 rounded-full" style="background: {colorFor(i)}"></span>
						<span class="truncate" title={g.group}>{g.group}</span>
					</div>
				{/each}
			</div>

			<div class="relative min-w-0" bind:clientWidth={width}>
				{#if width > 0}
					<svg {width} {height} role="img" aria-label={label} class="block overflow-visible">
						<title>{label}</title>
						<desc>{groups.map(summary).join('. ')}</desc>
						<!-- Grid + axis -->
						{#each scaleInfo.ticks as t (t)}
							<line
								x1={x(t)}
								x2={x(t)}
								y1={0}
								y2={groups.length * ROW}
								stroke="var(--border)"
								stroke-width="1"
								stroke-dasharray={t === 0 ? undefined : '2 3'}
							/>
							<text
								x={x(t)}
								y={groups.length * ROW + 16}
								text-anchor="middle"
								class="fill-muted-foreground text-[11px] tabular-nums"
							>
								{format(t)}
							</text>
						{/each}

						{#each groups as g, i (g.group)}
							{@const cy = i * ROW + ROW / 2}
							{@const color = colorFor(i)}
							<g
								role="presentation"
								class="transition-opacity duration-150"
								opacity={hovered === null || hovered === i ? 1 : 0.45}
								onpointerenter={() => (hovered = i)}
								onpointerleave={() => (hovered = null)}
							>
								<title>{summary(g)}</title>
								<rect x={0} y={i * ROW} {width} height={ROW} fill="transparent" />
								{#if !num(g.median)}
									<text
										x={width / 2}
										y={cy + 4}
										text-anchor="middle"
										class="fill-muted-foreground text-[11px]"
									>
										— no data (n = {g.n})
									</text>
								{:else}
									{#if num(g.whisker_low) && num(g.whisker_high)}
										<line
											x1={x(g.whisker_low)}
											x2={x(g.whisker_high)}
											y1={cy}
											y2={cy}
											stroke="var(--muted-foreground)"
											stroke-width="1.5"
										/>
										<line
											x1={x(g.whisker_low)}
											x2={x(g.whisker_low)}
											y1={cy - 6}
											y2={cy + 6}
											stroke="var(--muted-foreground)"
											stroke-width="1.5"
										/>
										<line
											x1={x(g.whisker_high)}
											x2={x(g.whisker_high)}
											y1={cy - 6}
											y2={cy + 6}
											stroke="var(--muted-foreground)"
											stroke-width="1.5"
										/>
									{/if}
									{#if num(g.q1) && num(g.q3)}
										<rect
											x={x(g.q1)}
											y={cy - 10}
											width={Math.max(2, x(g.q3) - x(g.q1))}
											height={20}
											rx="2"
											fill={color}
											fill-opacity="0.18"
											stroke={color}
											stroke-width="1.5"
										/>
									{/if}
									<line
										x1={x(g.median)}
										x2={x(g.median)}
										y1={cy - 10}
										y2={cy + 10}
										stroke={color}
										stroke-width="3"
										stroke-linecap="round"
									/>
									{#if drawPoints}
										{#each g.values ?? [] as v, j (j)}
											<circle
												cx={x(v)}
												cy={cy + jitter(j + i * 31) * 7}
												r="2.5"
												fill={color}
												fill-opacity="0.6"
												stroke="var(--card)"
												stroke-width="0.5"
											/>
										{/each}
									{/if}
									{#each g.outliers ?? [] as v, j (j)}
										<circle
											cx={x(v)}
											{cy}
											r="3.5"
											fill="var(--card)"
											stroke={color}
											stroke-width="1.5"
										/>
									{/each}
									{#if num(g.mean)}
										<path
											d="M {x(g.mean)} {cy - 5} L {x(g.mean) + 5} {cy} L {x(g.mean)} {cy + 5} L {x(
												g.mean
											) - 5} {cy} Z"
											fill="var(--card)"
											stroke="var(--foreground)"
											stroke-width="1.25"
										/>
									{/if}
								{/if}
							</g>
						{/each}
					</svg>
					{#if tooltip}
						<div
							class="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border border-border bg-popover px-2 py-1 text-[11px] text-popover-foreground shadow-md"
							style="left: {tooltip.left}px; top: {tooltip.top}px; max-width: 240px"
						>
							{tooltip.text}
						</div>
					{/if}
				{:else}
					<div style="height: {height}px"></div>
				{/if}
			</div>

			<!-- n per group (counts always visible) -->
			<div class="text-right" aria-hidden="true">
				{#each groups as g (g.group)}
					<div
						class="flex flex-col items-end justify-center text-[11px] leading-tight tabular-nums text-muted-foreground"
						style="height: {ROW}px"
					>
						<span>n {g.n}</span>
						{#if g.n_missing}
							<span class="text-warning-fg">{g.n_missing} NA</span>
						{/if}
					</div>
				{/each}
			</div>
		</div>
		<figcaption class="flex flex-wrap items-center justify-between gap-2">
			<ChartLegend
				label="Box plot marks"
				items={[
					{
						label: 'IQR (box)',
						shape: 'square',
						color: 'var(--muted-foreground)'
					},
					{ label: 'Median', shape: 'line', color: 'var(--foreground)' },
					{ label: 'Mean', shape: 'diamond', color: 'var(--foreground)' },
					{
						label: 'Whiskers 1.5×IQR',
						shape: 'dash',
						color: 'var(--muted-foreground)'
					},
					...(drawPoints
						? [
								{
									label: 'Runs',
									shape: 'dot' as const,
									color: 'var(--muted-foreground)'
								}
							]
						: []),
					{ label: 'Outlier', shape: 'ring', color: 'var(--muted-foreground)' }
				]}
			/>
			<span class="text-[11px] text-muted-foreground">
				{unit ?? ''}{unit && direction ? ' · ' : ''}{direction}
			</span>
		</figcaption>
	</figure>
{/if}
