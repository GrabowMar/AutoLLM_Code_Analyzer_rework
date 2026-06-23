<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Store from '@lucide/svelte/icons/store';
	import Eye from '@lucide/svelte/icons/eye';
	import StopCircle from '@lucide/svelte/icons/circle-stop';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Clock from '@lucide/svelte/icons/clock';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import Ban from '@lucide/svelte/icons/ban';
	import AlertTriangle from '@lucide/svelte/icons/triangle-alert';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		listRuns,
		cancelRun,
		deleteRun,
		type AnalysisRunListItem,
	} from '$lib/api/runs';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { statusColors, severityColors } from '$lib/constants/analysis';
	import { formatDate, statusLabel } from '$lib/utils/analysis';

	let loading = $state(true);
	let error = $state('');
	let runs = $state<AnalysisRunListItem[]>([]);
	let total = $state(0);
	let totalPages = $state(1);
	let currentPage = $state(1);
	let perPage = $state(25);
	let statusFilter = $state('');
	let searchQuery = $state('');
	let refreshing = $state(false);
	let cancellingIds = $state(new Set<string>());
	let deletingIds = $state(new Set<string>());

	let pollTimer: ReturnType<typeof setInterval> | undefined;
	let hasActive = $derived(runs.some((r) => r.status === 'running' || r.status === 'pending'));

	const statusOptions = ['running', 'pending', 'completed', 'partial', 'failed', 'cancelled'];

	const activeFilters = $derived.by(() => {
		const tags: FilterTag[] = [];
		if (statusFilter) {
			tags.push({
				key: 'status',
				label: `Status: ${statusLabel(statusFilter)}`,
				onRemove: () => { statusFilter = ''; currentPage = 1; fetchRuns(); },
			});
		}
		return tags;
	});

	const resultsText = $derived(
		total > 0
			? `Showing ${(currentPage - 1) * perPage + 1}–${Math.min(currentPage * perPage, total)} of ${total} runs`
			: '',
	);

	const displayedRuns = $derived(
		runs.filter(
			(r) =>
				!searchQuery ||
				(r.name ?? '').toLowerCase().includes(searchQuery.toLowerCase()) ||
				r.id.toLowerCase().includes(searchQuery.toLowerCase()),
		),
	);

	function runName(run: AnalysisRunListItem): string {
		return run.name || `Analysis #${run.id.slice(0, 8)}`;
	}

	function severityBreakdown(run: AnalysisRunListItem): [string, number][] {
		const counts = run.summary?.severity_counts as Record<string, number> | undefined;
		if (!counts || typeof counts !== 'object') return [];
		return Object.entries(counts).filter(([, v]) => (v as number) > 0) as [string, number][];
	}

	async function fetchRuns() {
		try {
			const data = await listRuns({
				page: currentPage,
				per_page: perPage,
				status: statusFilter || undefined,
			});
			runs = data.items;
			total = data.total;
			totalPages = data.pages;
			currentPage = data.page;
			error = '';
		} catch (e) {
			error = 'Failed to load analysis runs.';
			console.error(e);
		}
	}

	async function loadAll(showLoading = true) {
		if (showLoading) loading = true;
		await fetchRuns();
		loading = false;
		refreshing = false;
	}

	function handleRefresh() {
		refreshing = true;
		loadAll(false);
	}

	function selectStatus(status: string) {
		statusFilter = status;
		currentPage = 1;
		fetchRuns();
	}

	function goToPage(page: number) {
		if (page < 1 || page > totalPages) return;
		currentPage = page;
		fetchRuns();
	}

	async function handleCancel(id: string) {
		cancellingIds = new Set([...cancellingIds, id]);
		try {
			await cancelRun(id);
			toast.success('Run cancelled');
			await loadAll(false);
		} catch (e) {
			console.error('Failed to cancel run:', e);
			toast.error('Failed to cancel run');
		} finally {
			const next = new Set(cancellingIds);
			next.delete(id);
			cancellingIds = next;
		}
	}

	// Delete is gated behind a confirmation dialog.
	let confirmDeleteId = $state<string | null>(null);
	let confirmOpen = $state(false);
	let confirmBusy = $state(false);

	function askDelete(id: string) {
		confirmDeleteId = id;
		confirmOpen = true;
	}

	async function confirmDelete() {
		const id = confirmDeleteId;
		if (!id) return;
		confirmBusy = true;
		deletingIds = new Set([...deletingIds, id]);
		try {
			await deleteRun(id);
			toast.success('Run deleted');
			confirmOpen = false;
			await loadAll(false);
		} catch (e) {
			console.error('Failed to delete run:', e);
			toast.error('Failed to delete run');
		} finally {
			confirmBusy = false;
			const next = new Set(deletingIds);
			next.delete(id);
			deletingIds = next;
		}
	}

	onMount(() => {
		loadAll();
		pollTimer = setInterval(() => {
			if (hasActive) fetchRuns();
		}, 5000);
		return () => clearInterval(pollTimer);
	});
