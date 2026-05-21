<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { searchDocs } from '$lib/api/system';
	import type { DocNode, DocSearchResult } from '$lib/api/system';
	import { flattenLeaves } from '$lib/docs/utils';
	import { metaFor } from '$lib/docs/categories';
	import Search from '@lucide/svelte/icons/search';
	import FileText from '@lucide/svelte/icons/file-text';
	import Hash from '@lucide/svelte/icons/hash';
	import CornerDownLeft from '@lucide/svelte/icons/corner-down-left';

	interface Props {
		open: boolean;
		tree: DocNode[];
		onclose: () => void;
	}

	let { open = $bindable(), tree, onclose }: Props = $props();

	let query = $state('');
	let searchResults = $state<DocSearchResult[]>([]);
	let selected = $state(0);
	let isSearching = $state(false);
	let searchTimeout: ReturnType<typeof setTimeout> | null = null;
	let inputEl: HTMLInputElement | undefined = $state();

	const allLeaves = $derived(flattenLeaves(tree));

	// Combined results: search results when query is non-empty,
	// otherwise the full leaf list filtered by fuzzy title match.
	interface Item {
		kind: 'page' | 'match';
		slug: string;
		title: string;
		category?: string;
		section?: string;
		snippet?: string;
	}

	const items = $derived<Item[]>(
		query.trim()
			? searchResults.map((r) => ({
					kind: 'match',
					slug: r.slug,
					title: r.title,
					category: r.category,
					section: r.section,
					snippet: r.snippet,
				}))
			: allLeaves.slice(0, 50).map((l) => ({
					kind: 'page',
					slug: l.slug,
					title: l.title,
					category: l.category,
				})),
	);

	function handleSearch(q: string) {
		if (searchTimeout) clearTimeout(searchTimeout);
		if (!q.trim()) {
			searchResults = [];
			isSearching = false;
			return;
		}
		isSearching = true;
		searchTimeout = setTimeout(async () => {
			searchResults = await searchDocs(q);
			isSearching = false;
			selected = 0;
		}, 180);
	}

	$effect(() => {
		handleSearch(query);
	});

	$effect(() => {
		if (selected >= items.length) selected = Math.max(0, items.length - 1);
	});

	$effect(() => {
		if (open) {
			query = '';
			selected = 0;
			queueMicrotask(() => inputEl?.focus());
		}
	});

	function go(item: Item) {
		open = false;
		onclose();
		goto(`/docs/${item.slug}`);
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			selected = Math.min(selected + 1, items.length - 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			selected = Math.max(selected - 1, 0);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			const item = items[selected];
			if (item) go(item);
		} else if (e.key === 'Escape') {
			e.preventDefault();
			open = false;
			onclose();
		}
	}

	function onGlobalKey(e: KeyboardEvent) {
		const isMod = e.metaKey || e.ctrlKey;
		if (isMod && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			open = !open;
			if (!open) onclose();
		} else if (e.key === '/' && !open) {
			const target = e.target as HTMLElement | null;
			if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName)) return;
			if (target?.isContentEditable) return;
			e.preventDefault();
			open = true;
		}
	}

	onMount(() => {
		window.addEventListener('keydown', onGlobalKey);
	});
	onDestroy(() => window.removeEventListener('keydown', onGlobalKey));
</script>

{#if open}
	<div
		class="fixed inset-0 z-50 flex items-start justify-center bg-black/40 backdrop-blur-sm p-4 pt-[10vh] sm:pt-[15vh]"
		role="presentation"
		onclick={() => {
			open = false;
			onclose();
		}}
	>
		<div
			class="w-full max-w-xl overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-2xl"
			role="dialog"
			aria-modal="true"
			aria-label="Search documentation"
			onclick={(e) => e.stopPropagation()}
			onkeydown={onKey}
		>
			<div class="flex items-center gap-2 border-b px-3">
				<Search class="h-4 w-4 text-muted-foreground" />
				<input
					bind:this={inputEl}
					bind:value={query}
					placeholder="Search docs… (press Enter to open)"
					class="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
				/>
				<kbd class="hidden rounded border bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground sm:inline">ESC</kbd>
			</div>

			<div class="max-h-[60vh] overflow-y-auto p-1">
				{#if isSearching}
					<p class="px-3 py-6 text-center text-xs text-muted-foreground">Searching…</p>
				{:else if items.length === 0}
					<p class="px-3 py-6 text-center text-xs text-muted-foreground">
						{query.trim() ? 'No results' : 'No docs yet'}
					</p>
				{:else}
					<ul class="space-y-0.5">
						{#each items as item, i (item.slug + '-' + i)}
							{@const meta = metaFor(item.category)}
							<li>
								<button
									type="button"
									onclick={() => go(item)}
									onmouseenter={() => (selected = i)}
									class="w-full rounded-md px-3 py-2 text-left transition-colors {selected === i
										? 'bg-accent text-accent-foreground'
										: 'hover:bg-muted/60'}"
								>
									<div class="flex items-center gap-2">
										{#if item.section}
											<Hash class="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
										{:else}
											<FileText class="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
										{/if}
										<span class="text-sm font-medium truncate">{item.title}</span>
										{#if item.section}
											<span class="text-xs text-muted-foreground truncate">/ {item.section}</span>
										{/if}
										<span class="ml-auto text-[10px] {meta.accent} truncate">{meta.label}</span>
									</div>
									{#if item.snippet}
										<p class="mt-1 line-clamp-1 pl-5 text-xs text-muted-foreground">
											{item.snippet}
										</p>
									{/if}
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<div class="flex items-center justify-between border-t bg-muted/30 px-3 py-1.5 text-[10px] text-muted-foreground">
				<div class="flex items-center gap-3">
					<span class="flex items-center gap-1"><kbd class="rounded bg-background px-1 py-0.5 font-mono">↑↓</kbd> Navigate</span>
					<span class="flex items-center gap-1"><CornerDownLeft class="h-3 w-3" /> Open</span>
				</div>
				<span class="hidden sm:inline">{items.length} results</span>
			</div>
		</div>
	</div>
{/if}
