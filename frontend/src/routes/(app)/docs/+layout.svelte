<script lang="ts">
	import '$lib/styles/docs.css';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { getDocsTree, getDocsCategories } from '$lib/api/system';
	import type { DocNode } from '$lib/api/system';
	import { groupByCategory } from '$lib/docs/utils';
	import { metaFor, CATEGORY_ORDER } from '$lib/docs/categories';
	import CommandPalette from '$lib/components/docs/CommandPalette.svelte';
	import * as Sheet from '$lib/components/ui/sheet';
	import Search from '@lucide/svelte/icons/search';
	import Menu from '@lucide/svelte/icons/menu';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import FileText from '@lucide/svelte/icons/file-text';
	import House from '@lucide/svelte/icons/house';

	let { children } = $props();

	let tree = $state<DocNode[]>([]);
	let categoryOrder = $state<string[]>(CATEGORY_ORDER);
	let paletteOpen = $state(false);
	let mobileOpen = $state(false);

	const grouped = $derived(groupByCategory(tree, categoryOrder));
	const currentSlug = $derived(($page.params?.slug as string | undefined) ?? '');

	onMount(async () => {
		const [t, c] = await Promise.all([
			getDocsTree(),
			getDocsCategories().catch(() => CATEGORY_ORDER),
		]);
		tree = t;
		if (Array.isArray(c) && c.length) categoryOrder = c;
	});

	$effect(() => {
		// Close mobile drawer on navigation.
		const _ = currentSlug;
		mobileOpen = false;
	});

	function isActive(slug: string) {
		return currentSlug === slug;
	}
</script>

<svelte:head>
	<title>Documentation - LLM Lab</title>
</svelte:head>

{#snippet sidebarContent(onnav: () => void)}
	<div class="mb-3 flex items-center justify-between">
		<a
			href="/docs"
			class="flex items-center gap-2 text-sm font-semibold tracking-tight"
			onclick={onnav}
		>
			<House class="h-4 w-4 text-primary" />
			Documentation
		</a>
	</div>

	<button
		type="button"
		onclick={() => {
			paletteOpen = true;
			onnav();
		}}
		class="mb-4 flex w-full items-center justify-between rounded-md border bg-background px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
	>
		<span class="flex items-center gap-2">
			<Search class="h-3.5 w-3.5" />
			Search docs…
		</span>
		<kbd class="hidden rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px] sm:inline">
			⌘K
		</kbd>
	</button>

	<nav class="space-y-4 pb-6">
		<a
			href="/docs"
			onclick={onnav}
			class="flex items-center gap-1.5 rounded px-2 py-1.5 text-xs transition-colors hover:bg-muted {currentSlug ===
			''
				? 'bg-muted font-medium text-foreground'
				: 'text-muted-foreground hover:text-foreground'}"
		>
			<FileText class="h-3.5 w-3.5 shrink-0" />
			Overview
		</a>

		{#each grouped as group (group.category)}
			{@const meta = metaFor(group.category)}
			{@const Icon = meta.icon}
			<div>
				<div
					class="mb-1 flex items-center gap-1.5 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
				>
					<Icon class="h-3 w-3 {meta.accent}" />
					{group.category}
				</div>
				<ul class="space-y-0.5 border-l border-border/60 pl-2">
					{#each group.items as item (item.slug)}
						<li>
							<a
								href="/docs/{item.slug}"
								onclick={onnav}
								class="-ml-px flex items-center gap-1.5 border-l-2 py-1 pl-2 pr-2 text-xs transition-colors {isActive(
									item.slug,
								)
									? 'border-primary bg-muted font-medium text-foreground'
									: 'border-transparent text-muted-foreground hover:border-border hover:text-foreground'}"
							>
								<ChevronRight class="h-3 w-3 shrink-0 opacity-50" />
								<span class="truncate">{item.title}</span>
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</nav>
{/snippet}

<!--
  Escape the (app)/+layout padding (p-3 sm:p-4 md:p-5 plus pt-[calc(3rem+...)]).
  AppHeader is fixed h-12 (=3rem); we re-add pt-12 so content starts just below it.
  This gives the docs sidebar a flush left edge against the AppSidebar, no double
  padding, and a clean min-height that fills the viewport minus the header.
-->
<div
	class="docs-shell -m-3 -mt-[calc(3rem+0.75rem)] flex min-h-[calc(100dvh-3rem)] pt-12 sm:-m-4 sm:-mt-[calc(3rem+1rem)] md:-m-5 md:-mt-[calc(3rem+1.25rem)]"
>
	<!-- Desktop sidebar (sticky under fixed AppHeader) -->
	<aside
		class="hidden shrink-0 border-r bg-muted/20 md:block md:w-64 lg:w-72"
		aria-label="Documentation navigation"
	>
		<div class="sticky top-12 max-h-[calc(100dvh-3rem)] overflow-y-auto px-3 py-4">
			{@render sidebarContent(() => {})}
		</div>
	</aside>

	<!-- Mobile sidebar (sheet drawer) -->
	<Sheet.Root bind:open={mobileOpen}>
		<Sheet.Content side="left" class="w-72 overflow-y-auto p-4">
			{@render sidebarContent(() => (mobileOpen = false))}
		</Sheet.Content>
	</Sheet.Root>

	<!-- Main column -->
	<div class="flex min-w-0 flex-1 flex-col">
		<!-- Mobile docs toolbar (sticky beneath AppHeader, not absolute) -->
		<div
			class="sticky top-12 z-20 flex items-center justify-between border-b bg-background/90 px-4 py-2 backdrop-blur md:hidden"
		>
			<button
				type="button"
				onclick={() => (mobileOpen = true)}
				class="inline-flex items-center gap-2 rounded-md border bg-background px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted"
				aria-label="Open documentation navigation"
			>
				<Menu class="h-3.5 w-3.5" />
				Browse
			</button>
			<button
				type="button"
				onclick={() => (paletteOpen = true)}
				class="inline-flex items-center gap-1.5 rounded-md border bg-background px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted"
				aria-label="Search documentation"
			>
				<Search class="h-3.5 w-3.5" />
				Search
			</button>
		</div>

		<main class="min-w-0 flex-1">
			{@render children()}
		</main>
	</div>
</div>

<CommandPalette bind:open={paletteOpen} {tree} onclose={() => (paletteOpen = false)} />
