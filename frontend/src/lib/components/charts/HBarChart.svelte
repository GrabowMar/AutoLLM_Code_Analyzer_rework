<script lang="ts" module>
	export interface HBarItem {
		label: string;
		/** null = not computable (NA), drawn as "—", never as 0. */
		value: number | null;
		/** Draw this bar in the neutral tone (e.g. a pooled "All" row). */
		muted?: boolean;
	}
</script>

<script lang="ts">
	/**
	 * HBarChart — one horizontal bar per item with the value printed at its
	 * end. Supports negative values (bars grow from zero) and labelled
	 * reference lines (e.g. Krippendorff's α 0.667 / 0.8).
	 *
	 * Props: `items: HBarItem[]`, `domain` (default data range incl. 0),
	 * `refs: {value, label}[]`, `format(v)`, `color` (CSS var), `label`.
	 */
	import { linearScale, niceTicks, formatTick } from './scale';
	import ChartEmpty from './ChartEmpty.svelte';

	interface Props {
		items: HBarItem[];
		domain?: [number, number];
		refs?: { value: number; label: string }[];
		format?: (value: number) => string;
		color?: string;
		label?: string;
	}

	let {
		items,
		domain,
		refs = [],
		format = (v: number) => formatTick(v),
		color = 'var(--chart-2)',
		label = 'Bar chart'
	}: Props = $props();

	const ROW = 28;
	const AXIS = 22;
	const PAD = 8;

	let width = $state(0);

	function num(v: number | null | undefined): v is number {
		return typeof v === 'number' && Number.isFinite(v);
	}

	const values = $derived(items.map((it) => it.value).filter(num));
	const scaleInfo = $derived.by(() => {
		if (domain)
			return {
				min: domain[0],
				max: domain[1],
				ticks: niceTicks(domain[0], domain[1], 4).ticks
			};
		const all = [...values, ...refs.map((r) => r.value), 0];
		return niceTicks(Math.min(...all), Math.max(...all), Math.max(2, Math.floor(width / 90)));
	});
	const x = $derived(
		linearScale([scaleInfo.min, scaleInfo.max], [PAD, Math.max(PAD + 1, width - PAD)])
	);
	const zero = $derived(x(Math.min(Math.max(0, scaleInfo.min), scaleInfo.max)));
	const height = $derived(items.length * ROW + AXIS);
</script>

{#if items.length === 0}
	<ChartEmpty message="No values to plot." height={100} />
{:else}
	<figure aria-label={label}>
		<div
			class="grid grid-cols-[minmax(5.5rem,11rem)_minmax(0,1fr)_auto] items-start gap-x-3 text-xs"
		>
			<div aria-hidden="true">
				{#each items as it (it.label)}
					<div class="flex items-center" style="height: {ROW}px">
						<span class="truncate" class:text-muted-foreground={it.muted} title={it.label}
							>{it.label}</span
						>
					</div>
				{/each}
			</div>
			<div class="min-w-0" bind:clientWidth={width}>
				{#if width > 0}
					<svg {width} {height} role="img" aria-label={label} class="block overflow-visible">
						<title>{label}</title>
						<desc>
							{items
								.map((it) => `${it.label}: ${num(it.value) ? format(it.value) : 'not available'}`)
								.join('. ')}
						</desc>
						{#each scaleInfo.ticks as t (t)}
							<line
								x1={x(t)}
								x2={x(t)}
								y1={0}
								y2={items.length * ROW}
								stroke="var(--border)"
								stroke-dasharray="2 3"
							/>
							<text
								x={x(t)}
								y={items.length * ROW + 15}
								text-anchor="middle"
								class="fill-muted-foreground text-[11px] tabular-nums">{format(t)}</text
							>
						{/each}
						<line
							x1={zero}
							x2={zero}
							y1={0}
							y2={items.length * ROW}
							stroke="var(--muted-foreground)"
						/>
						{#each items as it, i (it.label)}
							{@const cy = i * ROW + ROW / 2}
							{#if num(it.value)}
								<rect
									x={Math.min(zero, x(it.value))}
									y={cy - 8}
									width={Math.max(1, Math.abs(x(it.value) - zero))}
									height="16"
									rx="2"
									fill={it.muted ? 'var(--muted-foreground)' : color}
									fill-opacity={it.muted ? 0.45 : 0.85}
								>
									<title>{it.label}: {format(it.value)}</title>
								</rect>
							{:else}
								<text x={zero + 6} y={cy + 4} class="fill-muted-foreground text-[11px]"
									>— not computable</text
								>
							{/if}
						{/each}
						{#each refs as r (r.value)}
							<line
								x1={x(r.value)}
								x2={x(r.value)}
								y1={-4}
								y2={items.length * ROW}
								stroke="var(--warning)"
								stroke-dasharray="4 3"
								stroke-width="1.25"
							/>
						{/each}
					</svg>
				{:else}
					<div style="height: {height}px"></div>
				{/if}
			</div>
			<div class="text-right tabular-nums" aria-hidden="true">
				{#each items as it (it.label)}
					<div class="flex items-center justify-end font-medium" style="height: {ROW}px">
						{num(it.value) ? format(it.value) : '—'}
					</div>
				{/each}
			</div>
		</div>
		{#if refs.length > 0}
			<figcaption class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
				{#each refs as r (r.value)}
					<span class="inline-flex items-center gap-1.5">
						<span class="h-0 w-4 border-t-2 border-dashed border-warning" aria-hidden="true"></span>
						{r.label} ({format(r.value)})
					</span>
				{/each}
			</figcaption>
		{/if}
	</figure>
{/if}
