<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import {
		getGenerationJobs,
		getGenerationJobsStats,
		cancelGenerationJob,
		deleteGenerationJob,
		retryGenerationJob,
		type GenerationJobList,
		type PaginatedJobs,
	} from '$lib/api/client';
	import {
		getContainers,
		buildContainerForJob,
		startContainer,
		stopContainer,
		removeContainer,
		type ContainerInstance,
	} from '$lib/api/runtime';
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Search from '@lucide/svelte/icons/search';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Eye from '@lucide/svelte/icons/eye';
	import Bot from '@lucide/svelte/icons/bot';
	import Pencil from '@lucide/svelte/icons/pencil';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Clock from '@lucide/svelte/icons/clock';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ChevronsLeft from '@lucide/svelte/icons/chevrons-left';
	import ChevronsRight from '@lucide/svelte/icons/chevrons-right';
	import X from '@lucide/svelte/icons/x';
	import Copy from '@lucide/svelte/icons/copy';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Ban from '@lucide/svelte/icons/ban';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import Server from '@lucide/svelte/icons/server';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Hammer from '@lucide/svelte/icons/hammer';
	import Layers from '@lucide/svelte/icons/layers';
	import { goto } from '$app/navigation';

	let loading = $state(true);
	let refreshing = $state(false);
	let data = $state<PaginatedJobs | null>(null);
	let containers = $state<ContainerInstance[]>([]);
	let containerBusy = $state<Record<string, string | null>>({});

	let searchQuery = $state('');
	let modeFilter = $state('');
	let statusFilter = $state('');
	let containerFilter = $state('');
	let sortOption = $state('created_desc');
	let currentPage = $state(1);
	let perPage = $state(25);

	let stats = $state<{
		total: number;
		completed: number;
		running: number;
		failed: number;
		pending: number;
		cancelled: number;
	} | null>(null);

	let pollTimer: ReturnType<typeof setInterval> | null = null;
	let debounceTimer: ReturnType<typeof setTimeout>;

	const statusColors: Record<string, string> = {
		completed: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30 dark:text-emerald-400 dark:border-emerald-500/20',
		failed: 'bg-red-500/10 text-red-500 border-red-500/30 dark:text-red-400 dark:border-red-500/20',
		running: 'bg-amber-500/10 text-amber-500 border-amber-500/30 dark:text-amber-400 dark:border-amber-500/20',
		pending: 'bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20',
		cancelled: 'bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20',
	};

	const containerStatusColors: Record<string, string> = {
		pending: 'bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20',
		building: 'bg-amber-500/10 text-amber-500 border-amber-500/30 dark:text-amber-400 dark:border-amber-500/20 animate-pulse',
		running: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30 dark:text-emerald-400 dark:border-emerald-500/20',
		stopped: 'bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20',
		failed: 'bg-red-500/10 text-red-500 border-red-500/30 dark:text-red-400 dark:border-red-500/20',
		removed: 'bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20',
	};

	async function fetchContainers() {
		try {
			const res = await getContainers({ per_page: 100 });
			containers = res.containers || [];
		} catch (e) {
			console.error('Failed to load containers:', e);
		}
	}

	async function fetchJobs() {
		try {
			const params: Record<string, any> = {
				page: currentPage,
				per_page: perPage,
			};
			if (modeFilter) params.mode = modeFilter;
			if (statusFilter) params.status = statusFilter;
			if (searchQuery) params.search = searchQuery;
			if (containerFilter) params.container_status = containerFilter;
			if (sortOption) params.sort_by = sortOption;

			const [jobsData, statsData] = await Promise.all([
				getGenerationJobs(params),
				getGenerationJobsStats(),
				fetchContainers()
			]);
			data = jobsData;
			stats = statsData;
			checkAndStartPolling();
		} catch (e: any) {
			toast.error('Failed to load applications');
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	function checkAndStartPolling() {
		stopPolling();
		const hasTransitioning = containers.some(c => c.status === 'building' || c.status === 'pending');
		const hasBusyActions = Object.values(containerBusy).some(v => v != null);

		if (hasTransitioning || hasBusyActions) {
			pollTimer = setInterval(async () => {
				await fetchContainers();
				const stillTransitioning = containers.some(c => c.status === 'building' || c.status === 'pending');
				const stillBusy = Object.values(containerBusy).some(v => v != null);
				if (!stillTransitioning && !stillBusy) {
					stopPolling();
				}
			}, 5000);
		}
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	async function runContainerAction(jobId: string, actionName: string, apiCall: () => Promise<unknown>, successMessage: string) {
		containerBusy[jobId] = actionName;
		try {
			await apiCall();
			toast.success(successMessage);
			await fetchContainers();
			checkAndStartPolling();
		} catch (e: any) {
			const msg = e?.message || 'Action failed';
			toast.error(`${actionName} failed: ${msg}`);
		} finally {
			containerBusy[jobId] = null;
		}
	}

	function handleBuildContainer(jobId: string) {
		runContainerAction(jobId, 'build', () => buildContainerForJob(jobId), 'Container build started');
	}

	function handleStartContainer(jobId: string, containerId: string) {
		runContainerAction(jobId, 'start', () => startContainer(containerId), 'Container starting');
	}

	function handleStopContainer(jobId: string, containerId: string) {
		runContainerAction(jobId, 'stop', () => stopContainer(containerId), 'Container stopping');
	}

	function handleRemoveContainer(jobId: string, containerId: string) {
		if (!confirm('Remove this container? Generated code will be preserved.')) return;
		runContainerAction(jobId, 'remove', () => removeContainer(containerId), 'Container removed');
	}

	async function refresh() {
		refreshing = true;
		await fetchJobs();
		toast.success('Refreshed');
	}

	function goToPage(p: number) {
		currentPage = p;
		fetchJobs();
	}

	function debouncedLoad() {
		clearTimeout(debounceTimer);
		currentPage = 1;
		debounceTimer = setTimeout(fetchJobs, 300);
	}

	function applyFilters() {
		currentPage = 1;
		fetchJobs();
	}

	function clearFilters() {
		searchQuery = '';
		modeFilter = '';
		statusFilter = '';
		containerFilter = '';
		sortOption = 'created_desc';
		currentPage = 1;
		fetchJobs();
	}

	function formatDuration(seconds: number | null): string {
		if (seconds == null) return '—';
		if (seconds < 60) return `${seconds.toFixed(1)}s`;
		const m = Math.floor(seconds / 60);
		const s = Math.round(seconds % 60);
		return `${m}m ${s}s`;
	}

	function timeAgo(dateStr: string): string {
		const diff = Date.now() - new Date(dateStr).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		if (days < 30) return `${days}d ago`;
		return new Date(dateStr).toLocaleDateString();
	}

	function getDescription(job: GenerationJobList): string {
		if (job.template_name) return job.template_name;
		if (job.scaffolding_name) return job.scaffolding_name;
		return 'Untitled Application';
	}

	function copyId(id: string) {
		navigator.clipboard.writeText(id);
		toast.success('Copied job ID');
	}

	async function handleCancel(id: string) {
		try {
			const result = await cancelGenerationJob(id);
			if (result.success) {
				toast.success('Job cancelled');
				await fetchJobs();
			} else {
				toast.error('Cannot cancel this job');
			}
		} catch {
			toast.error('Failed to cancel job');
		}
	}

	async function handleDelete(id: string) {
		if (!confirm('Are you sure you want to delete this job? This cannot be undone.')) return;
		try {
			const result = await deleteGenerationJob(id);
			if (result.success) {
				toast.success('Job deleted');
				await fetchJobs();
			} else {
				toast.error('Cannot delete this job');
			}
		} catch {
			toast.error('Failed to delete job');
		}
	}

	async function handleRetry(id: string) {
		try {
			const newJob = await retryGenerationJob(id);
			toast.success('Job retried — new job created');
			goto(`/applications/${newJob.id}`);
		} catch {
			toast.error('Failed to retry job');
		}
	}

	function getAccessUrl(c: ContainerInstance) {
		return c.app_url ?? null;
	}

	const containersByJobId = $derived(
		containers.reduce((acc, c) => {
			if (c.generation_job_id) acc[c.generation_job_id] = c;
			return acc;
		}, {} as Record<string, ContainerInstance>)
	);

	const filteredItems = $derived(data?.items ?? []);

	const activeFilters = $derived.by(() => {
		const tags: { key: string; label: string; clear: () => void }[] = [];
		if (searchQuery) {
			tags.push({
				key: 'search',
				label: `Search: "${searchQuery}"`,
				clear: () => {
					searchQuery = '';
					applyFilters();
				}
			});
		}
		if (modeFilter) {
			const labels: Record<string, string> = { custom: 'Custom', scaffolding: 'Scaffolding', copilot: 'Copilot' };
			tags.push({
				key: 'mode',
				label: `Mode: ${labels[modeFilter] || modeFilter}`,
				clear: () => {
					modeFilter = '';
					applyFilters();
				}
			});
		}
		if (containerFilter) {
			const labels: Record<string, string> = {
				running: 'Running Container',
				stopped: 'Stopped Container',
				building: 'Building/Provisioning',
				none: 'No Container'
			};
			tags.push({
				key: 'container',
				label: labels[containerFilter] || containerFilter,
				clear: () => {
					containerFilter = '';
					applyFilters();
				}
			});
		}
		return tags;
	});

	const hasActiveFilters = $derived(activeFilters.length > 0);

	function selectStatusTab(status: string) {
		statusFilter = status;
		applyFilters();
	}

	onMount(() => {
		fetchJobs();
	});

	onDestroy(() => {
		stopPolling();
	});
</script>

<svelte:head>
	<title>Applications - LLM Lab</title>
	<meta name="description" content="View and manage generated web applications from LLM models, monitor containers, and access active projects." />
</svelte:head>

<div class="space-y-3">
	<!-- Header -->
	<div class="flex items-center justify-between gap-2">
		<div class="page-header min-w-0">
			<h1>Applications</h1>
			<p class="hidden sm:block">Browse and manage generated web applications.</p>
		</div>
		<div class="flex flex-wrap items-center justify-end gap-2">
			<Button
				id="btn-refresh"
				variant="outline"
				size="sm"
				onclick={refresh}
				disabled={refreshing}
			>
				<RefreshCw class="h-3.5 w-3.5 sm:mr-2 {refreshing ? 'animate-spin' : ''}" />
				<span class="hidden sm:inline">Refresh</span>
			</Button>
			<Button
				id="btn-generate-new"
				variant="outline"
				size="sm"
				href="/sample-generator"
			>
				<FlaskConical class="h-3.5 w-3.5 sm:mr-2" />
				<span class="hidden sm:inline">Generate New</span>
			</Button>
		</div>
	</div>

	<!-- Stats Bar -->
	{#if stats}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			<Badge variant="secondary" class="gap-1.5">
				<AppWindow class="h-3 w-3" />
				{stats.total} total apps
			</Badge>
			<Badge variant="secondary" class="gap-1.5 text-emerald-600 dark:text-emerald-400">
				<CircleCheck class="h-3 w-3" />
				{stats.completed} completed
			</Badge>
			<Badge variant="secondary" class="gap-1.5 text-amber-600 dark:text-amber-400">
				<RefreshCw class="h-3 w-3 animate-pulse" />
				{stats.running} running
			</Badge>
			<Badge variant="outline" class="text-red-600 dark:text-red-400">
				<CircleX class="h-3 w-3" />
				{stats.failed} failed
			</Badge>
			<Badge variant="outline">
				<Clock class="h-3 w-3" />
				{stats.pending} pending
			</Badge>
		</div>
	{/if}

	<!-- Quick Filter Presets -->
	<div class="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-hide text-xs">
		<span class="text-xs text-muted-foreground mr-1 shrink-0">Quick:</span>
		<button
			class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {statusFilter === '' ? 'bg-primary/10 border-primary/30 text-primary dark:text-primary-foreground font-semibold' : 'border-input bg-card'}"
			onclick={() => selectStatusTab('')}
		>
			<AppWindow class="h-3 w-3" /> All
			{#if stats}
				<span class="ml-1.5 text-[10px] text-muted-foreground font-semibold font-mono">{stats.total}</span>
			{/if}
		</button>
		<button
			class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {statusFilter === 'completed' ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-700 dark:text-emerald-400 font-semibold' : 'border-input bg-card'}"
			onclick={() => selectStatusTab('completed')}
		>
			<CircleCheck class="h-3 w-3 text-emerald-500" /> Completed
			{#if stats}
				<span class="ml-1.5 text-[10px] text-emerald-600 dark:text-emerald-400 font-semibold font-mono">{stats.completed}</span>
			{/if}
		</button>
		<button
			class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {statusFilter === 'running' ? 'bg-amber-500/10 border-amber-500/40 text-amber-700 dark:text-amber-400 font-semibold' : 'border-input bg-card'}"
			onclick={() => selectStatusTab('running')}
		>
			<span class="relative flex h-1.5 w-1.5 shrink-0">
				<span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
				<span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-amber-500"></span>
			</span>
			Running
			{#if stats}
				<span class="ml-1.5 text-[10px] text-amber-600 dark:text-amber-400 font-semibold font-mono">{stats.running}</span>
			{/if}
		</button>
		<button
			class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {statusFilter === 'failed' ? 'bg-red-500/10 border-red-500/40 text-red-700 dark:text-red-400 font-semibold' : 'border-input bg-card'}"
			onclick={() => selectStatusTab('failed')}
		>
			<CircleX class="h-3 w-3 text-red-500" /> Failed
			{#if stats}
				<span class="ml-1.5 text-[10px] text-red-600 dark:text-red-400 font-semibold font-mono">{stats.failed}</span>
			{/if}
		</button>
		<button
			class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {statusFilter === 'pending' ? 'bg-zinc-500/10 border-zinc-500/40 text-zinc-700 dark:text-zinc-400 font-semibold' : 'border-input bg-card'}"
			onclick={() => selectStatusTab('pending')}
		>
			<Clock class="h-3 w-3 text-zinc-400" /> Pending
			{#if stats}
				<span class="ml-1.5 text-[10px] text-zinc-500 dark:text-zinc-400 font-semibold font-mono">{stats.pending}</span>
			{/if}
		</button>
	</div>

	<!-- Filters & Sort Bar -->
	<div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3">
		<!-- Search -->
		<div class="relative w-full sm:flex-1 sm:max-w-sm">
			<Search class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
			<Input
				id="search-input"
				placeholder="Search applications..."
				class="pl-9 h-9"
				bind:value={searchQuery}
				oninput={debouncedLoad}
			/>
		</div>

		<!-- Select Filters -->
		<select
			id="mode-filter"
			class="h-9 w-full sm:w-auto rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring cursor-pointer transition-colors"
			bind:value={modeFilter}
			onchange={applyFilters}
		>
			<option value="">All Modes</option>
			<option value="custom">Custom</option>
			<option value="scaffolding">Scaffolding</option>
			<option value="copilot">Copilot</option>
		</select>

		<select
			id="container-filter"
			class="h-9 w-full sm:w-auto rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring cursor-pointer transition-colors"
			bind:value={containerFilter}
			onchange={applyFilters}
		>
			<option value="">All Containers</option>
			<option value="running">Running</option>
			<option value="stopped">Stopped</option>
			<option value="building">Building / Provisioning</option>
			<option value="none">No Container</option>
		</select>

		<select
			id="sort-filter"
			class="h-9 w-full sm:w-auto rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring cursor-pointer transition-colors"
			bind:value={sortOption}
			onchange={applyFilters}
		>
			<option value="created_desc">Newest First</option>
			<option value="created_asc">Oldest First</option>
			<option value="duration_desc">Longest Duration</option>
			<option value="duration_asc">Shortest Duration</option>
			<option value="model_asc">Model Name (A-Z)</option>
		</select>

		<!-- Clear Filters Button -->
		{#if hasActiveFilters}
			<Button
				id="clear-filters-btn"
				variant="ghost"
				size="sm"
				onclick={clearFilters}
				class="h-9 px-2 text-xs text-muted-foreground hover:text-foreground border border-transparent hover:border-border"
			>
				<X class="h-4 w-4 mr-1.5" />
				Clear Filters
			</Button>
		{/if}
	</div>

	<!-- Active Filter Tags -->
	{#if activeFilters.length > 0}
		<div class="flex flex-wrap items-center gap-1.5 pt-1 text-xs">
			<span class="text-xs text-muted-foreground">Active:</span>
			{#each activeFilters as tag (tag.key)}
				<Badge variant="secondary" class="gap-1 pr-1.5 py-0.5 text-xs font-normal">
					{tag.label}
					<button class="ml-0.5 rounded-full hover:bg-muted-foreground/20 p-0.5" onclick={tag.clear}>
						<X class="h-2.5 w-2.5" />
					</button>
				</Badge>
			{/each}
			<button class="text-xs text-muted-foreground hover:text-foreground underline ml-1" onclick={clearFilters}>Clear all</button>
		</div>
	{/if}

	<!-- Results Count -->
	{#if data && !loading}
		<div class="flex items-center justify-between">
			<p class="text-xs text-muted-foreground">
				Showing {(data.page - 1) * data.per_page + 1}–{Math.min(data.page * data.per_page, data.total)} of <strong>{data.total}</strong> applications
			</p>
			<div class="flex items-center gap-2">
				<select
					class="h-7 rounded-md border border-input bg-background px-2 text-xs ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring cursor-pointer transition-colors"
					bind:value={perPage}
					onchange={() => { currentPage = 1; fetchJobs(); }}
				>
					<option value={10}>10 / page</option>
					<option value={25}>25 / page</option>
					<option value={50}>50 / page</option>
					<option value={100}>100 / page</option>
				</select>
			</div>
		</div>
	{/if}

	<!-- Results List -->
	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<div class="flex flex-col items-center gap-3">
					<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
					<span class="text-xs text-muted-foreground font-mono">Fetching applications dashboard...</span>
				</div>
			</Card.Content>
		</Card.Root>
	{:else if filteredItems.length === 0}
		<Card.Root>
			<Card.Content class="py-20 text-center max-w-md mx-auto">
				<div class="h-12 w-12 bg-muted rounded-full flex items-center justify-center mx-auto mb-4 border">
					<AppWindow class="h-6 w-6 text-muted-foreground" />
				</div>
				<h3 class="text-lg font-bold mb-1 text-foreground">No applications found</h3>
				<p class="text-xs text-muted-foreground mb-4 leading-relaxed">
					{hasActiveFilters
						? 'No applications match your filtering criteria. Try resetting filters or looking for another term.'
						: 'No code generation jobs are registered yet. Get started by designing your first application.'}
				</p>
				{#if hasActiveFilters}
					<Button id="btn-empty-clear" variant="outline" size="sm" onclick={clearFilters}>Clear Filters</Button>
				{:else}
					<Button id="btn-empty-create" variant="outline" size="sm" href="/sample-generator">Go to Sample Generator</Button>
				{/if}
			</Card.Content>
		</Card.Root>
	{:else}
		<!-- Table (desktop) -->
		<div class="hidden md:block">
			<Card.Root>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="w-full">
							<thead>
								<tr class="border-b bg-muted/40 transition-colors sticky top-0 z-10">
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">Application</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">Job Status</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">App / Container</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">Duration</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">Created</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each filteredItems as job, i (job.id)}
									{@const container = containersByJobId[job.id]}
									{@const isBusy = containerBusy[job.id]}
									<tr class="border-b transition-colors hover:bg-muted/50 group
										{i % 2 === 0 ? '' : 'bg-muted/15'}
										{job.status === 'failed' ? 'hover:bg-destructive/[0.04]' : ''}">
										
										<!-- Application (unified) -->
										<td class="px-3 py-2 align-middle">
											<div class="flex items-center gap-2.5">
												<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted group-hover:bg-primary/10 transition-colors">
													{#if job.mode === 'custom'}
														<Pencil class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
													{:else if job.mode === 'scaffolding'}
														<Layers class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
													{:else if job.mode === 'copilot'}
														<Bot class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
													{/if}
												</div>
												<div class="min-w-0">
													<a href="/applications/{job.id}" class="text-sm font-semibold hover:text-primary transition-colors block truncate max-w-[280px]" title={getDescription(job)}>
														{getDescription(job)}
													</a>
													<span class="text-[11px] text-muted-foreground block truncate max-w-[280px]">
														<span class="capitalize font-medium text-primary/80">{job.mode}</span> &middot; {job.model_name ?? '—'}
													</span>
												</div>
											</div>
										</td>

										<!-- Job Status -->
										<td class="px-3 py-2.5 align-middle">
											<div class="flex flex-col gap-1">
												<Badge variant="outline" class="w-fit text-[10px] uppercase font-mono px-2 py-0.5 {statusColors[job.status] ?? ''}">
													{#if job.status === 'running'}
														<span class="mr-1 h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse"></span>
													{/if}
													{#if job.status === 'completed'}
														<CircleCheck class="mr-1 h-3.5 w-3.5" />
													{:else if job.status === 'failed'}
														<CircleX class="mr-1 h-3.5 w-3.5" />
													{:else if job.status === 'pending'}
														<Clock class="mr-1 h-3.5 w-3.5" />
													{/if}
													{job.status}
												</Badge>
												{#if job.error_message}
													<div class="flex items-center gap-1 mt-0.5">
														<AlertTriangle class="h-3 w-3 text-destructive shrink-0" />
														<span class="text-[10px] text-destructive truncate max-w-[160px]" title={job.error_message}>{job.error_message}</span>
													</div>
												{/if}
											</div>
										</td>

										<!-- App / Container -->
										<td class="px-3 py-2.5 align-middle">
											{#if container}
												<div class="flex flex-col gap-1.5">
													<div class="flex items-center gap-2">
														<Badge variant="outline" class="gap-1 text-[10px] uppercase font-mono px-2 py-0.5 {containerStatusColors[container.status] ?? ''}">
															{#if container.status === 'running'}
																<span class="relative flex h-2 w-2">
																	<span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
																	<span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
																</span>
															{:else if container.status === 'building'}
																<LoaderCircle class="h-3 w-3 animate-spin text-amber-400" />
															{/if}
															{container.status}
														</Badge>
														{#if container.status === 'running'}
															<span class="text-[10px] text-muted-foreground/75 font-mono">Port {container.app_port}</span>
														{/if}
													</div>

													{#if container.status === 'running'}
														{@const accessUrl = getAccessUrl(container)}
														{#if accessUrl}
															<Button
																id="access-app-btn-{job.id}"
																variant="outline"
																size="sm"
																href={accessUrl}
																target="_blank"
																rel="noopener noreferrer"
																class="h-8 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/10 hover:border-emerald-500/50 w-fit"
															>
																<ExternalLink class="h-3.5 w-3.5 mr-1.5" />
																Access App
															</Button>
														{/if}
													{:else if container.status === 'stopped'}
														<Button
															id="start-container-btn-{job.id}"
															variant="outline"
															size="sm"
															class="h-8 text-xs gap-1.5 border-zinc-700/50 hover:bg-emerald-500/10 hover:text-emerald-400 hover:border-emerald-500/40 w-fit"
															disabled={isBusy != null}
															onclick={() => handleStartContainer(job.id, container.id)}
														>
															{#if isBusy === 'start'}
																<LoaderCircle class="h-3 w-3 animate-spin" />
																Starting...
															{:else}
																<Play class="h-3 w-3 fill-current" />
																Start Container
															{/if}
														</Button>
													{:else if container.status === 'failed'}
														<div class="flex flex-col gap-1">
															<span class="text-[10px] text-red-400 font-mono line-clamp-1" title={container.last_error}>{container.last_error || 'Container crash'}</span>
															<Button
																id="retry-container-btn-{job.id}"
																variant="outline"
																size="sm"
																class="h-8 text-xs gap-1.5 border-red-500/20 hover:bg-amber-500/10 hover:text-amber-400 hover:border-amber-500/40 w-fit"
																disabled={isBusy != null}
																onclick={() => handleStartContainer(job.id, container.id)}
															>
																{#if isBusy === 'start'}
																	<LoaderCircle class="h-3 w-3 animate-spin" />
																	Starting...
																{:else}
																	<Play class="h-3 w-3" />
																	Restart Container
																{/if}
															</Button>
														</div>
													{:else if container.status === 'building'}
														<span class="text-[11px] text-amber-400/90 font-medium flex items-center gap-1.5">
															<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
															Provisioning environment...
														</span>
													{/if}
												</div>
											{:else if job.status === 'completed'}
												<Button
													id="provision-app-btn-{job.id}"
													variant="outline"
													size="sm"
													class="h-8 text-xs font-semibold gap-1.5 border-primary/30 text-primary hover:bg-primary/10 hover:border-primary/50 transition-all duration-200 shadow-sm w-fit"
													disabled={isBusy != null}
													onclick={() => handleBuildContainer(job.id)}
												>
													{#if isBusy === 'build'}
														<LoaderCircle class="h-3.5 w-3.5 animate-spin mr-1.5" />
														Building...
													{:else}
														<Hammer class="h-3.5 w-3.5 mr-1.5" />
														Provision App
													{/if}
												</Button>
											{:else}
												<span class="text-xs text-muted-foreground/60 italic">Not available</span>
											{/if}
										</td>

										<!-- Duration -->
										<td class="px-3 py-2.5 text-right align-middle">
											<span class="text-xs font-mono tabular-nums text-muted-foreground">{formatDuration(job.duration_seconds)}</span>
										</td>

										<!-- Created -->
										<td class="px-3 py-2.5 align-middle">
											<div class="flex flex-col gap-0.5">
												<span class="text-xs text-foreground font-medium">{timeAgo(job.created_at)}</span>
												<span class="text-[9px] text-muted-foreground/70 font-mono tabular-nums">{new Date(job.created_at).toLocaleString()}</span>
											</div>
										</td>

										<!-- Actions -->
										<td class="px-3 py-2.5 align-middle">
											<div class="flex items-center justify-end gap-1">
												<!-- Container quick toggle (Start/Stop) -->
												{#if container}
													{#if container.status === 'running'}
														<Button
															id="action-stop-{job.id}"
															variant="ghost"
															size="sm"
															class="h-8 w-8 p-0 hover:bg-amber-500/10 hover:text-amber-400 text-muted-foreground"
															title="Stop Container"
															disabled={isBusy != null}
															onclick={() => handleStopContainer(job.id, container.id)}
														>
															{#if isBusy === 'stop'}
																<LoaderCircle class="h-4 w-4 animate-spin text-amber-500" />
															{:else}
																<Square class="h-4 w-4 fill-current text-amber-500" />
															{/if}
														</Button>
													{:else if container.status === 'stopped' || container.status === 'failed'}
														<Button
															id="action-start-{job.id}"
															variant="ghost"
															size="sm"
															class="h-8 w-8 p-0 hover:bg-emerald-500/10 hover:text-emerald-400 text-muted-foreground"
															title="Start Container"
															disabled={isBusy != null}
															onclick={() => handleStartContainer(job.id, container.id)}
														>
															{#if isBusy === 'start'}
																<LoaderCircle class="h-4 w-4 animate-spin text-emerald-500" />
															{:else}
																<Play class="h-4 w-4 fill-current text-emerald-500" />
															{/if}
														</Button>
													{/if}

													<!-- Remove Container -->
													{#if container.status !== 'building' && container.status !== 'pending'}
														<Button
															id="action-remove-container-{job.id}"
															variant="ghost"
															size="sm"
															class="h-8 w-8 p-0 hover:bg-red-500/10 hover:text-red-400 text-muted-foreground/60"
															title="Delete Container (Preserves Code)"
															disabled={isBusy != null}
															onclick={() => handleRemoveContainer(job.id, container.id)}
														>
															{#if isBusy === 'remove'}
																<LoaderCircle class="h-4 w-4 animate-spin text-red-500" />
															{:else}
																<Server class="h-4 w-4 stroke-[1.5] text-red-400/80" />
															{/if}
														</Button>
													{/if}
												{/if}

												<!-- Job Details / View -->
												<Button
													id="action-view-{job.id}"
													variant="ghost"
													size="sm"
													class="h-8 w-8 p-0 text-muted-foreground hover:text-foreground hover:bg-muted"
													href="/applications/{job.id}"
													title="View details"
												>
													<Eye class="h-4 w-4" />
												</Button>

												<!-- Retry/Failure -->
												{#if job.status === 'failed' || job.status === 'cancelled'}
													<Button
														id="action-retry-{job.id}"
														variant="ghost"
														size="sm"
														class="h-8 w-8 p-0 hover:bg-blue-500/10 text-muted-foreground hover:text-blue-400"
														title="Retry job"
														onclick={() => handleRetry(job.id)}
													>
														<RotateCcw class="h-4 w-4" />
													</Button>
												{/if}
												{#if job.status === 'failed'}
													<Button
														id="action-failure-{job.id}"
														variant="ghost"
														size="sm"
														class="h-8 w-8 p-0 hover:bg-red-500/10 text-muted-foreground hover:text-red-400"
														href="/applications/{job.id}/failure"
														title="Failure logs"
													>
														<AlertTriangle class="h-4 w-4 text-red-400" />
													</Button>
												{/if}

												<!-- Cancel pending/running -->
												{#if job.status === 'pending' || job.status === 'running'}
													<Button
														id="action-cancel-{job.id}"
														variant="ghost"
														size="sm"
														class="h-8 w-8 p-0 hover:bg-amber-500/10 text-muted-foreground hover:text-amber-500"
														title="Cancel job"
														onclick={() => handleCancel(job.id)}
													>
														<Ban class="h-4 w-4" />
													</Button>
												{/if}

												<!-- Copy ID -->
												<Button
													id="action-copy-{job.id}"
													variant="ghost"
													size="sm"
													class="h-8 w-8 p-0 text-muted-foreground/60 hover:text-foreground hover:bg-muted"
													title="Copy Job ID"
													onclick={() => copyId(job.id)}
												>
													<Copy class="h-3.5 w-3.5" />
												</Button>

												<!-- Delete Job -->
												{#if job.status !== 'running'}
													<Button
														id="action-delete-{job.id}"
														variant="ghost"
														size="sm"
														class="h-8 w-8 p-0 hover:bg-red-500/10 text-muted-foreground hover:text-red-400"
														title="Delete Job"
														onclick={() => handleDelete(job.id)}
													>
														<Trash2 class="h-4 w-4" />
													</Button>
												{/if}
											</div>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Cards (mobile) -->
		<div class="md:hidden space-y-2.5">
			{#each filteredItems as job (job.id)}
				{@const container = containersByJobId[job.id]}
				{@const isBusy = containerBusy[job.id]}
				<div class="block rounded-lg border bg-card p-3.5 transition-colors hover:bg-muted/50 space-y-3">
					<!-- Card Header: Mode & Time -->
					<div class="flex items-center justify-between gap-2">
						<div class="flex items-center gap-2.5 min-w-0 flex-1">
							<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted">
								{#if job.mode === 'custom'}
									<Pencil class="h-4 w-4 text-muted-foreground" />
								{:else if job.mode === 'scaffolding'}
									<Layers class="h-4 w-4 text-muted-foreground" />
								{:else if job.mode === 'copilot'}
									<Bot class="h-4 w-4 text-muted-foreground" />
								{/if}
							</div>
							<div class="min-w-0">
								<a href="/applications/{job.id}" class="text-sm font-semibold hover:text-primary transition-colors block truncate">{getDescription(job)}</a>
								<span class="text-[11px] text-muted-foreground block truncate">
									<span class="capitalize font-medium text-primary/80">{job.mode}</span> &middot; {job.model_name ?? '—'}
								</span>
							</div>
						</div>
						<div class="flex items-center gap-1.5 shrink-0 text-[11px] text-muted-foreground font-medium">
							{timeAgo(job.created_at)}
						</div>
					</div>

					<!-- Status Badges & Container States -->
					<div class="grid grid-cols-2 gap-2.5 py-2.5 border-y">
						<!-- Job Status -->
						<div class="flex flex-col gap-1">
							<span class="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Job Status</span>
							<Badge variant="outline" class="w-fit text-[9px] font-mono px-1.5 py-0.5 {statusColors[job.status] ?? ''}">
								{#if job.status === 'running'}
									<span class="mr-1 h-1 w-1 rounded-full bg-amber-500 animate-pulse"></span>
								{/if}
								{job.status}
							</Badge>
						</div>

						<!-- Container / App -->
						<div class="flex flex-col gap-1">
							<span class="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Container</span>
							{#if container}
								<Badge variant="outline" class="w-fit text-[9px] font-mono px-1.5 py-0.5 {containerStatusColors[container.status] ?? ''}">
									{#if container.status === 'running'}
										<span class="h-1 w-1 rounded-full bg-emerald-500 animate-ping mr-1"></span>
									{/if}
									{container.status}
								</Badge>
							{:else}
								<span class="text-xs text-muted-foreground/60 italic">Not created</span>
							{/if}
						</div>
					</div>

					<!-- Container Actions Box -->
					{#if container || job.status === 'completed'}
						<div class="bg-muted/30 border rounded-lg p-2.5 space-y-2">
							{#if container}
								{#if container.status === 'running'}
									{@const accessUrl = getAccessUrl(container)}
									<div class="flex items-center justify-between gap-2 flex-wrap">
										<span class="text-[10.5px] text-muted-foreground font-mono">Port: {container.app_port}</span>
										{#if accessUrl}
											<Button
												id="mobile-access-app-btn-{job.id}"
												variant="outline"
												size="sm"
												href={accessUrl}
												target="_blank"
												rel="noopener noreferrer"
												class="h-8 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/10 hover:border-emerald-500/50"
											>
												<ExternalLink class="h-3 w-3 mr-1.5" />
												Access App
											</Button>
										{/if}
									</div>
								{:else if container.status === 'stopped'}
									<div class="flex items-center justify-between gap-2">
										<span class="text-xs text-muted-foreground">App is stopped.</span>
										<Button
											id="mobile-start-btn-{job.id}"
											variant="outline"
											size="sm"
											class="h-8 text-xs gap-1 hover:bg-emerald-500/10 hover:text-emerald-400 border-zinc-700/50"
											disabled={isBusy != null}
											onclick={() => handleStartContainer(job.id, container.id)}
										>
											{#if isBusy === 'start'}
												<LoaderCircle class="h-3 w-3 animate-spin" />
												Starting...
											{:else}
												<Play class="h-3 w-3 fill-current" />
												Start App
											{/if}
										</Button>
									</div>
								{:else if container.status === 'failed'}
									<div class="flex flex-col gap-1.5">
										<span class="text-[10px] text-red-400 font-mono line-clamp-1">{container.last_error || 'Container failed'}</span>
										<Button
											id="mobile-restart-btn-{job.id}"
											variant="outline"
											size="sm"
											class="h-8 text-xs gap-1 hover:bg-amber-500/10 hover:text-amber-400 border-red-500/20"
											disabled={isBusy != null}
											onclick={() => handleStartContainer(job.id, container.id)}
										>
											{#if isBusy === 'start'}
												<LoaderCircle class="h-3 w-3 animate-spin" />
												Starting...
											{:else}
												<Play class="h-3 w-3" />
												Restart App
											{/if}
										</Button>
									</div>
								{:else if container.status === 'building'}
									<span class="text-xs text-amber-400/90 font-medium flex items-center gap-1.5 justify-center py-1">
										<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
										Provisioning environment...
									</span>
								{/if}
							{:else if job.status === 'completed'}
								<div class="flex items-center justify-between gap-2">
									<span class="text-xs text-muted-foreground">No environment built.</span>
									<Button
										id="mobile-provision-btn-{job.id}"
										variant="outline"
										size="sm"
										class="h-8 text-xs font-semibold gap-1.5 border-primary/30 text-primary hover:bg-primary/10 hover:border-primary/50"
										disabled={isBusy != null}
										onclick={() => handleBuildContainer(job.id)}
									>
										{#if isBusy === 'build'}
											<LoaderCircle class="h-3 w-3 animate-spin mr-1.5" />
											Building...
										{:else}
											<Hammer class="h-3 w-3 mr-1.5" />
											Provision
										{/if}
									</Button>
								</div>
							{/if}
						</div>
					{/if}

					<!-- Footer Info & Actions -->
					<div class="flex items-center justify-between pt-3 border-t">
						<span class="text-[11px] font-mono text-muted-foreground">{formatDuration(job.duration_seconds)}</span>
						
						<!-- Mobile Actions Group -->
						<div class="flex items-center gap-1">
							{#if container}
								{#if container.status === 'running'}
									<Button
										id="mobile-action-stop-{job.id}"
										variant="ghost"
										size="sm"
										class="h-8 w-8 p-0 text-amber-500 hover:bg-amber-500/10"
										title="Stop Container"
										disabled={isBusy != null}
										onclick={() => handleStopContainer(job.id, container.id)}
									>
										{#if isBusy === 'stop'}
											<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
										{:else}
											<Square class="h-3.5 w-3.5 fill-current" />
										{/if}
									</Button>
								{/if}
								{#if container.status !== 'building' && container.status !== 'pending'}
									<Button
										id="mobile-action-remove-container-{job.id}"
										variant="ghost"
										size="sm"
										class="h-8 w-8 p-0 text-red-400 hover:bg-red-500/10"
										title="Remove Container"
										disabled={isBusy != null}
										onclick={() => handleRemoveContainer(job.id, container.id)}
									>
										{#if isBusy === 'remove'}
											<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
										{:else}
											<Server class="h-3.5 w-3.5" />
										{/if}
									</Button>
								{/if}
							{/if}

							<Button variant="ghost" size="sm" class="h-8 w-8 p-0" href="/applications/{job.id}" title="View details">
								<Eye class="h-4 w-4" />
							</Button>
							{#if job.status === 'failed' || job.status === 'cancelled'}
								<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-blue-400 hover:bg-blue-500/10" title="Retry" onclick={() => handleRetry(job.id)}>
									<RotateCcw class="h-4 w-4" />
								</Button>
							{/if}
							{#if job.status === 'failed'}
								<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-red-400 hover:bg-red-500/10" href="/applications/{job.id}/failure" title="Failure details">
									<AlertTriangle class="h-4 w-4" />
								</Button>
							{/if}
							{#if job.status === 'pending' || job.status === 'running'}
								<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-amber-400 hover:bg-amber-500/10" title="Cancel" onclick={() => handleCancel(job.id)}>
									<Ban class="h-4 w-4" />
								</Button>
							{/if}
							<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-muted-foreground" title="Copy ID" onclick={() => copyId(job.id)}>
								<Copy class="h-3.5 w-3.5" />
							</Button>
							{#if job.status !== 'running'}
								<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-red-400 hover:bg-red-500/10" title="Delete" onclick={() => handleDelete(job.id)}>
									<Trash2 class="h-3.5 w-3.5" />
								</Button>
							{/if}
						</div>
					</div>
				</div>
			{/each}
		</div>

		<!-- Pagination -->
		{#if data && data.pages > 1}
			{@const d = data}
			<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pt-4 border-t text-sm text-muted-foreground">
				<span class="font-mono text-xs">
					Showing Page {d.page} of {d.pages} &middot; {d.total} applications total
				</span>
				<div class="flex items-center justify-center gap-1.5">
					<Button
						variant="outline"
						size="sm"
						disabled={d.page <= 1}
						onclick={() => goToPage(1)}
						class="h-9 px-2 text-muted-foreground hover:text-foreground"
					>
						<ChevronsLeft class="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="sm"
						disabled={d.page <= 1}
						onclick={() => goToPage(d.page - 1)}
						class="h-9 px-2 text-muted-foreground hover:text-foreground"
					>
						<ChevronLeft class="h-4 w-4" />
					</Button>
					{#each Array.from({ length: Math.min(5, d.pages) }, (_, i) => {
						const start = Math.max(1, Math.min(d.page - 2, d.pages - 4));
						return start + i;
					}).filter((p) => p <= d.pages) as p}
						<Button
							variant="outline"
							size="sm"
							class="h-9 min-w-[36px] font-mono {p === d.page ? 'bg-primary/20 border-primary/40 text-primary font-bold shadow-sm shadow-primary/10' : 'text-muted-foreground hover:text-foreground hover:border-border'}"
							onclick={() => goToPage(p)}
						>
							{p}
						</Button>
					{/each}
					<Button
						variant="outline"
						size="sm"
						disabled={d.page >= d.pages}
						onclick={() => goToPage(d.page + 1)}
						class="h-9 px-2 text-muted-foreground hover:text-foreground"
					>
						<ChevronRight class="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="sm"
						disabled={d.page >= d.pages}
						onclick={() => goToPage(d.pages)}
						class="h-9 px-2 text-muted-foreground hover:text-foreground"
					>
						<ChevronsRight class="h-4 w-4" />
					</Button>
				</div>
			</div>
		{/if}
	{/if}
</div>
