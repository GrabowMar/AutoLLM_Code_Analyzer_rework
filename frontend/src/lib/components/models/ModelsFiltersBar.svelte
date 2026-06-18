<script lang="ts">
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import { Button } from '$lib/components/ui/button';
	import CloudDownload from '@lucide/svelte/icons/cloud-download';
	import Download from '@lucide/svelte/icons/download';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Eye from '@lucide/svelte/icons/eye';
	import Wrench from '@lucide/svelte/icons/wrench';
	import Radio from '@lucide/svelte/icons/radio';
	import Braces from '@lucide/svelte/icons/braces';
	import Gift from '@lucide/svelte/icons/gift';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import ArrowUpDown from '@lucide/svelte/icons/arrow-up-down';

	interface Props {
		searchQuery: string;
		selectedProvider: string;
		filterCapability: string;
		filterPriceRange: string;
		filterContextRange: string;
		filterUsedInApps: boolean;
		sortBy: string;
		sortDir: 'asc' | 'desc';
		providers: string[];
		syncing: boolean;
		activeFilters: FilterTag[];
		onSearchInput: () => void;
		onProviderChange: () => void;
		onApplyQuickFilter: (type: string) => void;
		onToggleCapability: (value: string) => void;
		onTogglePriceRange: (value: string) => void;
		onToggleContextRange: (value: string) => void;
		onToggleUsedInApps: () => void;
		onClearAllFilters: () => void;
		onSync: () => void;
		onExport: (format: 'csv' | 'json') => void;
	}

	let {
		searchQuery = $bindable(),
		selectedProvider = $bindable(),
		filterCapability,
		filterPriceRange,
		filterContextRange,
		filterUsedInApps,
		sortBy,
		sortDir,
		providers,
		syncing,
		activeFilters,
		onSearchInput,
		onProviderChange,
		onApplyQuickFilter,
		onToggleCapability,
		onTogglePriceRange,
		onToggleContextRange,
		onToggleUsedInApps,
		onClearAllFilters,
		onSync,
		onExport,
	}: Props = $props();

	const capabilityOptions = [
		{ value: 'vision', label: 'Vision', icon: Eye },
		{ value: 'function_calling', label: 'Functions', icon: Wrench },
		{ value: 'streaming', label: 'Streaming', icon: Radio },
		{ value: 'json_mode', label: 'JSON Mode', icon: Braces },
	] as const;

	const priceOptions = [
		{ value: 'free', label: 'Free' },
		{ value: 'low', label: '<$1/1M' },
		{ value: 'medium', label: '$1–$10/1M' },
		{ value: 'high', label: '>$10/1M' },
	] as const;

	const contextOptions = [
		{ value: 'small', label: '<8K' },
		{ value: 'medium', label: '8K–32K' },
		{ value: 'large', label: '32K–128K' },
		{ value: 'xlarge', label: '>128K' },
	] as const;

	const sortOptions = [
		{ value: 'name', dir: 'asc' as const, label: 'Name A–Z' },
		{ value: 'context_length', dir: 'desc' as const, label: 'Context ↓' },
		{ value: 'cost_efficiency', dir: 'desc' as const, label: 'Efficiency ↓' },
		{ value: 'created_at', dir: 'desc' as const, label: 'Newest' },
	] as const;
</script>

<FilterBar
	searchPlaceholder="Search models…"
	bind:searchValue={searchQuery}
	onSearchInput={onSearchInput}
	activeTags={activeFilters}
	onClearAll={onClearAllFilters}
