<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import {
	getModelsExportUrl,
	getModels,
	getModelsStats,
	getProviders,
	syncModelsFromOpenRouter,
	type PaginatedModels,
	type ModelsStats,
} from '$lib/api/client';
import { formatApiError } from '$lib/api/core';
import { onMount } from 'svelte';
import { toast } from 'svelte-sonner';
import Cpu from '@lucide/svelte/icons/cpu';
import Upload from '@lucide/svelte/icons/upload';
import Trophy from '@lucide/svelte/icons/trophy';
import Gift from '@lucide/svelte/icons/gift';
import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
import ModelsFiltersBar from '$lib/components/models/ModelsFiltersBar.svelte';
import ModelsTable from '$lib/components/models/ModelsTable.svelte';
import ModelsPagination from '$lib/components/models/ModelsPagination.svelte';

let searchQuery = $state('');
let selectedProvider = $state('');
let currentPage = $state(1);
let perPage = $state(50);
let sortBy = $state('');
let sortDir = $state<'asc' | 'desc'>('asc');

let filterCapability = $state('');
let filterPriceRange = $state('');
let filterContextRange = $state('');
let filterFreeOnly = $state(false);
let filterUsedInApps = $state(false);

let data = $state<PaginatedModels | null>(null);
let stats = $state<ModelsStats | null>(null);
let providers = $state<string[]>([]);
let loading = $state(true);
let syncing = $state(false);
let error = $state('');
let selectedModelSlugs = $state<Set<string>>(new Set());

let debounceTimer: ReturnType<typeof setTimeout>;
const visibleModelSlugs = $derived(data?.items.map((item) => item.canonical_slug) ?? []);
const allVisibleSelected = $derived(
	visibleModelSlugs.length > 0 && visibleModelSlugs.every((slug) => selectedModelSlugs.has(slug)),
);

const activeFilters = $derived.by(() => {
	const tags: { key: string; label: string; clear: () => void }[] = [];
	if (filterCapability) {
		const capLabels: Record<string, string> = { vision: 'Vision', function_calling: 'Functions', streaming: 'Streaming', json_mode: 'JSON Mode' };
		tags.push({ key: 'cap', label: capLabels[filterCapability] ?? filterCapability, clear: () => { filterCapability = ''; applyFilterAndReload(); } });
	}
	if (filterPriceRange) {
		const priceLabels: Record<string, string> = { free: 'Free', low: '<$1/1M', medium: '$1–$10/1M', high: '>$10/1M' };
		tags.push({ key: 'price', label: priceLabels[filterPriceRange] ?? filterPriceRange, clear: () => { filterPriceRange = ''; applyFilterAndReload(); } });
	}
	if (filterContextRange) {
		const ctxLabels: Record<string, string> = { small: '<8K ctx', medium: '8K–32K ctx', large: '32K–128K ctx', xlarge: '>128K ctx' };
		tags.push({ key: 'ctx', label: ctxLabels[filterContextRange] ?? filterContextRange, clear: () => { filterContextRange = ''; applyFilterAndReload(); } });
	}
	if (filterFreeOnly) {
		tags.push({ key: 'free', label: 'Free only', clear: () => { filterFreeOnly = false; applyFilterAndReload(); } });
	}
	if (filterUsedInApps) {
		tags.push({ key: 'used_in_apps', label: 'Used in Apps', clear: () => { filterUsedInApps = false; applyFilterAndReload(); } });
	}
	if (selectedProvider) {
		tags.push({ key: 'provider', label: `Provider: ${selectedProvider}`, clear: () => { selectedProvider = ''; applyFilterAndReload(); } });
	}
	return tags;
});

async function load() {
	loading = true;
	error = '';
	try {
		data = await getModels({
			page: currentPage,
			per_page: perPage,
			search: searchQuery,
			provider: selectedProvider,
			capability: filterCapability,
			free_only: filterFreeOnly,
			sort_by: sortBy,
			sort_dir: sortDir,
			price_range: filterPriceRange,
			context_range: filterContextRange,
			used_in_apps: filterUsedInApps,
		});
	} catch {
		error = 'Failed to load models.';
	} finally {
		loading = false;
	}
}

