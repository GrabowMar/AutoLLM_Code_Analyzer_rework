<script lang="ts">
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import { Input } from '$lib/components/ui/input';
import { Separator } from '$lib/components/ui/separator';
import Search from '@lucide/svelte/icons/search';
import CloudDownload from '@lucide/svelte/icons/cloud-download';
import Download from '@lucide/svelte/icons/download';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import X from '@lucide/svelte/icons/x';
import Eye from '@lucide/svelte/icons/eye';
import Wrench from '@lucide/svelte/icons/wrench';
import Radio from '@lucide/svelte/icons/radio';
import Braces from '@lucide/svelte/icons/braces';
import Gift from '@lucide/svelte/icons/gift';
import Sparkles from '@lucide/svelte/icons/sparkles';
import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';

interface ActiveFilterTag {
	key: string;
	label: string;
	clear: () => void;
}

interface Props {
	searchQuery: string;
	selectedProvider: string;
	filterCapability: string;
	filterPriceRange: string;
	filterContextRange: string;
	sortBy: string;
	sortDir: 'asc' | 'desc';
	providers: string[];
	syncing: boolean;
	activeFilters: ActiveFilterTag[];
	onSearchInput: () => void;
	onProviderChange: () => void;
	onApplyQuickFilter: (type: string) => void;
	onToggleCapability: (value: string) => void;
	onTogglePriceRange: (value: string) => void;
	onToggleContextRange: (value: string) => void;
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
	onClearAllFilters,
	onSync,
	onExport,
}: Props = $props();

let showAdvancedFilters = $state(false);

const capabilityOptions = [
	{ value: 'vision', label: 'Vision', icon: Eye },
	{ value: 'function_calling', label: 'Functions', icon: Wrench },
	{ value: 'streaming', label: 'Streaming', icon: Radio },
	{ value: 'json_mode', label: 'JSON Mode', icon: Braces },
] as const;

const priceRangeOptions = [
	{ value: 'free', label: 'Free' },
	{ value: 'low', label: '<$1/1M' },
	{ value: 'medium', label: '$1–$10/1M' },
	{ value: 'high', label: '>$10/1M' },
] as const;

const contextRangeOptions = [
	{ value: 'small', label: '<8K' },
	{ value: 'medium', label: '8K–32K' },
	{ value: 'large', label: '32K–128K' },
	{ value: 'xlarge', label: '>128K' },
] as const;
</script>

