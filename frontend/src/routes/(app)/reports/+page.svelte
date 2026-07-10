<script lang="ts">
	import { formatDateTime as formatDate } from '$lib/utils/formatters';
	import { reportStatusColors as statusColors } from '$lib/constants/colors';
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
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Search from '@lucide/svelte/icons/search';
	import ArrowDownUp from '@lucide/svelte/icons/arrow-down-up';
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

	const typeConfig: Record<ReportType, { label: string; color: string; icon: typeof Brain }> = {
		model_analysis: { label: 'Model Analysis', color: 'bg-blue-500/15 text-blue-400 border-blue-500/30', icon: Brain },
		template_comparison: { label: 'Template Comparison', color: 'bg-purple-500/15 text-purple-400 border-purple-500/30', icon: GitCompareArrows },
		tool_analysis: { label: 'Tool Analysis', color: 'bg-teal-500/15 text-teal-400 border-teal-500/30', icon: Wrench },
		generation_analytics: { label: 'Generation Analytics', color: 'bg-orange-500/15 text-orange-400 border-orange-500/30', icon: TrendingUp },
		comprehensive: { label: 'Comprehensive', color: 'bg-red-500/15 text-red-400 border-red-500/30', icon: Layers },
		experiment_report: { label: 'Experiment Report', color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', icon: FlaskConical }
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

	async function remove(id: string) {
		if (!confirm('Delete this report?')) return;
		await deleteReport(id);
		await load();
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

<svelte:head>
	<title>Reports — LLM Eval Lab</title>
</svelte:head>

<div class="space-y-6">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header min-w-0">
			<h1>Reports</h1>
			<p>Generated insights across models, templates and analysis tools</p>
		</div>
		<div class="flex flex-wrap gap-2">
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
		</div>
	</div>

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
				<button class="fb-chip {typeFilter === 'all' ? 'fb-chip-on' : ''}" onclick={() => { typeFilter = 'all'; }}>All</button>
				{#each Object.entries(typeConfig) as [k, v] (k)}
					<button class="fb-chip {typeFilter === k ? 'fb-chip-indigo' : ''}" onclick={() => { typeFilter = k as ReportType; }}>{v.label}</button>
				{/each}
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Status</span>
				<button class="fb-chip {statusFilter === 'all' ? 'fb-chip-on' : ''}" onclick={() => { statusFilter = 'all'; }}>All</button>
				<button class="fb-chip {statusFilter === 'pending' ? 'fb-chip-slate' : ''}" onclick={() => { statusFilter = 'pending'; }}>Pending</button>
				<button class="fb-chip {statusFilter === 'generating' ? 'fb-chip-amber' : ''}" onclick={() => { statusFilter = 'generating'; }}>Generating</button>
				<button class="fb-chip {statusFilter === 'completed' ? 'fb-chip-emerald' : ''}" onclick={() => { statusFilter = 'completed'; }}>Completed</button>
				<button class="fb-chip {statusFilter === 'failed' ? 'fb-chip-red' : ''}" onclick={() => { statusFilter = 'failed'; }}>Failed</button>
				<div class="fb-group ml-2">
					<span class="fb-group-label">Sort</span>
					<button class="fb-chip {sortBy === 'newest' ? 'fb-chip-on' : ''}" onclick={() => { sortBy = 'newest'; }}>Newest</button>
					<button class="fb-chip {sortBy === 'oldest' ? 'fb-chip-on' : ''}" onclick={() => { sortBy = 'oldest'; }}>Oldest</button>
					<button class="fb-chip {sortBy === 'title' ? 'fb-chip-on' : ''}" onclick={() => { sortBy = 'title'; }}>A–Z</button>
				</div>
			</div>
		{/snippet}
	</FilterBar>

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<Card.Root><Card.Content class="p-6 text-destructive text-sm" style="font-family: var(--font-mono);"><span class="font-semibold">error:</span> {error}</Card.Content></Card.Root>
	{:else if reports.length === 0}
		<Card.Root>
			<Card.Content class="py-16 text-center">
				<GitCompareArrows class="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
				<h3 class="text-lg font-medium mb-1">No reports yet</h3>
				<p class="text-sm text-muted-foreground mb-4">Generate your first report to get insights across models, templates and tools.</p>
				<Button size="sm" onclick={() => goto('/reports/create')}>
					<Plus class="mr-2 h-4 w-4" />Generate your first report
				</Button>
			</Card.Content>
		</Card.Root>
	{:else}
		<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{#each reports as r (r.report_id)}
				{@const cfg = typeConfig[r.report_type]}
				{@const TypeIcon = cfg?.icon ?? Brain}
				<Card.Root class="flex flex-col">
					<Card.Header class="pb-3">
						<div class="flex items-start justify-between gap-2">
							<Badge class={cfg?.color ?? ''}>
								<TypeIcon class="mr-1 h-3 w-3" />
								{cfg?.label ?? r.report_type}
							</Badge>
							<Badge class={statusColors[r.status] ?? ''}>{r.status}</Badge>
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
										class="h-full rounded-full bg-amber-500 transition-all duration-500"
										style="width: {r.progress_percent}%"
									></div>
								</div>
							</div>
						{/if}
						{#if r.status === 'failed' && r.error_message}
							<div class="text-red-400 line-clamp-2">{r.error_message}</div>
						{/if}
					</Card.Content>
					<Card.Footer class="gap-2 pt-3">
						<Button size="sm" variant="outline" class="flex-1" onclick={() => goto(`/reports/${r.report_id}`)}>
							<Eye class="mr-1 h-3 w-3" />View
						</Button>
						<Button size="sm" variant="outline" onclick={() => remove(r.report_id)}>
							<Trash2 class="h-3 w-3" />
						</Button>
					</Card.Footer>
				</Card.Root>
			{/each}
		</div>
	{/if}
</div>
