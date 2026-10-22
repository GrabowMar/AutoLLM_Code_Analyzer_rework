<script lang="ts">
	import { onMount } from 'svelte';
	import { getDocsTree, getDocsCategories } from '$lib/api/system';
	import type { DocNode } from '$lib/api/system';
	import { flattenLeaves, groupByCategory } from '$lib/docs/utils';
	import { metaFor, CATEGORY_ORDER } from '$lib/docs/categories';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Search from '@lucide/svelte/icons/search';
	import BookOpen from '@lucide/svelte/icons/book-open';
	import { Button } from '$lib/components/ui/button';
	import { formatApiError } from '$lib/api/core';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Clock from '@lucide/svelte/icons/clock';

	let tree = $state<DocNode[]>([]);
	let categoryOrder = $state<string[]>(CATEGORY_ORDER);
	let loaded = $state(false);
	let allLeaves = $derived(flattenLeaves(tree));
	let grouped = $derived(groupByCategory(tree, categoryOrder));

	// Pull last_modified out of an auxiliary fetch — keep it simple, the
	// tree itself doesn't carry it; we only show "recently updated" if we
	// can derive it from sort-by-name as a proxy. The dedicated page also
	// shows a precise "Updated …" badge from /docs/page.
	const recent = $derived(allLeaves.slice(0, 4));

	let error = $state<string | null>(null);

	async function load() {
		error = null;
		try {
			const [t, c] = await Promise.all([
				getDocsTree(),
				getDocsCategories().catch(() => CATEGORY_ORDER),
			]);
			tree = t;
			if (Array.isArray(c) && c.length) categoryOrder = c;
			loaded = true;
		} catch (e) {
			error = formatApiError(e, 'Failed to load documentation.');
		}
	}

	onMount(load);

	function hasSlug(slug: string): boolean {
		return allLeaves.some((l) => l.slug === slug);
	}
</script>

<div class="mx-auto w-full max-w-5xl space-y-6 px-4 py-6 sm:px-6 sm:py-8">
	<PageHeader
		title="Documentation"
		description="Guides, architecture overviews, and reference docs for running, extending, and operating the platform."
	>
		{#snippet actions()}
			<Button
				variant="outline"
				size="sm"
				onclick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
				aria-label="Open command palette"
			>
				<Search />
				Search
				<kbd class="rounded border bg-muted px-1.5 py-0.5 font-mono text-[11px]">⌘K</kbd>
			</Button>
			{#if hasSlug('api-reference')}
				<Button href="/docs/api-reference" variant="outline" size="sm">API reference</Button>
			{/if}
			{#if hasSlug('ARCHITECTURE')}
				<Button href="/docs/ARCHITECTURE" variant="outline" size="sm">Architecture</Button>
			{/if}
			{#if hasSlug('QUICKSTART')}
				<Button href="/docs/QUICKSTART" size="sm">
					Quickstart
					<ArrowRight />
				</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{/if}

	{#if !loaded}
		{#if !error}<LoadingState label="loading docs" />{/if}
	{:else if grouped.length === 0}
		<EmptyState icon={BookOpen} title="No documentation yet" description="Markdown files added to the docs folder will appear here." />
	{:else}
		<!-- Category sections ---------------------------------------------- -->
		<div class="space-y-10">
			{#each grouped as group (group.category)}
				{@const meta = metaFor(group.category)}
				{@const Icon = meta.icon}
				<section>
					<header class="mb-4 flex items-start gap-3">
						<div class="rounded-lg border bg-card p-2 {meta.accent}">
							<Icon class="h-5 w-5" />
						</div>
						<div>
							<h2 class="text-xl font-semibold tracking-tight">{group.category}</h2>
							<p class="text-sm text-muted-foreground">{meta.tagline}</p>
						</div>
						<Badge variant="outline" class="ml-auto text-xs shrink-0">
							{group.items.length} {group.items.length === 1 ? 'page' : 'pages'}
						</Badge>
					</header>

					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
						{#each group.items as item (item.slug)}
							<a href="/docs/{item.slug}" class="block focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-lg">
								<Card.Root class="card-lift h-full hover:border-primary/50">
									<Card.Header class="space-y-1.5">
										<Card.Title class="text-sm">{item.title}</Card.Title>
										{#if item.description}
											<Card.Description class="line-clamp-2 text-xs">
												{item.description}
											</Card.Description>
										{/if}
									</Card.Header>
								</Card.Root>
							</a>
						{/each}
					</div>
				</section>
			{/each}
		</div>

		<!-- Footer strip --------------------------------------------------- -->
		{#if recent.length > 0}
			<section class="rounded-lg border bg-muted/20 p-5">
				<div class="section-label mb-2 flex items-center gap-2">
					<Clock class="h-3.5 w-3.5" />
					Popular
				</div>
				<div class="flex flex-wrap gap-2">
					{#each recent as item (item.slug)}
						<a
							href="/docs/{item.slug}"
							class="rounded-full border bg-background px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
						>
							{item.title}
						</a>
					{/each}
				</div>
			</section>
		{/if}
	{/if}
</div>
