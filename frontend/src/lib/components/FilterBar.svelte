<script lang="ts">
	import type { Snippet } from 'svelte';
	import Search from '@lucide/svelte/icons/search';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import X from '@lucide/svelte/icons/x';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import PaginationBar from '$lib/components/PaginationBar.svelte';

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
		/** Current page number (1-based). When provided, pagination controls appear in results bar. */
		page?: number;
		/** Total number of pages */
		pages?: number;
		/** Called when user navigates to a page */
		onGoToPage?: (p: number) => void;
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
		page,
		pages,
		onGoToPage,
		filters,
		actions,
		class: cls = '',
	}: Props = $props();

	let filtersExpanded = $state(false);

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
	<div class="flex items-center gap-2 p-2">
		<!-- Search -->
		<div class="relative min-w-0 flex-1">
			<Search
				class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60"
				aria-hidden="true"
			/>
			<input
				type="search"
				class="fb-search-input h-9 w-full rounded-md border border-input bg-surface-1 pl-9 pr-8 text-sm shadow-xs placeholder:text-muted-foreground/70 transition-all hover:border-primary/40 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
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
					class="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 select-none items-center rounded border border-border bg-muted px-1 font-mono text-[9px] text-muted-foreground/50 sm:inline-flex"
					aria-hidden="true">/</kbd
				>
			{/if}
		</div>

		<!-- Filter toggle button -->
		{#if filters}
			<button
				class="relative inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-md border px-3 text-xs font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 sm:px-2.5 {filtersExpanded
					? 'border-border bg-muted text-foreground'
					: 'border-border bg-card text-muted-foreground hover:border-primary/40 hover:bg-accent hover:text-accent-foreground'}"
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
			<div class="flex shrink-0 items-center gap-1.5">
				{@render actions()}
			</div>
		{/if}
	</div>

	<!-- ── Row 2: Filter groups (collapsible) ─────────────────── -->
	{#if filters && filtersExpanded}
		<div class="space-y-2 border-t border-border px-3 py-2.5">
			{@render filters()}
		</div>
	{/if}

	<!-- ── Row 3: Active filter tags ──────────────────────────── -->
	{#if activeTags.length > 0}
		<div class="flex flex-wrap items-center gap-1.5 border-t border-border bg-muted/30 px-3 py-1.5">
			<span class="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wider">Active:</span>
			{#each activeTags as tag (tag.key)}
				<span
					class="fb-tag {tag.color ?? ''}"
				>
					{tag.label}
					<button
						class="cursor-pointer rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
						onclick={tag.onRemove}
						aria-label="Remove filter: {tag.label}"
					>
						<X class="h-2.5 w-2.5" />
					</button>
				</span>
			{/each}
			{#if onClearAll}
				<button
					class="ml-0.5 cursor-pointer text-[10px] text-muted-foreground/60 underline underline-offset-2 transition-colors hover:text-foreground"
					onclick={onClearAll}
				>
					Clear all
				</button>
			{/if}
		</div>
	{/if}

	<!-- ── Row 4: Results + pagination bar ───────────────────────── -->
	<PaginationBar
		{resultsText}
		{page}
		{pages}
		{onGoToPage}
		class="border-t border-border"
	/>
</div>
