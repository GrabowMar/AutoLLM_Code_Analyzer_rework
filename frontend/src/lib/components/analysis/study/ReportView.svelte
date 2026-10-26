<script lang="ts">
	/**
	 * ReportView — generic renderer of the typed post-run study report:
	 * integrity header (report / protocol / plan hashes), headline figures,
	 * a section index, then every section with every series (chart) and
	 * every typed table. Data quality comes before results, as the report
	 * orders it. Numbers link back to runs and applications (provenance).
	 *
	 * Props: `report: StudyReport`, `verified` (stored report matches its
	 * hash), `stale` (a run changed after generation), `studyId`.
	 */
	import type { StudyReport } from '$lib/api/analysis';
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import NaValue from '../NaValue.svelte';
	import Hint from '../Hint.svelte';
	import ReportSectionView from './report/ReportSectionView.svelte';
	import { buildProvenance } from './report/provenance';
	import { factorList, fmtDateTime, fmtFloat, fmtP, fmtUsd, humanize, shortHash } from './format';

	interface Props {
		report: StudyReport;
		verified?: boolean | null;
		stale?: boolean | null;
		studyId: string;
	}

	let { report, verified = null, stale = null, studyId }: Props = $props();

	const prov = $derived(buildProvenance(report));
	const summary = $derived(report.summary);
	const omnibus = $derived(summary.primary_omnibus);
	const definitions = $derived(report.settings.outcome_definitions ?? []);
	const integrity = $derived(report.protocol_integrity);
	const deviationsTotal = $derived(
		summary.deviations.major + summary.deviations.warn + summary.deviations.info
	);

	const EFFECT_NAMES: Record<string, string> = {
		kendalls_w: "Kendall's W",
		epsilon_squared: 'ε²'
	};
</script>

