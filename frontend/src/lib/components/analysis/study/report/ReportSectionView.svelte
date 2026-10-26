<script lang="ts">
	/**
	 * ReportSectionView — one report section: description and notes, every
	 * series (chart) and every table. Items keyed per outcome
	 * (`outcome.<k>`, `box.<k>`, `ranks.<k>`, `forest.<k>`) are grouped
	 * behind an outcome switcher (primary outcome first) so 18 outcomes do
	 * not become 36 stacked figures; everything else renders in order.
	 * Severity-by-group tables also get a grouped bar chart.
	 *
	 * Props: `section`, `prov`, `definitions` (outcome definitions),
	 * `primaryOutcome`, `ciLevel`.
	 */
	import type { OutcomeDefinition, ReportSection, ReportTable } from '$lib/api/analysis';
	import { SEVERITIES } from '$lib/api/analysis';
	import * as Select from '$lib/components/ui/select';
	import { Label } from '$lib/components/ui/label';
	import GroupedBarChart from '$lib/components/charts/GroupedBarChart.svelte';
	import { severityChartColors } from '$lib/components/charts/theme';
	import ReportSeriesView from './ReportSeriesView.svelte';
	import ReportTableView from './ReportTableView.svelte';
	import type { ReportProvenance } from './provenance';

	interface Props {
		section: ReportSection;
		prov: ReportProvenance;
		definitions: OutcomeDefinition[];
		primaryOutcome: string;
		ciLevel?: number;
	}

	let { section, prov, definitions, primaryOutcome, ciLevel = 0.95 }: Props = $props();

	const OUTCOME_RE = /^(?:outcome|box|ranks|forest)\.(.+)$/;
	const outcomeOf = (key: string): string | null => OUTCOME_RE.exec(key)?.[1] ?? null;

	const defByKey = $derived(new Map(definitions.map((d) => [d.key, d])));
	const outcomeLabel = (key: string) => defByKey.get(key)?.label ?? key;

	const outcomeKeys = $derived.by(() => {
		const seen = new Set<string>();
		for (const item of [...section.series, ...section.tables]) {
			const k = outcomeOf(item.key);
			if (k) seen.add(k);
		}
		const order = definitions.map((d) => d.key);
		return [...seen].sort((a, b) => {
			const ia = order.indexOf(a);
			const ib = order.indexOf(b);
			return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
		});
	});
	const grouped = $derived(outcomeKeys.length >= 2);

	let chosen = $state('');
	const selected = $derived(
		chosen && outcomeKeys.includes(chosen)
			? chosen
			: outcomeKeys.includes(primaryOutcome)
				? primaryOutcome
				: (outcomeKeys[0] ?? '')
	);
	const selectedDef = $derived(defByKey.get(selected));

	const standaloneSeries = $derived(
		section.series.filter((s) => !grouped || outcomeOf(s.key) === null)
	);
	const standaloneTables = $derived(
		section.tables.filter((t) => !grouped || outcomeOf(t.key) === null)
	);
	const outcomeSeries = $derived(
		grouped ? section.series.filter((s) => outcomeOf(s.key) === selected) : []
	);
	const outcomeTables = $derived(
		grouped ? section.tables.filter((t) => outcomeOf(t.key) === selected) : []
	);

	const outcomeItems = $derived(
		outcomeKeys.map((k) => {
			const d = defByKey.get(k);
			return {
				value: k,
				label: `${d?.label ?? k}${k === primaryOutcome ? ' (primary)' : ''}`,
				description: [d?.family, d?.unit].filter(Boolean).join(' · ')
			};
		})
	);

	/** A group × severity table (static issues by severity) as grouped bars. */
	function severityChart(table: ReportTable) {
		const keys = table.columns.map((c) => c.key);
		if (!SEVERITIES.every((s) => keys.includes(s))) return null;
		const x = table.columns[0]?.key;
		if (!x) return null;
		const data = table.rows
			.filter((r) => typeof r[x] === 'string')
			.map((r) => {
				const out: Record<string, string | number> = { [x]: String(r[x]) };
				for (const s of SEVERITIES) if (typeof r[s] === 'number') out[s] = r[s] as number;
				return out;
			});
		return {
			x,
			data,
			series: SEVERITIES.map((s) => ({
				key: s,
				label: s.charAt(0).toUpperCase() + s.slice(1),
				color: severityChartColors[s] ?? 'var(--chart-2)'
			}))
		};
	}
</script>

<div class="space-y-6">
	{#if section.notes.length > 0}
		<ul class="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
			{#each section.notes as note, i (i)}<li>{note}</li>{/each}
		</ul>
	{/if}

	{#each standaloneSeries as series (series.key)}
		<ReportSeriesView {series} {prov} tables={section.tables} {outcomeLabel} {ciLevel} />
	{/each}

	{#if grouped}
		<div class="space-y-4 rounded-md border border-border p-3 sm:p-4">
			<div class="flex flex-wrap items-end justify-between gap-3">
				<div class="w-full max-w-md space-y-1.5">
					<Label for="outcome-{section.key}">Outcome</Label>
					<Select.Simple
						id="outcome-{section.key}"
						value={selected}
						items={outcomeItems}
						onValueChange={(v) => (chosen = v)}
					/>
				</div>
				<span class="text-xs text-muted-foreground">{outcomeKeys.length} outcomes</span>
			</div>
			{#if selectedDef}
				<p class="text-xs text-muted-foreground">
					{selectedDef.description}
					{#if selectedDef.unit}· Unit: {selectedDef.unit}{/if}
					{#if selectedDef.higher_is_better !== null}· {selectedDef.higher_is_better
							? 'higher'
							: 'lower'} is better{/if}
					· {selectedDef.family}{selectedDef.primary ? ' (primary)' : ''}
					{#if selectedDef.requires.length}· requires {selectedDef.requires.join(', ')}{/if}
				</p>
			{/if}
			{#each outcomeSeries as series (series.key)}
				<ReportSeriesView {series} {prov} tables={section.tables} {outcomeLabel} {ciLevel} />
			{/each}
			{#each outcomeTables as table (table.key)}
				<ReportTableView {table} {prov} />
			{/each}
			{#if outcomeSeries.length === 0 && outcomeTables.length === 0}
				<p class="text-sm text-muted-foreground">
					Nothing reported for this outcome in this section.
				</p>
			{/if}
		</div>
	{/if}

	{#each standaloneTables as table (table.key)}
		{@const chart = severityChart(table)}
		{#if chart && chart.data.length > 0}
			<GroupedBarChart
				data={chart.data}
				x={chart.x}
				series={chart.series}
				label="{table.title} (grouped bars)"
			/>
		{/if}
		<ReportTableView {table} {prov} />
	{/each}
</div>
