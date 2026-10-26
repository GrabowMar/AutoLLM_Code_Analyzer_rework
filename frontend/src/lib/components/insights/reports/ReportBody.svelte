<script lang="ts">
	/**
	 * Renders a completed platform report: a typed view per report type for
	 * schema v2 data, the generic structured renderer otherwise (legacy v1
	 * reports, unknown types).
	 */
	import { reportDataV2, type ReportDetail } from '$lib/api/reports';
	import CoverageNotice from '../CoverageNotice.svelte';
	import ReportSection from './ReportSection.svelte';
	import GenericData from './GenericData.svelte';
	import ModelAnalysisView from './ModelAnalysisView.svelte';
	import TemplateComparisonView from './TemplateComparisonView.svelte';
	import ExperimentReportView from './ExperimentReportView.svelte';
	import ToolAnalysisView from './ToolAnalysisView.svelte';
	import GenerationAnalyticsView from './GenerationAnalyticsView.svelte';
	import ComprehensiveView from './ComprehensiveView.svelte';

	interface Props {
		report: Pick<ReportDetail, 'report_type' | 'report_data'>;
	}

	let { report }: Props = $props();

	const typed = $derived(reportDataV2(report));
	const legacy = $derived(!typed && report.report_data?.schema_version !== 2);
</script>

{#if typed?.type === 'model_analysis'}
	<ModelAnalysisView data={typed.data} />
{:else if typed?.type === 'template_comparison'}
	<TemplateComparisonView data={typed.data} />
{:else if typed?.type === 'experiment_report'}
	<ExperimentReportView data={typed.data} />
{:else if typed?.type === 'tool_analysis'}
	<ToolAnalysisView data={typed.data} />
{:else if typed?.type === 'generation_analytics'}
	<GenerationAnalyticsView data={typed.data} />
{:else if typed?.type === 'comprehensive'}
	<ComprehensiveView data={typed.data} />
{:else}
	<div class="space-y-6">
		{#if legacy}
			<CoverageNotice
				title="Legacy report format"
				items={[
					'This report predates analysis v2: its numbers were not taken from one analysis plan and may mix tool sets or count failed tools as clean.',
					'Regenerate it to get comparability, coverage and provenance.'
				]}
			/>
		{/if}
		<ReportSection title="Report data">
			{#if report.report_data && Object.keys(report.report_data).length > 0}
				<GenericData value={report.report_data} />
			{:else}
				<p class="text-xs text-muted-foreground">The report has no data.</p>
			{/if}
		</ReportSection>
	</div>
{/if}
