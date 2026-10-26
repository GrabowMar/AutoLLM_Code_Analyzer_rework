<script lang="ts" module>
	export interface ForestItem {
		label: string;
		/** Effect size; null = not computable (shown as NA, never 0). */
		estimate: number | null;
		lo: number | null;
		hi: number | null;
		/** Adjusted p-value shown next to the row. */
		p?: number | null;
	}
</script>

<script lang="ts">
	/**
	 * ForestPlot — effect sizes with confidence intervals, one row per
	 * comparison, on a fixed symmetric axis (Cliff's δ: [-1, 1]) with the
	 * no-effect line and shaded magnitude bands.
	 *
	 * Props:
	 * - `items: ForestItem[]`; `domain` (default [-1, 1]);
	 * - `bands`: magnitude thresholds of |effect| (default Cliff's δ:
	 *   negligible 0.147, small 0.33, medium 0.474);
	 * - `estimateLabel` (e.g. "Cliff's δ"), `negativeLabel` / `positiveLabel`
	 *   (what each side means), `pLabel` (column header, default "p adj."),
	 *   `label` (accessible name).
	 */
	import { linearScale } from './scale';
	import ChartEmpty from './ChartEmpty.svelte';
	import ChartLegend from './ChartLegend.svelte';

	interface Props {
		items: ForestItem[];
		domain?: [number, number];
		bands?: { negligible: number; small: number; medium: number } | null;
		estimateLabel?: string;
		negativeLabel?: string;
		positiveLabel?: string;
		pLabel?: string;
		label?: string;
	}

	let {
		items,
		domain = [-1, 1],
		bands = { negligible: 0.147, small: 0.33, medium: 0.474 },
		estimateLabel = 'Effect',
		negativeLabel = '',
		positiveLabel = '',
		pLabel = 'p adj.',
		label = 'Effect sizes with confidence intervals'
	}: Props = $props();

	const ROW = 34;
	const AXIS = 26;
	const PAD = 12;

	let width = $state(0);

	function num(v: number | null | undefined): v is number {
		return typeof v === 'number' && Number.isFinite(v);
	}
	function clamp(v: number): number {
		return Math.min(Math.max(v, domain[0]), domain[1]);
	}
	function fmt(v: number | null | undefined): string {
		if (!num(v)) return '—';
		return (v > 0 ? '+' : '') + v.toFixed(2);
	}
	function fmtP(v: number | null | undefined): string {
		if (!num(v)) return '—';
		return v < 0.001 ? '< 0.001' : v.toFixed(3);
	}

	const x = $derived(linearScale(domain, [PAD, Math.max(PAD + 1, width - PAD)]));
	const height = $derived(items.length * ROW + AXIS);
	const ticks = $derived.by(() => {
		const [a, b] = domain;
		const step = (b - a) / 4;
		return [0, 1, 2, 3, 4].map((i) => Number((a + i * step).toFixed(6)));
	});
	const anyEstimate = $derived(items.some((it) => num(it.estimate)));

	function rowText(it: ForestItem): string {
		if (!num(it.estimate)) return `${it.label}: ${estimateLabel} not computable`;
		const ci = num(it.lo) && num(it.hi) ? `, CI [${fmt(it.lo)}, ${fmt(it.hi)}]` : ', no CI';
		const p = num(it.p) ? `, ${pLabel} ${fmtP(it.p)}` : '';
		return `${it.label}: ${estimateLabel} ${fmt(it.estimate)}${ci}${p}`;
	}
</script>

