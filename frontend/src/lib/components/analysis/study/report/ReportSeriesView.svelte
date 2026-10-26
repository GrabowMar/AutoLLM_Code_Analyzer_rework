<script lang="ts">
	/**
	 * ReportSeriesView — renders one typed report series by kind:
	 * funnel, status_matrix (click-through to the run), heatmap, box,
	 * proportion (joined with its table's Wilson CIs), mean_ranks, forest,
	 * bar, upset and scatter. Unknown kinds fall back to their JSON.
	 *
	 * Props: `series`, `prov`, `tables` (same-section tables, for joins),
	 * `outcomeLabel(key)`, `ciLevel` (for captions).
	 */
	import type {
		BoxSeries,
		ForestSeries,
		FunnelSeries,
		HeatmapSeries,
		MeanRanksSeries,
		BarSeries,
		ReportSeries,
		ReportTable,
		ScatterSeries,
		StatusMatrixSeries,
		UpsetSeries
	} from '$lib/api/analysis';
	import { Segmented } from '$lib/components/ui/segmented';
	import BoxPlotChart from '$lib/components/charts/BoxPlotChart.svelte';
	import ForestPlot from '$lib/components/charts/ForestPlot.svelte';
	import FunnelChart from '$lib/components/charts/FunnelChart.svelte';
	import HeatmapGrid from '$lib/components/charts/HeatmapGrid.svelte';
	import HBarChart from '$lib/components/charts/HBarChart.svelte';
	import ProportionChart, {
		type ProportionItem
	} from '$lib/components/charts/ProportionChart.svelte';
	import ScatterPlot from '$lib/components/charts/ScatterPlot.svelte';
	import UpsetChart from '$lib/components/charts/UpsetChart.svelte';
	import StatusMatrix from '../StatusMatrix.svelte';
	import { fmtFloat, fmtUsd } from '../format';
	import { jobHref, runHref, toolCellHref, type ReportProvenance } from './provenance';

	interface Props {
		series: ReportSeries;
		prov: ReportProvenance;
		tables?: ReportTable[];
		outcomeLabel?: (key: string) => string;
		ciLevel?: number;
	}

	let {
		series,
		prov,
		tables = [],
		outcomeLabel = (k: string) => k,
		ciLevel = 0.95
	}: Props = $props();

	// ── status matrix ───────────────────────────────────────────────────
	function matrixRows(s: StatusMatrixSeries) {
		return s.rows.map((r) => {
			const run = prov.runByJob.get(r.key);
			return {
				key: r.key,
				label: r.label,
				group: r.group,
				run: run ?? null,
				href: run ? runHref(run) : jobHref(r.key),
				muted: !r.in_analysis,
				note: r.in_analysis ? undefined : 'not analysed'
			};
		});
	}

	function sortedMatrix(s: StatusMatrixSeries) {
		const order = s.rows
			.map((r, i) => ({ r, i }))
			.sort(
				(a, b) =>
					(a.r.group || '').localeCompare(b.r.group || '') || a.r.label.localeCompare(b.r.label)
			);
		const rows = matrixRows(s);
		return {
			rows: order.map(({ i }) => rows[i]),
			cells: order.map(({ i }) => s.cells[i] ?? []),
			reasons: order.map(({ i }) => s.reasons[i] ?? [])
		};
	}

	// ── heatmap formatting ──────────────────────────────────────────────
	function heatmapFormat(s: HeatmapSeries): {
		format: (v: number) => string;
		domain?: [number, number];
	} {
		const values = s.values.flat().filter((v): v is number => typeof v === 'number');
		const allInt = values.every((v) => Number.isInteger(v));
		if (allInt) return { format: (v) => v.toLocaleString() };
		if (values.every((v) => v >= 0 && v <= 1))
			return { format: (v) => v.toFixed(2), domain: [0, 1] };
		return { format: (v) => fmtFloat(v) };
	}

	function heatmapNa(s: HeatmapSeries): string {
		if (s.key.includes('jaccard')) {
			return 'Not computed: no run in which both tools succeeded produced an issue (Jaccard undefined). Never 0.';
		}
		return 'Not available (NA): no data for this cell. Never counted as 0.';
	}

	// ── proportion: join the Wilson CI from the outcome table ───────────
	function proportionItems(s: BoxSeries): ProportionItem[] {
		const table = tables.find((t) => t.key === `outcome.${s.outcome}`);
		return s.groups.map((g) => {
			const row = table?.rows.find((r) => r.group === g.group);
			const ci = Array.isArray(row?.share_ci) ? (row?.share_ci as unknown[]) : null;
			const lo = ci && typeof ci[0] === 'number' ? ci[0] : null;
			const hi = ci && typeof ci[1] === 'number' ? ci[1] : null;
			const share = typeof row?.share === 'number' ? row.share : g.mean;
			const successes = typeof row?.successes === 'number' ? row.successes : null;
			return {
				group: g.group,
				share,
				lo,
				hi,
				n: g.n,
				successes,
				n_missing: g.n_missing
			};
		});
	}

	// ── scatter axis choice ─────────────────────────────────────────────
	let scatterX = $state<'ai' | 'generation'>('ai');
	function scatterPoints(s: ScatterSeries) {
		return s.points.map((p) => ({
			label: p.group,
			x: scatterX === 'ai' ? p.ai_cost_per_run : p.generation_cost_per_target,
			y: p.primary_median
		}));
	}

	function isAgreement(s: BarSeries): boolean {
		return /krippendorff|α|alpha/i.test(s.title) || s.key === 'agreement';
	}
