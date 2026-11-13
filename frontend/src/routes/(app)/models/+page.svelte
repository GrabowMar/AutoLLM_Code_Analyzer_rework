<script lang="ts">
import { Button } from '$lib/components/ui/button';
import {
	getModelsExportUrl,
	getModels,
	getModelsStats,
	getModelFacets,
	type ModelFacets,
	syncModelsFromOpenRouter,
	type PaginatedModels,
	type ModelsStats,
} from '$lib/api/client';
import { formatApiError } from '$lib/api/core';
import { onMount } from 'svelte';
import { toast } from 'svelte-sonner';
import Upload from '@lucide/svelte/icons/upload';
import Trophy from '@lucide/svelte/icons/trophy';
import FileSpreadsheet from '@lucide/svelte/icons/file-spreadsheet';
import Download from '@lucide/svelte/icons/download';
import ChevronDown from '@lucide/svelte/icons/chevron-down';
import CloudDownload from '@lucide/svelte/icons/cloud-download';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import FileJson from '@lucide/svelte/icons/file-json';
import FileText from '@lucide/svelte/icons/file-text';
import { DropdownMenu } from 'bits-ui';
import { getModelCovariatesCsvUrl } from '$lib/api/model-covariates';
import ModelsFiltersBar from '$lib/components/models/ModelsFiltersBar.svelte';
import {
	EMPTY_FILTERS,
	countActive,
	filtersFromUrl,
	filtersToQuery,
	filtersToUrl,
	type ModelFilters,
} from '$lib/components/models/filters';
import { page } from '$app/state';
import { replaceState } from '$app/navigation';
import ModelsTable from '$lib/components/models/ModelsTable.svelte';
import PaginationBar from '$lib/components/PaginationBar.svelte';
import PageHeader from '$lib/components/PageHeader.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';

// Filters live in the URL (?provider=…&capability=seed,reasoning…), so a filtered view is a shareable link.
let filters = $state<ModelFilters>(filtersFromUrl(page.url.searchParams));
let currentPage = $state(1);
let perPage = $state(50);
let sortBy = $state(page.url.searchParams.get('sort') ?? '');
let sortDir = $state<'asc' | 'desc'>(page.url.searchParams.get('dir') === 'desc' ? 'desc' : 'asc');

let data = $state<PaginatedModels | null>(null);
let stats = $state<ModelsStats | null>(null);
let facets = $state<ModelFacets | null>(null);
let loading = $state(true);
let syncing = $state(false);
let error = $state('');
let selectedModelSlugs = $state<Set<string>>(new Set());

let debounceTimer: ReturnType<typeof setTimeout>;
const visibleModelSlugs = $derived(data?.items.map((item) => item.canonical_slug) ?? []);
const allVisibleSelected = $derived(
	visibleModelSlugs.length > 0 && visibleModelSlugs.every((slug) => selectedModelSlugs.has(slug)),
);

/** Active filters (search excluded): drives the empty state's wording. */
const activeCount = $derived(countActive(filters));

function syncUrl() {
	const params = filtersToUrl(filters, page.url.searchParams);
	if (sortBy) params.set('sort', sortBy);
	else params.delete('sort');
	if (sortBy && sortDir === 'desc') params.set('dir', 'desc');
	else params.delete('dir');
	const qs = params.toString();
	const next = `${page.url.pathname}${qs ? `?${qs}` : ''}`;
	if (next !== `${page.url.pathname}${page.url.search}`) replaceState(next, page.state);
}

let loadSeq = 0;
async function load() {
	const seq = ++loadSeq;
	loading = true;
	error = '';
	syncUrl();
	try {
		const result = await getModels({
			page: currentPage,
			per_page: perPage,
			sort_by: sortBy,
			sort_dir: sortDir,
			...filtersToQuery(filters),
		});
		// Rapid filter clicks: only the newest request may land.
		if (seq === loadSeq) data = result;
	} catch {
		if (seq === loadSeq) error = 'Failed to load models.';
	} finally {
		if (seq === loadSeq) loading = false;
	}
}

async function loadMeta() {
	const [s, f] = await Promise.all([getModelsStats(), getModelFacets()]);
	stats = s;
	facets = f;
}

function setSearch(value: string) {
	filters.search = value;
	clearTimeout(debounceTimer);
	currentPage = 1;
	debounceTimer = setTimeout(load, 300);
}

function setFilters(patch: Partial<ModelFilters>) {
	filters = { ...filters, ...patch };
	currentPage = 1;
	load();
}

/** Toolbar "Clear": drops the filters, keeps the search text and the sort. */
function clearFilters() {
	setFilters({ ...EMPTY_FILTERS, search: filters.search });
}

