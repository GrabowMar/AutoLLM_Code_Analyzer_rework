<script lang="ts">
	import { onMount } from 'svelte';
	import { severityColors } from '$lib/constants/colors';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import TrendingUp from '@lucide/svelte/icons/trending-up';
	import Clock from '@lucide/svelte/icons/clock';
	import Cpu from '@lucide/svelte/icons/cpu';
	import LineChart from '@lucide/svelte/icons/line-chart';
	import PieChart from '@lucide/svelte/icons/pie-chart';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
	import Activity from '@lucide/svelte/icons/activity';
	import Code from '@lucide/svelte/icons/code';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
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

	onMount(async () => {
		try {
			data = await getStatisticsDashboard();
			trendsData = data.trends;
		} catch (e) {
			error = (e as { message?: string })?.message ?? 'Failed to load statistics';
		} finally {
			loading = false;
		}
	});

	$effect(() => {
		void trendDays;
		if (data) fetchTrends(trendDays);
	});


	const severityBarColor: Record<string, string> = {
		critical: 'bg-red-500',
		high: 'bg-orange-500',
		medium: 'bg-amber-500',
		low: 'bg-blue-500',
		info: 'bg-slate-500',
	};

	function scoreColor(val: number, max: number = 10): string {
		const pct = max === 100 ? val : (val / max) * 100;
		if (pct >= 80) return 'text-emerald-500';
		if (pct >= 60) return 'text-amber-500';
		return 'text-destructive';
	}

	function pct01(v: number | null | undefined): string {
		if (v == null) return '—';
		return (v * 100).toFixed(1);
	}

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
	let kpis = $derived(
		data
			? [
					{
						title: 'Total Analyses',
						icon: BarChart3,
						color: 'text-blue-500',
						bg: 'bg-blue-500/10',
						value: fmtNumber(data.overview.total_analyses),
						delta: `${data.overview.analyses_completed} completed`,
					},
					{
						title: 'Success Rate',
						icon: TrendingUp,
						color: 'text-emerald-500',
						bg: 'bg-emerald-500/10',
						value: `${data.overview.analyses_success_rate}%`,
						delta: `Apps: ${data.overview.apps_success_rate}%`,
					},
					{
						title: 'Avg. Duration',
						icon: Clock,
						color: 'text-amber-500',
						bg: 'bg-amber-500/10',
						value: fmtDuration(data.overview.avg_analysis_duration_seconds),
						delta: 'per analysis',
					},
					{
						title: 'Active Models',
						icon: Cpu,
						color: 'text-violet-500',
						bg: 'bg-violet-500/10',
						value: fmtNumber(data.overview.models_in_use),
						delta: `${new Set(data.models.map((m) => m.provider)).size} providers`,
					},
				]
			: [],
	);
</script>

<svelte:head>
	<title>Statistics - LLM Lab</title>
</svelte:head>

