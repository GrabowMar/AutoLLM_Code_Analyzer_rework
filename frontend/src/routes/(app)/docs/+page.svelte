<script lang="ts">
	import { onMount } from 'svelte';
	import { getDocsTree, getDocsCategories } from '$lib/api/system';
	import type { DocNode } from '$lib/api/system';
	import { flattenLeaves, groupByCategory } from '$lib/docs/utils';
	import { metaFor, CATEGORY_ORDER } from '$lib/docs/categories';
	import { formatApiError } from '$lib/api/core';
	import { tableFrame } from '$lib/components/table';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import BookOpen from '@lucide/svelte/icons/book-open';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import FileText from '@lucide/svelte/icons/file-text';
	import Rocket from '@lucide/svelte/icons/rocket';
	import LayoutTemplate from '@lucide/svelte/icons/layout-template';
	import Braces from '@lucide/svelte/icons/braces';

	let tree = $state<DocNode[]>([]);
	let categoryOrder = $state<string[]>(CATEGORY_ORDER);
	let loaded = $state(false);
	let error = $state<string | null>(null);
	const allLeaves = $derived(flattenLeaves(tree));
	const grouped = $derived(groupByCategory(tree, categoryOrder));

	// Entry points shown above the categories, when those docs exist.
	const FEATURED = [
		{ slug: 'QUICKSTART', label: 'Quickstart', icon: Rocket, accent: 'text-primary' },
		{ slug: 'ARCHITECTURE', label: 'Architecture', icon: LayoutTemplate, accent: 'text-accent-2' },
		{ slug: 'api-reference', label: 'API reference', icon: Braces, accent: 'text-accent-3' },
	];
	const featured = $derived(
		FEATURED.flatMap((f) => {
			const leaf = allLeaves.find((l) => l.slug === f.slug);
			return leaf ? [{ ...f, description: leaf.description ?? '' }] : [];
		})
	);

	async function load() {
		error = null;
		try {
			const [t, c] = await Promise.all([getDocsTree(), getDocsCategories().catch(() => CATEGORY_ORDER)]);
			tree = t;
			if (Array.isArray(c) && c.length) categoryOrder = c;
			loaded = true;
		} catch (e) {
			error = formatApiError(e, 'Failed to load documentation.');
		}
	}

	onMount(load);
</script>

<div class="space-y-4">
	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{/if}

	{#if !loaded}
		{#if !error}<LoadingState label="loading docs" />{/if}
	{:else if grouped.length === 0}
		<EmptyState icon={BookOpen} title="No documentation yet" description="Markdown files added to the docs folder will appear here." />
	{:else}
		{#if featured.length > 0}
			<section aria-labelledby="docs-start-here" class="space-y-2">
				<SectionHeading id="docs-start-here" size="sm" title="Start here" />
				<div class="grid gap-3 sm:grid-cols-3">
					{#each featured as f (f.slug)}
						<a
							href="/docs/{f.slug}"
							class="group flex h-full flex-col gap-1.5 rounded-md border border-border bg-card px-4 py-3 shadow-[var(--shadow-card)] transition-colors hover:border-primary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							<span class="flex items-center gap-2">
								<f.icon class="size-4 shrink-0 {f.accent}" aria-hidden="true" />
								<span class="font-display text-base font-semibold">{f.label}</span>
								<ArrowRight class="ml-auto size-4 text-muted-foreground transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-primary" aria-hidden="true" />
							</span>
							{#if f.description}<span class="line-clamp-2 text-xs text-muted-foreground">{f.description}</span>{/if}
						</a>
					{/each}
				</div>
			</section>
		{/if}

		<!-- One pane card per category: heading strip, then the pages as a flush table. -->
		{#each grouped as group (group.category)}
			{@const meta = metaFor(group.category)}
			<section class="min-w-0 overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]" aria-labelledby="docs-cat-{group.category}">
				<div class="flex items-center gap-2 border-b border-border px-4 py-2.5">
					<meta.icon class="size-4 shrink-0 {meta.accent}" aria-hidden="true" />
					<h2 id="docs-cat-{group.category}" class="shrink-0 font-display text-base font-semibold">{group.category}</h2>
					<p class="hidden min-w-0 truncate text-xs text-muted-foreground sm:block" title={meta.tagline}>{meta.tagline}</p>
					<span class="ml-auto shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
						{group.items.length} {group.items.length === 1 ? 'page' : 'pages'}
					</span>
				</div>
				<div class="table-frame" {@attach tableFrame}>
					<table>
						<tbody>
							{#each group.items as item (item.slug)}
								<tr class="row-link group">
									<td class="p-0">
										<a href="/docs/{item.slug}" class="flex items-start gap-3 px-4 py-2.5 focus-visible:outline-none">
											<FileText class="mt-0.5 size-4 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden="true" />
											<span class="min-w-0 flex-1">
												<span class="block text-sm font-medium group-hover:text-primary">{item.title}</span>
												{#if item.description}
													<span class="cell-sub mt-0.5 line-clamp-1 block max-w-[80ch] text-xs text-muted-foreground" title={item.description}>{item.description}</span>
												{/if}
											</span>
											<ChevronRight class="mt-0.5 size-4 shrink-0 text-muted-foreground/60 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-primary" aria-hidden="true" />
										</a>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/each}
	{/if}
</div>
