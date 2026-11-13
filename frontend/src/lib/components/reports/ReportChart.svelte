<script lang="ts">
	/**
	 * ReportChart — one report table drawn as horizontal bars, one row per identity value
	 * (`chartSpec`): a single series in `chart-1`, the 95% interval as a whisker with end caps when
	 * the table has one, the server's value printed beside each bar (never color alone), a zero line
	 * when the axis spans 0. In compare mode the other report's value is a thin `chart-2` bar under
	 * each row, with a legend. Rows are focusable; hover or focus shows the row's tooltip.
	 *
	 * Props: `table`, `spec`, `other` (the matched table of the compared report), `otherLabel`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { cn } from '$lib/utils';
	import type { ReportTable } from '$lib/api/reports';
	import { chartDomain, chartRows, metricValue, parseInterval, type ChartSpec } from './chart';
	import { otherCell, rowIndex } from './compare';

	interface Props {
		table: ReportTable;
		spec: ChartSpec;
		other?: ReportTable | null;
		otherLabel?: string;
	}

	let { table, spec, other = null, otherLabel = 'Compared report' }: Props = $props();

	const MAX = 40;
	/** '' = the first measure. */
	let metricKey = $state('');
	let byValue = $state(false);
	let hover = $state<number | null>(null);

	const metric = $derived(spec.metrics.find((m) => String(m.index) === metricKey) ?? spec.metrics[0]);
	const otherRows = $derived(rowIndex(other));
	const rows = $derived.by(() => {
		const base = chartRows(table, spec, metric).map((r) => {
			const oc = otherCell(other, otherRows.get(table.rows[r.index][0] ?? ''), metric.header);
			const ov = oc == null ? null : metricValue(oc, metric.kind);
			const ci = oc == null ? null : metric.ciIndex != null ? null : parseInterval(oc);
			return { ...r, other: ov, otherText: oc, otherLo: ci?.[0], otherHi: ci?.[1] };
		});
		const sorted = byValue ? [...base].sort((a, b) => b.value - a.value) : base;
		return sorted.slice(0, MAX);
	});
	const total = $derived(chartRows(table, spec, metric).length);
	const domain = $derived(
		chartDomain(
			[...rows, ...rows.filter((r) => r.other != null).map((r) => ({ value: r.other as number, lo: r.otherLo, hi: r.otherHi }))],
			metric.kind
		)
	);
	const pos = (v: number) => ((v - domain[0]) / (domain[1] - domain[0])) * 100;
	const zero = $derived(pos(0));
	const ticks = $derived.by(() => {
		const [a, b] = domain;
		if (metric.kind === 'share' && a === 0 && b === 1) return [0, 0.25, 0.5, 0.75, 1];
		const span = b - a;
		const step = niceStep(span / 4);
		const out: number[] = [];
		for (let t = Math.ceil(a / step) * step; t <= b + 1e-9; t += step) out.push(Number(t.toFixed(10)));
		return out;
	});
	const tickText = (t: number) => (metric.kind === 'share' && domain[1] <= 1 ? `${Math.round(t * 100)}%` : t.toLocaleString(undefined, { maximumFractionDigits: 2 }));
	const hasCi = $derived(rows.some((r) => r.lo != null));

	function niceStep(raw: number): number {
		const p = 10 ** Math.floor(Math.log10(raw || 1));
		const f = raw / p;
		return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
	}
	function bar(v: number) {
		const a = Math.min(zero, pos(v));
		return { left: a, width: Math.max(Math.abs(pos(v) - zero), 0.4) };
	}
</script>