</script>

<svelte:head>
	<title>Analysis - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header min-w-0">
			<h1>Analysis</h1>
			<p>Run and monitor analyzers against your generated apps.</p>
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<Button variant="outline" size="sm" href="/analyzers">
				<Store class="mr-2 h-3.5 w-3.5" />
				Tool Shop
			</Button>
			<Button variant="outline" size="sm" onclick={handleRefresh} disabled={refreshing}>
				<RefreshCw class="mr-2 h-3.5 w-3.5 {refreshing ? 'animate-spin' : ''}" />
				Refresh
			</Button>
			<Button size="sm" href="/analysis/create">
				<Plus class="mr-2 h-3.5 w-3.5" />
				New
			</Button>
		</div>
	</div>

	<FilterBar
		searchPlaceholder="Filter loaded runs by name or ID…"
		bind:searchValue={searchQuery}
		activeTags={activeFilters}
		{resultsText}
		page={currentPage}
		pages={totalPages}
		onGoToPage={goToPage}
		onClearAll={() => { statusFilter = ''; searchQuery = ''; currentPage = 1; fetchRuns(); }}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Status</span>
				<button class="fb-chip {statusFilter === '' ? 'fb-chip-on' : ''}" onclick={() => selectStatus('')}>All</button>
				{#each statusOptions as s}
					<button class="fb-chip {statusFilter === s ? 'fb-chip-on' : ''}" onclick={() => selectStatus(s)}>{statusLabel(s)}</button>
				{/each}
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Per page</span>
				{#each [10, 25, 50, 100] as n}
					<button class="fb-chip {perPage === n ? 'fb-chip-on' : ''}" onclick={() => { perPage = n; currentPage = 1; fetchRuns(); }}>{n}</button>
				{/each}
			</div>
		{/snippet}
	</FilterBar>

	{#if loading}
		<!-- Skeleton table — reserves space to avoid layout shift -->
		<Card.Root>
			<Card.Content class="p-0">
				<div class="animate-pulse motion-reduce:animate-none divide-y divide-border">
					<div class="flex items-center gap-3 bg-muted/40 px-3 py-2.5">
						<div class="h-3 w-24 rounded bg-muted"></div>
						<div class="ml-auto h-3 w-16 rounded bg-muted"></div>
					</div>
					{#each Array(6) as _}
						<div class="flex items-center gap-3 px-3 py-3">
							<div class="h-8 w-8 shrink-0 rounded-md bg-muted"></div>
							<div class="h-3 w-40 rounded bg-muted"></div>
							<div class="ml-auto flex items-center gap-2">
								<div class="h-5 w-16 rounded-full bg-muted"></div>
								<div class="h-5 w-10 rounded-full bg-muted"></div>
							</div>
						</div>
					{/each}
				</div>
			</Card.Content>
		</Card.Root>
	{:else if error}
		<Card.Root>
			<Card.Content class="flex flex-col items-center gap-3 py-12">
				<AlertTriangle class="h-8 w-8 text-red-400" />
				<p class="text-sm text-muted-foreground">{error}</p>
				<Button variant="outline" size="sm" onclick={() => loadAll()}>Retry</Button>
			</Card.Content>
		</Card.Root>
	{:else if runs.length === 0}
		<Card.Root>
			<Card.Content class="py-16 text-center">
				<Microscope class="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
				<h3 class="text-lg font-medium mb-1">No analysis runs found</h3>
				<p class="text-sm text-muted-foreground mb-4">
					{statusFilter ? 'No runs match your filter.' : 'No analysis runs yet.'}
				</p>
				{#if !statusFilter}
					<Button size="sm" href="/analysis/create">
						<Plus class="mr-2 h-3.5 w-3.5" />
						Run your first analysis
					</Button>
				{/if}
			</Card.Content>
		</Card.Root>
	{:else}
		<div class="hidden md:block">
			<Card.Root>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="w-full">
							<thead>
								<tr class="border-b bg-muted/40">
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Name</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Created</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Tools</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground">Findings</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each displayedRuns as run, i (run.id)}
									<tr
										class="border-b transition-colors hover:bg-muted/50 group cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset {i % 2 === 0 ? '' : 'bg-muted/15'} {run.status === 'failed' ? 'bg-destructive/[0.03]' : ''}"
										onclick={() => window.location.href = `/analysis/${run.id}`}
										onkeydown={(e) => { if (e.key === 'Enter') window.location.href = `/analysis/${run.id}`; }}
										tabindex="0"
										role="link"
										aria-label="Open analysis run {runName(run)}"
									>
										<td class="px-3 py-2 align-top">
											<div class="flex items-center gap-2.5">
												<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted group-hover:bg-primary/10 transition-colors">
													<Microscope class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
												</div>
												<div class="min-w-0">
													<span class="text-sm font-medium block truncate max-w-[220px]">{runName(run)}</span>
													<span class="text-[11px] text-muted-foreground font-mono block">{run.id.slice(0, 8)}</span>
												</div>
											</div>
										</td>
										<td class="px-3 py-2 text-sm">{formatDate(run.created_at)}</td>
										<td class="px-3 py-2">
											<div class="flex flex-wrap gap-1">
												{#each run.tool_slugs.slice(0, 4) as slug}
													<Badge variant="secondary" class="text-[10px]">{slug}</Badge>
												{/each}
												{#if run.tool_slugs.length > 4}
													<Badge variant="outline" class="text-[10px]">+{run.tool_slugs.length - 4}</Badge>
												{/if}
											</div>
										</td>
										<td class="px-3 py-2 align-top">
											<Badge variant="outline" class="text-[10px] {statusColors[run.status] ?? ''}">
												{#if run.status === 'running'}
													<LoaderCircle class="mr-1 h-3 w-3 animate-spin motion-reduce:animate-none" />
												{:else if run.status === 'pending'}
													<Clock class="mr-1 h-3 w-3" />
												{:else if run.status === 'completed'}
													<Check class="mr-1 h-3 w-3" />
												{:else if run.status === 'failed'}
													<X class="mr-1 h-3 w-3" />
												{:else if run.status === 'cancelled'}
													<Ban class="mr-1 h-3 w-3" />
												{:else if run.status === 'partial'}
													<AlertTriangle class="mr-1 h-3 w-3" />
												{/if}
												{statusLabel(run.status)}
											</Badge>
										</td>
										<td class="px-3 py-2 align-top">
											{#if severityBreakdown(run).length > 0}
												<div class="flex flex-wrap gap-1">
													{#each severityBreakdown(run) as [sev, count]}
														<Badge variant="outline" class="text-[10px] {severityColors[sev] ?? ''}">{count} {sev.charAt(0).toUpperCase()}</Badge>
													{/each}
												</div>
											{:else}
												<span class="text-xs text-muted-foreground">—</span>
											{/if}
										</td>
										<td class="px-3 py-2">
											<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
											<div class="flex items-center justify-end gap-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>
												<Button variant="ghost" size="sm" class="h-7 w-7 p-0" href="/analysis/{run.id}" title="View results" aria-label="View results">
													<Eye class="h-3.5 w-3.5" />
												</Button>
												{#if run.status === 'running' || run.status === 'pending'}
													<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Cancel" aria-label="Cancel run" disabled={cancellingIds.has(run.id)} onclick={() => handleCancel(run.id)}>
														{#if cancellingIds.has(run.id)}
															<LoaderCircle class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
														{:else}
															<StopCircle class="h-3.5 w-3.5 text-amber-400" />
														{/if}
													</Button>
												{:else}
													<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Delete" aria-label="Delete run" disabled={deletingIds.has(run.id)} onclick={() => askDelete(run.id)}>
														{#if deletingIds.has(run.id)}
															<LoaderCircle class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
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

		<!-- Mobile cards -->
		<div class="md:hidden space-y-3">
			{#each displayedRuns as run (run.id)}
				<a href="/analysis/{run.id}" class="block border rounded-lg p-3 bg-card transition-colors hover:bg-muted/30">
					<div class="flex items-center justify-between gap-2 mb-2">
						<div class="flex flex-col min-w-0">
							<span class="text-sm font-medium truncate">{runName(run)}</span>
							<span class="font-mono text-xs text-muted-foreground">{run.id.slice(0, 8)}</span>
						</div>
						<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[run.status] ?? ''}">{statusLabel(run.status)}</Badge>
					</div>
					{#if severityBreakdown(run).length > 0}
						<div class="flex flex-wrap gap-1 mb-2">
							{#each severityBreakdown(run) as [sev, count]}
								<Badge variant="outline" class="text-[10px] {severityColors[sev] ?? ''}">{count} {sev.charAt(0).toUpperCase()}</Badge>
							{/each}
						</div>
					{/if}
					<div class="border-t pt-2 text-xs text-muted-foreground">{formatDate(run.created_at)}</div>
				</a>
			{/each}
		</div>
	{/if}

	<PaginationBar
		resultsText={resultsText}
		page={currentPage}
		pages={totalPages}
		onGoToPage={goToPage}
		class="rounded-md border border-border"
	/>
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete analysis run?"
	description="This permanently removes the run and all of its findings. This cannot be undone."
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={confirmDelete}
/>
