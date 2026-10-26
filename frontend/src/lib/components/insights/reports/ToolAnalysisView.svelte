<script lang="ts">
	/** Typed view of a `tool_analysis` report (schema v2): tool behaviour, not code quality. */
	import type { ToolAnalysisReportData } from '$lib/api/reports';
	import Kpi from '../Kpi.svelte';
	import CoverageNotice from '../CoverageNotice.svelte';
	import { fmtInt } from '../format';
	import ReportSection from './ReportSection.svelte';
	import ToolBehaviourTable from './ToolBehaviourTable.svelte';
	import MetricsByTool from './MetricsByTool.svelte';
	import ProvenancePanel from './ProvenancePanel.svelte';

	interface Props {
		data: ToolAnalysisReportData;
		/** Rendered inside another report: no provenance section of its own. */
		embedded?: boolean;
	}

	let { data, embedded = false }: Props = $props();

	const noData = $derived(
		data.tools.reduce((sum, t) => sum + t.failed + t.timed_out + (t.statuses.skipped ?? 0), 0)
	);
	const scans = $derived(data.tools.reduce((sum, t) => sum + t.scans, 0));
	const hasMetrics = $derived(Object.keys(data.metrics_by_tool ?? {}).length > 0);
</script>

<div class="space-y-6">
	<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
		<Kpi label="Analysis runs" value={fmtInt(data.total_tasks)} sub="non-canary" />
		<Kpi
			label="Tool executions"
			value={fmtInt(data.total_executions)}
			sub="{data.tools.length} tools"
		/>
		<Kpi label="Succeeded scans" value={fmtInt(scans)} sub="{noData} produced no data" />
		<Kpi
			label="Findings"
			value={scans > 0 ? fmtInt(data.total_findings) : null}
			reason="No succeeded scan: no findings could be produced"
			sub="from succeeded scans"
		/>
	</div>

	{#if noData > 0}
		<CoverageNotice
			title="{noData} tool execution{noData === 1 ? '' : 's'} failed, timed out or were skipped"
			items={[
				'They produced no data. Their runs are partial or failed and never count as clean scans.',
				'Success rate counts only executions that actually ran on files (not applicable / skipped / cancelled excluded).'
			]}
		/>
	{/if}

	<ReportSection
		id="tools"
		title={data.tool_filter ? `Tool: ${data.tool_filter}` : 'Tools'}
		description="Every non-canary execution (tool behaviour across runs, not one measurement per job)."
		flush
	>
		{#if data.tools.length === 0}
			<p class="px-4 text-xs text-muted-foreground">No tool execution in scope.</p>
		{:else}
			<div class="px-4 md:px-0"><ToolBehaviourTable tools={data.tools} /></div>
		{/if}
	</ReportSection>

	{#if hasMetrics}
		<ReportSection
			id="metrics"
			title="Tool metrics"
			description="Aggregated over succeeded executions only."
		>
			<MetricsByTool metrics={data.metrics_by_tool} />
		</ReportSection>
	{/if}

	{#if !embedded && data.provenance}
		<ReportSection id="provenance" title="Provenance">
			<ProvenancePanel provenance={data.provenance} />
		</ReportSection>
	{/if}
</div>
