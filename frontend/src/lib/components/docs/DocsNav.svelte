<script lang="ts">
	/**
	 * DocsNav — the docs pages grouped by category, in the same rail card as the
	 * Generation Library's nav: section labels, a primary bar on the open page.
	 * Rendered in the layout's sticky column from `lg`, and in a sheet below it.
	 */
	import type { DocNode } from '$lib/api/system';
	import { metaFor } from '$lib/docs/categories';
	import House from '@lucide/svelte/icons/house';

	interface Props {
		groups: Array<{ category: string; items: DocNode[] }>;
		currentSlug: string;
		onNavigate?: () => void;
		/** Drop the card surface (inside the mobile sheet). */
		bare?: boolean;
	}
	let { groups, currentSlug, onNavigate, bare = false }: Props = $props();
</script>

{#snippet link(href: string, label: string, active: boolean)}
	<li class="relative">
		{#if active}<span class="absolute inset-y-1 left-0 w-0.5 rounded-full bg-primary" aria-hidden="true"></span>{/if}
		<a
			{href}
			aria-current={active ? 'page' : undefined}
			title={label}
			onclick={onNavigate}
			class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors duration-150 {active
				? 'bg-primary/10 font-medium text-primary'
				: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
		>
			<span class="truncate">{label}</span>
		</a>
	</li>
{/snippet}

<nav
	aria-label="Documentation"
	class="space-y-4 {bare ? '' : 'rounded-md border border-border bg-card p-2 pt-3 shadow-[var(--shadow-card)]'}"
>
	<ul>
		<li class="relative">
			{#if currentSlug === ''}<span class="absolute inset-y-1 left-0 w-0.5 rounded-full bg-primary" aria-hidden="true"></span>{/if}
			<a
				href="/docs"
				aria-current={currentSlug === '' ? 'page' : undefined}
				onclick={onNavigate}
				class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors duration-150 {currentSlug === ''
					? 'bg-primary/10 font-medium text-primary'
					: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
			>
				<House class="size-4 shrink-0" aria-hidden="true" />
				Overview
			</a>
		</li>
	</ul>
	{#each groups as g (g.category)}
		{@const meta = metaFor(g.category)}
		<div class="space-y-1 border-t border-border/60 pt-3">
			<p class="section-label flex items-center gap-1.5 px-2">
				<meta.icon class="size-3.5 {meta.accent}" aria-hidden="true" />
				<span class="flex-1 truncate">{g.category}</span>
				<span class="font-mono tabular-nums">{g.items.length}</span>
			</p>
			<ul class="space-y-0.5">
				{#each g.items as item (item.slug)}
					{@render link(`/docs/${item.slug}`, item.title, currentSlug === item.slug)}
				{/each}
			</ul>
		</div>
	{/each}
</nav>
