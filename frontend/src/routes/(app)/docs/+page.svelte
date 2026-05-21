<script lang="ts">
	import { onMount } from 'svelte';
	import { getDocsTree, getDocsCategories } from '$lib/api/system';
	import type { DocNode } from '$lib/api/system';
	import { flattenLeaves, groupByCategory } from '$lib/docs/utils';
	import { metaFor, CATEGORY_ORDER } from '$lib/docs/categories';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
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

	onMount(async () => {
		const [t, c] = await Promise.all([
			getDocsTree(),
			getDocsCategories().catch(() => CATEGORY_ORDER),
		]);
		tree = t;
		if (Array.isArray(c) && c.length) categoryOrder = c;
		loaded = true;
	});

	function hasSlug(slug: string): boolean {
		return allLeaves.some((l) => l.slug === slug);
	}
</script>

<svelte:head>
	<title>Documentation - LLM Lab</title>
</svelte:head>

<div class="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 sm:py-8 md:py-10">
	<!-- Hero ----------------------------------------------------------- -->
	<section class="relative overflow-hidden rounded-2xl border bg-gradient-to-br from-primary/5 via-background to-background p-8 md:p-12">
		<div class="absolute -right-12 -top-12 h-48 w-48 rounded-full bg-primary/10 blur-3xl" aria-hidden="true"></div>
		<div class="relative max-w-2xl">
			<Badge variant="outline" class="mb-4 gap-1.5">
				<Sparkles class="h-3 w-3 text-primary" />
				Documentation
			</Badge>
			<h1 class="text-3xl font-bold tracking-tight md:text-4xl">
				Everything you need to ship with LLM Lab
			</h1>
			<p class="mt-3 text-base text-muted-foreground md:text-lg">
				Guides, architecture overviews, and reference docs for running, extending, and
				operating the platform.
			</p>
			<div class="mt-6 flex flex-wrap gap-2">
				{#if hasSlug('QUICKSTART')}
					<a
						href="/docs/QUICKSTART"
						class="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
					>
						Quickstart
						<ArrowRight class="h-3.5 w-3.5" />
					</a>
				{/if}
				{#if hasSlug('ARCHITECTURE')}
					<a
						href="/docs/ARCHITECTURE"
						class="inline-flex items-center gap-1.5 rounded-md border bg-background px-4 py-2 text-sm font-medium transition-colors hover:bg-muted"
					>
						Architecture
					</a>
				{/if}
				{#if hasSlug('api-reference')}
					<a
						href="/docs/api-reference"
						class="inline-flex items-center gap-1.5 rounded-md border bg-background px-4 py-2 text-sm font-medium transition-colors hover:bg-muted"
					>
						API reference
					</a>
				{/if}
				<button
					type="button"
					class="inline-flex items-center gap-2 rounded-md border bg-background/60 px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted"
					onclick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
					aria-label="Open command palette"
				>
					Search
					<kbd class="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">⌘K</kbd>
				</button>
			</div>
		</div>
	</section>

	{#if !loaded}
		<div class="flex items-center justify-center py-20">
			<LoaderCircle class="h-6 w-6 animate-spin text-muted-foreground" />
		</div>
	{:else}
		<!-- Category sections ---------------------------------------------- -->
		<div class="mt-10 space-y-10">
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
								<Card.Root class="h-full transition-all hover:border-primary/50 hover:shadow-sm">
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
			<section class="mt-12 rounded-lg border bg-muted/20 p-5">
				<div class="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
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
