<script lang="ts">
	/**
	 * The measured analysis of one group of jobs (a model, a condition, a
	 * template row): severity counts (static vs AI), per-trial statistics and
	 * the measurement outcomes. Null totals render "—" with the reason.
	 */
	import type { ReportGroupRow } from '$lib/api/reports';
	import SeverityBreakdown from '../SeverityBreakdown.svelte';
	import CoverageNotice from '../CoverageNotice.svelte';
	import StatTable, { type StatRow } from './StatTable.svelte';
	import { exclusionLabel, outcomeLabel } from '../format';

	interface Props {
		row: ReportGroupRow;
	}

	let { row }: Props = $props();

	const stats = $derived(row.stats);
	const noRuns = $derived(stats.analyzed === 0);
	const staticReason = $derived(
		noRuns
			? 'No trial has a canonical measured run under the plan'
			: 'Static measurement unavailable in every measured run (a required tool failed) — missing, not zero'
	);
	const aiReason = $derived(
		noRuns
			? 'No trial has a canonical measured run under the plan'
			: 'No successful AI review in the measured runs'
	);

	const trialRows = $derived<StatRow[]>([
		{ label: 'Static findings', stat: stats.findings_total, digits: 2, hint: 'counted, all tools' },
		{
			label: 'Static issues',
			stat: stats.issues_total,
			digits: 2,
			hint: 'deduplicated across tools'
		},
		{ label: 'Critical + high findings', stat: stats.critical_high, digits: 2 },
		{ label: 'Severity-weighted issues', stat: stats.weighted_score, digits: 2 },
		{ label: 'Weighted issues / KLOC', stat: stats.weighted_per_kloc, digits: 2 },
		{ label: 'Functional pass rate', stat: stats.functional?.pass_rate, share: true },
		{ label: 'Generation cost (USD)', stat: stats.cost, digits: 4 },
		{ label: 'Generation duration (s)', stat: stats.duration, digits: 1 },
		{ label: 'Tokens', stat: stats.tokens, digits: 0 },
		{ label: 'AI consensus findings', stat: stats.ai_findings_total, digits: 2, ai: true },
		{ label: 'AI critical + high', stat: stats.ai_critical_high, digits: 2, ai: true }
	]);

	const outcomeRows = $derived<StatRow[]>(
		Object.entries(stats.outcomes ?? {}).map(([key, stat]) => {
			const meta = outcomeLabel(key);
			return { label: meta.label, stat, digits: meta.digits, share: meta.share, ai: meta.ai };
		})
	);

	const excludedReasons = $derived(Object.entries(stats.excluded_reasons ?? {}));
</script>

<div class="space-y-5">
	{#if stats.mixed_prompt_versions}
		<CoverageNotice
			title="Trials come from {stats.prompt_hashes.length} prompt versions"
			items={[
				'The statistics blend results from different templates; filter by prompt or bundle for a like-for-like comparison.'
			]}
		/>
	{/if}

	<section class="space-y-2">
		<h3 class="section-label">
			Findings by severity · {stats.analyzed} measured trial{stats.analyzed === 1 ? '' : 's'}
		</h3>
		<SeverityBreakdown
			rows={[
				{
					label: 'Static findings',
					counts: row.findings,
					reason: staticReason,
					hint: 'counted, all tools'
				},
				{
					label: 'Static issues',
					counts: row.issues,
					reason: staticReason,
					hint: 'deduplicated across tools'
				},
				{ label: 'AI consensus', counts: row.ai_findings, reason: aiReason, ai: true }
			]}
		/>
	</section>

	<section class="space-y-2">
		<h3 class="section-label">Per-trial statistics</h3>
		<p class="text-xs text-muted-foreground">
			{stats.trials} eligible trial{stats.trials === 1 ? '' : 's'} · {stats.analyzed} with a measured
			run{#if stats.excluded}{' · '}{stats.excluded} completed job{stats.excluded === 1 ? '' : 's'} excluded
				({excludedReasons.map(([k, n]) => `${exclusionLabel(k)} ×${n}`).join(', ')}){/if}. "Missing"
			counts trials without the value — never folded in as zeros.
		</p>
		<StatTable rows={trialRows} caption="Per-trial statistics" />
	</section>

	{#if outcomeRows.length > 0}
		<section class="space-y-2">
			<h3 class="section-label">Measurement outcomes</h3>
			<StatTable rows={outcomeRows} caption="Measurement outcomes" />
		</section>
	{/if}
</div>