</script>

<figure class="space-y-2">
	<figcaption class="text-sm font-semibold">{series.title}</figcaption>
	{#if series.kind === 'funnel'}
		<FunnelChart steps={(series as FunnelSeries).steps} label={series.title} />
	{:else if series.kind === 'status_matrix'}
		{@const s = series as StatusMatrixSeries}
		{@const m = sortedMatrix(s)}
		<StatusMatrix
			rows={m.rows}
			columns={s.columns.map((c) => ({ key: c, label: c }))}
			cells={m.cells}
			reasons={m.reasons}
			cellHref={(i, j) => {
				const run = m.rows[i]?.run;
				return run
					? toolCellHref(run, s.columns[j] ?? '', m.cells[i]?.[j] ?? '')
					: (m.rows[i]?.href ?? null);
			}}
			label={s.title}
		/>
	{:else if series.kind === 'heatmap'}
		{@const s = series as HeatmapSeries}
		{@const f = heatmapFormat(s)}
		<HeatmapGrid
			rows={s.rows}
			rowLabels={s.row_labels}
			columns={s.columns}
			values={s.values}
			format={f.format}
			domain={f.domain}
			legendLabel={s.title}
			naReason={heatmapNa(s)}
			cellTitle={s.per_run
				? (i, j) => {
						const v = s.per_run?.[i]?.[j];
						return typeof v === 'number' ? `${fmtFloat(v)} per run` : '';
					}
				: undefined}
			label={s.title}
		/>
	{:else if series.kind === 'box'}
		{@const s = series as BoxSeries}
		<BoxPlotChart
			groups={s.groups}
			unit={s.unit}
			higherIsBetter={s.higher_is_better}
			format={(v) => fmtFloat(v)}
			label="{s.title}: distribution by group"
		/>
	{:else if series.kind === 'proportion'}
		{@const s = series as BoxSeries}
		<ProportionChart
			items={proportionItems(s)}
			ciLabel="{Math.round(ciLevel * 100)}% Wilson CI"
			higherIsBetter={s.higher_is_better}
			label="{s.title}: share by group"
		/>
	{:else if series.kind === 'mean_ranks'}
		{@const s = series as MeanRanksSeries}
		<HBarChart
			items={s.groups.map((g) => ({ label: g.group, value: g.mean_rank }))}
			domain={[0, Math.max(1, s.groups.length)]}
			format={(v) => fmtFloat(v, 2)}
			label="{s.title} over {s.n_blocks} blocks"
		/>
		<p class="text-[11px] text-muted-foreground">
			Mean rank within each of {s.n_blocks} block(s); rank 1 = lowest value of {outcomeLabel(
				s.outcome
			)}.
		</p>
	{:else if series.kind === 'forest'}
		{@const s = series as ForestSeries}
		<ForestPlot
			items={s.items.map((it) => ({
				label: it.label,
				estimate: it.delta,
				lo: it.lo,
				hi: it.hi,
				p: it.p_adj
			}))}
			estimateLabel="Cliff's δ"
			negativeLabel="first group lower"
			positiveLabel="first group higher"
			label={s.title}
		/>
	{:else if series.kind === 'bar'}
		{@const s = series as BarSeries}
		<HBarChart
			items={s.items.map((it) => ({
				label: it.group,
				value: it.value,
				muted: it.group === 'All'
			}))}
			domain={isAgreement(s) ? [Math.min(0, ...s.items.map((it) => it.value ?? 0)), 1] : undefined}
			refs={isAgreement(s)
				? [
						{ value: 0.667, label: 'tentative' },
						{ value: 0.8, label: 'reliable' }
					]
				: []}
			format={(v) => fmtFloat(v, 3)}
			label={s.title}
		/>
	{:else if series.kind === 'upset'}
		{@const s = series as UpsetSeries}
		<UpsetChart sets={s.sets} intersections={s.intersections} setLabel="Tool" label={s.title} />
	{:else if series.kind === 'scatter'}
		{@const s = series as ScatterSeries}
		<div class="flex flex-wrap items-center gap-2">
			<span class="text-xs text-muted-foreground" id="scatter-x-{s.key}">Cost axis</span>
			<Segmented
				size="sm"
				aria-labelledby="scatter-x-{s.key}"
				bind:value={scatterX}
				items={[
					{ value: 'ai', label: 'AI review / run' },
					{ value: 'generation', label: 'Generation / target' }
				]}
			/>
		</div>
		<ScatterPlot
			points={scatterPoints(s)}
			xLabel={scatterX === 'ai'
				? 'AI review cost per run (USD)'
				: 'Generation cost per target (USD)'}
			yLabel="Median {outcomeLabel(s.outcome)}"
			formatX={(v) => fmtUsd(v)}
			formatY={(v) => fmtFloat(v)}
			tickX={(v) => `$${Number(v.toPrecision(2))}`}
			label={s.title}
		/>
	{:else}
		<pre
			class="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-[11px]">{JSON.stringify(
				series,
				null,
				2
			)}</pre>
	{/if}
</figure>
