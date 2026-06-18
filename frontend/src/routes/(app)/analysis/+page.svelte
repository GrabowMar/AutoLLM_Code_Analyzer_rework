<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Search from '@lucide/svelte/icons/search';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Eye from '@lucide/svelte/icons/eye';
	import StopCircle from '@lucide/svelte/icons/circle-stop';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Clock from '@lucide/svelte/icons/clock';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import Ban from '@lucide/svelte/icons/ban';
	import AlertTriangle from '@lucide/svelte/icons/triangle-alert';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ArrowUpDown from '@lucide/svelte/icons/arrow-up-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import { onMount } from 'svelte';
	import {
		getAnalysisTasks,
		getAnalysisStats,
		cancelAnalysisTask,
		deleteAnalysisTask,
		type AnalysisTaskList,
		type AnalysisStats,
		type PaginatedAnalysisTasks,
	} from '$lib/api/client';
	import { statusColors, severityColors } from '$lib/constants/analysis';
	import { downloadExport } from '$lib/api/export';
	import Download from '@lucide/svelte/icons/download';
	import { formatDate, formatDuration, statusLabel } from '$lib/utils/analysis';

	let loading = $state(true);
	let error = $state('');
	let tasks = $state<AnalysisTaskList[]>([]);
	let stats = $state<AnalysisStats | null>(null);
	let totalTasks = $state(0);
	let totalPages = $state(1);
	let currentPage = $state(1);
	let perPage = $state(25);
	let searchQuery = $state('');
	let statusFilter = $state('');
	let sortBy = $state('created_at');
	let sortDir = $state<'asc' | 'desc'>('desc');
	let refreshing = $state(false);
	let cancellingIds = $state(new Set<string>());
	let deletingIds = $state(new Set<string>());

	let debounceTimer: ReturnType<typeof setTimeout> | undefined;
	let pollTimer: ReturnType<typeof setInterval> | undefined;

	let hasRunningTasks = $derived(tasks.some((t) => t.status === 'running' || t.status === 'pending'));

	const activeFilters = $derived.by(() => {
		const tags: FilterTag[] = [];
		if (statusFilter) {
			const labelMap: Record<string, string> = { running: 'Running', pending: 'Pending', completed: 'Completed', partial: 'Partial', failed: 'Failed', cancelled: 'Cancelled' };
			tags.push({ key: 'status', label: `Status: ${labelMap[statusFilter] ?? statusFilter}`, onRemove: () => { statusFilter = ''; currentPage = 1; fetchTasks(); } });
		}
		return tags;
	});

	const resultsText = $derived(
		totalTasks > 0
			? `Showing ${(currentPage - 1) * perPage + 1}–${Math.min(currentPage * perPage, totalTasks)} of ${totalTasks} tasks`
			: ''
	);

	type StatusPill = {
		value: string;
		label: string;
		count: (s: AnalysisStats) => number;
		activeClass: string;
		inactiveClass: string;
	};

	const statusPills: StatusPill[] = [
		{
			value: '',
			label: 'All',
			count: (s) => s.total_tasks,
			activeClass: 'bg-primary/10 border-primary/30 text-primary font-semibold',
			inactiveClass: 'border-input bg-card',
		},
		{
			value: 'running',
			label: 'Running',
			count: (s) => s.running_tasks,
			activeClass: 'bg-blue-500/10 border-blue-500/40 text-blue-700 dark:text-blue-400 font-semibold',
			inactiveClass: 'border-input bg-card',
		},
		{
			value: 'pending',
			label: 'Pending',
			count: () => 0,
			activeClass: 'bg-zinc-500/10 border-zinc-500/40 text-zinc-700 dark:text-zinc-400 font-semibold',
			inactiveClass: 'border-input bg-card',
		},
		{
			value: 'completed',
			label: 'Completed',
			count: (s) => s.completed_tasks,
			activeClass: 'bg-emerald-500/10 border-emerald-500/40 text-emerald-700 dark:text-emerald-400 font-semibold',
			inactiveClass: 'border-input bg-card',
		},
		{
			value: 'partial',
			label: 'Partial',
			count: () => 0,
			activeClass: 'bg-amber-500/10 border-amber-500/40 text-amber-700 dark:text-amber-400 font-semibold',
			inactiveClass: 'border-input bg-card',
		},
		{
			value: 'failed',
			label: 'Failed',
			count: (s) => s.failed_tasks,
			activeClass: 'bg-red-500/10 border-red-500/40 text-red-700 dark:text-red-400 font-semibold',
			inactiveClass: 'border-input bg-card',
		},
		{
			value: 'cancelled',
			label: 'Cancelled',
			count: () => 0,
			activeClass: 'bg-zinc-500/10 border-zinc-500/40 text-zinc-700 dark:text-zinc-400 font-semibold',
			inactiveClass: 'border-input bg-card',
		},
	];

	type SortableCol = { key: string; label: string; align?: 'right' };
	const sortableColumns: SortableCol[] = [
		{ key: 'name', label: 'Name' },
		{ key: 'created_at', label: 'Created' },
		{ key: 'duration_seconds', label: 'Duration', align: 'right' },
	];

	async function fetchTasks() {
		try {
			const data: PaginatedAnalysisTasks = await getAnalysisTasks({
				page: currentPage,
				per_page: perPage,
				status: statusFilter || undefined,
				search: searchQuery || undefined,
				sort_by: sortBy,
				sort_dir: sortDir,
			});
			tasks = data.items;
			totalTasks = data.total;
			totalPages = data.pages;
			currentPage = data.page;
			error = '';
		} catch (e) {
			error = 'Failed to load analysis tasks.';
			console.error(e);
		}
	}

	async function fetchStats() {
		try {
			stats = await getAnalysisStats();
		} catch (e) {
			console.error('Failed to load stats:', e);
		}
	}

	async function loadAll(showLoading = true) {
		if (showLoading) loading = true;
		await Promise.all([fetchTasks(), fetchStats()]);
		loading = false;
		refreshing = false;
	}

	function handleRefresh() {
		refreshing = true;
		loadAll(false);
	}

	function handleSearchInput() {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			currentPage = 1;
			fetchTasks();
		}, 300);
	}

	function selectStatusPill(status: string) {
		statusFilter = status;
		currentPage = 1;
		fetchTasks();
	}

	function handlePerPageChange() {
		currentPage = 1;
		fetchTasks();
	}

	function goToPage(page: number) {
		if (page < 1 || page > totalPages) return;
		currentPage = page;
		fetchTasks();
	}

	function toggleSort(field: string) {
		if (sortBy === field) {
			sortDir = sortDir === 'asc' ? 'desc' : 'asc';
		} else {
			sortBy = field;
			sortDir = field === 'name' ? 'asc' : 'desc';
		}
		currentPage = 1;
		fetchTasks();
	}

	async function handleCancel(taskId: string) {
		cancellingIds = new Set([...cancellingIds, taskId]);
		try {
			await cancelAnalysisTask(taskId);
			await loadAll(false);
		} catch (e) {
			console.error('Failed to cancel task:', e);
		} finally {
			const next = new Set(cancellingIds);
			next.delete(taskId);
			cancellingIds = next;
		}
	}

	async function handleDelete(taskId: string) {
		deletingIds = new Set([...deletingIds, taskId]);
		try {
			await deleteAnalysisTask(taskId);
			await loadAll(false);
		} catch (e) {
			console.error('Failed to delete task:', e);
		} finally {
			const next = new Set(deletingIds);
			next.delete(taskId);
			deletingIds = next;
		}
	}

	function setupPolling() {
		clearInterval(pollTimer);
		pollTimer = setInterval(() => {
			if (hasRunningTasks) {
				fetchTasks();
				fetchStats();
			}
		}, 5000);
	}

	function taskDisplayName(task: AnalysisTaskList): string {
		return task.name || `Analysis #${task.id.slice(0, 8)}`;
	}

	function getSeverityBreakdown(task: AnalysisTaskList): [string, number][] {
		const bySeverity = task.results_summary?.by_severity;
		if (!bySeverity || typeof bySeverity !== 'object') return [];
		return Object.entries(bySeverity).filter(([, v]) => (v as number) > 0) as [string, number][];
	}

	onMount(() => {
		loadAll();
		setupPolling();
		return () => {
			clearTimeout(debounceTimer);
			clearInterval(pollTimer);
		};
	});
