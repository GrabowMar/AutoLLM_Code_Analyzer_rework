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
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { severityColors } from '$lib/constants/analysis';
	import { formatDate, statusLabel } from '$lib/utils/formatters';
	import { tableFrame } from '$lib/components/table';

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

<div class="space-y-6">
	<PageHeader title="Analysis" description="Run and monitor analyzers against your generated apps.">
		{#snippet actions()}
			<Button variant="outline" size="sm" href="/analyzers">
				<Store class="mr-2 h-3.5 w-3.5" />
				Tool Shop
			</Button>
			<Button variant="outline" size="sm" onclick={handleRefresh} disabled={refreshing}>
				<RefreshCw class="mr-2 h-3.5 w-3.5 {refreshing ? 'animate-spin motion-reduce:animate-none' : ''}" />
				Refresh
			</Button>
			<Button size="sm" href="/analysis/create">
				<Plus class="mr-2 h-3.5 w-3.5" />
				New analysis
			</Button>
		{/snippet}
	</PageHeader>

	<FilterBar
		searchPlaceholder="Filter loaded runs by name or ID…"
		bind:searchValue={searchQuery}
		activeTags={activeFilters}
		{resultsText}
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
		<LoadingState label="loading analysis runs" />
	{:else if error}
		<ErrorAlert message={error} onretry={() => loadAll()} />
	{:else if runs.length === 0}
		<EmptyState
			icon={Microscope}
			title="No analysis runs found"
			description={statusFilter ? 'No runs match your filter.' : 'No analysis runs yet.'}
		>
			{#snippet action()}
				{#if statusFilter}
					<Button variant="outline" size="sm" onclick={() => selectStatus('')}>Clear filter</Button>
				{:else}
					<Button size="sm" href="/analysis/create">
						<Plus class="mr-2 h-3.5 w-3.5" />
						Run your first analysis
					</Button>
				{/if}
			{/snippet}
		</EmptyState>
	{:else}
		<div class="hidden md:block">
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead>
								<tr>
									<th>Name</th>
									<th>Created</th>
									<th>Tools</th>
									<th>Status</th>
									<th>Findings</th>
									<th class="text-right"><span class="sr-only">Actions</span></th>
								</tr>
							</thead>
							<tbody>
								{#each displayedRuns as run (run.id)}
									<tr
										class="row-link group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset {run.status === 'failed' ? 'bg-destructive/[0.03]' : ''}"
										onclick={() => window.location.href = `/analysis/${run.id}`}
										onkeydown={(e) => { if (e.key === 'Enter') window.location.href = `/analysis/${run.id}`; }}
										tabindex="0"
										role="link"
										aria-label="Open analysis run {runName(run)}"
									>
										<td class="align-top">
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
										<td class="text-sm">{formatDate(run.created_at)}</td>
										<td>
											<div class="flex flex-wrap gap-1">
												{#each run.tool_slugs.slice(0, 4) as slug}
													<Badge variant="secondary" class="text-[11px]">{slug}</Badge>
												{/each}
												{#if run.tool_slugs.length > 4}
													<Badge variant="outline" class="text-[11px]">+{run.tool_slugs.length - 4}</Badge>
												{/if}
											</div>
										</td>
										<td class="align-top">
											<StatusBadge status={run.status} label={statusLabel(run.status)} />
										</td>
										<td class="align-top">
											{#if severityBreakdown(run).length > 0}
												<div class="flex flex-wrap gap-1">
													{#each severityBreakdown(run) as [sev, count]}
														<Badge variant="outline" class="text-[11px] {severityColors[sev] ?? ''}">{count} {sev.charAt(0).toUpperCase()}</Badge>
													{/each}
												</div>
											{:else}
												<span class="text-xs text-muted-foreground">—</span>
											{/if}
										</td>
										<td>
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
															<StopCircle class="h-3.5 w-3.5 text-warning-fg" />
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
					{@render pager('border-t border-border')}
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
						<StatusBadge status={run.status} label={statusLabel(run.status)} />
					</div>
					{#if severityBreakdown(run).length > 0}
						<div class="flex flex-wrap gap-1 mb-2">
							{#each severityBreakdown(run) as [sev, count]}
								<Badge variant="outline" class="text-[11px] {severityColors[sev] ?? ''}">{count} {sev.charAt(0).toUpperCase()}</Badge>
							{/each}
						</div>
					{/if}
					<div class="border-t pt-2 text-xs text-muted-foreground">{formatDate(run.created_at)}</div>
				</a>
			{/each}
			{@render pager('rounded-md border border-border')}
		</div>
	{/if}
</div>

{#snippet pager(cls: string)}
	<PaginationBar {resultsText} page={currentPage} pages={totalPages} onGoToPage={goToPage} class={cls} scrollToCard />
{/snippet}

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete analysis run?"
	description="This permanently removes the run and all of its findings. This cannot be undone."
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={confirmDelete}
/>
