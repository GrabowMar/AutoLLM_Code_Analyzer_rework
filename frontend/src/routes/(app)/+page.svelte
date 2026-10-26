<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAuth } from '$lib/stores/auth.svelte';
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
		type GenerationJobList
	} from '$lib/api/client';
	import { listAnalysisRuns, type AnalysisRunListItem } from '$lib/api/analysis';
	import { formatApiError } from '$lib/api/core';
	import { timeAgo, normalizeStatus, type RecentApp } from '$lib/utils/dashboard';
	import HeroPanel from '$lib/components/dashboard/HeroPanel.svelte';
	import KpiCard from '$lib/components/dashboard/KpiCard.svelte';
	import TrendPanel from '$lib/components/dashboard/TrendPanel.svelte';
	import SeverityPanel from '$lib/components/dashboard/SeverityPanel.svelte';
	import AnalyzerHealthGrid from '$lib/components/dashboard/AnalyzerHealthGrid.svelte';
	import RecentRunsTable from '$lib/components/dashboard/RecentRunsTable.svelte';
	import RecentJobsTable from '$lib/components/dashboard/RecentJobsTable.svelte';
	import ActivityFeed from '$lib/components/dashboard/ActivityFeed.svelte';
	import QuickActions from '$lib/components/dashboard/QuickActions.svelte';
	import Boxes from '@lucide/svelte/icons/boxes';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';

	const auth = getAuth();

	const AUTO_REFRESH_MS = 60_000;

	let totalAnalyses = $state<number | null>(null);
	let completedAnalyses = $state<number | null>(null);
	let runningAnalyses = $state<number | null>(null);
	let recentAnalyses = $state<AnalysisRunListItem[]>([]);
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
				jobsResult
			] = await Promise.allSettled([
				listAnalysisRuns({ per_page: 5 }),
				// Complete = every required tool succeeded or was not applicable.
				listAnalysisRuns({ status: ['completed', 'partial'], is_complete: true, per_page: 1 }),
				listAnalysisRuns({ status: ['queued', 'running'], per_page: 1 }),
				getStatisticsOverview(),
				getStatisticsRecentActivity(8),
				getStatisticsAnalyzerHealth(),
				getStatisticsSeverity(),
				getStatisticsTrends(30),
				getGenerationJobs({ per_page: 5 })
			]);

			totalAnalyses = runsResult.status === 'fulfilled' ? runsResult.value.total : null;
			completedAnalyses =
				completedResult.status === 'fulfilled' ? completedResult.value.total : null;
			runningAnalyses = runningResult.status === 'fulfilled' ? runningResult.value.total : null;

			recentAnalyses = runsResult.status === 'fulfilled' ? runsResult.value.items : [];

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
							time: timeAgo(j.created_at)
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

	// allauth's `display` falls back to the email; greet by the local part instead.
	const userName = $derived.by(() => {
		if (!auth.isAuthenticated || !auth.user) return null;
		const name = auth.user.name || auth.user.display || auth.user.email;
		return name.includes('@') ? name.split('@')[0] : name;
	});

	const analysesSubtitle = $derived(
		completedAnalyses !== null ? `${completedAnalyses} complete` : ''
	);
	const analysesChange = $derived(runningAnalyses !== null ? `${runningAnalyses} in progress` : '');

	// Counted static findings per statically measured job; with none the total
	// is no data (the KPI shows "—"), never 0 findings.
	const findingsSubtitle = $derived.by(() => {
		if (!overview) return 'Loading…';
		if (overview.total_findings == null) return 'No static measurement yet';
		const jobs = overview.static_measured_jobs ?? overview.measured_jobs ?? null;
		if (!jobs) return 'counted static findings';
		return `${(overview.total_findings / jobs).toFixed(1)} per measured job`;
	});
</script>

<svelte:head>
	<title>Dashboard · LLM Lab</title>
</svelte:head>

<div class="bento gap-4 sm:gap-5">
	<div class="b-12 rise-in" style="--stagger-i: 0">
		<HeroPanel {userName} {isRefreshing} {lastRefreshed} onRefresh={handleRefresh} />
	</div>

	<!-- KPI row -->
	<div class="b-12 rise-in grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4" style="--stagger-i: 1">
		<KpiCard
			title="Models"
			value={overview ? overview.models_in_use : null}
			subtitle="Models in use"
			change={overview ? `${overview.total_apps} total apps` : ''}
			icon={Boxes}
			href="/models"
			accent="cyan"
		/>
		<KpiCard
			title="Applications"
			value={overview ? overview.total_apps : null}
			subtitle={overview ? `${overview.apps_completed} completed` : 'Loading…'}
			change={overview ? `${overview.apps_success_rate}% success` : ''}
			icon={AppWindow}
			href="/applications"
			accent="green"
		/>
		<KpiCard
			title="Analyses"
			value={totalAnalyses}
			subtitle={analysesSubtitle || 'Loading…'}
			change={analysesChange}
			icon={BarChart3}
			href="/analysis"
			accent="amber"
		/>
		<KpiCard
			title="Findings"
			value={overview ? overview.total_findings : null}
			subtitle={findingsSubtitle}
			change={overview
				? overview.static_measured_jobs != null
					? `${overview.static_measured_jobs} statically measured job${overview.static_measured_jobs === 1 ? '' : 's'}`
					: `${overview.analyses_completed} analyses`
				: ''}
			icon={ShieldAlert}
			href="/statistics"
			accent="violet"
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
	<div class="b-8 rise-in space-y-4 sm:space-y-5" style="--stagger-i: 5">
		<RecentRunsTable runs={recentAnalyses} loading={isLoading} />
		<RecentJobsTable jobs={recentApps} />
	</div>
	<div class="b-4 rise-in space-y-4 sm:space-y-5" style="--stagger-i: 6">
		<QuickActions
			{isRefreshing}
			{pendingAction}
			onRefresh={handleRefresh}
			onSyncModels={handleSyncModels}
			onClearCaches={handleClearCaches}
			onClearStuck={handleClearStuck}
		/>
		<ActivityFeed items={liveActivity} {isRefreshing} onRefresh={handleRefresh} />
	</div>
</div>
