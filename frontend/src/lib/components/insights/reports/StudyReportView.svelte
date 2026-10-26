<script lang="ts">
	/**
	 * Typed view of a `study_report` (REPORT_VERSION 2): validity threats
	 * first, then the analysis plan the issue tables were read under and its
	 * coverage (jobs outside the plan are listed, never counted as clean),
	 * then the paper sections. Sections without a dedicated layout render
	 * through `GenericData` (null stays "—", never 0). AI consensus columns
	 * are separate from the static counts in every table.
	 */
	import type { StudyReportData } from '$lib/api/reports';
	import CoverageNotice from '../CoverageNotice.svelte';
	import PlanHash from '../PlanHash.svelte';
	import { humanize } from '../format';
	import ReportSection from './ReportSection.svelte';
	import GenericData from './GenericData.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import { Badge } from '$lib/components/ui/badge';

	interface Props {
		data: StudyReportData;
	}

	let { data }: Props = $props();

	const plan = $derived(data.protocol?.analysis_plan ?? null);
	const warnings = $derived((data.validity ?? []).filter((v) => v.level === 'warning'));
	const notes = $derived((data.validity ?? []).filter((v) => v.level !== 'warning'));
	const coverage = $derived(data.issues?.coverage ?? null);
	const issuesRest = $derived.by(() => {
		const { coverage: _c, plan_hash: _p, ...rest } = data.issues ?? {};
		return rest;
	});
	const protocolRest = $derived.by(() => {
		const { analysis_plan: _a, tools: _t, ...rest } = data.protocol ?? { report_version: 2 };
		return rest;
	});

	/** Sections rendered generically, in paper order. */
	const SECTIONS: { key: string; title: string; description?: string }[] = [
		{
			key: 'funnel',
			title: 'Outcome funnel',
			description: 'Generated → analysed → statically measured → evaluated → deployed → correct.'
		},
		{
			key: 'model_outcomes',
			title: 'Model outcomes',
			description:
				'Plan metrics per model. Static outcomes use only apps with a static measurement, divided by measured SLOC.'
		},
		{
			key: 'planned_comparisons',
			title: 'Planned comparisons',
			description: "Against the experiment's pre-registered analysis plan."
		},
		{ key: 'ranking', title: 'Ranking' },
		{
			key: 'security_bounds',
			title: 'Security bounds',
			description: 'Apps without an available static measurement are unknown, not "no issues".'
		},
		{ key: 'code_metrics', title: 'Code metrics' },
		{ key: 'correlations', title: 'Correlations' },
		{ key: 'healing', title: 'Build healing' },
		{ key: 'accounting', title: 'Accounting' },
		{ key: 'tool_inventory', title: 'Tool inventory' },
		{ key: 'definitions', title: 'Definitions' },
		{ key: 'metric_definitions', title: 'Metric definitions' }
	];
</script>

<div class="space-y-6">
	{#if warnings.length > 0}
		<CoverageNotice
			title="{warnings.length} threat{warnings.length === 1 ? '' : 's'} to validity"
			items={warnings.map((w) => `${w.message} (${w.section})`)}
		/>
	{/if}
	{#if notes.length > 0}
		<CoverageNotice
			tone="info"
			title="Notes"
			items={notes.map((w) => `${w.message} (${w.section})`)}
		/>
	{/if}

	<ReportSection
		id="analysis-plan"
		title="Analysis plan"
		description="The frozen plan the issue tables were read under. Runs are comparable only under one plan."
	>
		{#if plan}
			<dl class="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
				<div class="min-w-0">
					<dt class="section-label">Plan</dt>
					<dd class="mt-0.5">
						{#if plan.plan_hash}<PlanHash hash={plan.plan_hash} />{:else}<NaValue
								reason="No job of this report was measured under any plan"
							/>{/if}
					</dd>
				</div>
				<div class="min-w-0">
					<dt class="section-label">Plan rule</dt>
					<dd class="mt-0.5">{plan.plan_rule ? humanize(plan.plan_rule) : '—'}</dd>
				</div>
				<div class="min-w-0">
					<dt class="section-label">Analyzer image</dt>
					<dd class="mt-0.5 font-mono break-all">{plan.analyzer_image ?? '—'}</dd>
				</div>
				<div class="min-w-0">
					<dt class="section-label">AI reviewer</dt>
					<dd class="mt-0.5">
						{#if plan.reviewer}
							<span class="font-mono">{plan.reviewer.model ?? '—'}</span>
							{#if plan.reviewer.samples != null}· {plan.reviewer.samples} samples{/if}
						{:else}
							off
						{/if}
					</dd>
				</div>
			</dl>
			{#if plan.tools.length > 0}
				<div class="mt-4 flex flex-wrap gap-1.5">
					{#each plan.tools as tool, i (tool.slug ?? i)}
						<Badge variant="outline" class="gap-1 font-mono text-[11px]">
							{tool.slug}{#if tool.version}<span class="text-muted-foreground">{tool.version}</span
								>{/if}{#if tool.required}<span class="text-muted-foreground">· required</span>{/if}
						</Badge>
					{/each}
				</div>
			{/if}
		{:else}
			<p class="text-xs text-muted-foreground">The report does not record an analysis plan.</p>
		{/if}
	</ReportSection>

	{#if coverage}
		<ReportSection
			id="coverage"
			title="Analysis coverage"
			description="Jobs not measured under the plan, with the reason. They are left out of every static number."
		>
			<GenericData value={coverage} />
		</ReportSection>
	{/if}

	{#each SECTIONS as section (section.key)}
		{@const value = data[section.key]}
		{#if value != null && !(Array.isArray(value) && value.length === 0)}
			<ReportSection id={section.key} title={section.title} description={section.description}>
				<GenericData {value} />
			</ReportSection>
		{/if}
	{/each}

	{#if Object.keys(issuesRest).length > 0}
		<ReportSection
			id="issues"
			title="Deduplicated issues"
			description="Counted static issues (engine issue keys, one per cluster); AI consensus is reported apart and never added to static counts."
		>
			<GenericData value={issuesRest} />
		</ReportSection>
	{/if}

	{#if Array.isArray(data.per_app) && data.per_app.length > 0}
		<ReportSection
			id="per-app"
			title="Per app"
			description="One row per job. Empty analysis columns mean the job was not measured under the plan (see analysis status)."
		>
			<GenericData value={data.per_app} />
		</ReportSection>
	{/if}

	<ReportSection id="protocol" title="Protocol">
		<GenericData value={protocolRest} />
	</ReportSection>
</div>
