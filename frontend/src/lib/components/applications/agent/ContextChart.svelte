<script lang="ts">
	// Prompt tokens per main-role call (context growth over the run): one
	// series, ink on paper, values readable on hover/focus and in the caption.
	import { fmtTokensShort, lineGeometry, type LinePoint } from './format';

	interface Props {
		series: number[];
		height?: number;
		/** What one x step is ("call"). */
		unit?: string;
	}

	let { series, height = 150, unit = 'call' }: Props = $props();

	let width = $state(0);
	let hover = $state<LinePoint | null>(null);

	const pad = { left: 44, right: 10, top: 10, bottom: 20 };
	const geometry = $derived(lineGeometry(series, Math.max(width, 120), height, pad));
	const first = $derived(series[0]);
	const last = $derived(series.at(-1));
	const label = $derived(
		series.length
			? `Prompt tokens per ${unit}: ${series.length} ${unit}s, from ${fmtTokensShort(first)} to ${fmtTokensShort(last)}, peak ${fmtTokensShort(geometry.max)}`
			: 'No prompt-token series',
	);

	function onMove(event: PointerEvent) {
		const svg = event.currentTarget as SVGSVGElement;
		const rect = svg.getBoundingClientRect();
		const x = event.clientX - rect.left;
		let best: LinePoint | null = null;
		for (const p of geometry.points) {
			if (!best || Math.abs(p.x - x) < Math.abs(best.x - x)) best = p;
		}
		hover = best;
	}
</script>

<figure class="space-y-1">
	<div class="relative w-full" bind:clientWidth={width} style="height: {height}px">
		{#if width > 0 && geometry.points.length}
			<svg
				width={width}
				{height}
				role="img"
				aria-label={label}
				class="block overflow-visible"
				onpointermove={onMove}
				onpointerleave={() => (hover = null)}
			>
				{#each geometry.yTicks as tick, i (i)}
					<line x1={pad.left} x2={width - pad.right} y1={tick.y} y2={tick.y} stroke="var(--border)" stroke-width="1" />
					<text x={pad.left - 6} y={tick.y + 3} text-anchor="end" class="fill-muted-foreground font-mono text-[11px]">{fmtTokensShort(tick.value)}</text>
				{/each}
				<path d={geometry.path} fill="none" stroke="var(--chart-1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
				<text x={pad.left} y={height - 4} class="fill-muted-foreground font-mono text-[11px]">{unit} 1</text>
				<text x={width - pad.right} y={height - 4} text-anchor="end" class="fill-muted-foreground font-mono text-[11px]">{unit} {series.length}</text>
				{#if hover}
					<line x1={hover.x} x2={hover.x} y1={pad.top} y2={height - pad.bottom} stroke="var(--muted-foreground)" stroke-width="1" stroke-dasharray="2 3" />
					<circle cx={hover.x} cy={hover.y} r="4" fill="var(--chart-1)" stroke="var(--card)" stroke-width="2" />
				{/if}
			</svg>
			{#if hover}
				<div
					class="pointer-events-none absolute top-0 rounded border bg-popover px-2 py-1 font-mono text-[11px] tabular-nums shadow-sm"
					style="left: {Math.min(Math.max(hover.x - 60, 0), Math.max(width - 130, 0))}px"
				>
					{unit} {hover.index + 1}: {hover.value.toLocaleString()} tokens
				</div>
			{/if}
		{/if}
	</div>
	<figcaption class="text-[11px] text-muted-foreground">{label}.</figcaption>
</figure>
