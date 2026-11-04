<script lang="ts">
	/**
	 * RunHeader — the sample page header (spec 10.4): kind, outcome `StatusBadge`, usable, model, factor labels,
	 * cost, wall time and the `beyond_plan` badge, inside the app's `PageHeader`.
	 *
	 * Props: `run`, `back` (defaults to the study or the Applications list), `actions` and `nav` snippets
	 * (passed to `PageHeader`; the page puts its tabs in `nav`), `stream` (SSE state for the live dot).
	 */
	import type { Snippet } from 'svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import type { RunOut } from '$lib/api/v3/types';
	import { fmtRate, fmtSeconds, fmtUsd, kindLabel, OUTCOME_TONES, outcomeLabel, shortSha } from './format';

	interface Props {
		run: RunOut;
		back?: { href: string; label: string };
		actions?: Snippet;
		nav?: Snippet;
		stream?: string;
	}

	let { run, back, actions, nav, stream = 'idle' }: Props = $props();

	const labels = $derived(Object.entries(run.labels ?? {}).filter(([key]) => key !== 'model'));
	const title = $derived(run.labels?.task ?? run.labels?.unit ?? `${kindLabel(run.kind)} ${shortSha(run.id, 8)}`);
	const backLink = $derived(
		back ??
			(run.study.study_id
				? { href: `/experiments/${run.study.study_id}?tab=data`, label: run.study.study_name ?? 'Experiment' }
				: { href: '/applications', label: 'Applications' }),
	);
	const live = $derived(run.status === 'running' || run.status === 'pending');
</script>

<PageHeader
	{title}
	documentTitle={`Sample ${shortSha(run.id, 8)}`}
	description={`${kindLabel(run.kind)} · ${run.protocol}${run.study.study_name ? ` · experiment ${run.study.study_name}` : ''}`}
	back={backLink}
	{actions}
	{nav}
>
	{#snippet meta()}
		<StatusBadge status={run.status} />
		{#if run.outcome}
			<StatusBadge status={run.outcome} tones={OUTCOME_TONES} label={outcomeLabel(run.outcome)} />
		{/if}
		{#if run.usable === true}
			<Badge variant="outline" title="Model-attributable outcome: analysed as data">usable</Badge>
		{:else if run.usable === false}
			<Badge variant="secondary" title="Infra error or excluded: not analysed as data">not usable</Badge>
		{/if}
		{#if run.beyond_plan}
			<Badge
				variant="secondary"
				title="Created on a cell that already had a usable run: excluded from results and analysis exports"
			>
				beyond plan
			</Badge>
		{/if}
		{#if run.validity?.truncated}
			<Badge variant="secondary" title="Truncated: included in the default (ITT) analysis, excluded in the sensitivity analysis">
				truncated
			</Badge>
		{/if}
		{#if live}
			<!-- A queued or running run says where it is; the page follows it (SSE, polling when the stream is down). -->
			<span
				class="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground"
				role="status"
				title={stream === 'open' ? 'Live: updates as the run progresses' : 'Refreshing every few seconds'}
			>
				<span class="status-dot-live h-1.5 w-1.5 rounded-full bg-info" aria-hidden="true"></span>
				{run.status === 'pending' ? 'queued, waiting for the executor' : run.phase ? run.phase.replace(/_/g, ' ') : 'running'}{stream === 'open' ? ' · live' : ''}
			</span>
		{/if}
	{/snippet}

	<div class="header-stats">
		<span class="header-stat"><b class="font-mono">{run.model ?? '—'}</b> model</span>
		{#each labels as [key, value] (key)}
			<span class="header-stat" title={key}><b>{value}</b> {key.replace(/_/g, ' ')}</span>
		{/each}
		<span class="header-stat"><b>{fmtUsd(run.cost_usd)}</b> cost</span>
		<span class="header-stat"><b>{fmtSeconds(run.wall_s)}</b> wall time</span>
		{#if run.kind === 'app_agentic' || run.kind === 'reference'}
			<span class="header-stat" title="Agent checks only; infra and flaky checks excluded"
				><b>{fmtRate(run.agent_pass_rate)}</b> pass rate</span
			>
		{/if}
		{#if run.attempts.length > 1}
			<span class="header-stat"><b>{run.attempts.length}</b> attempts</span>
		{/if}
		<span class="header-stat font-mono" title={run.id}>{shortSha(run.id, 8)}</span>
	</div>
</PageHeader>