async function loadMeta() {
	const [s, p] = await Promise.all([getModelsStats(), getProviders()]);
	stats = s;
	providers = p;
}

function debouncedLoad() {
	clearTimeout(debounceTimer);
	currentPage = 1;
	debounceTimer = setTimeout(load, 300);
}

function applyFilterAndReload() {
	currentPage = 1;
	load();
}

function resetFilterState() {
	filterCapability = '';
	filterPriceRange = '';
	filterContextRange = '';
	filterFreeOnly = false;
	filterUsedInApps = false;
	selectedProvider = '';
	searchQuery = '';
	sortBy = '';
	sortDir = 'asc';
	currentPage = 1;
}

function clearAllFilters() {
	resetFilterState();
	load();
}

function applyQuickFilter(type: string) {
	resetFilterState();
	if (type === 'free') { filterPriceRange = 'free'; }
	else if (type === 'vision') { filterCapability = 'vision'; }
	else if (type === 'large-context') { filterContextRange = 'xlarge'; }
	else if (type === 'efficient') { sortBy = 'cost_efficiency'; sortDir = 'desc'; }
	else if (type === 'functions') { filterCapability = 'function_calling'; }
	else if (type === 'used-in-apps') { filterUsedInApps = true; }
	load();
}

function toggleUsedInApps() {
	filterUsedInApps = !filterUsedInApps;
	applyFilterAndReload();
}

function toggleSort(field: string) {
	if (sortBy === field) {
		sortDir = sortDir === 'asc' ? 'desc' : 'asc';
	} else {
		sortBy = field;
		sortDir = 'asc';
	}
	currentPage = 1;
	load();
}

async function handleSync() {
	syncing = true;
	try {
		const result = await syncModelsFromOpenRouter();
		await Promise.all([load(), loadMeta()]);
		toast.success(`Synced ${result.upserted} of ${result.fetched} models from OpenRouter.`);
	} catch (caughtError) {
		toast.error(formatApiError(caughtError, 'Sync failed.'));
	} finally {
		syncing = false;
	}
}

function goToPage(p: number) {
	currentPage = p;
	load();
}

function toggleModelSelection(slug: string) {
	const next = new Set(selectedModelSlugs);
	if (next.has(slug)) next.delete(slug);
	else next.add(slug);
	selectedModelSlugs = next;
}

function toggleVisibleModelSelection() {
	const next = new Set(selectedModelSlugs);
	if (allVisibleSelected) {
		for (const slug of visibleModelSlugs) next.delete(slug);
	} else {
		for (const slug of visibleModelSlugs) next.add(slug);
	}
	selectedModelSlugs = next;
}

function clearSelection() {
	selectedModelSlugs = new Set();
}

function openComparison() {
	if (selectedModelSlugs.size === 0) return;
	const models = encodeURIComponent(Array.from(selectedModelSlugs).join(','));
	window.location.href = `/models/compare?models=${models}`;
}

function downloadExport(format: 'csv' | 'json' = 'csv') {
	window.location.href = getModelsExportUrl(format);
}

function toggleCapability(value: string) {
	filterCapability = filterCapability === value ? '' : value;
	applyFilterAndReload();
}

function togglePriceRange(value: string) {
	filterPriceRange = filterPriceRange === value ? '' : value;
	applyFilterAndReload();
}

function toggleContextRange(value: string) {
	filterContextRange = filterContextRange === value ? '' : value;
	applyFilterAndReload();
}

function changeMobileSort() {
	currentPage = 1;
	load();
}

function toggleSortDir() {
	sortDir = sortDir === 'asc' ? 'desc' : 'asc';
	currentPage = 1;
	load();
}

onMount(() => {
	load();
	loadMeta();
});
</script>

<svelte:head>
	<title>Models - LLM Lab</title>
</svelte:head>

