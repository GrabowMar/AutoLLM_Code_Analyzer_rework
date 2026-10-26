<script lang="ts">
	import { onMount } from 'svelte';
	import { severityColors } from '$lib/constants/colors';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import LineChart from '@lucide/svelte/icons/line-chart';
	import PieChart from '@lucide/svelte/icons/pie-chart';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
	import Activity from '@lucide/svelte/icons/activity';
	import Code from '@lucide/svelte/icons/code';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { ScoreCell, rowLink, tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';
	import { modelPathSegment } from '$lib/api/models';
	import {
		getStatisticsDashboard,
		getStatisticsTrends,
		type StatisticsDashboard,
		type AnalysisTrends,
	} from '$lib/api/client';

	let data = $state<StatisticsDashboard | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Trends period selector — separate from the dashboard fetch
	const dayOptions = [7, 14, 30, 90] as const;
	let trendDays = $state<7 | 14 | 30 | 90>(7);
	let trendsData = $state<AnalysisTrends | null>(null);
	let trendsLoading = $state(false);

	async function fetchTrends(days: number) {
		trendsLoading = true;
		try {
			trendsData = await getStatisticsTrends(days);
		} catch {
			// fall back to dashboard trends
		} finally {
			trendsLoading = false;
		}
	}

	async function load() {
		loading = true;
		error = null;
		try {
			data = await getStatisticsDashboard();
			trendsData = data.trends;
		} catch (e) {
			error = (e as { message?: string })?.message ?? 'Failed to load statistics';
		} finally {
			loading = false;
		}
	}

	onMount(load);

	$effect(() => {
		void trendDays;
		if (data) fetchTrends(trendDays);
	});


	const severityBarColor: Record<string, string> = {
		critical: 'bg-destructive',
		high: 'bg-destructive/60',
		medium: 'bg-warning',
		low: 'bg-info',
		info: 'bg-muted-foreground',
	};

	// Column leaders get the primary color only when held by at most two models.
	type ScoreKey = 'success' | 'smoke' | 'empirical' | 'mss' | 'composite';
	const leaders = $derived.by(() => {
		const models = data?.models ?? [];
		const out: Partial<Record<ScoreKey, number>> = {};
		if (models.length < 2) return out;
		const cols: [ScoreKey, (m: (typeof models)[number]) => number | null | undefined][] = [
			['success', (m) => m.success_rate],
			['smoke', (m) => m.smoke_pass_rate],
			['empirical', (m) => m.empirical_quality],
			['mss', (m) => m.mss],
			['composite', (m) => m.composite],
		];
		for (const [key, get] of cols) {
			const vals = models.map(get).filter((v): v is number => v != null);
			if (!vals.length) continue;
			const max = Math.max(...vals);
			if (max > 0 && vals.filter((v) => v === max).length <= 2) out[key] = max;
		}
		return out;
	});
	const isBest = (key: ScoreKey, v: number | null | undefined) => v != null && leaders[key] === v;

	function fmtDuration(seconds: number): string {
		if (!seconds) return '—';
		if (seconds < 60) return `${seconds.toFixed(0)}s`;
		const m = Math.floor(seconds / 60);
		const s = Math.round(seconds - m * 60);
		return `${m}m ${s}s`;
	}

	function fmtNumber(n: number): string {
		return n.toLocaleString();
	}

	function fmtCost(usd: number): string {
		if (!usd) return '$0.00';
		if (usd < 1) return `$${usd.toFixed(4)}`;
		return `$${usd.toFixed(2)}`;
	}

	function dayLabel(iso: string): string {
		const d = new Date(iso);
		return d.toLocaleDateString(undefined, { weekday: 'short' });
	}

	let trendMax = $derived(
		Math.max(1, ...(trendsData?.series ?? data?.trends.series ?? []).map((p) => p.total)),
	);
</script>

{#snippet headerStats()}
	{#if data}
		{@const o = data.overview}
		<div class="header-stats">
			<span class="header-stat"><b>{fmtNumber(o.total_analyses)}</b> analyses</span>
			<span class="header-stat"><b>{fmtNumber(o.analyses_completed)}</b> completed</span>
			<span class="header-stat" title="Apps: {o.apps_success_rate}% success"><b>{o.analyses_success_rate}%</b> success</span>
			<span class="header-stat"><b>{fmtDuration(o.avg_analysis_duration_seconds)}</b> avg per analysis</span>
			<span class="header-stat" title="{new Set(data.models.map((m) => m.provider)).size} providers"><b>{fmtNumber(o.models_in_use)}</b> active models</span>
			<span class="header-stat"><b>{data.analyzer_health.online}/{data.analyzer_health.total}</b> analyzers online</span>
		</div>
	{/if}
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="Statistics"
		description="Platform-wide analytics and performance metrics."
		children={data ? headerStats : undefined}
	>
		{#snippet meta()}
			{#if data}
				{@const health = data.analyzer_health}
				<StatusBadge
					status={health.offline === 0 ? 'healthy' : 'degraded'}
					label={health.offline === 0 ? 'System healthy' : 'Degraded'}
				/>
			{/if}
		{/snippet}
	</PageHeader>

	{#if loading}
		<LoadingState label="loading statistics" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if data}
		<!-- Charts Row -->
		<div class="grid gap-4 lg:grid-cols-2">
			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<LineChart class="h-5 w-5 text-muted-foreground" />
						<Card.Title>Analysis Trends</Card.Title>
						<div class="ml-auto flex gap-1">
							{#each dayOptions as d}
								<Button
									size="sm"
									variant={trendDays === d ? 'default' : 'ghost'}
									class="h-6 px-2 text-xs"
									onclick={() => { trendDays = d; }}
									aria-pressed={trendDays === d}
								>
									{d}d
								</Button>
							{/each}
						</div>
					</div>
				</Card.Header>
				<Card.Content>
					{#if trendsLoading}
						<LoadingState label="loading trends" class="h-40 py-0" />
					{:else}
						{@const activeTrends = trendsData ?? data.trends}
						<div class="flex h-40 items-end gap-1.5">
							{#each activeTrends.series as point}
								<div class="flex flex-1 flex-col items-center justify-end gap-1" title="{point.date}: {point.total} analyses">
									<div
										class="w-full rounded-t bg-chart-1 transition-all duration-300"
										style="height: {Math.round((point.total / trendMax) * 140)}px"
									></div>
									<span class="text-[11px] text-muted-foreground">{dayLabel(point.date)}</span>
								</div>
							{/each}
						</div>
						<p class="mt-3 text-center text-xs text-muted-foreground">
							Analyses run in last {activeTrends.days} days: {activeTrends.total}
						</p>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<PieChart class="h-5 w-5 text-muted-foreground" />
						<Card.Title>Severity Distribution</Card.Title>
					</div>
				</Card.Header>
				<Card.Content>
					<div class="space-y-3">
						{#each data.severity.distribution as sev}
							<div class="flex items-center gap-2 text-sm">
								<span class="w-14 text-xs capitalize text-muted-foreground">{sev.severity}</span>
								<div class="h-3 flex-1 overflow-hidden rounded-full bg-muted">
									<div
										class="h-full rounded-full {severityBarColor[sev.severity] ?? 'bg-muted-foreground'}"
										style="width: {sev.percent}%"
									></div>
								</div>
								<span class="w-10 text-right font-mono text-xs">{sev.count}</span>
								<span class="w-8 text-right text-[11px] text-muted-foreground">{sev.percent}%</span>
							</div>
						{/each}
					</div>
					<p class="mt-3 text-center text-xs text-muted-foreground">
						Total findings: {data.severity.total}
						{#if data.severity.by_source}
							{@const aiTotal = Object.values(data.severity.by_source.ai).reduce((a, b) => a + b, 0)}
							{#if aiTotal > 0}
								· incl. {aiTotal} from AI review
							{/if}
						{/if}
					</p>
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Model Comparison -->
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title>Model Comparison</Card.Title>
			</Card.Header>
			<Card.Content class="p-0">
				{#if data.models.length === 0}
					<EmptyState compact title="No model data yet" description="Model metrics appear once applications are generated and analyzed." />
				{:else}
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead>
								<tr>
									<th class="whitespace-nowrap">Model</th>
									<th class="num">Apps</th>
									<th class="num" title="Share of generated apps that completed">Success %</th>
									<th class="num">Smoke pass</th>
									<th class="num" title="Measured quality over n trials">Empirical (measured)</th>
									<th class="num">MSS (meta)</th>
									<th class="num">Composite</th>
								</tr>
							</thead>
							<tbody>
								{#each data.models as m}
									{@const href = m.model_id ? `/models/${modelPathSegment(m.model_id)}` : null}
									{@const provider = modelNamePrefix(m.name) || m.provider}
									<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
									<tr class={href ? 'row-link' : ''} onclick={href ? rowLink(href) : undefined}>
										<td class="max-w-0 w-[30%]">
											<a
												href={href ?? '/models'}
												class="block truncate font-medium hover:text-primary"
												title={m.name}
												onclick={(e) => { if (!m.model_id) e.preventDefault(); }}
											>{shortModelName(m.name)}</a>
											{#if provider}<span class="cell-sub block truncate text-[11px] text-muted-foreground">{provider}</span>{/if}
										</td>
										<td class="num text-xs">{m.apps}</td>
										<ScoreCell value={m.success_rate / 100} best={isBest('success', m.success_rate)} />
										<ScoreCell value={m.smoke_pass_rate} best={isBest('smoke', m.smoke_pass_rate)} />
										<ScoreCell value={m.empirical_quality} strong best={isBest('empirical', m.empirical_quality)} sub={m.empirical_quality != null ? `n=${m.n_trials}` : undefined} />
										<ScoreCell value={m.mss} best={isBest('mss', m.mss)} />
										<ScoreCell value={m.composite} best={isBest('composite', m.composite)} />
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Tool Effectiveness + Top Findings -->
		<div class="grid gap-4 lg:grid-cols-3">
			<div class="lg:col-span-2">
				<Card.Root class="gap-0 overflow-clip py-0">
					<Card.Header class="border-b border-border py-3">
						<Card.Title>Tool Effectiveness</Card.Title>
					</Card.Header>
					<Card.Content class="p-0">
						{#if data.tools.length === 0}
							<EmptyState compact title="No analyzer runs yet" description="Tool metrics appear after the first analysis completes." />
						{:else}
							<div class="table-frame" {@attach tableFrame}>
								<table>
									<thead>
										<tr>
											<th>Tool</th>
											<th>Type</th>
											<th class="num">Scans</th>
											<th class="num">Findings</th>
											<th class="num">Avg/Scan</th>
											<th>Top Rule</th>
										</tr>
									</thead>
									<tbody>
										{#each data.tools as t}
											<tr>
												<td class="font-medium">{t.name}</td>
												<td>
													<Badge variant="secondary" class="text-[11px] capitalize">{t.type}</Badge>
												</td>
												<td class="num text-xs">{t.scans}</td>
												<td class="num text-xs">{t.findings}</td>
												<td class="num text-xs">{t.avg_per_scan.toFixed(1)}</td>
												<td class="font-mono text-[11px] text-muted-foreground">{t.top_rule || '—'}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
			</div>

			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<AlertTriangle class="h-4 w-4 text-muted-foreground" />
						<Card.Title>Top Findings</Card.Title>
					</div>
				</Card.Header>
				<Card.Content class="space-y-3">
					{#if data.top_findings.length === 0}
						<EmptyState compact title="No findings yet" />
					{:else}
						{#each data.top_findings as f}
							<div class="flex items-start gap-2">
								<Badge
									variant="outline"
									class="shrink-0 text-[11px] {severityColors[f.severity] ?? ''}"
								>
									{f.severity}
								</Badge>
								<div class="flex-1 text-xs">{f.title}</div>
								<span class="shrink-0 font-mono text-xs text-muted-foreground">{f.count}</span>
							</div>
						{/each}
					{/if}
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Code Generation + Recent Activity -->
		<div class="grid gap-4 lg:grid-cols-3">
			<div class="lg:col-span-2">
				<Card.Root class="gap-0 py-0">
					<Card.Header class="border-b border-border py-3">
						<div class="flex items-center gap-2">
							<Code class="h-4 w-4 text-muted-foreground" />
							<Card.Title>Code Generation Stats</Card.Title>
						</div>
					</Card.Header>
					<Card.Content class="p-0">
						{@const cg = data.code_generation}
						<dl class="grid grid-cols-2 gap-x-4 gap-y-2 px-4 py-3 sm:grid-cols-3">
							{#each [{ label: 'Apps generated', value: fmtNumber(cg.total_apps) }, { label: 'Success rate', value: `${cg.success_rate}%` }, { label: 'Avg gen time', value: fmtDuration(cg.avg_duration_seconds) }, { label: 'Lines of code', value: fmtNumber(cg.total_lines_of_code) }, { label: 'Tokens', value: fmtNumber(cg.total_tokens) }, { label: 'Cost', value: fmtCost(cg.total_cost_usd) }] as stat}
								<div>
									<dt class="kpi-label">{stat.label}</dt>
									<dd class="font-mono text-base font-medium tabular-nums">{stat.value}</dd>
								</div>
							{/each}
						</dl>
					</Card.Content>
				</Card.Root>
			</div>

			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<Activity class="h-4 w-4 text-muted-foreground" />
						<Card.Title>Recent Activity</Card.Title>
					</div>
				</Card.Header>
				<Card.Content class="space-y-2">
					{#if data.recent_activity.length === 0}
						<EmptyState compact title="No recent activity" />
					{:else}
						{#each data.recent_activity.slice(0, 8) as item}
							<div class="flex items-center justify-between gap-2 text-xs">
								<div class="flex min-w-0 items-center gap-2">
									<Badge variant="outline" class="text-[11px] capitalize">{item.kind}</Badge>
									<span class="truncate">{item.title}</span>
								</div>
								<span class="shrink-0 text-[11px] text-muted-foreground">
									{new Date(item.created_at).toLocaleDateString()}
								</span>
							</div>
						{/each}
					{/if}
				</Card.Content>
			</Card.Root>
		</div>
	{/if}
</div>