{#if items.length === 0}
	<ChartEmpty message="No comparisons to plot." height={100} />
{:else}
	<figure class="space-y-2" aria-label={label}>
		<div class="grid grid-cols-[minmax(6rem,14rem)_minmax(0,1fr)_auto] items-start gap-x-3 text-xs">
			<div aria-hidden="true">
				{#each items as it, i (i)}
					<div class="flex items-center" style="height: {ROW}px">
						<span class="truncate" title={it.label}>{it.label}</span>
					</div>
				{/each}
			</div>
			<div class="min-w-0" bind:clientWidth={width}>
				{#if width > 0}
					<svg {width} {height} role="img" aria-label={label} class="block overflow-visible">
						<title>{label}</title>
						<desc>{items.map(rowText).join('. ')}</desc>
						{#if bands}
							<rect
								x={x(-bands.negligible)}
								y={0}
								width={x(bands.negligible) - x(-bands.negligible)}
								height={items.length * ROW}
								fill="var(--muted)"
								opacity="0.7"
							/>
							{#each [bands.small, bands.medium] as b (b)}
								{#each [-b, b] as edge (edge)}
									<line
										x1={x(edge)}
										x2={x(edge)}
										y1={0}
										y2={items.length * ROW}
										stroke="var(--border)"
										stroke-dasharray="2 3"
									/>
								{/each}
							{/each}
						{/if}
						<line
							x1={x(0)}
							x2={x(0)}
							y1={0}
							y2={items.length * ROW}
							stroke="var(--muted-foreground)"
							stroke-width="1.25"
						/>
						{#each ticks as t (t)}
							<text
								x={x(t)}
								y={items.length * ROW + 16}
								text-anchor="middle"
								class="fill-muted-foreground text-[11px] tabular-nums"
							>
								{t}
							</text>
						{/each}
						{#each items as it, i (i)}
							{@const cy = i * ROW + ROW / 2}
							<g>
								<title>{rowText(it)}</title>
								{#if num(it.estimate)}
									{#if num(it.lo) && num(it.hi)}
										<line
											x1={x(clamp(it.lo))}
											x2={x(clamp(it.hi))}
											y1={cy}
											y2={cy}
											stroke="var(--chart-2)"
											stroke-width="2"
										/>
										{#each [it.lo, it.hi] as edge, k (k)}
											<line
												x1={x(clamp(edge))}
												x2={x(clamp(edge))}
												y1={cy - 5}
												y2={cy + 5}
												stroke="var(--chart-2)"
												stroke-width="2"
											/>
										{/each}
									{/if}
									<rect
										x={x(clamp(it.estimate)) - 5}
										y={cy - 5}
										width="10"
										height="10"
										fill="var(--chart-2)"
										stroke="var(--card)"
										stroke-width="1"
									/>
								{:else}
									<text x={x(0) + 8} y={cy + 4} class="fill-muted-foreground text-[11px]"
										>— not computable</text
									>
								{/if}
							</g>
						{/each}
					</svg>
				{:else}
					<div style="height: {height}px"></div>
				{/if}
			</div>
			<div class="text-right tabular-nums" aria-hidden="true">
				{#each items as it, i (i)}
					<div class="flex flex-col items-end justify-center leading-tight" style="height: {ROW}px">
						<span class="font-medium">{fmt(it.estimate)}</span>
						<span class="text-[11px] text-muted-foreground">
							{#if num(it.lo) && num(it.hi)}[{fmt(it.lo)}, {fmt(it.hi)}]{:else}no CI{/if}
							{#if it.p !== undefined}· {pLabel} {fmtP(it.p)}{/if}
						</span>
					</div>
				{/each}
			</div>
		</div>
		<figcaption
			class="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground"
		>
			<ChartLegend
				label="Forest plot marks"
				items={[
					{ label: estimateLabel, shape: 'square', color: 'var(--chart-2)' },
					{
						label: 'Confidence interval',
						shape: 'line',
						color: 'var(--chart-2)'
					},
					...(bands
						? [
								{
									label: `Negligible (|δ| < ${bands.negligible})`,
									shape: 'square' as const,
									color: 'var(--muted-foreground)'
								},
								{
									label: `Small / medium bounds (${bands.small}, ${bands.medium})`,
									shape: 'dash' as const,
									color: 'var(--muted-foreground)'
								}
							]
						: [])
				]}
			/>
			{#if negativeLabel || positiveLabel}
				<span>← {negativeLabel} · {positiveLabel} →</span>
			{/if}
			{#if !anyEstimate}
				<span class="basis-full"
					>No effect size could be computed (too few paired observations).</span
				>
			{/if}
		</figcaption>
	</figure>
{/if}
