<script lang="ts">
	/**
	 * Typed view of a `comprehensive` report (schema v2): platform totals,
	 * generation analytics, tool behaviour and rank sensitivity.
	 */
	import type { ComprehensiveReportData } from '$lib/api/reports';
	import Kpi from '../Kpi.svelte';
	import CoverageNotice from '../CoverageNotice.svelte';
	import CoverageFunnel from '../CoverageFunnel.svelte';
	import SensitivityTable from '../SensitivityTable.svelte';
	import { fmtInt, formatTimestamp } from '../format';
	import ReportSection from './ReportSection.svelte';
	import GenerationAnalyticsView from './GenerationAnalyticsView.svelte';
	import ToolAnalysisView from './ToolAnalysisView.svelte';

	interface Props {
		data: ComprehensiveReportData;
	}

	let { data }: Props = $props();

	const p = $derived(data.platform);
	const reason = $derived(
		p.measured_jobs === 0
			? 'No job has a complete measured analysis yet'
			: 'No measured run has an available static measurement'
	);
</script>

<div class="space-y-6">
	<p class="text-xs text-muted-foreground">Generated {formatTimestamp(data.generated_at)}.</p>

	<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
		<Kpi label="Models" value={fmtInt(p.total_models)} />
		<Kpi label="Generation jobs" value={fmtInt(p.total_jobs)} />
		<Kpi label="Analysis runs" value={fmtInt(p.total_tasks)} sub="non-canary" />
		<Kpi
			label="Measured jobs"
			value={fmtInt(p.measured_jobs)}
			sub="{p.plans_in_use} plan{p.plans_in_use === 1 ? '' : 's'} in use"
		/>
		<Kpi label="Static findings" value={fmtInt(p.total_findings)} {reason} sub="counted" />
		<Kpi label="Static issues" value={fmtInt(p.total_issues)} {reason} sub="deduplicated" />
		<Kpi
			label="AI consensus"
			value={fmtInt(p.total_ai_findings)}
			reason="No successful AI review in the measured runs"
			sub="opinions"
			muted
		/>
	</div>

	{#if p.plans_in_use > 1}
		<CoverageNotice
			title="Totals span {p.plans_in_use} analysis plans"
			items={[
				"Platform totals are descriptive: each job's latest complete measurement whatever its plan. Do not compare them across models — use Rankings, a model/template/experiment report or a Study, which hold the plan fixed."
			]}
		/>
	{/if}

	<section id="platform-generation" class="scroll-mt-20 space-y-3">
		<h2 class="font-display text-lg font-semibold">Generation analytics</h2>
		<GenerationAnalyticsView data={data.generation_analytics} />
	</section>

	<section id="platform-tools" class="scroll-mt-20 space-y-3">
		<h2 class="font-display text-lg font-semibold">Tool behaviour</h2>
		<ToolAnalysisView data={data.tool_analysis} embedded />
	</section>

	<ReportSection
		id="sensitivity"
		title="Rank sensitivity"
		description="Stability of the empirical model ranking under alternative severity weights."
	>
		<div class="space-y-5">
			{#if data.rankings_sensitivity.comparability}
				<CoverageFunnel coverage={data.rankings_sensitivity.comparability} />
			{/if}
			<SensitivityTable sensitivity={data.rankings_sensitivity} />
		</div>
	</ReportSection>
</div>
