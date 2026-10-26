<script lang="ts">
	/** Typed view of a `model_analysis` report (schema v2). */
	import type { ModelAnalysisReportData } from '$lib/api/reports';
	import Kpi from '../Kpi.svelte';
	import CoverageFunnel from '../CoverageFunnel.svelte';
	import CoverageNotice from '../CoverageNotice.svelte';
	import { funnelGapCount, funnelGapLines, funnelTotal } from '../coverage';
	import { fmtInt, fmtShare } from '../format';
	import ReportSection from './ReportSection.svelte';
	import GroupDetail from './GroupDetail.svelte';
	import ToolExecutionTable from './ToolExecutionTable.svelte';
	import MetricsByTool from './MetricsByTool.svelte';
	import LocPerJob from './LocPerJob.svelte';
	import ProvenancePanel from './ProvenancePanel.svelte';

	interface Props {
		data: ModelAnalysisReportData;
	}

	let { data }: Props = $props();

	const coverage = $derived(data.analysis_coverage);
	const gaps = $derived(funnelGapCount(coverage));
	const nothingMeasured = $derived(coverage.measured === 0);
	const staticReason = $derived(
		nothingMeasured
			? 'No job of this model has a complete measured analysis'
			: 'Static measurement unavailable in every measured run (a required tool failed)'
	);
	const hasMetrics = $derived(Object.keys(data.metrics_by_tool ?? {}).length > 0);
</script>

<div class="space-y-6">
	{#if gaps > 0}
		<CoverageNotice
			title="{gaps} of {funnelTotal(coverage)} completed jobs are not in the analysis numbers"
			items={funnelGapLines(coverage)}
		/>
	{/if}

	<div class="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
		<Kpi
			label="Jobs"
			value={fmtInt(data.generation.total_jobs)}
			sub="{data.generation.completed_jobs} completed · {data.generation.failed_jobs} failed"
		/>
		<Kpi
			label="Generation success"
			value={fmtShare(data.generation.success_rate, 0)}
			reason="No job has finished"
			sub="of finished jobs"
		/>
		<Kpi
			label="Measured trials"
			value={fmtInt(data.stats.analyzed)}
			sub="of {data.stats.trials} eligible"
		/>
		<Kpi
			label="Static findings"
			value={fmtInt(data.total_findings)}
			reason={staticReason}
			sub="counted, all tools"
		/>
		<Kpi
			label="Static issues"
			value={fmtInt(data.total_issues)}
			reason={staticReason}
			sub="deduplicated"
		/>
		<Kpi
			label="AI consensus"
			value={fmtInt(data.ai_total)}
			reason={nothingMeasured ? staticReason : 'No successful AI review in the measured runs'}
			sub="opinions"
			muted
		/>
		<Kpi
			label="Measured SLOC"
			value={fmtInt(data.loc.measured_loc)}
			reason="No measured run"
			sub="model-written"
		/>
	</div>

	<ReportSection
		id="analysis"
		title="Measured analysis"
		description="Canonical complete runs of this model's eligible jobs under one analysis plan."
	>
		<GroupDetail row={data} />
	</ReportSection>

	<ReportSection
		id="tools"
		title="Tool executions in the measured runs"
		description="A failed, timed-out or skipped tool produced no data for that run — its zero findings are not a clean result."
	>
		{#if data.tools.length === 0}
			<p class="text-xs text-muted-foreground">No measured run, so no tool executions.</p>
		{:else}
			<div class="space-y-5">
				<ToolExecutionTable tools={data.tools} />
				{#if hasMetrics}
					<div class="space-y-2 border-t pt-4">
						<h3 class="section-label">Tool metrics (succeeded executions)</h3>
						<MetricsByTool metrics={data.metrics_by_tool} />
					</div>
				{/if}
			</div>
		{/if}
	</ReportSection>

	<ReportSection
		id="loc"
		title="Lines of code"
		description="SLOC of the measured runs — the same jobs the findings come from."
	>
		<LocPerJob loc={data.loc} />
	</ReportSection>

	<ReportSection id="coverage" title="Analysis coverage">
		<CoverageFunnel {coverage} />
	</ReportSection>

	<ReportSection id="provenance" title="Provenance">
		<ProvenancePanel provenance={data.provenance} />
	</ReportSection>
</div>
