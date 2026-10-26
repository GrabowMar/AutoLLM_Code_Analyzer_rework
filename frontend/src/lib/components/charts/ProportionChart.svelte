<script lang="ts" module>
	export interface ProportionItem {
		group: string;
		/** Share in [0, 1]; null = no data (NA). */
		share: number | null;
		/** Confidence interval (e.g. Wilson) in [0, 1]. */
		lo: number | null;
		hi: number | null;
		n: number;
		successes?: number | null;
		n_missing?: number;
	}
</script>

<script lang="ts">
	/**
	 * ProportionChart — a share per group with its confidence interval on a
	 * fixed 0–100% axis (dot + whiskers), plus "k / n" counts. Groups without
	 * data show "no data", never 0%.
	 *
	 * Props: `items: ProportionItem[]`, `ciLabel` (e.g. "95% Wilson CI"),
	 * `higherIsBetter`, `label` (accessible name).
	 */
	import { seriesColor } from './theme';
	import { linearScale } from './scale';
	import ChartEmpty from './ChartEmpty.svelte';
	import ChartLegend from './ChartLegend.svelte';

	interface Props {
		items: ProportionItem[];
		ciLabel?: string;
		higherIsBetter?: boolean | null;
		label?: string;
	}

	let {
		items,
		ciLabel = '95% CI',
		higherIsBetter = null,
		label = 'Share by group'
	}: Props = $props();

	const ROW = 34;
	const AXIS = 26;
	const PAD = 10;

	let width = $state(0);

	function num(v: number | null | undefined): v is number {
		return typeof v === 'number' && Number.isFinite(v);
	}
	function pct(v: number | null | undefined): string {
		return num(v) ? `${Number((v * 100).toFixed(1))}%` : '—';
	}

	const x = $derived(linearScale([0, 1], [PAD, Math.max(PAD + 1, width - PAD)]));
	const height = $derived(items.length * ROW + AXIS);
	const colorFor = (i: number) => (items.length <= 5 ? seriesColor(i + 1) : 'var(--chart-2)');

	function rowText(it: ProportionItem): string {
		if (!num(it.share)) return `${it.group}: no data (n = ${it.n})`;
		const ci = num(it.lo) && num(it.hi) ? `, ${ciLabel} ${pct(it.lo)}–${pct(it.hi)}` : '';
		const k = num(it.successes) ? ` (${it.successes} of ${it.n})` : ` (n = ${it.n})`;
		return `${it.group}: ${pct(it.share)}${k}${ci}`;
	}
</script>

{#if items.length === 0}
	<ChartEmpty message="No groups to plot." height={100} />
{:else}
	<figure class="space-y-2" aria-label={label}>
		<div
			class="grid grid-cols-[minmax(5.5rem,11rem)_minmax(0,1fr)_auto] items-start gap-x-3 text-xs"
		>
			<div aria-hidden="true">
				{#each items as it, i (it.group)}
					<div class="flex items-center gap-1.5" style="height: {ROW}px">
						<span class="h-2 w-2 shrink-0 rounded-full" style="background: {colorFor(i)}"></span>
						<span class="truncate" title={it.group}>{it.group}</span>
					</div>
				{/each}
			</div>
			<div class="min-w-0" bind:clientWidth={width}>
				{#if width > 0}
					<svg {width} {height} role="img" aria-label={label} class="block overflow-visible">
						<title>{label}</title>
						<desc>{items.map(rowText).join('. ')}</desc>
						{#each [0, 0.25, 0.5, 0.75, 1] as t (t)}
							<line
								x1={x(t)}
								x2={x(t)}
								y1={0}
								y2={items.length * ROW}
								stroke="var(--border)"
								stroke-dasharray={t === 0 ? undefined : '2 3'}
							/>
							<text
								x={x(t)}
								y={items.length * ROW + 16}
								text-anchor="middle"
								class="fill-muted-foreground text-[11px] tabular-nums">{t * 100}%</text
							>
						{/each}
						{#each items as it, i (it.group)}
							{@const cy = i * ROW + ROW / 2}
							{@const color = colorFor(i)}
							<g>
								<title>{rowText(it)}</title>
								{#if num(it.share)}
									<rect
										x={x(0)}
										y={cy - 7}
										width={Math.max(0, x(it.share) - x(0))}
										height="14"
										rx="2"
										fill={color}
										fill-opacity="0.14"
									/>
									{#if num(it.lo) && num(it.hi)}
										<line
											x1={x(it.lo)}
											x2={x(it.hi)}
											y1={cy}
											y2={cy}
											stroke={color}
											stroke-width="2"
										/>
										{#each [it.lo, it.hi] as edge, k (k)}
											<line
												x1={x(edge)}
												x2={x(edge)}
												y1={cy - 5}
												y2={cy + 5}
												stroke={color}
												stroke-width="2"
											/>
										{/each}
									{/if}
									<circle
										cx={x(it.share)}
										{cy}
										r="5"
										fill={color}
										stroke="var(--card)"
										stroke-width="1.5"
									/>
								{:else}
									<text x={x(0) + 6} y={cy + 4} class="fill-muted-foreground text-[11px]"
										>— no data</text
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
				{#each items as it (it.group)}
					<div class="flex flex-col items-end justify-center leading-tight" style="height: {ROW}px">
						<span class="font-medium">{pct(it.share)}</span>
						<span class="text-[11px] text-muted-foreground">
							{#if num(it.successes)}{it.successes}/{it.n}{:else}n {it.n}{/if}
							{#if num(it.lo) && num(it.hi)}· [{pct(it.lo)}, {pct(it.hi)}]{/if}
						</span>
					</div>
				{/each}
			</div>
		</div>
		<figcaption
			class="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground"
		>
			<ChartLegend
				label="Proportion marks"
				items={[
					{ label: 'Share', shape: 'dot', color: 'var(--muted-foreground)' },
					{ label: ciLabel, shape: 'line', color: 'var(--muted-foreground)' }
				]}
			/>
			{#if higherIsBetter !== null}
				<span>{higherIsBetter ? 'Higher is better' : 'Lower is better'}</span>
			{/if}
		</figcaption>
	</figure>
{/if}
