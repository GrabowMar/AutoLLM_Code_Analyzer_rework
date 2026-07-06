<script lang="ts">
	import { generationStatusColors as statusColors } from '$lib/constants/colors';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAuth } from '$lib/stores/auth.svelte';
	import {
		getAnalysisStats,
		getAnalysisTasks,
		getStatisticsOverview,
		getStatisticsRecentActivity,
		getStatisticsAnalyzerHealth,
		getStatisticsSeverity,
		getStatisticsTrends,
		getGenerationJobs,
		clearCaches,
		clearStuckAnalysis,
		clearStuckGeneration,
		syncModelsFromOpenRouter,
		type StatisticsOverview,
		type RecentActivityItem,
		type AnalyzerHealth,
		type SeverityDistribution,
		type AnalysisTrends,
		type GenerationJobList,
		type AnalysisTaskList,
	} from '$lib/api/client';
	import { formatApiError } from '$lib/api/core';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Separator } from '$lib/components/ui/separator';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import Boxes from '@lucide/svelte/icons/boxes';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import FileText from '@lucide/svelte/icons/file-text';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Activity from '@lucide/svelte/icons/activity';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Clock from '@lucide/svelte/icons/clock';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import CloudDownload from '@lucide/svelte/icons/cloud-download';
	import Eraser from '@lucide/svelte/icons/eraser';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Cpu from '@lucide/svelte/icons/cpu';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import Plus from '@lucide/svelte/icons/plus';
	import Wand2 from '@lucide/svelte/icons/wand-2';
	import TrendingUp from '@lucide/svelte/icons/trending-up';
	import HeartPulse from '@lucide/svelte/icons/heart-pulse';
	import Loader2 from '@lucide/svelte/icons/loader-2';
	import type { Component } from 'svelte';

	const auth = getAuth();

	function timeAgo(dateStr: string): string {
		const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
		if (seconds < 60) return 'just now';
		if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
		if (seconds < 86400) {
			const h = Math.floor(seconds / 3600);
			return `${h} hour${h > 1 ? 's' : ''} ago`;
		}
		const d = Math.floor(seconds / 86400);
		return `${d} day${d > 1 ? 's' : ''} ago`;
	}

	function greeting(): string {
		const h = new Date().getHours();
		if (h < 5) return 'Good night';
		if (h < 12) return 'Good morning';
		if (h < 18) return 'Good afternoon';
		return 'Good evening';
	}

	const validStatuses = ['completed', 'running', 'failed', 'pending', 'cancelled'] as const;
	type AnalysisStatus = (typeof validStatuses)[number];

	function normalizeStatus(status: string): AnalysisStatus {
		const lower = status.toLowerCase();
		if (validStatuses.includes(lower as AnalysisStatus)) return lower as AnalysisStatus;
		return 'pending';
	}

	interface RecentAnalysis {
		id: string;
		name: string;
		status: AnalysisStatus;
		time: string;
	}

	interface RecentApp {
		id: string;
		name: string;
		model: string;
		status: AnalysisStatus;
		time: string;
	}

	let analysisStatsValue = $state<string>('—');
	let analysisStatsSubtitle = $state<string>('Loading…');
	let analysisStatsChange = $state<string>('');
	let recentAnalyses = $state<RecentAnalysis[]>([]);
	let recentApps = $state<RecentApp[]>([]);
	let overview = $state<StatisticsOverview | null>(null);
	let liveActivity = $state<RecentActivityItem[]>([]);
	let analyzerHealth = $state<AnalyzerHealth | null>(null);
	let severity = $state<SeverityDistribution | null>(null);
	let trends = $state<AnalysisTrends | null>(null);
	let isRefreshing = $state(false);
	let pendingAction = $state<string | null>(null);
	let lastRefreshed = $state<Date | null>(null);

	async function loadAll() {
		isRefreshing = true;
		const [
			statsResult,
			tasksResult,
			overviewResult,
			activityResult,
			healthResult,
			severityResult,
			trendsResult,
			jobsResult,
		] = await Promise.allSettled([
			getAnalysisStats(),
			getAnalysisTasks({ per_page: 5 }),
			getStatisticsOverview(),
			getStatisticsRecentActivity(8),
			getStatisticsAnalyzerHealth(),
			getStatisticsSeverity(),
			getStatisticsTrends(14),
			getGenerationJobs({ per_page: 5 }),
		]);

		if (statsResult.status === 'fulfilled') {
			const stats = statsResult.value;
			analysisStatsValue = String(stats.total_tasks);
			analysisStatsSubtitle = `${stats.completed_tasks} completed`;
			analysisStatsChange = `${stats.running_tasks} running`;
		} else {
			analysisStatsValue = '—';
			analysisStatsSubtitle = 'Unable to load';
			analysisStatsChange = '';
		}

		recentAnalyses = tasksResult.status === 'fulfilled'
			? tasksResult.value.items.map((task: AnalysisTaskList) => ({
				id: task.id,
				name: task.name,
				status: normalizeStatus(task.status),
				time: timeAgo(task.created_at),
			}))
			: [];

		overview = overviewResult.status === 'fulfilled' ? overviewResult.value : null;
		liveActivity = activityResult.status === 'fulfilled' ? activityResult.value : [];
		analyzerHealth = healthResult.status === 'fulfilled' ? healthResult.value : null;
		severity = severityResult.status === 'fulfilled' ? severityResult.value : null;
		trends = trendsResult.status === 'fulfilled' ? trendsResult.value : null;

		recentApps = jobsResult.status === 'fulfilled'
			? jobsResult.value.items.map((j: GenerationJobList) => ({
				id: j.id,
				name: j.template_name || j.scaffolding_name || `Job ${j.id.slice(0, 8)}`,
				model: j.model_name || j.model_id_str || '—',
				status: normalizeStatus(j.status),
				time: timeAgo(j.created_at),
			}))
			: [];

		lastRefreshed = new Date();
		isRefreshing = false;
	}

	onMount(loadAll);

	async function handleRefresh() {
		await loadAll();
		toast.success('Dashboard refreshed');
	}

	async function handleSyncModels() {
		pendingAction = 'sync';
		try {
			const result = await syncModelsFromOpenRouter();
			toast.success(`Models synced: ${result.upserted} of ${result.fetched} records`);
			await loadAll();
		} catch (e) {
			toast.error(formatApiError(e, 'Sync failed.'));
		} finally {
			pendingAction = null;
		}
	}

	async function handleClearCaches() {
		pendingAction = 'caches';
		try {
			const result = await clearCaches();
			if (result.success) toast.success(result.message || 'Caches cleared');
			else toast.error(result.error || 'Failed to clear caches');
		} catch (e) {
			toast.error(`Failed: ${(e as Error).message}`);
		} finally {
			pendingAction = null;
		}
	}

	async function handleClearStuck() {
		pendingAction = 'stuck';
		try {
			const [a, g] = await Promise.all([clearStuckAnalysis(60), clearStuckGeneration(60)]);
			toast.success(`Released ${a.updated} stuck analyses and ${g.updated} generation jobs`);
			await loadAll();
		} catch (e) {
			toast.error(`Failed: ${(e as Error).message}`);
		} finally {
			pendingAction = null;
		}
	}

	interface SummaryCard {
		title: string;
		value: () => string;
		subtitle: () => string;
		change: () => string;
		icon: Component;
		href: string;
		accent: string;
		ring: string;
	}

	const summaryCards: SummaryCard[] = [
		{
			title: 'Models',
			value: () => (overview ? String(overview.models_in_use) : '—'),
			subtitle: () => 'Models in use',
			change: () => (overview ? `${overview.total_apps} total apps` : ''),
			icon: Boxes,
			href: '/models',
			accent: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/50',
			ring: 'group-hover:ring-blue-500/20',
		},
		{
			title: 'Applications',
			value: () => (overview ? String(overview.total_apps) : '—'),
			subtitle: () => (overview ? `${overview.apps_completed} completed` : 'Loading…'),
			change: () => (overview ? `${overview.apps_success_rate}% success` : ''),
			icon: AppWindow,
			href: '/applications',
			accent: 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/50',
			ring: 'group-hover:ring-emerald-500/20',
		},
		{
			title: 'Analyses',
			value: () => analysisStatsValue,
			subtitle: () => analysisStatsSubtitle,
			change: () => analysisStatsChange,
			icon: BarChart3,
			href: '/analysis',
			accent: 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/50',
			ring: 'group-hover:ring-amber-500/20',
		},
		{
			title: 'Findings',
			value: () => (overview ? String(overview.total_findings) : '—'),
			subtitle: () =>
				overview
					? `${overview.total_apps ? (overview.total_findings / overview.total_apps).toFixed(1) : 0}/app avg`
					: 'Loading…',
			change: () => (overview ? `${overview.analyses_completed} analyses` : ''),
			icon: ShieldAlert,
			href: '/statistics',
			accent: 'text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-950/50',
			ring: 'group-hover:ring-violet-500/20',
		},
	];

	const severityColors: Record<string, string> = {
		critical: 'bg-red-500',
		high: 'bg-orange-500',
		medium: 'bg-amber-500',
		low: 'bg-blue-500',
		info: 'bg-slate-400',
	};

	// Build SVG sparkline path for the trends data
	const sparklinePath = $derived.by(() => {
		const series = trends?.series ?? [];
		if (series.length < 2) return { line: '', fill: '', max: 0, points: [] as Array<{ x: number; y: number; date: string; total: number }> };
		const max = Math.max(1, ...series.map((p) => p.total));
		const w = 100;
		const h = 32;
		const stepX = w / (series.length - 1);
		const points = series.map((p, i) => ({
			x: +(i * stepX).toFixed(2),
			y: +(h - (p.total / max) * (h - 4) - 2).toFixed(2),
			date: p.date,
			total: p.total,
		}));
		const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x} ${p.y}`).join(' ');
		const fill = `${line} L${w} ${h} L0 ${h} Z`;
		return { line, fill, max, points };
	});
</script>

<svelte:head>
	<title>Dashboard - LLM Lab</title>
</svelte:head>

<div class="space-y-6 sm:space-y-8">
	<!-- Hero header -->
	<div class="relative overflow-hidden rounded-md border border-border bg-card p-4 sm:p-5">
		<div class="pointer-events-none absolute -top-16 -right-16 h-48 w-48 rounded-full bg-primary/10 blur-3xl" aria-hidden="true"></div>
		<div class="relative flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
			<div class="space-y-1">
				<div class="flex items-center gap-2 text-xs font-medium text-muted-foreground" style="font-family: var(--font-mono);">
					<Sparkles class="h-3 w-3 text-primary" />
					<span>{greeting()}</span>
					<span class="text-primary" aria-hidden="true">/</span>
					<span>{new Date().toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}</span>
				</div>
				<h1 class="text-lg font-bold tracking-tight sm:text-2xl cursor-blink truncate" style="font-family: var(--font-display);">
					{#if auth.isAuthenticated && auth.user}
						<span class="text-muted-foreground">$&nbsp;</span>welcome&nbsp;<span class="text-primary break-all">{auth.user.display || auth.user.email.split('@')[0]}</span>
					{:else}
						<span class="text-muted-foreground">$&nbsp;</span>research_dashboard
					{/if}
				</h1>
				<p class="text-sm text-muted-foreground" style="font-family: var(--font-mono);">
					// llm evaluation · code generation · security analysis
				</p>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<Button href="/sample-generator" size="sm" class="gap-1.5">
					<Wand2 class="h-3.5 w-3.5" />
					Generate App
				</Button>
				<Button href="/analysis/create" size="sm" variant="outline" class="gap-1.5">
					<Plus class="h-3.5 w-3.5" />
					New Analysis
				</Button>
				<Button onclick={handleRefresh} size="icon" variant="ghost" class="h-9 w-9" disabled={isRefreshing} aria-label="Refresh dashboard">
					{#if isRefreshing}
						<Loader2 class="h-4 w-4 animate-spin" />
					{:else}
						<RefreshCw class="h-4 w-4" />
					{/if}
				</Button>
			</div>
		</div>
		{#if lastRefreshed}
			<p class="relative mt-3 text-[11px] text-muted-foreground">
				Updated {timeAgo(lastRefreshed.toISOString())}
			</p>
		{/if}
	</div>

	<!-- Summary Cards -->
	<div class="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
		{#each summaryCards as card (card.title)}
			<a href={card.href} class="group block">
				<Card.Root class="relative h-full overflow-hidden ring-1 ring-transparent transition-all hover:border-primary/30 hover:shadow-md {card.ring}">
					<Card.Header class="flex flex-row items-center justify-between space-y-0 px-3 pb-1 pt-3 sm:px-5 sm:pb-2 sm:pt-5">
						<Card.Title class="text-xs font-medium text-muted-foreground sm:text-sm">{card.title}</Card.Title>
						<div class="flex h-7 w-7 items-center justify-center rounded-lg sm:h-8 sm:w-8 {card.accent}">
							<card.icon class="h-3.5 w-3.5 sm:h-4 sm:w-4" />
						</div>
					</Card.Header>
					<Card.Content class="px-3 pb-3 sm:px-5 sm:pb-5">
						<div class="text-xl font-bold tabular-nums sm:text-2xl">{card.value()}</div>
						<p class="mt-0.5 text-[11px] text-muted-foreground sm:mt-1 sm:text-xs">{card.subtitle()}</p>
						<p class="mt-1 text-[11px] text-emerald-600 dark:text-emerald-400 sm:mt-2 sm:text-xs">{card.change()}</p>
					</Card.Content>
					<div class="absolute bottom-3 right-4 opacity-0 transition-opacity group-hover:opacity-100">
						<ArrowRight class="h-4 w-4 text-muted-foreground" />
					</div>
				</Card.Root>
			</a>
		{/each}
	</div>

	<!-- Insights row: trends + severity -->
	<div class="grid gap-6 lg:grid-cols-2">
		<!-- Analysis trend sparkline -->
		<Card.Root>
			<Card.Header>
				<div class="flex items-center justify-between">
					<div class="flex items-center gap-2">
						<TrendingUp class="h-4 w-4 text-muted-foreground" />
						<div>
							<Card.Title>Analysis Activity</Card.Title>
							<Card.Description>Last 14 days</Card.Description>
						</div>
					</div>
					<div class="text-right">
						<div class="text-xl font-bold tabular-nums">{trends?.total ?? 0}</div>
						<p class="text-[11px] text-muted-foreground">total runs</p>
					</div>
				</div>
			</Card.Header>
			<Card.Content>
				{#if trends && trends.series.length > 1}
					<div class="space-y-3">
						<svg viewBox="0 0 100 32" class="h-20 w-full" preserveAspectRatio="none" role="img" aria-label="14-day analysis trend">
							<defs>
								<linearGradient id="sparkfill" x1="0" x2="0" y1="0" y2="1">
									<stop offset="0%" stop-color="currentColor" stop-opacity="0.25" />
									<stop offset="100%" stop-color="currentColor" stop-opacity="0" />
								</linearGradient>
							</defs>
							<path d={sparklinePath.fill} fill="url(#sparkfill)" class="text-primary" />
							<path d={sparklinePath.line} fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="text-primary" vector-effect="non-scaling-stroke" />
						</svg>
						<div class="flex justify-between text-[10px] text-muted-foreground">
							<span>{trends.series[0]?.date.slice(5)}</span>
							<span>peak {sparklinePath.max}</span>
							<span>{trends.series[trends.series.length - 1]?.date.slice(5)}</span>
						</div>
					</div>
				{:else}
					<div class="flex h-20 items-center justify-center text-xs text-muted-foreground">
						Not enough data to chart yet.
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Severity distribution -->
		<Card.Root>
			<Card.Header>
				<div class="flex items-center justify-between">
					<div class="flex items-center gap-2">
						<ShieldAlert class="h-4 w-4 text-muted-foreground" />
						<div>
							<Card.Title>Finding Severity</Card.Title>
							<Card.Description>Distribution across all analyses</Card.Description>
						</div>
					</div>
					<div class="text-right">
						<div class="text-xl font-bold tabular-nums">{severity?.total ?? 0}</div>
						<p class="text-[11px] text-muted-foreground">findings</p>
					</div>
				</div>
			</Card.Header>
			<Card.Content>
				{#if severity && severity.total > 0}
					<div class="space-y-3">
						<!-- Stacked bar -->
						<div class="flex h-2.5 w-full overflow-hidden rounded-full bg-muted">
							{#each severity.distribution as bucket (bucket.severity)}
								{#if bucket.count > 0}
									<div
										class={severityColors[bucket.severity.toLowerCase()] ?? 'bg-slate-400'}
										style="width: {bucket.percent}%"
										title="{bucket.severity}: {bucket.count} ({bucket.percent}%)"
									></div>
								{/if}
							{/each}
						</div>
						<!-- Legend -->
						<div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3">
							{#each severity.distribution as bucket (bucket.severity)}
								<div class="flex items-center justify-between gap-2">
									<div class="flex min-w-0 items-center gap-1.5">
										<span class="h-2 w-2 shrink-0 rounded-full {severityColors[bucket.severity.toLowerCase()] ?? 'bg-slate-400'}"></span>
										<span class="truncate capitalize">{bucket.severity}</span>
									</div>
									<span class="tabular-nums text-muted-foreground">{bucket.count}</span>
								</div>
							{/each}
						</div>
					</div>
				{:else}
					<div class="flex h-20 items-center justify-center text-xs text-muted-foreground">
						No findings recorded yet.
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Main content grid -->
	<div class="grid gap-6 lg:grid-cols-12">
		<!-- Left Column -->
		<div class="space-y-6 lg:col-span-8">
			<!-- Analyzer Health (real data) -->
			<Card.Root>
				<Card.Header>
					<div class="flex items-center justify-between">
						<div class="flex items-center gap-2">
							<HeartPulse class="h-4 w-4 text-muted-foreground" />
							<div>
								<Card.Title>Analyzer Health</Card.Title>
								<Card.Description>
									{#if analyzerHealth}
										<span class="text-emerald-600 dark:text-emerald-400 font-medium">{analyzerHealth.online}</span> online ·
										<span class="text-destructive font-medium">{analyzerHealth.offline}</span> offline
									{:else}
										Loading…
									{/if}
								</Card.Description>
							</div>
						</div>
						<Button variant="ghost" size="icon" class="h-7 w-7" onclick={handleRefresh} disabled={isRefreshing} aria-label="Refresh">
							<RefreshCw class="h-3.5 w-3.5 {isRefreshing ? 'animate-spin' : ''}" />
						</Button>
					</div>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="table-card-mobile w-full">
							<thead>
								<tr class="border-b bg-muted/30">
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Analyzer</th>
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Type</th>
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
								</tr>
							</thead>
							<tbody class="divide-y">
								{#if analyzerHealth}
									{#each analyzerHealth.analyzers as a (a.name)}
										<tr class="transition-colors hover:bg-muted/30">
											<td class="px-4 py-2.5" data-label="Analyzer">
												<div class="flex items-center gap-2.5">
													{#if a.available}
														<CircleCheck class="h-4 w-4 text-emerald-500" />
													{:else}
														<CircleX class="h-4 w-4 text-destructive" />
													{/if}
													<span class="text-sm font-medium">{a.display_name}</span>
												</div>
											</td>
											<td class="px-4 py-2.5" data-label="Type">
												<span class="text-xs uppercase tracking-wide text-muted-foreground">{a.type}</span>
											</td>
											<td class="px-4 py-2.5" data-label="Status">
												<span class="text-xs {a.available ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}">
													{a.message}
												</span>
											</td>
										</tr>
									{/each}
								{:else}
									<tr><td colspan="3" class="px-4 py-6 text-center text-sm text-muted-foreground">Loading analyzers…</td></tr>
								{/if}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<!-- Recent Analyses -->
			<Card.Root>
				<Card.Header>
					<div class="flex items-center justify-between">
						<div class="flex items-center gap-2">
							<FlaskConical class="h-4 w-4 text-muted-foreground" />
							<div>
								<Card.Title>Recent Analyses</Card.Title>
								<Card.Description>Latest analysis tasks and their status.</Card.Description>
							</div>
						</div>
						<Button variant="ghost" size="sm" href="/analysis" class="text-xs">
							View all
							<ArrowRight class="ml-1 h-3 w-3" />
						</Button>
					</div>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="table-card-mobile w-full">
							<thead>
								<tr class="border-b bg-muted/30">
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Task</th>
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Time</th>
								</tr>
							</thead>
							<tbody class="divide-y">
								{#each recentAnalyses as analysis (analysis.id)}
									<tr class="transition-colors hover:bg-muted/30">
										<td class="px-4 py-2.5" data-label="Task">
											<a href="/analysis/{analysis.id}" class="text-sm font-medium hover:text-primary hover:underline">{analysis.name}</a>
										</td>
										<td class="px-4 py-2.5" data-label="Status">
											<span class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium {statusColors[analysis.status]}">
												{analysis.status}
											</span>
										</td>
										<td class="px-4 py-2.5" data-label="Time">
											<span class="text-xs text-muted-foreground">{analysis.time}</span>
										</td>
									</tr>
								{:else}
									<tr>
										<td colspan="3" class="px-4 py-6 text-center text-sm text-muted-foreground">No analyses yet — start one above.</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<!-- Recent Generation Jobs (real) -->
			<Card.Root>
				<Card.Header>
					<div class="flex items-center justify-between">
						<div class="flex items-center gap-2">
							<AppWindow class="h-4 w-4 text-muted-foreground" />
							<div>
								<Card.Title>Recent Generation Jobs</Card.Title>
								<Card.Description>Latest application generation runs.</Card.Description>
							</div>
						</div>
						<Button variant="ghost" size="sm" href="/sample-generator" class="text-xs">
							View all
							<ArrowRight class="ml-1 h-3 w-3" />
						</Button>
					</div>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="table-card-mobile w-full">
							<thead>
								<tr class="border-b bg-muted/30">
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Job</th>
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground hide-mobile">Model</th>
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
									<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Created</th>
								</tr>
							</thead>
							<tbody class="divide-y">
								{#each recentApps as app (app.id)}
									<tr class="transition-colors hover:bg-muted/30">
										<td class="px-4 py-2.5" data-label="Job">
											<span class="text-sm font-medium">{app.name}</span>
										</td>
										<td class="px-4 py-2.5 hide-mobile" data-label="Model">
											<span class="text-sm text-muted-foreground">{app.model}</span>
										</td>
										<td class="px-4 py-2.5" data-label="Status">
											<span class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium {statusColors[app.status]}">
												{app.status}
											</span>
										</td>
										<td class="px-4 py-2.5" data-label="Created">
											<span class="text-xs text-muted-foreground">{app.time}</span>
										</td>
									</tr>
								{:else}
									<tr>
										<td colspan="4" class="px-4 py-6 text-center text-sm text-muted-foreground">No generation jobs yet.</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Right Column -->
		<div class="space-y-6 lg:col-span-4">
			<!-- Quick Actions (functional) -->
			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<Cpu class="h-4 w-4 text-muted-foreground" />
						<div>
							<Card.Title>Quick Actions</Card.Title>
							<Card.Description>Maintenance shortcuts</Card.Description>
						</div>
					</div>
				</Card.Header>
				<Card.Content>
					<div class="grid grid-cols-2 gap-2">
						<Button variant="outline" size="sm" class="justify-start" onclick={handleRefresh} disabled={isRefreshing}>
							{#if isRefreshing}<Loader2 class="mr-2 h-3.5 w-3.5 animate-spin" />{:else}<RefreshCw class="mr-2 h-3.5 w-3.5 text-blue-500" />{/if}
							Refresh
						</Button>
						<Button variant="outline" size="sm" class="justify-start" onclick={handleSyncModels} disabled={pendingAction !== null}>
							{#if pendingAction === 'sync'}<Loader2 class="mr-2 h-3.5 w-3.5 animate-spin" />{:else}<CloudDownload class="mr-2 h-3.5 w-3.5 text-primary" />{/if}
							Sync Models
						</Button>
						<Button variant="outline" size="sm" class="justify-start" onclick={handleClearCaches} disabled={pendingAction !== null}>
							{#if pendingAction === 'caches'}<Loader2 class="mr-2 h-3.5 w-3.5 animate-spin" />{:else}<Eraser class="mr-2 h-3.5 w-3.5 text-amber-500" />{/if}
							Clear Caches
						</Button>
						<Button variant="outline" size="sm" class="justify-start" onclick={handleClearStuck} disabled={pendingAction !== null}>
							{#if pendingAction === 'stuck'}<Loader2 class="mr-2 h-3.5 w-3.5 animate-spin" />{:else}<Activity class="mr-2 h-3.5 w-3.5 text-emerald-500" />{/if}
							Clear Stuck
						</Button>
						<Button variant="outline" size="sm" class="col-span-2 justify-start" href="/system">
							<Cpu class="mr-2 h-3.5 w-3.5 text-muted-foreground" />
							Open System Panel
						</Button>
					</div>
				</Card.Content>
			</Card.Root>

			<!-- Activity Feed (real only — no mocks) -->
			<Card.Root>
				<Card.Header>
					<div class="flex items-center justify-between">
						<div class="flex items-center gap-2">
							<Clock class="h-4 w-4 text-muted-foreground" />
							<Card.Title>Recent Activity</Card.Title>
						</div>
						<Button variant="ghost" size="icon" class="h-7 w-7" onclick={handleRefresh} disabled={isRefreshing} aria-label="Refresh">
							<RefreshCw class="h-3.5 w-3.5 {isRefreshing ? 'animate-spin' : ''}" />
						</Button>
					</div>
				</Card.Header>
				<Card.Content>
					{#if liveActivity.length > 0}
						<div class="space-y-1">
							{#each liveActivity as event, i (event.kind + event.id)}
								{@const EvtIcon = event.kind === 'analysis' ? Activity : AppWindow}
								{@const evtColor =
									event.status === 'completed'
										? 'text-emerald-500'
										: event.status === 'failed'
											? 'text-destructive'
											: event.status === 'running'
												? 'text-amber-500'
												: 'text-blue-500'}
								<div class="flex items-start gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-muted/50">
									<div class="mt-0.5">
										<EvtIcon class="h-3.5 w-3.5 {evtColor}" />
									</div>
									<div class="min-w-0 flex-1">
										<p class="break-words text-xs leading-relaxed">
											<span class="capitalize">{event.kind}</span>: {event.title}
											<span class="text-muted-foreground">({event.status})</span>
										</p>
										<p class="mt-0.5 text-[11px] text-muted-foreground">{timeAgo(event.created_at)}</p>
									</div>
								</div>
								{#if i < liveActivity.length - 1}
									<Separator />
								{/if}
							{/each}
						</div>
					{:else}
						<div class="flex flex-col items-center justify-center py-8 text-center">
							<Activity class="h-8 w-8 text-muted-foreground/50" />
							<p class="mt-2 text-xs text-muted-foreground">No recent activity yet.</p>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>
	</div>
</div>