</script>

<svelte:head>
	<title>Analysis Hub - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header min-w-0">
			<h1>Analysis Hub</h1>
			<p>Run and monitor analysis tasks across your applications.</p>
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<Button variant="outline" size="sm" onclick={handleRefresh} disabled={refreshing}>
				<RefreshCw class="mr-2 h-3.5 w-3.5 {refreshing ? 'animate-spin' : ''}" />
				Refresh
			</Button>
			<!-- Export dropdown -->
			<details class="relative">
				<summary class="list-none">
					<Button variant="outline" size="sm" tag="span">
						<Download class="mr-2 h-3.5 w-3.5" />
						Export
					</Button>
				</summary>
				<div class="absolute right-0 z-50 mt-1 w-52 rounded-md border bg-popover p-1 shadow-md">
					<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('analysis-tasks.csv', 'analysis-tasks.csv')}>Tasks CSV</button>
					<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('analysis-tasks.json', 'analysis-tasks.json')}>Tasks JSON</button>
					<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('findings.csv', 'findings.csv')}>Findings CSV</button>
					<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('findings.json', 'findings.json')}>Findings JSON</button>
					<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('findings.sarif', 'findings.sarif')}>Findings SARIF</button>
				</div>
			</details>
			<Button size="sm" href="/analysis/create">
				<Plus class="mr-2 h-3.5 w-3.5" />
				New
			</Button>
		</div>
	</div>

	<!-- Stats -->
	{#if stats}
		<div class="flex flex-wrap items-center gap-2">
			<Badge variant="outline" class="gap-1.5">
				<Microscope class="h-3 w-3" />
				{stats.total_tasks} tasks
			</Badge>
			<Badge variant="outline" class="gap-1.5 border-blue-500/30 text-blue-400">
				{stats.running_tasks} running
			</Badge>
			<Badge variant="outline" class="gap-1.5 border-emerald-500/30 text-emerald-500">
				{stats.completed_tasks} completed
			</Badge>
			{#if stats.failed_tasks > 0}
				<Badge variant="outline" class="gap-1.5 border-red-500/30 text-red-400">
					{stats.failed_tasks} failed
				</Badge>
			{/if}
			{#if stats.total_findings > 0}
				<Badge variant="outline" class="gap-1.5">
					{stats.total_findings} findings
				</Badge>
			{/if}
		</div>
	{/if}

	<!-- Filter Bar -->
	<FilterBar
		searchPlaceholder="Search by name…"
		bind:searchValue={searchQuery}
		onSearchInput={handleSearchInput}
		activeTags={activeFilters}
		resultsText={resultsText}
		page={currentPage}
		pages={totalPages}
		onGoToPage={goToPage}
		onClearAll={() => { searchQuery = ''; statusFilter = ''; currentPage = 1; fetchTasks(); }}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Status</span>
				<button class="fb-chip {statusFilter === '' ? 'fb-chip-on' : ''}" onclick={() => selectStatusPill('')}>All
					{#if stats}<span class="ml-1 font-mono text-[10px]">{stats.total_tasks}</span>{/if}
				</button>
				<button class="fb-chip {statusFilter === 'running' ? 'fb-chip-blue' : ''}" onclick={() => selectStatusPill('running')}>
					<span class="relative flex h-1.5 w-1.5 shrink-0">
						<span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
						<span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-blue-500"></span>
					</span>
					Running
					{#if stats && stats.running_tasks > 0}<span class="ml-1 font-mono text-[10px]">{stats.running_tasks}</span>{/if}
				</button>
				<button class="fb-chip {statusFilter === 'pending' ? 'fb-chip-slate' : ''}" onclick={() => selectStatusPill('pending')}>Pending</button>
				<button class="fb-chip {statusFilter === 'completed' ? 'fb-chip-emerald' : ''}" onclick={() => selectStatusPill('completed')}>Completed
					{#if stats && stats.completed_tasks > 0}<span class="ml-1 font-mono text-[10px]">{stats.completed_tasks}</span>{/if}
				</button>
				<button class="fb-chip {statusFilter === 'partial' ? 'fb-chip-amber' : ''}" onclick={() => selectStatusPill('partial')}>Partial</button>
				<button class="fb-chip {statusFilter === 'failed' ? 'fb-chip-red' : ''}" onclick={() => selectStatusPill('failed')}>Failed
					{#if stats && stats.failed_tasks > 0}<span class="ml-1 font-mono text-[10px]">{stats.failed_tasks}</span>{/if}
				</button>
				<button class="fb-chip {statusFilter === 'cancelled' ? 'fb-chip-slate' : ''}" onclick={() => selectStatusPill('cancelled')}>Cancelled</button>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Per page</span>
				{#each [10, 25, 50, 100] as n}
					<button class="fb-chip {perPage === n ? 'fb-chip-on' : ''}" onclick={() => { perPage = n; currentPage = 1; fetchTasks(); }}>{n}</button>
				{/each}
				<div class="fb-group ml-2">
					<span class="fb-group-label">Sort</span>
					{#each sortableColumns as col}
						<button
							class="fb-chip {sortBy === col.key ? 'fb-chip-indigo' : ''}"
							onclick={() => { if (sortBy === col.key) { sortDir = sortDir === 'asc' ? 'desc' : 'asc'; } else { sortBy = col.key; sortDir = 'desc'; } currentPage = 1; fetchTasks(); }}
						>{col.label}{#if sortBy === col.key}<span class="ml-0.5 opacity-70">{sortDir === 'asc' ? '↑' : '↓'}</span>{/if}</button>
					{/each}
				</div>
			</div>
		{/snippet}
	</FilterBar>

	<!-- Loading -->
	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<!-- Error -->
		<Card.Root>
			<Card.Content class="flex flex-col items-center gap-3 py-12">
				<AlertTriangle class="h-8 w-8 text-red-400" />
				<p class="text-sm text-muted-foreground">{error}</p>
				<Button variant="outline" size="sm" onclick={() => loadAll()}>Retry</Button>
			</Card.Content>
		</Card.Root>
	{:else if tasks.length === 0}
		<!-- Empty -->
		<Card.Root>
			<Card.Content class="py-16 text-center">
				<Microscope class="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
				<h3 class="text-lg font-medium mb-1">No analysis tasks found</h3>
				<p class="text-sm text-muted-foreground mb-4">
					{searchQuery || statusFilter ? 'No tasks match your filters.' : 'No analysis tasks yet.'}
				</p>
				{#if !searchQuery && !statusFilter}
					<Button size="sm" href="/analysis/create">
						<Plus class="mr-2 h-3.5 w-3.5" />
						Run your first analysis
					</Button>
				{/if}
			</Card.Content>
		</Card.Root>
	{:else}
		<!-- Tasks Table (desktop) -->
		<div class="hidden md:block">
			<Card.Root>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="w-full">
							<thead>
								<tr class="border-b bg-muted/40 sticky top-0 z-10">
									{#each sortableColumns as col}
										<th
											class="px-3 py-2.5 text-{col.align ?? 'left'} text-xs font-medium text-muted-foreground whitespace-nowrap"
											aria-sort={sortBy === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
										>
											<button
												class="inline-flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded"
												onclick={() => toggleSort(col.key)}
											>
												{col.label}
												{#if sortBy === col.key}
													{#if sortDir === 'asc'}
														<ArrowUp class="h-3 w-3 text-primary" />
													{:else}
														<ArrowDown class="h-3 w-3 text-primary" />
													{/if}
												{:else}
													<ArrowUpDown class="h-3 w-3 opacity-30" />
												{/if}
											</button>
										</th>
									{/each}
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Status</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Findings</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each tasks as task, i (task.id)}
									<tr
										class="border-b transition-colors hover:bg-muted/50 group cursor-pointer
											{i % 2 === 0 ? '' : 'bg-muted/15'}
											{task.status === 'failed' ? 'bg-destructive/[0.03]' : ''}"
										onclick={() => window.location.href = `/analysis/${task.id}`}
										onkeydown={(e) => { if (e.key === 'Enter') window.location.href = `/analysis/${task.id}`; }}
										tabindex="0"
										role="link"
									>
										<td class="px-3 py-2 align-top">
											<div class="flex items-center gap-2.5">
												<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted group-hover:bg-primary/10 transition-colors">
													<Microscope class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
												</div>
												<div class="min-w-0">
													<span class="text-sm font-medium block truncate max-w-[220px]">{taskDisplayName(task)}</span>
													<span class="text-[11px] text-muted-foreground font-mono block">{task.id.slice(0, 8)}</span>
												</div>
											</div>
										</td>
										<td class="px-3 py-2">
											<div class="flex flex-col gap-0.5">
												<span class="text-sm text-foreground">{formatDate(task.created_at)}</span>
											</div>
										</td>
										<td class="px-3 py-2 text-right">
											<span class="text-sm font-mono tabular-nums text-muted-foreground">{formatDuration(task.duration_seconds)}</span>
										</td>
										<td class="px-3 py-2 align-top">
											<Badge variant="outline" class="text-[10px] {statusColors[task.status] ?? ''}">
												{#if task.status === 'running'}
													<LoaderCircle class="mr-1 h-3 w-3 animate-spin" />
												{:else if task.status === 'pending'}
													<Clock class="mr-1 h-3 w-3" />
												{:else if task.status === 'completed'}
													<Check class="mr-1 h-3 w-3" />
												{:else if task.status === 'failed'}
													<X class="mr-1 h-3 w-3" />
												{:else if task.status === 'cancelled'}
													<Ban class="mr-1 h-3 w-3" />
												{:else if task.status === 'partial'}
													<AlertTriangle class="mr-1 h-3 w-3" />
												{/if}
												{statusLabel(task.status)}
											</Badge>
										</td>
										<td class="px-3 py-2 align-top">
											{#if getSeverityBreakdown(task).length > 0}
												<div class="flex flex-wrap gap-1">
													{#each getSeverityBreakdown(task) as [sev, count]}
														<Badge variant="outline" class="text-[10px] {severityColors[sev] ?? ''}">{count} {sev.charAt(0).toUpperCase()}</Badge>
													{/each}
												</div>
											{:else}
												<span class="text-xs text-muted-foreground">—</span>
											{/if}
										</td>
										<td class="px-3 py-2">
											<!-- svelte-ignore a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions a11y_click_events_have_key_events -->
											<div class="flex items-center justify-end gap-1" onclick={(e) => e.stopPropagation()}>
												<Button variant="ghost" size="sm" class="h-7 w-7 p-0" href="/analysis/{task.id}" title="View results">
													<Eye class="h-3.5 w-3.5" />
												</Button>
												{#if task.status === 'running' || task.status === 'pending'}
													<Button
														variant="ghost"
														size="sm"
														class="h-7 w-7 p-0"
														title="Cancel"
														disabled={cancellingIds.has(task.id)}
														onclick={() => handleCancel(task.id)}
													>
														{#if cancellingIds.has(task.id)}
															<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
														{:else}
															<StopCircle class="h-3.5 w-3.5 text-amber-400" />
														{/if}
													</Button>
												{/if}
												{#if task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled' || task.status === 'partial'}
													<Button
														variant="ghost"
														size="sm"
														class="h-7 w-7 p-0"
														title="Delete"
														disabled={deletingIds.has(task.id)}
														onclick={() => handleDelete(task.id)}
													>
														{#if deletingIds.has(task.id)}
															<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
														{:else}
															<Trash2 class="h-3.5 w-3.5 text-destructive" />
														{/if}
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

		<!-- Tasks Cards (mobile) -->
		<div class="md:hidden space-y-3">
			{#each tasks as task (task.id)}
				<a href="/analysis/{task.id}" class="block border rounded-lg p-3 bg-card transition-colors hover:bg-muted/30">
					<div class="flex items-center justify-between gap-2 mb-2">
						<div class="flex flex-col min-w-0">
							<span class="text-sm font-medium truncate">{taskDisplayName(task)}</span>
							<span class="font-mono text-xs text-muted-foreground">{task.id.slice(0, 8)}</span>
						</div>
						<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[task.status] ?? ''}">
							{#if task.status === 'running'}
								<LoaderCircle class="mr-1 h-3 w-3 animate-spin" />
							{:else if task.status === 'pending'}
								<Clock class="mr-1 h-3 w-3" />
							{:else if task.status === 'completed'}
								<Check class="mr-1 h-3 w-3" />
							{:else if task.status === 'failed'}
								<X class="mr-1 h-3 w-3" />
							{:else if task.status === 'cancelled'}
								<Ban class="mr-1 h-3 w-3" />
							{:else if task.status === 'partial'}
								<AlertTriangle class="mr-1 h-3 w-3" />
							{/if}
							{statusLabel(task.status)}
						</Badge>
					</div>

					<div class="space-y-1.5 mb-2">
						{#if getSeverityBreakdown(task).length > 0}
							<div class="flex flex-wrap gap-1">
								{#each getSeverityBreakdown(task) as [sev, count]}
									<Badge variant="outline" class="text-[10px] {severityColors[sev] ?? ''}">{count} {sev.charAt(0).toUpperCase()}</Badge>
								{/each}
							</div>
						{/if}
					</div>

					<div class="flex items-center justify-between border-t pt-2">
						<div class="text-xs text-muted-foreground">
							<span>{formatDuration(task.duration_seconds)}</span>
							<span class="mx-1">·</span>
							<span>{formatDate(task.created_at)}</span>
						</div>
						<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
						<div class="flex items-center gap-1" onclick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
							<Button variant="ghost" size="sm" class="h-7 w-7 p-0" href="/analysis/{task.id}" title="View results">
								<Eye class="h-3.5 w-3.5" />
							</Button>
							{#if task.status === 'running' || task.status === 'pending'}
								<Button
									variant="ghost"
									size="sm"
									class="h-7 w-7 p-0"
									title="Cancel"
									disabled={cancellingIds.has(task.id)}
									onclick={() => handleCancel(task.id)}
								>
									{#if cancellingIds.has(task.id)}
										<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
									{:else}
										<StopCircle class="h-3.5 w-3.5 text-amber-400" />
									{/if}
								</Button>
							{/if}
							{#if task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled' || task.status === 'partial'}
								<Button
									variant="ghost"
									size="sm"
									class="h-7 w-7 p-0"
									title="Delete"
									disabled={deletingIds.has(task.id)}
									onclick={() => handleDelete(task.id)}
								>
									{#if deletingIds.has(task.id)}
										<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
									{:else}
										<Trash2 class="h-3.5 w-3.5 text-destructive" />
									{/if}
								</Button>
							{/if}
						</div>
					</div>
				</a>
			{/each}
		</div>
	{/if}

	<PaginationBar
		resultsText={totalTasks > 0 ? `Showing ${(currentPage - 1) * perPage + 1}–${Math.min(currentPage * perPage, totalTasks)} of ${totalTasks} tasks` : ''}
		page={currentPage}
		pages={totalPages}
		onGoToPage={goToPage}
		class="rounded-md border border-border"
	/>
</div>