<!-- Quick Filter Presets -->
<div class="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-hide">
	<span class="text-xs text-muted-foreground mr-1 shrink-0">Quick:</span>
	<button
		class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {filterPriceRange === 'free' ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-700 dark:text-emerald-400' : 'border-input'}"
		onclick={() => onApplyQuickFilter('free')}
	>
		<Gift class="h-3 w-3" /> Free Models
	</button>
	<button
		class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {filterCapability === 'vision' ? 'bg-violet-500/10 border-violet-500/40 text-violet-700 dark:text-violet-400' : 'border-input'}"
		onclick={() => onApplyQuickFilter('vision')}
	>
		<Eye class="h-3 w-3" /> Vision
	</button>
	<button
		class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {filterCapability === 'function_calling' ? 'bg-orange-500/10 border-orange-500/40 text-orange-700 dark:text-orange-400' : 'border-input'}"
		onclick={() => onApplyQuickFilter('functions')}
	>
		<Wrench class="h-3 w-3" /> Functions
	</button>
	<button
		class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {filterContextRange === 'xlarge' ? 'bg-blue-500/10 border-blue-500/40 text-blue-700 dark:text-blue-400' : 'border-input'}"
		onclick={() => onApplyQuickFilter('large-context')}
	>
		128K+ Context
	</button>
	<button
		class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-muted shrink-0 whitespace-nowrap {sortBy === 'cost_efficiency' && sortDir === 'desc' ? 'bg-amber-500/10 border-amber-500/40 text-amber-700 dark:text-amber-400' : 'border-input'}"
		onclick={() => onApplyQuickFilter('efficient')}
	>
		<Sparkles class="h-3 w-3" /> Most Efficient
	</button>
</div>

<!-- Search + Provider + Actions Row -->
<div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3">
	<div class="relative w-full sm:flex-1 sm:max-w-sm">
		<Search class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
		<Input
			placeholder="Search models..."
			class="pl-9"
			bind:value={searchQuery}
			oninput={onSearchInput}
		/>
	</div>
	<select
		class="h-9 w-full sm:w-auto rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring"
		bind:value={selectedProvider}
		onchange={onProviderChange}
	>
		<option value="">All Providers</option>
		{#each providers as p}
			<option value={p}>{p}</option>
		{/each}
	</select>
	<div class="flex items-center gap-2 sm:ml-auto">
		<Button variant="outline" size="sm" class="sm:hidden" onclick={() => (showAdvancedFilters = !showAdvancedFilters)}>
			<SlidersHorizontal class="h-3.5 w-3.5" />
		</Button>
		<Button variant="outline" size="sm" onclick={onSync} disabled={syncing}>
			{#if syncing}
				<LoaderCircle class="h-3.5 w-3.5 animate-spin sm:mr-2" />
				<span class="hidden sm:inline">Syncing…</span>
			{:else}
				<CloudDownload class="h-3.5 w-3.5 sm:mr-2" />
				<span class="hidden sm:inline">Sync from OpenRouter</span>
			{/if}
		</Button>
		<Button variant="outline" size="sm" class="hidden sm:inline-flex" onclick={() => onExport('csv')}>
			<Download class="mr-2 h-3.5 w-3.5" />
			Export
		</Button>
	</div>
</div>

<!-- Advanced Filters Row -->
<div class="{showAdvancedFilters ? 'flex' : 'hidden'} sm:flex flex-wrap items-center gap-x-6 gap-y-2">
	<div class="flex items-center gap-2">
		<span class="text-xs font-semibold uppercase text-muted-foreground">Cap:</span>
		{#each capabilityOptions as opt}
			<button
				class="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium transition-colors {filterCapability === opt.value ? 'bg-primary text-primary-foreground border-primary' : 'border-input hover:bg-muted'}"
				onclick={() => onToggleCapability(opt.value)}
			>
				<opt.icon class="h-3 w-3" />
				{opt.label}
			</button>
		{/each}
	</div>
	<Separator orientation="vertical" class="h-5 hidden sm:block" />
	<div class="flex items-center gap-2">
		<span class="text-xs font-semibold uppercase text-muted-foreground">Price:</span>
		{#each priceRangeOptions as opt}
			<button
				class="rounded-md border px-2 py-0.5 text-xs font-medium transition-colors {filterPriceRange === opt.value ? 'bg-primary text-primary-foreground border-primary' : 'border-input hover:bg-muted'}"
				onclick={() => onTogglePriceRange(opt.value)}
			>
				{opt.label}
			</button>
		{/each}
	</div>
	<Separator orientation="vertical" class="h-5 hidden sm:block" />
	<div class="flex items-center gap-2">
		<span class="text-xs font-semibold uppercase text-muted-foreground">Context:</span>
		{#each contextRangeOptions as opt}
			<button
				class="rounded-md border px-2 py-0.5 text-xs font-medium transition-colors {filterContextRange === opt.value ? 'bg-primary text-primary-foreground border-primary' : 'border-input hover:bg-muted'}"
				onclick={() => onToggleContextRange(opt.value)}
			>
				{opt.label}
			</button>
		{/each}
	</div>
</div>

<!-- Active Filter Tags -->
{#if activeFilters.length > 0}
	<div class="flex flex-wrap items-center gap-1.5">
		<span class="text-xs text-muted-foreground">Active:</span>
		{#each activeFilters as tag (tag.key)}
			<Badge variant="secondary" class="gap-1 pr-1">
				{tag.label}
				<button class="ml-0.5 rounded-full hover:bg-muted-foreground/20 p-0.5" onclick={tag.clear}>
					<X class="h-2.5 w-2.5" />
				</button>
			</Badge>
		{/each}
		<button class="text-xs text-muted-foreground hover:text-foreground underline ml-1" onclick={onClearAllFilters}>Clear all</button>
	</div>
{/if}