<div class="space-y-3">
	<!-- Header -->
	<div class="flex items-center justify-between gap-2">
		<div class="page-header min-w-0">
			<h1>Models</h1>
			<p class="hidden sm:block">Browse and manage AI models available for research.</p>
		</div>
		<div class="flex flex-wrap items-center justify-end gap-2">
			<Button variant="outline" size="sm" onclick={openComparison} disabled={selectedModelSlugs.size === 0}>
				<GitCompareArrows class="h-3.5 w-3.5 sm:mr-2" />
				<span class="hidden sm:inline">Compare{selectedModelSlugs.size > 0 ? ` (${selectedModelSlugs.size})` : ''}</span>
			</Button>
			<Button variant="outline" size="sm" href="/rankings">
				<Trophy class="h-3.5 w-3.5 sm:mr-2" />
				<span class="hidden sm:inline">Rankings</span>
			</Button>
			<Button variant="outline" size="sm" href="/models/import">
				<Upload class="h-3.5 w-3.5 sm:mr-2" />
				<span class="hidden sm:inline">Import</span>
			</Button>
		</div>
	</div>

	<!-- Stats Bar -->
	{#if stats}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			<Badge variant="secondary" class="gap-1.5">
				<Cpu class="h-3 w-3" />
				{stats.total} models
			</Badge>
			<Badge variant="secondary" class="gap-1.5 text-emerald-600 dark:text-emerald-400">
				<Gift class="h-3 w-3" />
				{stats.free} free
			</Badge>
			<Badge variant="outline">{stats.providers} providers</Badge>
			{#if stats.avg_input_price > 0}
				<Badge variant="outline">Avg ${stats.avg_input_price.toFixed(2)}/1M input</Badge>
			{/if}
		</div>
	{/if}

	<ModelsFiltersBar
		bind:searchQuery
		bind:selectedProvider
		{filterCapability}
		{filterPriceRange}
		{filterContextRange}
		{filterUsedInApps}
		{sortBy}
		{sortDir}
		{providers}
		{syncing}
		{activeFilters}
		onSearchInput={debouncedLoad}
		onProviderChange={applyFilterAndReload}
		onApplyQuickFilter={applyQuickFilter}
		onToggleCapability={toggleCapability}
		onTogglePriceRange={togglePriceRange}
		onToggleContextRange={toggleContextRange}
		onToggleUsedInApps={toggleUsedInApps}
		onClearAllFilters={clearAllFilters}
		onSync={handleSync}
		onExport={downloadExport}
	/>

	<!-- Error -->
	{#if error}
		<div class="rounded-lg border border-destructive bg-destructive/10 p-4 text-sm text-destructive">
			{error}
		</div>
	{/if}

	<!-- Results Count -->
	{#if data && !loading}
		<div class="flex items-center justify-between">
			<p class="text-xs text-muted-foreground">
				Showing {(data.page - 1) * data.per_page + 1}–{Math.min(data.page * data.per_page, data.total)} of <strong>{data.total}</strong> models
			</p>
			<div class="flex items-center gap-2">
				{#if selectedModelSlugs.size > 0}
					<Button variant="ghost" size="sm" class="h-7 px-2 text-xs" onclick={clearSelection}>Clear selection</Button>
				{/if}
				<select
					class="h-7 rounded-md border border-input bg-background px-2 text-xs ring-offset-background focus:outline-none focus:ring-1 focus:ring-ring"
					bind:value={perPage}
					onchange={() => { currentPage = 1; load(); }}
				>
					<option value={25}>25 / page</option>
					<option value={50}>50 / page</option>
					<option value={100}>100 / page</option>
				</select>
			</div>
		</div>
	{/if}

	<ModelsTable
		{data}
		{loading}
		bind:sortBy
		{sortDir}
		{selectedModelSlugs}
		{visibleModelSlugs}
		{allVisibleSelected}
		hasActiveFilters={activeFilters.length > 0}
		onToggleSort={toggleSort}
		onToggleModelSelection={toggleModelSelection}
		onToggleVisibleSelection={toggleVisibleModelSelection}
		onChangeMobileSort={changeMobileSort}
		onToggleSortDir={toggleSortDir}
		onClearAllFilters={clearAllFilters}
	/>

	{#if data && data.pages > 1}
		<ModelsPagination page={data.page} pages={data.pages} onGoToPage={goToPage} />
	{/if}
</div>
