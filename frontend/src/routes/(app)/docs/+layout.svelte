<script lang="ts">
	import '$lib/styles/docs.css';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { getDocsTree, getDocsCategories } from '$lib/api/system';
	import type { DocNode } from '$lib/api/system';
	import { flattenLeaves, groupByCategory } from '$lib/docs/utils';
	import { CATEGORY_ORDER } from '$lib/docs/categories';
	import { pageTitle } from '$lib/stores/page-title.svelte';
	import CommandPalette from '$lib/components/docs/CommandPalette.svelte';
	import DocsNav from '$lib/components/docs/DocsNav.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Sheet from '$lib/components/ui/sheet';
	import Search from '@lucide/svelte/icons/search';
	import Menu from '@lucide/svelte/icons/menu';

	let { children } = $props();

	let tree = $state<DocNode[]>([]);
	let categoryOrder = $state<string[]>(CATEGORY_ORDER);
	let loaded = $state(false);
	let paletteOpen = $state(false);
	let mobileOpen = $state(false);

	const grouped = $derived(groupByCategory(tree, categoryOrder));
	const pageCount = $derived(flattenLeaves(tree).length);
	const currentSlug = $derived((page.params?.slug as string | undefined) ?? '');

	onMount(async () => {
		const [t, c] = await Promise.all([getDocsTree(), getDocsCategories().catch(() => CATEGORY_ORDER)]);
		tree = t;
		if (Array.isArray(c) && c.length) categoryOrder = c;
		loaded = true;
	});

	$effect(() => {
		// Close the mobile drawer on navigation.
		void currentSlug;
		mobileOpen = false;
	});
</script>

<div class="space-y-4">
	<PageHeader
		title="Documentation"
		documentTitle={currentSlug && pageTitle.current ? pageTitle.current : 'Documentation'}
		description="Guides, architecture overviews, and reference docs for running, extending, and operating the platform."
	>
		{#snippet meta()}
			{#if loaded}<span class="font-mono text-xs text-muted-foreground tabular-nums">{pageCount} pages</span>{/if}
		{/snippet}
		{#snippet actions()}
			<Button variant="outline" size="sm" class="lg:hidden" onclick={() => (mobileOpen = true)} aria-label="Browse documentation">
				<Menu /> Browse
			</Button>
			<Button variant="outline" size="sm" onclick={() => (paletteOpen = true)} aria-label="Search documentation">
				<Search />
				Search
				<kbd class="hidden rounded border bg-muted px-1.5 py-0.5 font-mono text-[11px] sm:inline">⌘K</kbd>
			</Button>
		{/snippet}
	</PageHeader>

	<div class="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
		<div class="hidden min-w-0 lg:sticky lg:top-20 lg:block lg:max-h-[calc(100dvh-6rem)] lg:self-start lg:overflow-y-auto">
			<DocsNav groups={grouped} {currentSlug} />
		</div>

		<div class="min-w-0">
			{@render children()}
		</div>
	</div>
</div>

<!-- Below lg the nav lives in a drawer, opened from the header's Browse button. -->
<Sheet.Root bind:open={mobileOpen}>
	<Sheet.Content side="left" class="w-72 overflow-y-auto p-3 pt-10">
		<DocsNav groups={grouped} {currentSlug} bare onNavigate={() => (mobileOpen = false)} />
	</Sheet.Content>
</Sheet.Root>

<CommandPalette bind:open={paletteOpen} {tree} onclose={() => (paletteOpen = false)} />
