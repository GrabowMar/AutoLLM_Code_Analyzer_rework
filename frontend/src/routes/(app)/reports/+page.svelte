<script lang="ts">
	import { formatDateTime as formatDate } from '$lib/utils/formatters';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Eye from '@lucide/svelte/icons/eye';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Brain from '@lucide/svelte/icons/brain';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import Wrench from '@lucide/svelte/icons/wrench';
	import TrendingUp from '@lucide/svelte/icons/trending-up';
	import Layers from '@lucide/svelte/icons/layers';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { downloadExport } from '$lib/api/export';
	import Download from '@lucide/svelte/icons/download';
	import {
		getReports,
		deleteReport,
		type ReportSummary,
		type ReportStatus,
		type ReportType
	} from '$lib/api/client';

	let reports = $state<ReportSummary[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');
	let typeFilter = $state<'all' | ReportType>('all');
	let statusFilter = $state<'all' | ReportStatus>('all');
	let searchQuery = $state('');
	let sortBy = $state<'newest' | 'oldest' | 'title'>('newest');

	// Debounce search
	let searchDebounce: ReturnType<typeof setTimeout> | null = null;
	function onSearchInput() {
		if (searchDebounce) clearTimeout(searchDebounce);
		searchDebounce = setTimeout(() => load(), 350);
	}

	const typeConfig: Record<ReportType, { label: string; icon: typeof Brain }> = {
		model_analysis: { label: 'Model Analysis', icon: Brain },
		template_comparison: { label: 'Template Comparison', icon: GitCompareArrows },
		tool_analysis: { label: 'Tool Analysis', icon: Wrench },
		generation_analytics: { label: 'Generation Analytics', icon: TrendingUp },
		comprehensive: { label: 'Comprehensive', icon: Layers },
		experiment_report: { label: 'Experiment Report', icon: FlaskConical }
	};

	async function load() {
		loading = true;
		error = '';
		try {
			const params: Record<string, string> = {};
			if (typeFilter !== 'all') params.report_type = typeFilter;
			if (statusFilter !== 'all') params.status = statusFilter;
			const res = await getReports(params);
			let items = res.reports;
			// Client-side search filter (title + description)
			if (searchQuery.trim()) {
				const q = searchQuery.toLowerCase();
				items = items.filter(
					(r) => r.title.toLowerCase().includes(q) || (r.description ?? '').toLowerCase().includes(q)
				);
			}
			// Client-side sort
			if (sortBy === 'newest') {
				items = [...items].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
			} else if (sortBy === 'oldest') {
				items = [...items].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
			} else if (sortBy === 'title') {
				items = [...items].sort((a, b) => a.title.localeCompare(b.title));
			}
			reports = items;
			total = res.pagination.total;
		} catch (e) {
			error = (e as Error)?.message || 'Failed to load reports';
		} finally {
			loading = false;
		}
	}

	// Delete is gated behind a confirmation dialog.
	let confirmDeleteId = $state<string | null>(null);
	let confirmOpen = $state(false);
	let confirmBusy = $state(false);

	function remove(id: string) {
		confirmDeleteId = id;
		confirmOpen = true;
	}

	async function confirmDelete() {
		if (!confirmDeleteId) return;
		confirmBusy = true;
		try {
			await deleteReport(confirmDeleteId);
			confirmOpen = false;
			await load();
		} catch (e) {
			toast.error((e as Error)?.message || 'Failed to delete report');
		} finally {
			confirmBusy = false;
		}
	}

	const activeFilters = $derived.by(() => {
		const tags: FilterTag[] = [];
		if (typeFilter !== 'all') {
			tags.push({ key: 'type', label: `Type: ${typeConfig[typeFilter as ReportType]?.label ?? typeFilter}`, onRemove: () => { typeFilter = 'all'; } });
		}
		if (statusFilter !== 'all') {
			tags.push({ key: 'status', label: `Status: ${statusFilter}`, onRemove: () => { statusFilter = 'all'; } });
		}
		return tags;
	});

	const resultsText = $derived(reports.length > 0 || !loading ? `${reports.length} of ${total} reports` : '');

	$effect(() => {
		void typeFilter;
		void statusFilter;
		void sortBy;
		load();
	});

	onMount(load);
</script>

<div class="space-y-6">
	<PageHeader title="Reports" description="Generated insights across models, templates and analysis tools.">
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw class="mr-2 h-4 w-4" />Refresh
			</Button>
			<!-- Export dropdown -->
			<details class="relative">
				<summary class="list-none">
					<Button variant="outline" size="sm" tag="span">
						<Download class="mr-2 h-4 w-4" />Export
					</Button>
				</summary>
				<div class="absolute right-0 z-50 mt-1 w-40 rounded-md border bg-popover p-1 shadow-md">
					<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('reports.csv')}>Reports CSV</button>
					<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('reports.json')}>Reports JSON</button>
				</div>
			</details>
			<Button size="sm" onclick={() => goto('/reports/create')}>
				<Plus class="mr-2 h-4 w-4" />New report
			</Button>
		{/snippet}
	</PageHeader>

	<FilterBar
		searchPlaceholder="Search reports by title…"
		bind:searchValue={searchQuery}
		onSearchInput={onSearchInput}
		activeTags={activeFilters}
		resultsText={resultsText}
		onClearAll={() => { typeFilter = 'all'; statusFilter = 'all'; searchQuery = ''; sortBy = 'newest'; }}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Type</span>
				<button class="fb-chip {typeFilter === 'all' ? 'fb-chip-on' : ''}" aria-pressed={typeFilter === 'all'} onclick={() => { typeFilter = 'all'; }}>All</button>
				{#each Object.entries(typeConfig) as [k, v] (k)}
					<button class="fb-chip {typeFilter === k ? 'fb-chip-on' : ''}" aria-pressed={typeFilter === k} onclick={() => { typeFilter = k as ReportType; }}>{v.label}</button>
				{/each}
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Status</span>
				<button class="fb-chip {statusFilter === 'all' ? 'fb-chip-on' : ''}" aria-pressed={statusFilter === 'all'} onclick={() => { statusFilter = 'all'; }}>All</button>
				{#each ['pending', 'generating', 'completed', 'failed'] as const as st (st)}
					<button class="fb-chip capitalize {statusFilter === st ? 'fb-chip-on' : ''}" aria-pressed={statusFilter === st} onclick={() => { statusFilter = st; }}>{st}</button>
				{/each}
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Sort</span>
				<button class="fb-chip {sortBy === 'newest' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'newest'} onclick={() => { sortBy = 'newest'; }}>Newest</button>
				<button class="fb-chip {sortBy === 'oldest' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'oldest'} onclick={() => { sortBy = 'oldest'; }}>Oldest</button>
				<button class="fb-chip {sortBy === 'title' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'title'} onclick={() => { sortBy = 'title'; }}>A–Z</button>
			</div>
		{/snippet}
	</FilterBar>

	{#if loading}
		<LoadingState label="loading reports" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if reports.length === 0}
		<EmptyState
			icon={GitCompareArrows}
			title="No reports yet"
			description="Generate your first report to get insights across models, templates and tools."
		>
			{#snippet action()}
				<Button size="sm" onclick={() => goto('/reports/create')}>
					<Plus class="mr-2 h-4 w-4" />Generate your first report
				</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{#each reports as r (r.report_id)}
				{@const cfg = typeConfig[r.report_type]}
				{@const TypeIcon = cfg?.icon ?? Brain}
				<Card.Root class="flex flex-col">
					<Card.Header class="pb-3">
						<div class="flex items-start justify-between gap-2">
							<Badge variant="outline">
								<TypeIcon class="mr-1 h-3 w-3" />
								{cfg?.label ?? r.report_type}
							</Badge>
							<StatusBadge status={r.status} />
						</div>
						<Card.Title class="text-base mt-2 line-clamp-2">{r.title}</Card.Title>
						{#if r.description}
							<Card.Description class="line-clamp-2">{r.description}</Card.Description>
						{/if}
					</Card.Header>
					<Card.Content class="flex-1 text-xs text-muted-foreground space-y-1">
						<div>Created: {formatDate(r.created_at)}</div>
						{#if r.status === 'generating'}
							<div class="space-y-1">
								<div class="flex justify-between text-xs">
									<span>Generating…</span>
									<span class="font-mono">{r.progress_percent}%</span>
								</div>
								<div class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
									<div
										class="h-full rounded-full bg-primary transition-[width] duration-300"
										style="width: {r.progress_percent}%"
									></div>
								</div>
							</div>
						{/if}
						{#if r.status === 'failed' && r.error_message}
							<div class="text-destructive line-clamp-2">{r.error_message}</div>
						{/if}
					</Card.Content>
					<Card.Footer class="gap-2 pt-3">
						<Button size="sm" variant="outline" class="flex-1" onclick={() => goto(`/reports/${r.report_id}`)}>
							<Eye class="mr-1 h-3 w-3" />View
						</Button>
						<Button size="sm" variant="outline" aria-label="Delete report {r.title}" onclick={() => remove(r.report_id)}>
							<Trash2 class="h-3 w-3" />
						</Button>
					</Card.Footer>
				</Card.Root>
			{/each}
		</div>
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete report?"
	description="This permanently removes the report. This cannot be undone."
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={confirmDelete}
/>
