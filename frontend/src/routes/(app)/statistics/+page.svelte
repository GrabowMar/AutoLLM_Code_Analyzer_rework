<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { tableFrame } from '$lib/components/table';
	import ChartEmpty from '$lib/components/charts/ChartEmpty.svelte';
	import AnalyzerHealthGrid from '$lib/components/dashboard/AnalyzerHealthGrid.svelte';
	import SeverityPanel from '$lib/components/dashboard/SeverityPanel.svelte';
	import AnalysisTrendChart from '$lib/components/insights/AnalysisTrendChart.svelte';
	import CoverageNotice from '$lib/components/insights/CoverageNotice.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import MaybeValue from '$lib/components/insights/MaybeValue.svelte';
	import PlanHash from '$lib/components/insights/PlanHash.svelte';
	import StatusCountsBar from '$lib/components/insights/StatusCountsBar.svelte';
	import {
		fmtInt,
		fmtNum,
		fmtScore,
		fmtSeconds,
		fmtShare,
		fmtUsd
	} from '$lib/components/insights/format';
	import { severityColors } from '$lib/constants/colors';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import {
		getStatisticsAnalyzerHealth,
		getStatisticsDashboard,
		getStatisticsTrends,
		type AnalysisTrends,
		type ModelComparisonRow,
		type StatisticsDashboard,
		type ToolEffectivenessRow,
		type TopFindingRow
	} from '$lib/api/statistics';
	import ChartColumn from '@lucide/svelte/icons/chart-column';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import Activity from '@lucide/svelte/icons/activity';
	import Code from '@lucide/svelte/icons/code';
	import Wrench from '@lucide/svelte/icons/wrench';
	import Trophy from '@lucide/svelte/icons/trophy';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Bot from '@lucide/svelte/icons/bot';

	const LIVE_REFRESH_MS = 15_000;
	const DAY_OPTIONS = ['7', '14', '30', '90'];

	let data = $state<StatisticsDashboard | null>(null);
	let loading = $state(true);
	let refreshing = $state(false);
	let error = $state<string | null>(null);

	let trendDays = $state('7');
	let trends = $state<AnalysisTrends | null>(null);
	let trendsLoading = $state(false);
	let trendsError = $state<string | null>(null);

	let healthRefreshing = $state(false);

	let controller: AbortController | null = null;

	/** First load shows a loading state; later loads keep the current render. */
	async function load(silent = false) {
		controller?.abort();
		const c = new AbortController();
		controller = c;
		if (silent) refreshing = true;
		else loading = !data;
		error = null;
		try {
			const res = await getStatisticsDashboard({ signal: c.signal });
			if (c.signal.aborted) return;
			data = res;
			if (trendDays === String(res.trends.days)) trends = res.trends;
		} catch (e) {
			if (c.signal.aborted || isAbortError(e)) return;
			error = formatApiError(e, 'Could not load the statistics.');
		} finally {
			if (controller === c) {
				loading = false;
				refreshing = false;
			}
		}
	}

	let trendsController: AbortController | null = null;
	async function loadTrends(days: number) {
		trendsController?.abort();
		const c = new AbortController();
		trendsController = c;
		trendsLoading = true;
		trendsError = null;
		try {
			const res = await getStatisticsTrends(days, { signal: c.signal });
			if (!c.signal.aborted) trends = res;
		} catch (e) {
			if (c.signal.aborted || isAbortError(e)) return;
			trendsError = formatApiError(e, 'Could not load the trends.');
		} finally {
			if (trendsController === c) trendsLoading = false;
		}
	}

	async function refreshHealth() {
		if (!data) return;
		healthRefreshing = true;
		try {
			const health = await getStatisticsAnalyzerHealth();
			data = { ...data, analyzer_health: health };
		} catch (e) {
			error = formatApiError(e, 'Could not refresh analyzer health.');
		} finally {
			healthRefreshing = false;
		}
	}

	onMount(() => {
		load();
		return () => {
			controller?.abort();
			trendsController?.abort();
		};
	});

	// Trend window changes fetch just the series (the dashboard holds 7 days).
	$effect(() => {
		const days = Number(trendDays);
		untrack(() => {
			if (!data) return;
			if (trends?.days === days) return;
			loadTrends(days);
		});
	});

	// While analyses are queued or running, refresh silently (no skeleton flash).
	const active = $derived(
		!!data && data.overview.analyses_running + data.overview.analyses_pending > 0
	);
	$effect(() => {
		if (!active) return;
		const timer = setInterval(() => {
			if (document.visibilityState === 'visible') load(true);
		}, LIVE_REFRESH_MS);
		return () => clearInterval(timer);
	});

	// ── Derived view data ──
	const overview = $derived(data?.overview ?? null);
	const measuredJobs = $derived(overview?.measured_jobs ?? null);
	const finishedRuns = $derived(
		overview
			? (overview.analyses_completed ?? 0) +
					(overview.analyses_partial ?? 0) +
					(overview.analyses_failed ?? 0) +
					(overview.analyses_cancelled ?? 0)
			: 0
	);
	const unmeasuredApps = $derived(
		overview && measuredJobs != null ? Math.max(0, overview.apps_completed - measuredJobs) : 0
	);
	const health = $derived(data?.analyzer_health ?? null);
	const healthBadge = $derived.by(() => {
		if (!health) return null;
		const statuses = health.analyzers.map((a) => a.status || (a.available ? 'passed' : 'untested'));
		const failed = statuses.filter((s) => s === 'failed').length;
		const untested = statuses.filter((s) => s === 'untested').length;
		const passed = statuses.filter((s) => s === 'passed').length;
		if (!health.selftest) return { status: 'untested', label: 'Analyzers not self-tested' };
		if (failed)
			return {
				status: 'degraded',
				label: `${failed} analyzer self-test${failed === 1 ? '' : 's'} failed`
			};
		if (untested) return { status: 'warning', label: `${passed} passed · ${untested} untested` };
		return { status: 'healthy', label: `All ${passed} analyzers passed self-test` };
	});
	const modelPlan = $derived(data?.models.find((m) => m.plan_hash)?.plan_hash ?? null);
	const staticTop = $derived((data?.top_findings ?? []).filter((f) => !f.is_ai));
	const aiTop = $derived((data?.top_findings ?? []).filter((f) => f.is_ai));
	const cg = $derived(data?.code_generation ?? null);

	function sum(counts: Record<string, number> | null | undefined): number {
		return Object.values(counts ?? {}).reduce((a, b) => a + (b ?? 0), 0);
	}

	function modelNoTrials(m: ModelComparisonRow): string {
		return m.apps_completed
			? `None of the ${m.apps_completed} completed jobs has a complete analysis under the reference plan`
			: 'No completed job';
	}

	function toolStatuses(t: ToolEffectivenessRow): Record<string, number> {
		const out: Record<string, number> = {
			succeeded: t.scans,
			failed: t.failed ?? 0,
			timed_out: t.timed_out ?? 0,
			skipped: t.skipped ?? 0,
			not_applicable: t.not_applicable ?? 0
		};
		const known = Object.values(out).reduce((a, b) => a + b, 0);
		if (t.runs != null && t.runs > known) out.other = t.runs - known;
		return out;
	}

	function successReason(t: ToolEffectivenessRow): string {
		if (!t.runs) return 'No execution of this tool in the canonical runs';
		return 'No attempted execution: every run was not applicable or skipped';
	}

	function activityHref(kind: string, id: string): string | null {
		if (kind === 'analysis') return `/analysis/${id}`;
		if (kind === 'generation') return `/applications/${id}`;
		return null;
	}
