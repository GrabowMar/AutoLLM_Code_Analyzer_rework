<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
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
	} from '$lib/api/client';
	import { listRuns, type AnalysisRunListItem } from '$lib/api/runs';
	import { formatApiError } from '$lib/api/core';
	import {
		timeAgo,
		normalizeStatus,
		type RecentAnalysis,
		type RecentApp,
	} from '$lib/utils/dashboard';
	import HeroPanel from '$lib/components/dashboard/HeroPanel.svelte';
	import TrendPanel from '$lib/components/dashboard/TrendPanel.svelte';
	import SeverityPanel from '$lib/components/dashboard/SeverityPanel.svelte';
	import AnalyzerHealthGrid from '$lib/components/dashboard/AnalyzerHealthGrid.svelte';
	import RecentRunsTable from '$lib/components/dashboard/RecentRunsTable.svelte';
	import RecentJobsTable from '$lib/components/dashboard/RecentJobsTable.svelte';
	import ActivityFeed from '$lib/components/dashboard/ActivityFeed.svelte';

	const AUTO_REFRESH_MS = 60_000;

	let totalAnalyses = $state<number | null>(null);
	let completedAnalyses = $state<number | null>(null);
	let runningAnalyses = $state<number | null>(null);
	let recentAnalyses = $state<RecentAnalysis[]>([]);
	let recentApps = $state<RecentApp[]>([]);
	let overview = $state<StatisticsOverview | null>(null);
	let liveActivity = $state<RecentActivityItem[]>([]);
	let analyzerHealth = $state<AnalyzerHealth | null>(null);
	let severity = $state<SeverityDistribution | null>(null);
	let trends = $state<AnalysisTrends | null>(null);
	let isLoading = $state(true);
	let isRefreshing = $state(false);
	let pendingAction = $state<string | null>(null);
	let lastRefreshed = $state<Date | null>(null);
	let inFlight = false;

	async function loadAll(opts: { silent?: boolean } = {}) {
		if (inFlight) return;
		inFlight = true;
		if (!opts.silent) isRefreshing = true;
		try {
			const [
				runsResult,
				completedResult,
				runningResult,
				overviewResult,
				activityResult,
				healthResult,
				severityResult,
				trendsResult,
				jobsResult,
			] = await Promise.allSettled([
				listRuns({ per_page: 5 }),
				listRuns({ status: 'completed', per_page: 1 }),
				listRuns({ status: 'running', per_page: 1 }),
				getStatisticsOverview(),
				getStatisticsRecentActivity(8),
				getStatisticsAnalyzerHealth(),
				getStatisticsSeverity(),
				getStatisticsTrends(30),
				getGenerationJobs({ per_page: 5 }),
			]);

			totalAnalyses = runsResult.status === 'fulfilled' ? runsResult.value.total : null;
			completedAnalyses =
				completedResult.status === 'fulfilled' ? completedResult.value.total : null;
			runningAnalyses = runningResult.status === 'fulfilled' ? runningResult.value.total : null;

			recentAnalyses =
				runsResult.status === 'fulfilled'
					? runsResult.value.items.map((run: AnalysisRunListItem) => ({
							id: run.id,
							name: run.name,
							status: normalizeStatus(run.status),
							time: timeAgo(run.created_at),
						}))
					: [];

			overview = overviewResult.status === 'fulfilled' ? overviewResult.value : null;
			liveActivity = activityResult.status === 'fulfilled' ? activityResult.value : [];
			analyzerHealth = healthResult.status === 'fulfilled' ? healthResult.value : null;
			severity = severityResult.status === 'fulfilled' ? severityResult.value : null;
			trends = trendsResult.status === 'fulfilled' ? trendsResult.value : null;

			recentApps =
				jobsResult.status === 'fulfilled'
					? jobsResult.value.items.map((j: GenerationJobList) => ({
							id: j.id,
							name: j.template_name || j.scaffolding_name || `Job ${j.id.slice(0, 8)}`,
							model: j.model_name || j.model_id_str || '—',
							status: normalizeStatus(j.status),
							time: timeAgo(j.created_at),
						}))
					: [];

			lastRefreshed = new Date();
		} finally {
			inFlight = false;
			isRefreshing = false;
			isLoading = false;
		}
	}

	onMount(() => {
		loadAll();
		// Silent auto-refresh keeps the dashboard live; paused while the tab
		// is hidden or a maintenance action is running.
		const interval = setInterval(() => {
			if (document.hidden || pendingAction !== null) return;
			loadAll({ silent: true });
		}, AUTO_REFRESH_MS);
		return () => clearInterval(interval);
	});

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

	const analysesSubtitle = $derived(
		completedAnalyses !== null ? `${completedAnalyses} completed` : ''
	);
	const analysesChange = $derived(runningAnalyses !== null ? `${runningAnalyses} running` : '');
</script>

<!-- Headline counts: the header's footer strip; each links to its page. -->
{#snippet headerStats()}
	<div class="header-stats">
		<a href="/models" class="header-stat hover:text-foreground" title={overview ? `${overview.total_apps} total apps` : undefined}>
			<b>{overview ? overview.models_in_use.toLocaleString() : '—'}</b> models in use
		</a>
		<a href="/applications" class="header-stat hover:text-foreground" title={overview ? `${overview.apps_success_rate}% success` : undefined}>
			<b>{overview ? overview.total_apps.toLocaleString() : '—'}</b> apps{#if overview}<span>· {overview.apps_completed} completed</span>{/if}
		</a>
		<a href="/analysis" class="header-stat hover:text-foreground">
			<b>{totalAnalyses ?? '—'}</b> analyses{#if analysesSubtitle}<span>· {analysesSubtitle}</span>{/if}{#if analysesChange}<span>· {analysesChange}</span>{/if}
		</a>
		<a href="/statistics" class="header-stat hover:text-foreground" title={overview ? `${overview.analyses_completed} analyses` : undefined}>
			<b>{overview ? overview.total_findings.toLocaleString() : '—'}</b> findings{#if overview}<span>· {overview.total_apps ? (overview.total_findings / overview.total_apps).toFixed(1) : 0}/app avg</span>{/if}
		</a>
	</div>
{/snippet}

<div class="bento gap-4">
	<div class="b-12 rise-in" style="--stagger-i: 0">
		<HeroPanel
			{isRefreshing}
			{lastRefreshed}
			{pendingAction}
			onRefresh={handleRefresh}
			onSyncModels={handleSyncModels}
			onClearCaches={handleClearCaches}
			onClearStuck={handleClearStuck}
			stats={headerStats}
		/>
	</div>

	<!-- Insights: trend + severity -->
	<div class="b-8 rise-in" style="--stagger-i: 2">
		<TrendPanel {trends} loading={isLoading} />
	</div>
	<div class="b-4 rise-in" style="--stagger-i: 3">
		<SeverityPanel {severity} loading={isLoading} />
	</div>

	<!-- Analyzer health tiles -->
	<div class="b-12 rise-in" style="--stagger-i: 4">
		<AnalyzerHealthGrid health={analyzerHealth} {isRefreshing} onRefresh={handleRefresh} />
	</div>

	<!-- Tables + side column -->
	<div class="b-8 rise-in space-y-4" style="--stagger-i: 5">
		<RecentRunsTable analyses={recentAnalyses} />
		<RecentJobsTable jobs={recentApps} />
	</div>
	<div class="b-4 rise-in space-y-4" style="--stagger-i: 6">
		<ActivityFeed items={liveActivity} {isRefreshing} onRefresh={handleRefresh} />
	</div>
</div>