>
	{#snippet filters()}
		<!-- Quick presets -->
		<div class="fb-group">
			<span class="fb-group-label">Quick</span>
			<button
				class="fb-chip {filterPriceRange === 'free' ? 'fb-chip-emerald' : ''}"
				onclick={() => onApplyQuickFilter('free')}
			>
				<Gift class="h-3 w-3" /> Free
			</button>
			<button
				class="fb-chip {filterCapability === 'vision' ? 'fb-chip-violet' : ''}"
				onclick={() => onApplyQuickFilter('vision')}
			>
				<Eye class="h-3 w-3" /> Vision
			</button>
			<button
				class="fb-chip {filterCapability === 'function_calling' ? 'fb-chip-orange' : ''}"
				onclick={() => onApplyQuickFilter('functions')}
			>
				<Wrench class="h-3 w-3" /> Functions
			</button>
			<button
				class="fb-chip {filterContextRange === 'xlarge' ? 'fb-chip-blue' : ''}"
				onclick={() => onApplyQuickFilter('large-context')}
			>
				128K+ Context
			</button>
			<button
				class="fb-chip {sortBy === 'cost_efficiency' && sortDir === 'desc' ? 'fb-chip-amber' : ''}"
				onclick={() => onApplyQuickFilter('efficient')}
			>
				<Sparkles class="h-3 w-3" /> Efficient
			</button>
			<button
				class="fb-chip {filterUsedInApps ? 'fb-chip-indigo' : ''}"
				onclick={onToggleUsedInApps}
			>
				<AppWindow class="h-3 w-3" /> Used in Apps
			</button>
		</div>

		<!-- Capability chips -->
		<div class="fb-group">
			<span class="fb-group-label">Cap.</span>
			{#each capabilityOptions as opt}
				<button
					class="fb-chip {filterCapability === opt.value ? 'fb-chip-on' : ''}"
					onclick={() => onToggleCapability(opt.value)}
					aria-pressed={filterCapability === opt.value}
				>
					<opt.icon class="h-3 w-3" />
					{opt.label}
				</button>
			{/each}
		</div>

		<!-- Price + Context + Provider in one row -->
		<div class="fb-group">
			<span class="fb-group-label">Price</span>
			{#each priceOptions as opt}
				<button
					class="fb-chip {filterPriceRange === opt.value ? 'fb-chip-on' : ''}"
					onclick={() => onTogglePriceRange(opt.value)}
					aria-pressed={filterPriceRange === opt.value}
				>{opt.label}</button>
			{/each}
		</div>

		<div class="fb-group">
			<span class="fb-group-label">Context</span>
			{#each contextOptions as opt}
				<button
					class="fb-chip {filterContextRange === opt.value ? 'fb-chip-on' : ''}"
					onclick={() => onToggleContextRange(opt.value)}
					aria-pressed={filterContextRange === opt.value}
				>{opt.label}</button>
			{/each}
		</div>

		<!-- Provider select + Sort chips -->
		<div class="fb-group">
			<span class="fb-group-label">Provider</span>
			<select
				class="fb-select"
				bind:value={selectedProvider}
				onchange={onProviderChange}
				aria-label="Filter by provider"
			>
				<option value="">All providers</option>
				{#each providers as p}
					<option value={p}>{p}</option>
				{/each}
			</select>

			<span class="fb-group-label ml-2">Sort</span>
			{#each sortOptions as opt}
				<button
					class="fb-chip {sortBy === opt.value && sortDir === opt.dir ? 'fb-chip-on' : ''}"
					onclick={() => onApplyQuickFilter(`sort-${opt.value}`)}
					aria-pressed={sortBy === opt.value && sortDir === opt.dir}
				>
					<ArrowUpDown class="h-3 w-3" />
					{opt.label}
				</button>
			{/each}
		</div>
	{/snippet}

	{#snippet actions()}
		<Button variant="outline" size="sm" onclick={onSync} disabled={syncing} class="h-9 shrink-0">
			{#if syncing}
				<LoaderCircle class="h-3.5 w-3.5 animate-spin sm:mr-1.5" />
				<span class="hidden sm:inline">Syncing…</span>
			{:else}
				<CloudDownload class="h-3.5 w-3.5 sm:mr-1.5" />
				<span class="hidden sm:inline">Sync</span>
			{/if}
		</Button>
		<Button variant="outline" size="sm" onclick={() => onExport('csv')} class="h-9 shrink-0 hidden sm:inline-flex">
			<Download class="h-3.5 w-3.5 sm:mr-1.5" />
			<span class="hidden sm:inline">Export</span>
		</Button>
	{/snippet}
</FilterBar>