<div class="space-y-3 p-4">
	<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
		{#if spec.metrics.length > 1}
			<div class="flex items-center gap-2">
				<span class="text-xs text-muted-foreground">Measure</span>
				<Select.Simple
					size="sm"
					class="min-w-44"
					aria-label="Measure to chart"
					value={String(metric.index)}
					onValueChange={(v) => (metricKey = v)}
					items={spec.metrics.map((m) => ({ value: String(m.index), label: m.header }))}
				/>
			</div>
		{:else}
			<span class="text-sm font-medium">{metric.header}</span>
		{/if}
		<label class="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
			<Checkbox bind:checked={byValue} /> Sort by value
		</label>
		{#if other}
			<span class="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" aria-label="Legend">
				<span class="inline-flex items-center gap-1.5"><span class="h-2.5 w-3.5 rounded-[2px] bg-[var(--chart-1)]"></span>This report</span>
				<span class="inline-flex items-center gap-1.5"><span class="h-1 w-3.5 rounded-[2px] bg-[var(--chart-2)]"></span>{otherLabel}</span>
			</span>
		{/if}
	</div>

	<div class="rc-plot" role="list" aria-label="{metric.header} by {table.columns[spec.labelIndex]}">
		{#each rows as r (r.index)}
			{@const b = bar(r.value)}
			<!-- svelte-ignore a11y_no_noninteractive_tabindex (focus shows the row's tooltip to keyboard users) -->
			<div
				class={cn('rc-row', hover === r.index && 'rc-row-on')}
				role="listitem"
				tabindex="0"
				aria-label="{r.label}: {r.text}{r.lo != null ? `, 95% CI ${r.lo} to ${r.hi}` : ''}{r.otherText != null ? `; ${otherLabel}: ${r.otherText || 'none'}` : ''}"
				onmouseenter={() => (hover = r.index)}
				onmouseleave={() => (hover = null)}
				onfocus={() => (hover = r.index)}
				onblur={() => (hover = null)}
			>
				<span class="rc-label" title={r.label}>{r.label}</span>
				<span class="rc-track">
					{#each ticks as t (t)}<span class="rc-grid" style:left="{pos(t)}%"></span>{/each}
					{#if domain[0] < 0}<span class="rc-zero" style:left="{zero}%"></span>{/if}
					<span class={cn('rc-bar', other && 'rc-bar-cmp')} style:left="{b.left}%" style:width="{b.width}%"></span>
					{#if r.lo != null && r.hi != null}
						<span class="rc-ci {other ? 'rc-ci-cmp' : ''}" style:left="{pos(r.lo)}%" style:width="{Math.max(pos(r.hi) - pos(r.lo), 0.3)}%"></span>
					{/if}
					{#if r.other != null}
						{@const ob = bar(r.other)}
						<span class="rc-other" style:left="{ob.left}%" style:width="{ob.width}%"></span>
					{/if}
					{#if hover === r.index}
						<span class="rc-tip" role="tooltip" style:left="{Math.min(Math.max(pos(r.value), 12), 88)}%">
							<b class="block font-sans font-medium">{r.label}</b>
							<span class="block">{metric.header}: <b class="font-medium">{r.text}</b></span>
							{#if r.lo != null && metric.ciIndex != null}<span class="block text-muted-foreground">95% CI {table.rows[r.index][metric.ciIndex]}</span>{/if}
							{#if other}<span class="block text-muted-foreground">{otherLabel}: {r.otherText ?? 'no matching row'}</span>{/if}
						</span>
					{/if}
				</span>
				<span class="rc-value">{r.text.replace(/\s*\[[^\]]*\]\s*$/, '')}</span>
			</div>
		{/each}
		<div class="rc-row rc-axis" aria-hidden="true">
			<span></span>
			<span class="rc-track">
				{#each ticks as t (t)}<span class="rc-tick" style:left="{pos(t)}%">{tickText(t)}</span>{/each}
			</span>
			<span></span>
		</div>
	</div>
	<p class="text-xs text-muted-foreground">
		Bar = {metric.header.toLowerCase()}{hasCi ? ' · whisker = 95% CI' : ''}{other ? ` · thin bar = ${otherLabel}` : ''}.
		{#if total > MAX}Showing {byValue ? 'the top' : 'the first'} {MAX} of {total} rows.{/if}
		Rows without a value are left out.
	</p>
</div>

<style>
	.rc-plot {
		display: grid;
		gap: 2px;
	}
	.rc-row {
		display: grid;
		grid-template-columns: minmax(7rem, 15rem) minmax(0, 1fr) minmax(3.5rem, auto);
		align-items: center;
		gap: 0.75rem;
		min-height: 1.75rem;
		border-radius: 3px;
		padding: 0 0.25rem;
		outline: none;
		transition: background-color 150ms;
	}
	.rc-row-on,
	.rc-row:focus-visible {
		background: color-mix(in oklab, var(--muted) 70%, transparent);
	}
	.rc-row:focus-visible {
		box-shadow: inset 0 0 0 2px var(--ring);
	}
	.rc-label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.8125rem;
	}
	.rc-value {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		text-align: right;
		white-space: nowrap;
	}
	.rc-track {
		position: relative;
		height: 1.25rem;
	}
	.rc-grid {
		position: absolute;
		top: -2px;
		bottom: -2px;
		width: 1px;
		background: color-mix(in oklab, var(--border) 55%, transparent);
	}
	.rc-zero {
		position: absolute;
		top: -3px;
		bottom: -3px;
		width: 1px;
		background: color-mix(in oklab, var(--foreground) 45%, transparent);
	}
	.rc-bar {
		position: absolute;
		top: 4px;
		height: 12px;
		border-radius: 0 4px 4px 0;
		background: var(--chart-1);
	}
	.rc-bar-cmp {
		top: 2px;
		height: 10px;
	}
	.rc-ci {
		position: absolute;
		top: 9px;
		height: 2px;
		background: var(--foreground);
		opacity: 0.8;
	}
	.rc-ci-cmp {
		top: 6px;
	}
	.rc-ci::before,
	.rc-ci::after {
		content: '';
		position: absolute;
		top: -4px;
		width: 2px;
		height: 10px;
		background: inherit;
	}
	.rc-ci::before {
		left: -1px;
	}
	.rc-ci::after {
		right: -1px;
	}
	.rc-other {
		position: absolute;
		top: 14px;
		height: 4px;
		border-radius: 0 2px 2px 0;
		background: var(--chart-2);
		box-shadow: 0 0 0 1px var(--card);
	}
	.rc-tip {
		position: absolute;
		bottom: calc(100% + 6px);
		transform: translateX(-50%);
		z-index: 5;
		min-width: 10rem;
		max-width: 18rem;
		padding: 0.375rem 0.5rem;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--popover);
		color: var(--popover-foreground);
		box-shadow: 0 4px 14px rgb(0 0 0 / 0.18);
		font-size: 0.75rem;
		line-height: 1.4;
		pointer-events: none;
	}
	.rc-axis {
		min-height: 1.25rem;
		margin-top: 2px;
	}
	.rc-tick {
		position: absolute;
		top: 0;
		transform: translateX(-50%);
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--muted-foreground);
		white-space: nowrap;
	}
	@media (max-width: 39.99rem) {
		.rc-row {
			grid-template-columns: minmax(0, 1fr) auto;
			row-gap: 0.125rem;
			padding: 0.25rem;
		}
		.rc-track {
			grid-column: 1 / -1;
			grid-row: 2;
		}
		.rc-axis > span:first-child,
		.rc-axis > span:last-child {
			display: none;
		}
	}
</style>