</script>

{#snippet topList(items: TopFindingRow[], emptyText: string)}
	{#if items.length === 0}
		<p class="text-xs text-muted-foreground">{emptyText}</p>
	{:else}
		<ul class="space-y-2.5">
			{#each items as f, i (`${f.tool}:${f.rule_id}:${f.severity}:${i}`)}
				<li class="flex items-start gap-2">
					<Badge
						variant="outline"
						class="shrink-0 text-[11px] capitalize {severityColors[f.severity] ?? ''}"
						>{f.severity}</Badge
					>
					<div class="min-w-0 flex-1">
						<p class="line-clamp-2 text-xs">{f.title}</p>
						{#if f.tool || f.rule_id}
							<p class="truncate font-mono text-[11px] text-muted-foreground">
								{f.tool}{f.rule_id ? ` · ${f.rule_id}` : ''}
							</p>
						{/if}
					</div>
					<span class="shrink-0 font-mono text-xs tabular-nums text-muted-foreground"
						>{f.count}</span
					>
				</li>
			{/each}
		</ul>
	{/if}
{/snippet}

<div class="space-y-6">
	<PageHeader
		title="Statistics"
		description="Platform-wide generation and analysis metrics. Finding totals count each job once, from its latest complete measured run; AI-reviewer opinions are kept apart."
	>
		{#snippet meta()}
			{#if healthBadge}<StatusBadge status={healthBadge.status} label={healthBadge.label} />{/if}
		{/snippet}
		{#snippet actions()}
			<Button variant="outline" size="sm" href="/rankings">
				<Trophy class="h-3.5 w-3.5" />Rankings
			</Button>
			<Button
				variant="outline"
				size="sm"
				onclick={() => load(true)}
				disabled={loading || refreshing}
			>
				<RefreshCw
					class="h-3.5 w-3.5 {refreshing ? 'animate-spin motion-reduce:animate-none' : ''}"
				/>
				Refresh
			</Button>
		{/snippet}
	</PageHeader>

	{#if error}
		<ErrorAlert message={error} onretry={() => load(!!data)} />
	{/if}

	{#if loading && !data}
		<LoadingState label="loading statistics" />
	{:else if data && overview}
		<!-- KPIs -->
		<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
			<div class="kpi-card">
				<div class="kpi-label">Analyses</div>
				<div class="kpi-value">{fmtInt(overview.total_analyses)}</div>
				<div class="text-xs text-muted-foreground">
					{overview.analyses_completed} complete · {overview.analyses_partial ?? 0} partial · {overview.analyses_failed}
					failed
				</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Complete rate</div>
				<div class="kpi-value">
					{#if finishedRuns > 0}{fmtNum(overview.analyses_success_rate, 1)}%{:else}<NaValue
							reason="No analysis has finished yet"
						/>{/if}
				</div>
				<div class="text-xs text-muted-foreground">every required tool succeeded</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Measured jobs</div>
				<div class="kpi-value">
					{#if measuredJobs != null}{measuredJobs}{:else}<NaValue
							reason="Not reported by the server"
						/>{/if}
				</div>
				<div class="text-xs text-muted-foreground">of {overview.apps_completed} completed apps</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Static findings</div>
				<div class="kpi-value">
					{#if overview.total_findings == null || overview.static_measured_jobs === 0}<NaValue
							reason="No job has a static measurement yet (no static tool succeeded): no data, not zero findings"
						/>{:else}{fmtInt(overview.total_findings)}{/if}
				</div>
				<div class="text-xs text-muted-foreground">
					counted, canonical runs{overview.static_measured_jobs != null
						? ` · over ${overview.static_measured_jobs} job${overview.static_measured_jobs === 1 ? '' : 's'}`
						: ''}
				</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">AI consensus</div>
				<div class="kpi-value text-muted-foreground">
					{#if overview.total_ai_findings == null || overview.ai_reviewed_jobs === 0}<NaValue
							reason="No measured analysis with a successful AI review yet"
						/>{:else}{fmtInt(overview.total_ai_findings)}{/if}
				</div>
				<div class="text-xs text-muted-foreground">
					opinions, not in the totals{overview.ai_reviewed_jobs != null
						? ` · over ${overview.ai_reviewed_jobs} reviewed job${overview.ai_reviewed_jobs === 1 ? '' : 's'}`
						: ''}
				</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Generation success</div>
				<div class="kpi-value">
					{#if overview.total_apps > 0}{fmtNum(overview.apps_success_rate, 1)}%{:else}<NaValue
							reason="No generation job yet"
						/>{/if}
				</div>
				<div class="text-xs text-muted-foreground">
					{overview.apps_completed} of {overview.total_apps} apps · {overview.models_in_use} models
				</div>
			</div>
		</div>

		{#if unmeasuredApps > 0}
			<CoverageNotice
				title="{unmeasuredApps} of {overview.apps_completed} completed apps have no complete measured analysis"
				items={[
					'They are not in the finding totals or the severity mix — missing data, not zero findings.',
					`${overview.analyses_partial ?? 0} partial and ${overview.analyses_failed} failed analyses so far; ${overview.analyses_running + overview.analyses_pending} queued or running.`
				]}
			/>
		{/if}

		<!-- Trends + severity -->
		<div class="grid gap-4 lg:grid-cols-12">
			<Card.Root class="lg:col-span-7">
				<Card.Header>
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div class="flex items-center gap-2">
							<ChartColumn class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
							<div>
								<Card.Title>Analysis runs per day</Card.Title>
								<Card.Description>
									{trends
										? `${trends.total} runs in the last ${trends.days} days`
										: 'Runs by outcome'}
								</Card.Description>
							</div>
						</div>
						<Segmented
							aria-label="Trend window"
							size="sm"
							bind:value={trendDays}
							items={DAY_OPTIONS.map((d) => ({ value: d, label: `${d}d` }))}
						/>
					</div>
				</Card.Header>
				<Card.Content class={trendsLoading ? 'opacity-70 transition-opacity' : ''}>
					{#if trendsError}
						<ErrorAlert message={trendsError} onretry={() => loadTrends(Number(trendDays))} />
					{:else if trends && trends.total > 0}
						<AnalysisTrendChart points={trends.series} />
					{:else if trends}
						<ChartEmpty message="No analysis runs in this window." />
					{:else}
						<ChartEmpty message="Loading trends…" />
					{/if}
				</Card.Content>
			</Card.Root>
			<div class="lg:col-span-5">
				<SeverityPanel severity={data.severity} {measuredJobs} />
			</div>
		</div>

		<!-- Model comparison -->
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<div class="flex flex-wrap items-start justify-between gap-2">
					<div class="min-w-0">
						<Card.Title>Model comparison</Card.Title>
						<Card.Description class="flex flex-wrap items-center gap-x-1.5 gap-y-1">
							Same scoring and plan as Rankings: analysed trials are complete, eligible jobs
							measured under
							{#if modelPlan}<PlanHash hash={modelPlan} />{:else}the reference plan{/if}
						</Card.Description>
					</div>
					<Button variant="outline" size="sm" href="/rankings">
						Open rankings <ArrowRight class="h-3.5 w-3.5" />
					</Button>
				</div>
			</Card.Header>
			<Card.Content class="p-0">
				{#if data.models.length === 0}
					<EmptyState
						compact
						title="No model data yet"
						description="Model metrics appear once applications are generated and analysed."
					/>
				{:else}
					<div class="table-frame" {@attach tableFrame}>
						<table class="table-card-mobile md:min-w-[56rem]">
							<thead>
								<tr>
									<th>Model</th>
									<th class="num">Apps</th>
									<th class="num" title="Complete, eligible jobs measured under the reference plan">
										Analysed trials
									</th>
									<th class="num">Empirical</th>
									<th class="num" title="Counted static issues of the analysed trials"
										>Static issues</th
									>
									<th class="num">Functional</th>
									<th class="num" title="AI consensus findings — opinions, never scored"
										>AI consensus</th
									>
									<th class="num">MSS</th>
									<th class="num">Composite</th>
								</tr>
							</thead>
							<tbody>
								{#each data.models as m (m.model_id)}
									{@const staticAvailable = m.measured_sloc != null}
									<tr>
										<td data-label="">
											<a
												href="/models/{encodeURIComponent(m.model_id)}"
												class="font-medium hover:underline">{m.name}</a
											>
											<div class="text-[11px] text-muted-foreground">{m.provider}</div>
										</td>
										<td data-label="Apps" class="num text-xs">
											{m.apps_completed}<span class="text-muted-foreground">/{m.apps}</span>
										</td>
										<td data-label="Analysed trials" class="num text-xs">
											{m.n_trials}
										</td>
										<td data-label="Empirical" class="num text-xs">
											{#if m.empirical_quality != null}
												<span class="font-semibold">{fmtScore(m.empirical_quality)}</span>
											{:else}
												<NaValue
													reason={m.n_trials
														? 'Static measurement unavailable in every analysed trial'
														: modelNoTrials(m)}
												/>
											{/if}
										</td>
										<td data-label="Static issues" class="num text-xs">
											{#if staticAvailable}
												{sum(m.issues ?? m.findings)}
												<span class="text-[11px] text-muted-foreground"
													>/ {fmtInt(m.measured_sloc)} SLOC</span
												>
											{:else}
												<NaValue
													reason={m.n_trials
														? 'Static measurement unavailable in every analysed trial (a required tool failed)'
														: modelNoTrials(m)}
												/>
											{/if}
										</td>
										<td data-label="Functional" class="num text-xs">
											<MaybeValue
												value={fmtShare(m.smoke_pass_rate, 0)}
												reason="No functional (conformance) result in the analysed trials"
											/>
										</td>
										<td data-label="AI consensus" class="num text-xs text-muted-foreground">
											{#if sum(m.ai_findings) > 0}
												{sum(m.ai_findings)}
											{:else}
												<NaValue
													reason={m.n_trials
														? 'No AI consensus findings recorded — the reviewer may not have run on these trials (see Rankings for review coverage)'
														: modelNoTrials(m)}
												/>
											{/if}
										</td>
										<td data-label="MSS" class="num text-xs">
											{fmtScore(m.mss)}
										</td>
										<td data-label="Composite" class="num text-xs">
											{fmtScore(m.composite)}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Tool effectiveness -->
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<div class="flex items-center gap-2">
					<Wrench class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
					<Card.Title>Tool effectiveness</Card.Title>
				</div>
				<Card.Description>
					Each job's latest finished run. Only succeeded executions are scans — failed, timed-out,
					skipped and not-applicable executions never count as clean scans or dilute the averages.
				</Card.Description>
			</Card.Header>
			<Card.Content class="p-0">
				{#if data.tools.length === 0}
					<EmptyState
						compact
						title="No tool executions yet"
						description="Tool metrics appear after the first analysis finishes."
					/>
				{:else}
					<div class="table-frame" {@attach tableFrame}>
						<table class="table-card-mobile md:min-w-[52rem]">
							<thead>
								<tr>
									<th>Tool</th>
									<th class="w-44">Executions</th>
									<th class="num">Scans</th>
									<th class="num" title="Succeeded / (succeeded + failed + timed out)">Success</th>
									<th class="num">Findings</th>
									<th class="num">Per scan</th>
									<th>Versions</th>
									<th>Top rule</th>
								</tr>
							</thead>
							<tbody>
								{#each data.tools as t (t.name)}
									<tr>
										<td data-label="">
											<span class="font-mono text-xs font-medium">{t.name}</span>
											<Badge variant="secondary" class="ml-1 text-[11px] capitalize">{t.type}</Badge
											>
										</td>
										<td data-label="Executions">
											<div class="w-full max-w-56">
												<StatusCountsBar counts={toolStatuses(t)} />
											</div>
										</td>
										<td data-label="Scans" class="num text-xs">{t.scans}</td>
										<td data-label="Success" class="num text-xs">
											{#if t.success_rate != null}
												<span
													class={t.success_rate >= 0.95
														? 'text-success-fg'
														: t.success_rate >= 0.8
															? 'text-warning-fg'
															: 'text-danger-fg'}>{fmtShare(t.success_rate, 0)}</span
												>
											{:else}
												<NaValue reason={successReason(t)} />
											{/if}
										</td>
										<td data-label="Findings" class="num text-xs">
											{#if t.scans > 0}{t.findings}{:else}<NaValue
													reason="No succeeded scan: no findings could be produced"
												/>{/if}
										</td>
										<td data-label="Per scan" class="num text-xs">
											{#if t.scans > 0}{fmtNum(t.avg_per_scan, 1)}{:else}<NaValue
													reason="No succeeded scan to average over"
												/>{/if}
										</td>
										<td data-label="Versions" class="font-mono text-[11px] text-muted-foreground">
											{#if t.versions && t.versions.length > 0}
												{t.versions.join(', ')}
											{:else}
												<NaValue reason="No succeeded execution recorded a tool version" />
											{/if}
										</td>
										<td
											data-label="Top rule"
											class="max-w-48 truncate font-mono text-[11px] text-muted-foreground"
										>
											{#if t.top_rule}<span title={t.top_rule}>{t.top_rule}</span>{:else}<NaValue
													reason={t.scans > 0 ? 'No findings with a rule id' : 'No succeeded scan'}
												/>{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Top findings + recent activity -->
		<div class="grid gap-4 lg:grid-cols-2">
			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<ShieldAlert class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
						<Card.Title>Most frequent findings</Card.Title>
					</div>
					<Card.Description
						>Counted findings by tool and rule, canonical complete runs.</Card.Description
					>
				</Card.Header>
				<Card.Content class="space-y-5">
					<section class="space-y-2">
						<h2 class="section-label">Static tools</h2>
						{@render topList(
							staticTop,
							measuredJobs === 0 ? 'No measured analysis yet.' : 'No counted static findings.'
						)}
					</section>
					{#if aiTop.length > 0}
						<section class="space-y-2 border-t pt-4">
							<h2 class="section-label flex items-center gap-1.5">
								<Bot class="h-3.5 w-3.5" aria-hidden="true" />AI review · consensus (opinions)
							</h2>
							{@render topList(aiTop, '')}
						</section>
					{/if}
				</Card.Content>
			</Card.Root>
			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<Activity class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
						<Card.Title>Recent activity</Card.Title>
					</div>
				</Card.Header>
				<Card.Content>
					{#if data.recent_activity.length === 0}
						<EmptyState compact title="No recent activity" />
					{:else}
						<ul class="space-y-2">
							{#each data.recent_activity.slice(0, 10) as item (`${item.kind}:${item.id}`)}
								{@const href = activityHref(item.kind, item.id)}
								<li class="flex items-center justify-between gap-2 text-xs">
									<div class="flex min-w-0 items-center gap-2">
										<Badge variant="outline" class="shrink-0 text-[11px] capitalize"
											>{item.kind}</Badge
										>
										{#if href}
											<a {href} class="truncate hover:underline">{item.title}</a>
										{:else}
											<span class="truncate">{item.title}</span>
										{/if}
									</div>
									<div class="flex shrink-0 items-center gap-2">
										<StatusBadge status={item.status} />
										<span class="hidden text-[11px] text-muted-foreground sm:inline">
											{new Date(item.created_at).toLocaleDateString()}
										</span>
									</div>
								</li>
							{/each}
						</ul>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Code generation -->
		{#if cg}
			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<Code class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
						<Card.Title>Code generation</Card.Title>
					</div>
					<Card.Description>
						Lines of code as the analysis measured them (lockfiles, build output and assets
						excluded), over the analysed jobs only.
					</Card.Description>
				</Card.Header>
				<Card.Content>
					<dl class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
						<div class="kpi-card">
							<dt class="kpi-label">Apps generated</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								{fmtInt(cg.total_apps)}
							</dd>
							<dd class="text-xs text-muted-foreground">
								{cg.completed} completed · {cg.failed} failed
							</dd>
						</div>
						<div class="kpi-card">
							<dt class="kpi-label">Success rate</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								{#if cg.total_apps > 0}{fmtNum(cg.success_rate, 1)}%{:else}<NaValue
										reason="No generation job yet"
									/>{/if}
							</dd>
						</div>
						<div class="kpi-card">
							<dt class="kpi-label">Avg generation time</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								<MaybeValue
									value={cg.completed > 0 && cg.avg_duration_seconds > 0
										? fmtSeconds(cg.avg_duration_seconds)
										: null}
									reason="No completed job recorded a duration"
								/>
							</dd>
						</div>
						<div class="kpi-card">
							<dt class="kpi-label">Measured SLOC</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								<MaybeValue
									value={cg.loc_jobs_covered ? fmtInt(cg.measured_lines_of_code ?? null) : null}
									reason="No analysed job yet: lines of code come from analysis measurements"
								/>
							</dd>
							<dd class="text-xs text-muted-foreground">
								model-written · {cg.loc_jobs_covered ?? 0} analysed jobs
							</dd>
						</div>
						<div class="kpi-card">
							<dt class="kpi-label">Included SLOC</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								<MaybeValue
									value={cg.loc_jobs_covered ? fmtInt(cg.total_lines_of_code) : null}
									reason="No analysed job yet: lines of code come from analysis measurements"
								/>
							</dd>
							<dd class="text-xs text-muted-foreground">all included files</dd>
						</div>
						<div class="kpi-card">
							<dt class="kpi-label">Tokens</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								{fmtInt(cg.total_tokens)}
							</dd>
							<dd class="text-xs text-muted-foreground">completed jobs</dd>
						</div>
						<div class="kpi-card">
							<dt class="kpi-label">Cost</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								{fmtUsd(cg.total_cost_usd)}
							</dd>
							<dd class="text-xs text-muted-foreground">recorded generation cost</dd>
						</div>
						<div class="kpi-card">
							<dt class="kpi-label">Templates · stacks</dt>
							<dd class="font-display text-2xl font-semibold tabular-nums">
								{cg.unique_templates} · {cg.unique_stacks ?? '—'}
							</dd>
						</div>
					</dl>
				</Card.Content>
			</Card.Root>
		{/if}

		<AnalyzerHealthGrid
			health={data.analyzer_health}
			isRefreshing={healthRefreshing}
			onRefresh={refreshHealth}
		/>
	{/if}
</div>