<div class="space-y-6">
	<!-- Integrity -->
	<div
		class="flex flex-wrap items-start gap-x-6 gap-y-2 rounded-md border border-border bg-card px-4 py-3 text-xs"
	>
		<div class="flex items-center gap-1.5">
			{#if verified === false || integrity.match === false}
				<ShieldAlert class="h-4 w-4 text-danger-fg" aria-hidden="true" />
			{:else}
				<ShieldCheck class="h-4 w-4 text-success-fg" aria-hidden="true" />
			{/if}
			<span class="text-muted-foreground">Report</span>
			<code class="font-mono" title={report.report_hash}>#{shortHash(report.report_hash)}</code>
			{#if verified === true}<StatusBadge status="passed" label="hash verified" />{/if}
			{#if verified === false}<StatusBadge status="failed" label="hash mismatch" />{/if}
		</div>
		<div class="flex items-center gap-1.5">
			<span class="text-muted-foreground">Protocol</span>
			<code class="font-mono" title={report.protocol_hash ?? ''}
				>#{shortHash(report.protocol_hash)}</code
			>
			{#if integrity.match === true}<StatusBadge status="passed" label="matches lock" />{/if}
			{#if integrity.match === false}<StatusBadge status="failed" label="changed since lock" />{/if}
		</div>
		<div class="flex items-center gap-1.5">
			<span class="text-muted-foreground">Plan</span>
			<code class="font-mono" title={report.plan_hash ?? ''}>#{shortHash(report.plan_hash)}</code>
		</div>
		<div class="flex items-center gap-1.5 text-muted-foreground">
			Generated {fmtDateTime(report.generated_at)} · schema v{report.schema_version}
			·
			<a
				class="text-foreground hover:text-primary hover:underline"
				href="/analysis/studies/{studyId}?tab=runs"
			>
				{report.inputs.runs} runs
			</a>
			· {report.inputs.findings_read.toLocaleString()} findings read
		</div>
	</div>

	{#if stale}
		<div
			class="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm"
			role="status"
		>
			<TriangleAlert class="mt-0.5 h-4 w-4 shrink-0 text-warning-fg" aria-hidden="true" />
			<p>
				<span class="font-medium">Out of date.</span>
				A study run changed after this report was generated (e.g. a retry finished). Regenerate it before
				citing numbers.
			</p>
		</div>
	{/if}

	<!-- Headline -->
	<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
		<div class="kpi-card">
			<span class="kpi-label">Targets analysed</span>
			<span class="kpi-value">{summary.analysed}</span>
			<span class="text-[11px] text-muted-foreground">
				{summary.targets_planned ?? '—'} planned · {summary.targets_eligible ?? '—'} eligible · {summary.targets_with_run}
				with a run
			</span>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">Complete runs</span>
			<span class="kpi-value">{summary.complete}</span>
			<span class="text-[11px] text-muted-foreground">
				{summary.analysed - summary.complete} with a required tool not OK (dependent outcomes NA)
			</span>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">Primary: {summary.primary_label}</span>
			<span class="kpi-value">
				{#if omnibus.p_adj === null && omnibus.p === null}
					<NaValue reason="No omnibus test could be computed (too few groups or observations)." />
				{:else}
					<span class="text-2xl">p {fmtP(omnibus.p_adj ?? omnibus.p)}</span>
					{#if omnibus.p_adj !== null}<span class="ml-1 text-xs font-normal text-muted-foreground"
							>adjusted</span
						>{/if}
				{/if}
			</span>
			<span class="text-[11px] text-muted-foreground">
				{omnibus.test ? humanize(omnibus.test) : 'no test'}
				{#if omnibus.statistic !== null}· stat {fmtFloat(omnibus.statistic)}{/if}
				{#if omnibus.df !== null}· df {omnibus.df}{/if}
				{#if omnibus.effect !== null}· {EFFECT_NAMES[omnibus.effect_name ?? ''] ??
						omnibus.effect_name}
					{fmtFloat(omnibus.effect)}{/if}
				{#if omnibus.n !== null}· n {omnibus.n}{/if}
			</span>
		</div>
		<a class="kpi-card" href="#section-deviations">
			<span class="kpi-label">Deviations</span>
			<span
				class="kpi-value"
				class:text-danger-fg={summary.deviations.major > 0}
				class:text-warning-fg={summary.deviations.major === 0 && summary.deviations.warn > 0}
			>
				{deviationsTotal}
			</span>
			<span class="text-[11px] text-muted-foreground">
				{summary.deviations.major} major · {summary.deviations.warn} warn · {summary.deviations
					.info} info
			</span>
		</a>
	</div>

	<dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
		<div>
			<dt class="section-label">Groups ({summary.groups.length})</dt>
			<dd class="truncate" title={summary.groups.join(', ')}>
				{factorList(report.settings.group_by)}
			</dd>
		</div>
		<div>
			<dt class="section-label">Design</dt>
			<dd>
				{report.settings.mode}{report.settings.block_by ? ` by ${report.settings.block_by}` : ''}
				{#if report.settings.planned_mode && report.settings.planned_mode !== report.settings.mode}
					<Hint
						underline={false}
						text="The design analysed differs from the protocol (e.g. incomplete blocks after failures)."
					>
						<span class="text-warning-fg">(planned {report.settings.planned_mode})</span>
					</Hint>
				{/if}
			</dd>
		</div>
		<div>
			<dt class="section-label">AI review cost</dt>
			<dd>{fmtUsd(summary.ai_cost_total)}</dd>
		</div>
		<div>
			<dt class="section-label">Generation cost</dt>
			<dd>{fmtUsd(summary.generation_cost_total)}</dd>
		</div>
	</dl>

	<!-- Section index -->
	<nav
		aria-label="Report sections"
		class="sticky top-0 z-20 -mx-1 overflow-x-auto bg-background/95 px-1 py-2 backdrop-blur"
	>
		<ol class="flex w-max gap-1.5 text-xs">
			{#each report.sections as section, i (section.key)}
				<li>
					<a
						href="#section-{section.key}"
						class="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2.5 py-1 text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
					>
						<span class="tabular-nums text-muted-foreground/70">{i + 1}</span>{section.title}
					</a>
				</li>
			{/each}
		</ol>
	</nav>

	{#each report.sections as section, i (section.key)}
		<Card.Root
			id="section-{section.key}"
			class="scroll-mt-16 rise-in"
			style="--stagger-i: {Math.min(i, 8)}"
		>
			<Card.Header>
				<Card.Title class="flex items-baseline gap-2">
					<span class="text-sm tabular-nums text-muted-foreground">{i + 1}</span>
					<h2 class="text-base">{section.title}</h2>
				</Card.Title>
				{#if section.description}<Card.Description>{section.description}</Card.Description>{/if}
			</Card.Header>
			<Card.Content>
				<ReportSectionView
					{section}
					{prov}
					{definitions}
					primaryOutcome={summary.primary_outcome}
					ciLevel={report.settings.ci_level}
				/>
			</Card.Content>
		</Card.Root>
	{/each}
</div>
