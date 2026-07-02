<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import {
		getGenerationJobs,
		getGenerationJobsStats,
		cancelGenerationJob,
		deleteGenerationJob,
		retryGenerationJob,
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
	import JobsTable from '$lib/components/applications/JobsTable.svelte';
	import JobCards from '$lib/components/applications/JobCards.svelte';
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Clock from '@lucide/svelte/icons/clock';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
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
	let modelFilter = $state('');
	let sortBy = $state('created_at');
	let sortDir = $state<'asc' | 'desc'>('desc');
	let currentPage = $state(1);
	let perPage = $state(25);

	// Derive unique models from loaded data for the model filter dropdown
	const uniqueModels = $derived.by(() => {
		if (!data?.items) return [];
		const seen = new Set<string>();
		return data.items
			.filter((j) => j.model_name && j.model_id_str)
			.filter((j) => {
				if (seen.has(j.model_id_str!)) return false;
				seen.add(j.model_id_str!);
				return true;
			})
			.map((j) => ({ name: j.model_name!, id: j.model_id_str! }))
			.sort((a, b) => a.name.localeCompare(b.name));
	});

	// Map sortBy/sortDir to backend sort_by values
	const sortOption = $derived.by(() => {
		if (sortBy === 'created_at') return sortDir === 'desc' ? 'created_desc' : 'created_asc';
		if (sortBy === 'duration_seconds') return sortDir === 'desc' ? 'duration_desc' : 'duration_asc';
		if (sortBy === 'model_name') return 'model_asc';
		return 'created_desc';
	});

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
			if (modelFilter) params.model_id = modelFilter;
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
		modelFilter = '';
		sortBy = 'created_at';
		sortDir = 'desc';
		currentPage = 1;
		fetchJobs();
	}

	function toggleSort(field: string) {
		if (sortBy === field) {
			// model_name only has asc from backend; toggle via created fallback
			if (field === 'model_name') {
				sortBy = 'created_at';
				sortDir = 'desc';
			} else {
				sortDir = sortDir === 'asc' ? 'desc' : 'asc';
			}
		} else {
			sortBy = field;
			sortDir = field === 'model_name' ? 'asc' : 'desc';
		}
		currentPage = 1;
		fetchJobs();
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

	const containersByJobId = $derived(
		containers.reduce((acc, c) => {
			if (c.generation_job_id) acc[c.generation_job_id] = c;
			return acc;
		}, {} as Record<string, ContainerInstance>)
	);

	const filteredItems = $derived(data?.items ?? []);

	const activeFilters = $derived.by((): FilterTag[] => {
		const tags: FilterTag[] = [];
		if (searchQuery) {
			tags.push({
				key: 'search',
				label: `Search: "${searchQuery}"`,
				onRemove: () => {
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
				onRemove: () => {
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
				onRemove: () => {
					containerFilter = '';
					applyFilters();
				}
			});
		}
		if (modelFilter) {
			const model = uniqueModels.find((m) => m.id === modelFilter);
			tags.push({
				key: 'model',
				label: `Model: ${model?.name ?? modelFilter}`,
				onRemove: () => {
					modelFilter = '';
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

	<FilterBar
		searchPlaceholder="Search applications..."
		bind:searchValue={searchQuery}
		onSearchInput={() => { currentPage = 1; debouncedLoad(); }}
		activeTags={activeFilters}
		resultsText={data && !loading
			? `Showing ${(data.page - 1) * data.per_page + 1}–${Math.min(data.page * data.per_page, data.total)} of ${data.total} applications`
			: ''}
		page={data?.page}
		pages={data?.pages}
		onGoToPage={goToPage}
		onClearAll={clearFilters}
	>
		{#snippet filters()}
			<!-- Row 1: Status -->
			<div class="fb-group">
				<span class="fb-group-label">Status</span>
				<button
					class="fb-chip {statusFilter === '' ? 'fb-chip-on' : ''}"
					onclick={() => selectStatusTab('')}
				>
					<AppWindow class="h-3 w-3" /> All
					{#if stats}<span class="ml-1 font-mono text-[10px]">{stats.total}</span>{/if}
				</button>
				<button
					class="fb-chip {statusFilter === 'completed' ? 'fb-chip-emerald' : ''}"
					onclick={() => selectStatusTab('completed')}
				>
					<CircleCheck class="h-3 w-3" /> Completed
					{#if stats}<span class="ml-1 font-mono text-[10px]">{stats.completed}</span>{/if}
				</button>
				<button
					class="fb-chip {statusFilter === 'running' ? 'fb-chip-amber' : ''}"
					onclick={() => selectStatusTab('running')}
				>
					<span class="relative flex h-1.5 w-1.5 shrink-0">
						<span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
						<span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-amber-500"></span>
					</span>
					Running
					{#if stats}<span class="ml-1 font-mono text-[10px]">{stats.running}</span>{/if}
				</button>
				<button
					class="fb-chip {statusFilter === 'failed' ? 'fb-chip-red' : ''}"
					onclick={() => selectStatusTab('failed')}
				>
					<CircleX class="h-3 w-3" /> Failed
					{#if stats}<span class="ml-1 font-mono text-[10px]">{stats.failed}</span>{/if}
				</button>
				<button
					class="fb-chip {statusFilter === 'pending' ? 'fb-chip-slate' : ''}"
					onclick={() => selectStatusTab('pending')}
				>
					<Clock class="h-3 w-3" /> Pending
					{#if stats}<span class="ml-1 font-mono text-[10px]">{stats.pending}</span>{/if}
				</button>
			</div>

			<!-- Row 2: Mode -->
			<div class="fb-group">
				<span class="fb-group-label">Mode</span>
				<button class="fb-chip {modeFilter === '' ? 'fb-chip-on' : ''}" onclick={() => { modeFilter = ''; applyFilters(); }}>All</button>
				<button class="fb-chip {modeFilter === 'custom' ? 'fb-chip-on' : ''}" onclick={() => { modeFilter = 'custom'; applyFilters(); }}>Custom</button>
				<button class="fb-chip {modeFilter === 'scaffolding' ? 'fb-chip-on' : ''}" onclick={() => { modeFilter = 'scaffolding'; applyFilters(); }}>Scaffolding</button>
				<button class="fb-chip {modeFilter === 'copilot' ? 'fb-chip-on' : ''}" onclick={() => { modeFilter = 'copilot'; applyFilters(); }}>Copilot</button>
			</div>

			<!-- Row 3: Container -->
			<div class="fb-group">
				<span class="fb-group-label">Container</span>
				<button class="fb-chip {containerFilter === '' ? 'fb-chip-on' : ''}" onclick={() => { containerFilter = ''; applyFilters(); }}>All</button>
				<button class="fb-chip {containerFilter === 'running' ? 'fb-chip-emerald' : ''}" onclick={() => { containerFilter = 'running'; applyFilters(); }}>Running</button>
				<button class="fb-chip {containerFilter === 'stopped' ? 'fb-chip-slate' : ''}" onclick={() => { containerFilter = 'stopped'; applyFilters(); }}>Stopped</button>
				<button class="fb-chip {containerFilter === 'building' ? 'fb-chip-amber' : ''}" onclick={() => { containerFilter = 'building'; applyFilters(); }}>Building</button>
				<button class="fb-chip {containerFilter === 'none' ? 'fb-chip-on' : ''}" onclick={() => { containerFilter = 'none'; applyFilters(); }}>None</button>
			</div>

			<!-- Row 4: Model + Sort -->
			<div class="fb-group flex-wrap">
				<span class="fb-group-label">Model</span>
				<select
					class="fb-select"
					bind:value={modelFilter}
					onchange={applyFilters}
				>
					<option value="">All Models</option>
					{#each uniqueModels as model}
						<option value={model.id}>{model.name}</option>
					{/each}
				</select>
				<span class="fb-group-label ml-2">Sort</span>
				<button
					class="fb-chip {sortBy === 'created_at' && sortDir === 'desc' ? 'fb-chip-on' : ''}"
					onclick={() => { sortBy = 'created_at'; sortDir = 'desc'; currentPage = 1; fetchJobs(); }}
				>Newest</button>
				<button
					class="fb-chip {sortBy === 'created_at' && sortDir === 'asc' ? 'fb-chip-on' : ''}"
					onclick={() => { sortBy = 'created_at'; sortDir = 'asc'; currentPage = 1; fetchJobs(); }}
				>Oldest</button>
				<button
					class="fb-chip {sortBy === 'duration_seconds' ? 'fb-chip-on' : ''}"
					onclick={() => { sortBy = 'duration_seconds'; sortDir = 'desc'; currentPage = 1; fetchJobs(); }}
				>Longest</button>
				<button
					class="fb-chip {sortBy === 'model_name' ? 'fb-chip-on' : ''}"
					onclick={() => { sortBy = 'model_name'; sortDir = 'asc'; currentPage = 1; fetchJobs(); }}
				>A–Z Model</button>
			</div>
		{/snippet}

		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={refresh} disabled={refreshing}>
				<RefreshCw class="h-3.5 w-3.5 {refreshing ? 'animate-spin' : ''}" />
				<span class="hidden sm:inline ml-1.5">Refresh</span>
			</Button>
			<select
				class="fb-select"
				bind:value={perPage}
				onchange={() => { currentPage = 1; fetchJobs(); }}
			>
				<option value={10}>10/pg</option>
				<option value={25}>25/pg</option>
				<option value={50}>50/pg</option>
				<option value={100}>100/pg</option>
			</select>
		{/snippet}
	</FilterBar>

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
			<JobsTable
				jobs={filteredItems}
				{containersByJobId}
				{containerBusy}
				{sortBy}
				{sortDir}
				onToggleSort={toggleSort}
				onBuild={handleBuildContainer}
				onStart={handleStartContainer}
				onStop={handleStopContainer}
				onRemoveContainer={handleRemoveContainer}
				onCancel={handleCancel}
				onDelete={handleDelete}
				onRetry={handleRetry}
				onCopyId={copyId}
			/>
		</div>

		<!-- Cards (mobile) -->
		<div class="md:hidden">
			<JobCards
				jobs={filteredItems}
				{containersByJobId}
				{containerBusy}
				onBuild={handleBuildContainer}
				onStart={handleStartContainer}
				onStop={handleStopContainer}
				onRemoveContainer={handleRemoveContainer}
				onCancel={handleCancel}
				onDelete={handleDelete}
				onRetry={handleRetry}
				onCopyId={copyId}
			/>
		</div>
	{/if}

	<PaginationBar
		resultsText={data && !loading
			? `Showing ${(data.page - 1) * data.per_page + 1}–${Math.min(data.page * data.per_page, data.total)} of ${data.total} applications`
			: ''}
		page={data?.page}
		pages={data?.pages}
		onGoToPage={goToPage}
		class="rounded-md border border-border"
	/>
</div>