/** Empty state "Clear all filters": search included. */
function clearAllFilters() {
	setFilters({ ...EMPTY_FILTERS });
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

/** One covariate row per model used in your jobs (Epoch AI, LMArena, Hugging Face, models.dev). */
function downloadCovariates() {
	window.location.href = getModelCovariatesCsvUrl(true);
}

const menuItemClass =
	'flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors duration-150 data-[highlighted]:bg-surface-2 [&>svg]:size-3.5 [&>svg]:shrink-0 [&>svg]:text-muted-foreground';

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

<div class="space-y-4">
	<PageHeader title="Models" description="Browse and manage AI models available for research.">
		{#snippet actions()}
			<Button variant="outline" size="sm" href="/rankings">
				<Trophy class="h-3.5 w-3.5 sm:mr-2" />
				<span class="hidden sm:inline">Rankings</span>
			</Button>
			<DropdownMenu.Root>
				<DropdownMenu.Trigger>
					{#snippet child({ props })}
						<Button {...props} variant="outline" size="sm" aria-label="Download">
							<Download class="h-3.5 w-3.5 sm:mr-2" />
							<span class="hidden sm:inline">Download</span>
							<ChevronDown class="ml-1 hidden h-3 w-3 opacity-60 sm:inline" />
						</Button>
					{/snippet}
				</DropdownMenu.Trigger>
				<DropdownMenu.Portal>
					<DropdownMenu.Content
						align="end"
						sideOffset={6}
						collisionPadding={12}
						class="z-50 min-w-56 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
					>
						<DropdownMenu.Item class={menuItemClass} onSelect={() => downloadExport('csv')}>
							<FileText />Catalog as CSV
						</DropdownMenu.Item>
						<DropdownMenu.Item class={menuItemClass} onSelect={() => downloadExport('json')}>
							<FileJson />Catalog as JSON
						</DropdownMenu.Item>
						<DropdownMenu.Separator class="-mx-1 my-1 h-px bg-border" />
						<DropdownMenu.Item class={menuItemClass} onSelect={downloadCovariates}>
							<FileSpreadsheet />
							<span class="flex flex-col">
								Research covariates CSV
								<span class="text-[11px] text-muted-foreground">Models used in your jobs</span>
							</span>
						</DropdownMenu.Item>
					</DropdownMenu.Content>
				</DropdownMenu.Portal>
			</DropdownMenu.Root>
			<Button variant="outline" size="sm" onclick={handleSync} disabled={syncing} title="Refresh the catalog from OpenRouter">
				{#if syncing}<LoaderCircle class="h-3.5 w-3.5 animate-spin sm:mr-2" />{:else}<CloudDownload class="h-3.5 w-3.5 sm:mr-2" />{/if}
				<span class="hidden sm:inline">{syncing ? 'Syncing…' : 'Sync'}</span>
			</Button>
			<Button size="sm" href="/models/import">
				<Upload class="h-3.5 w-3.5 sm:mr-2" />
				<span class="hidden sm:inline">Import</span>
			</Button>
		{/snippet}
		{#if stats}
			<div class="header-stats">
				<span class="header-stat"><b>{stats.total}</b> models</span>
				<span class="header-stat"><b>{stats.free}</b> free</span>
				<span class="header-stat"><b>{stats.providers}</b> providers</span>
				{#if stats.avg_input_price > 0}
					<span class="header-stat"><b>${stats.avg_input_price.toFixed(2)}</b> avg /1M input</span>
				{/if}
			</div>
		{/if}
	</PageHeader>

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{/if}

	<ModelsTable
		{data}
		{loading}
		bind:sortBy
		{sortDir}
		{selectedModelSlugs}
		{visibleModelSlugs}
		{allVisibleSelected}
		hasActiveFilters={activeCount > 0 || !!filters.search}
		onToggleSort={toggleSort}
		onToggleModelSelection={toggleModelSelection}
		onToggleVisibleSelection={toggleVisibleModelSelection}
		onChangeMobileSort={changeMobileSort}
		onToggleSortDir={toggleSortDir}
		onClearAllFilters={clearAllFilters}
		onClearSelection={clearSelection}
		onCompare={openComparison}
	>
		{#snippet toolbar()}
			<ModelsFiltersBar {filters} {facets} onSearch={setSearch} onChange={setFilters} onClear={clearFilters} />
		{/snippet}
		{#snippet footer()}
			<PaginationBar
				resultsText={data && !loading
					? `Showing ${(data.page - 1) * data.per_page + 1}–${Math.min(data.page * data.per_page, data.total)} of ${data.total} models`
					: ''}
				page={data?.page}
				pages={data?.pages}
				onGoToPage={goToPage}
				class="border-t border-border"
				scrollToCard
			/>
		{/snippet}
	</ModelsTable>
</div>
