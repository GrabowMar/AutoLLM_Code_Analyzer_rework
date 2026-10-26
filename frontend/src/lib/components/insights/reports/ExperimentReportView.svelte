<script lang="ts">
	/** Typed view of an `experiment_report` (schema v2). */
	import type { ExperimentReportData } from '$lib/api/reports';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import CoverageFunnel from '../CoverageFunnel.svelte';
	import CoverageNotice from '../CoverageNotice.svelte';
	import { funnelGapCount, funnelGapLines, funnelTotal } from '../coverage';
	import ReportSection from './ReportSection.svelte';
	import GroupComparisonTable, { type ComparisonGroup } from './GroupComparisonTable.svelte';
	import GroupSeverityTable from './GroupSeverityTable.svelte';
	import GroupDetailsList from './GroupDetailsList.svelte';
	import DeltaTable from './DeltaTable.svelte';
	import ProvenancePanel from './ProvenancePanel.svelte';

	interface Props {
		data: ExperimentReportData;
	}

	let { data }: Props = $props();

	const coverage = $derived(data.analysis_coverage);
	const gaps = $derived(funnelGapCount(coverage));
	const groups = $derived<ComparisonGroup[]>(
		data.conditions.map((c) => ({
			key: String(c.condition_id),
			label: c.label,
			sublabel: [c.model_id, c.bundle_key].filter(Boolean).join(' · '),
			href: `/models/${encodeURIComponent(c.model_id)}`,
			row: c
		}))
	);
</script>

<div class="space-y-6">
	<ReportSection id="experiment" title={data.experiment.name}>
		<div class="space-y-2 text-sm">
			<div class="flex flex-wrap items-center gap-2 text-xs">
				<StatusBadge status={data.experiment.status} />
				<span class="text-muted-foreground"
					>{data.total_conditions} conditions · {data.experiment.repeats} repeats each</span
				>
				<a
					href="/experiments/{data.experiment.id}"
					class="text-primary underline-offset-2 hover:underline">Open experiment</a
				>
			</div>
			{#if data.experiment.hypothesis}
				<p><span class="text-muted-foreground">Hypothesis:</span> {data.experiment.hypothesis}</p>
			{/if}
		</div>
	</ReportSection>

	{#if gaps > 0}
		<CoverageNotice
			title="{gaps} of {funnelTotal(coverage)} completed jobs are not in the analysis numbers"
			items={funnelGapLines(coverage)}
		/>
	{/if}

	<ReportSection
		id="conditions"
		title="Conditions"
		description="Per-trial means ± sd under one analysis plan. Static measurements first; AI consensus (opinions) apart."
		flush
	>
		{#if groups.length === 0}
			<p class="px-4 text-xs text-muted-foreground">The experiment has no conditions.</p>
		{:else}
			<div class="px-4 md:px-0"><GroupComparisonTable {groups} groupLabel="Condition" /></div>
		{/if}
	</ReportSection>

	{#if groups.length > 0}
		<ReportSection id="severity" title="Static issues by severity" flush>
			<div class="px-4"><GroupSeverityTable {groups} groupLabel="Condition" /></div>
		</ReportSection>
	{/if}

	<ReportSection
		id="comparisons"
		title="Pairwise differences"
		description="Descriptive A/B differences of the condition means on the headline metrics."
	>
		<DeltaTable comparisons={data.comparisons} />
	</ReportSection>

	{#if groups.length > 0}
		<ReportSection id="details" title="Condition details">
			<GroupDetailsList {groups} />
		</ReportSection>
	{/if}

	<ReportSection id="coverage" title="Analysis coverage">
		<CoverageFunnel {coverage} />
	</ReportSection>

	<ReportSection id="provenance" title="Provenance">
		<ProvenancePanel provenance={data.provenance} />
	</ReportSection>
</div>