<div class="space-y-4 sm:space-y-6">
	<div class="page-header">
		<h1>Statistics</h1>
		<p>Platform-wide analytics and performance metrics.</p>
	</div>

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<div class="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive" style="font-family: var(--font-mono);">
			<span class="font-semibold">error:</span> {error}
		</div>
	{:else if data}
		<!-- System Health -->
		{@const health = data.analyzer_health}
		{@const healthy = health.offline === 0}
		<div
			class="flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 {healthy
				? 'border-emerald-500/30 bg-emerald-500/5'
				: 'border-amber-500/30 bg-amber-500/5'}"
		>
			<Activity class="h-5 w-5 {healthy ? 'text-emerald-500' : 'text-amber-500'}" />
			<div class="flex-1">
				<span class="text-sm font-medium {healthy ? 'text-emerald-500' : 'text-amber-500'}">
					{healthy ? 'System Healthy' : 'Degraded'}
				</span>
				<span class="ml-2 text-xs text-muted-foreground">
					{health.online}/{health.total} analyzers online
				</span>
			</div>
			<Badge
				variant="outline"
				class="border-emerald-500/30 bg-emerald-500/15 text-[10px] text-emerald-500"
			>
				{healthy ? 'Operational' : 'Partial'}
			</Badge>
		</div>

		<!-- KPIs -->
		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
			{#each kpis as kpi}
				<div class="kpi-card">
					<div class="text-xs text-muted-foreground uppercase tracking-wider">{kpi.title}</div>
					<div class="text-2xl font-semibold font-mono tabular-nums">{kpi.value}</div>
					<div class="text-xs text-muted-foreground">{kpi.delta}</div>
				</div>
			{/each}
		</div>

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
								>
									{d}d
								</Button>
							{/each}
						</div>
					</div>
				</Card.Header>
				<Card.Content>
					{#if trendsLoading}
						<div class="flex h-40 items-center justify-center">
							<LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" />
						</div>
					{:else}
						{@const activeTrends = trendsData ?? data.trends}
						<div class="flex h-40 items-end gap-1.5">
							{#each activeTrends.series as point}
								<div class="flex flex-1 flex-col items-center justify-end gap-1" title="{point.date}: {point.total} analyses">
									<div
										class="w-full rounded-t bg-blue-500/70 transition-all duration-300"
										style="height: {Math.round((point.total / trendMax) * 140)}px"
									></div>
									<span class="text-[9px] text-muted-foreground">{dayLabel(point.date)}</span>
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
										class="h-full rounded-full {severityBarColor[sev.severity] ?? 'bg-slate-500'}"
										style="width: {sev.percent}%"
									></div>
								</div>
								<span class="w-10 text-right font-mono text-xs">{sev.count}</span>
								<span class="w-8 text-right text-[10px] text-muted-foreground">{sev.percent}%</span>
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
		<Card.Root>
			<Card.Header>
				<Card.Title>Model Comparison</Card.Title>
			</Card.Header>
			<Card.Content class="p-0">
				{#if data.models.length === 0}
					<p class="p-6 text-center text-sm text-muted-foreground">No model data yet.</p>
				{:else}
					<div class="table-scroll-wrapper">
						<table class="w-full text-sm">
							<thead>
								<tr class="border-b bg-muted/40 sticky top-0 z-10">
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Model</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Apps</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Success %</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Smoke pass</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Empirical (measured)</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">MSS (meta)</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Composite</th>
								</tr>
							</thead>
							<tbody>
								{#each data.models as m, i}
									<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'}">
										<td class="px-3 py-2 align-top">
											<div>
												<a
													href="/models/{m.model_id ?? ''}"
													class="font-medium text-sm hover:text-primary hover:underline underline-offset-2 transition-colors"
													onclick={(e) => { if (!m.model_id) e.preventDefault(); }}
												>{m.name}</a>
												<div class="text-[10px] text-muted-foreground">{m.provider}</div>
											</div>
										</td>
										<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs">{m.apps}</td>
										<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor(m.success_rate, 100)}">{m.success_rate.toFixed(0)}</td>
										<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {m.smoke_pass_rate != null ? scoreColor(m.smoke_pass_rate, 1) : ''}">{pct01(m.smoke_pass_rate)}</td>
										<td class="px-3 py-2 text-right align-top">
											{#if m.empirical_quality != null}
												<span class="font-mono tabular-nums text-xs font-bold {scoreColor(m.empirical_quality, 1)}">{pct01(m.empirical_quality)}</span>
												<span class="ml-1 text-[9px] text-muted-foreground">n={m.n_trials}</span>
											{:else}
												<span class="text-xs text-muted-foreground">—</span>
											{/if}
										</td>
										<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor(m.mss, 1)}">{pct01(m.mss)}</td>
										<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor(m.composite, 1)}">{pct01(m.composite)}</td>
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
				<Card.Root>
					<Card.Header>
						<Card.Title>Tool Effectiveness</Card.Title>
					</Card.Header>
					<Card.Content class="p-0">
						{#if data.tools.length === 0}
							<p class="p-6 text-center text-sm text-muted-foreground">No analyzer runs yet.</p>
						{:else}
							<div class="table-scroll-wrapper">
								<table class="w-full text-sm">
									<thead>
										<tr class="border-b bg-muted/40 sticky top-0 z-10">
											<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Tool</th>
											<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Type</th>
											<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Scans</th>
											<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Findings</th>
											<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Avg/Scan</th>
											<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Top Rule</th>
										</tr>
									</thead>
									<tbody>
										{#each data.tools as t, i}
											<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'}">
												<td class="px-3 py-2 align-top text-xs font-medium">{t.name}</td>
												<td class="px-3 py-2 align-top">
													<Badge variant="secondary" class="text-[10px] capitalize">{t.type}</Badge>
												</td>
												<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs">{t.scans}</td>
												<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs">{t.findings}</td>
												<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs">{t.avg_per_scan.toFixed(1)}</td>
												<td class="px-3 py-2 align-top font-mono text-[10px] text-muted-foreground">
													{t.top_rule || '—'}
												</td>
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
						<AlertTriangle class="h-4 w-4 text-amber-500" />
						<Card.Title>Top Findings</Card.Title>
					</div>
				</Card.Header>
				<Card.Content class="space-y-3">
					{#if data.top_findings.length === 0}
						<p class="text-center text-xs text-muted-foreground">No findings yet.</p>
					{:else}
						{#each data.top_findings as f}
							<div class="flex items-start gap-2">
								<Badge
									variant="outline"
									class="shrink-0 text-[9px] {severityColors[f.severity] ?? ''}"
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
				<Card.Root>
					<Card.Header>
						<div class="flex items-center gap-2">
							<Code class="h-4 w-4 text-muted-foreground" />
							<Card.Title>Code Generation Stats</Card.Title>
						</div>
					</Card.Header>
					<Card.Content>
						{@const cg = data.code_generation}
						<div class="grid gap-4 sm:grid-cols-3">
							{#each [{ label: 'Total Apps Generated', value: fmtNumber(cg.total_apps) }, { label: 'Success Rate', value: `${cg.success_rate}%` }, { label: 'Avg Gen Time', value: fmtDuration(cg.avg_duration_seconds) }, { label: 'Lines of Code', value: fmtNumber(cg.total_lines_of_code) }, { label: 'Total Tokens', value: fmtNumber(cg.total_tokens) }, { label: 'Total Cost', value: fmtCost(cg.total_cost_usd) }] as stat}
								<div class="kpi-card">
									<div class="text-xs text-muted-foreground uppercase tracking-wider">{stat.label}</div>
									<div class="text-2xl font-semibold font-mono tabular-nums">{stat.value}</div>
								</div>
							{/each}
						</div>
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
						<p class="text-center text-xs text-muted-foreground">No recent activity.</p>
					{:else}
						{#each data.recent_activity.slice(0, 8) as item}
							<div class="flex items-center justify-between gap-2 text-xs">
								<div class="flex min-w-0 items-center gap-2">
									<Badge variant="outline" class="text-[9px] capitalize">{item.kind}</Badge>
									<span class="truncate">{item.title}</span>
								</div>
								<span class="shrink-0 text-[10px] text-muted-foreground">
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
