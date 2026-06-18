<script lang="ts">
	import type { Snippet } from 'svelte';
	import Search from '@lucide/svelte/icons/search';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import X from '@lucide/svelte/icons/x';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';

	export interface FilterTag {
		key: string;
		label: string;
		/** optional extra tailwind classes for the tag bg/border/text */
		color?: string;
		onRemove: () => void;
	}

	interface Props {
		/** Placeholder text for the search input */
		searchPlaceholder?: string;
		/** Two-way bindable search value */
		searchValue?: string;
		/** Called whenever the search value changes (debounce in parent if needed) */
		onSearchInput?: (value: string) => void;
		/** Active filter tags to show below filter rows */
		activeTags?: FilterTag[];
		/** Summary text shown in the results footer, e.g. "12 of 45 models" */
		resultsText?: string;
		/** Called when user clicks "Clear all" */
		onClearAll?: () => void;
		/** Snippet for filter group rows rendered inside the collapsible area */
		filters?: Snippet;
		/** Snippet for extra action buttons rendered right of the search (Export, Sync…) */
		actions?: Snippet;
		/** Additional classes on the outer wrapper */
		class?: string;
	}

	let {
		searchPlaceholder = 'Search…',
		searchValue = $bindable(''),
		onSearchInput,
		activeTags = [],
		resultsText = '',
		onClearAll,
		filters,
		actions,
		class: cls = '',
	}: Props = $props();

	// On desktop, filters are expanded by default. On mobile, collapsed until toggled.
	let filtersExpanded = $state(true);

	function handleInput(e: Event) {
		const v = (e.target as HTMLInputElement).value;
		searchValue = v;
		onSearchInput?.(v);
	}

	function clearSearch() {
		searchValue = '';
		onSearchInput?.('');
	}

	const activeCount = $derived(activeTags.length);
</script>

<!-- '/' shortcut focuses the search input globally -->
<svelte:window
	onkeydown={(e) => {
		if (
			e.key === '/' &&
			!e.metaKey &&
			!e.ctrlKey &&
			!['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement).tagName)
		) {
			e.preventDefault();
			document.querySelector<HTMLInputElement>('.fb-search-input')?.focus();
		}
	}}
/>

<div class="fb-shell {cls}" role="search" aria-label="Filter and search">
	<!-- ── Row 1: Search + filter toggle + actions ─────────────── -->
	<div class="flex items-center gap-2 p-2.5">
		<!-- Search -->
		<div class="relative min-w-0 flex-1">
			<Search
				class="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/50"
				aria-hidden="true"
			/>
			<input
				type="search"
				class="fb-search-input h-9 w-full rounded-lg border border-input/60 bg-muted/30 pl-9 pr-8 text-sm placeholder:text-muted-foreground/50 transition-colors hover:border-input focus-visible:border-ring focus-visible:bg-background focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none"
				placeholder={searchPlaceholder}
				value={searchValue}
				oninput={handleInput}
				autocomplete="off"
				spellcheck="false"
			/>
			{#if searchValue}
				<button
					class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground/50 transition-colors hover:text-foreground"
					onclick={clearSearch}
					aria-label="Clear search"
					tabindex="-1"
				>
					<X class="h-3 w-3" />
				</button>
			{:else}
				<kbd
					class="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 select-none items-center rounded border border-border/50 bg-muted px-1 font-mono text-[9px] text-muted-foreground/40 sm:inline-flex"
					aria-hidden="true">/</kbd
				>
			{/if}
		</div>

		<!-- Filter toggle button -->
		{#if filters}
			<button
				class="relative inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg border px-3 text-xs font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:px-2.5 {filtersExpanded
					? 'border-border/80 bg-muted text-foreground'
					: 'border-border/50 bg-muted/30 text-muted-foreground hover:border-border hover:bg-muted hover:text-foreground'}"
				onclick={() => (filtersExpanded = !filtersExpanded)}
				aria-expanded={filtersExpanded}
				aria-label="Toggle filters"
			>
				<SlidersHorizontal class="h-3.5 w-3.5 shrink-0" />
				<span class="hidden sm:inline">Filters</span>
				{#if activeCount > 0}
					<span
						class="flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold leading-none text-primary-foreground"
					>
						{activeCount}
					</span>
				{/if}
				<ChevronDown
					class="h-3 w-3 shrink-0 transition-transform duration-200 {filtersExpanded
						? 'rotate-180'
						: ''}"
				/>
			</button>
		{/if}

		<!-- Extra action buttons -->
		{#if actions}
			<div class="flex shrink-0 items-center gap-2">
				{@render actions()}
			</div>
		{/if}
	</div>

	<!-- ── Row 2: Filter groups (collapsible) ─────────────────── -->
	{#if filters && filtersExpanded}
		<div class="space-y-2.5 border-t border-border/40 px-3 py-3">
			{@render filters()}
		</div>
	{/if}

	<!-- ── Row 3: Active filter tags ──────────────────────────── -->
	{#if activeTags.length > 0}
		<div class="flex flex-wrap items-center gap-1.5 border-t border-border/40 px-3 py-2">
			<span class="fb-group-label w-auto">Active</span>
			{#each activeTags as tag (tag.key)}
				<span
					class="inline-flex h-5 items-center gap-1 rounded-sm border pl-1.5 pr-0.5 text-[11px] {tag.color ??
						'border-border/50 bg-secondary text-secondary-foreground'}"
				>
					{tag.label}
					<button
						class="cursor-pointer rounded-sm p-0.5 opacity-50 transition-opacity hover:opacity-100"
						onclick={tag.onRemove}
						aria-label="Remove filter: {tag.label}"
					>
						<X class="h-2.5 w-2.5" />
					</button>
				</span>
			{/each}
			{#if onClearAll}
				<button
					class="ml-0.5 cursor-pointer text-[10px] text-muted-foreground/60 underline underline-offset-2 transition-colors hover:text-muted-foreground"
					onclick={onClearAll}
				>
					Clear all
				</button>
			{/if}
		</div>
	{/if}

	<!-- ── Row 4: Results bar ──────────────────────────────────── -->
	{#if resultsText}
		<div class="border-t border-border/40 bg-muted/20 px-3 py-1.5 text-[11px] text-muted-foreground">
			{resultsText}
		</div>
	{/if}
</div>
