<script lang="ts">
	/** Typed view of a `template_comparison` report (schema v2). */
	import type { TemplateComparisonReportData } from '$lib/api/reports';
	import CoverageFunnel from '../CoverageFunnel.svelte';
	import CoverageNotice from '../CoverageNotice.svelte';
	import SensitivityTable from '../SensitivityTable.svelte';
	import { funnelGapCount, funnelGapLines, funnelTotal } from '../coverage';
	import ReportSection from './ReportSection.svelte';
	import GroupComparisonTable, { type ComparisonGroup } from './GroupComparisonTable.svelte';
	import GroupSeverityTable from './GroupSeverityTable.svelte';
	import GroupDetailsList from './GroupDetailsList.svelte';
	import ProvenancePanel from './ProvenancePanel.svelte';

	interface Props {
		data: TemplateComparisonReportData;
	}

	let { data }: Props = $props();

	const coverage = $derived(data.analysis_coverage);
	const gaps = $derived(funnelGapCount(coverage));
	const groups = $derived<ComparisonGroup[]>(
		data.models.map((m) => ({
			key: `${m.model_id}::${m.bundle_key}`,
			label: m.model_name || m.model_id,
			sublabel: [m.provider, m.bundle_key].filter(Boolean).join(' · '),
			href: `/models/${encodeURIComponent(m.model_id)}`,
			row: m
		}))
	);
	const names = $derived(
		Object.fromEntries(data.models.map((m) => [m.model_id, m.model_name || m.model_id]))
	);
</script>

<div class="space-y-6">
	<ReportSection id="template" title={data.template.name}>
		<p class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
			<span class="mono-tag">{data.template.slug}</span>
			<span>{data.template.category}</span>
			<span>· {data.total_models} model / bundle row{data.total_models === 1 ? '' : 's'}</span>
		</p>
	</ReportSection>

	{#if data.mixed_bundle_versions}
		<CoverageNotice
			title="Rows come from {data.bundle_keys.length} prompt bundle versions"
			items={[
				`Bundles: ${data.bundle_keys.join(', ')}.`,
				'Rows with different bundles are not like-for-like; regenerate the report for one bundle to compare models directly.'
			]}
		/>
	{/if}
	{#if gaps > 0}
		<CoverageNotice
			title="{gaps} of {funnelTotal(coverage)} completed jobs are not in the analysis numbers"
			items={funnelGapLines(coverage)}
		/>
	{/if}

	<ReportSection
		id="models"
		title="Models on this template"
		description="Per-trial means ± sd under one analysis plan. Static measurements first; AI consensus (opinions) apart."
		flush
	>
		{#if groups.length === 0}
			<p class="px-4 text-xs text-muted-foreground">No generation job for this template.</p>
		{:else}
			<div class="px-4 md:px-0"><GroupComparisonTable {groups} groupLabel="Model" /></div>
		{/if}
	</ReportSection>

	{#if groups.length > 0}
		<ReportSection id="severity" title="Static issues by severity" flush>
			<div class="px-4"><GroupSeverityTable {groups} groupLabel="Model" /></div>
		</ReportSection>
	{/if}

	<ReportSection
		id="sensitivity"
		title="Rank sensitivity"
		description="Does the model order on this template depend on the severity weights?"
	>
		<SensitivityTable sensitivity={data.sensitivity} {names} />
	</ReportSection>

	{#if groups.length > 0}
		<ReportSection id="details" title="Model details">
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
