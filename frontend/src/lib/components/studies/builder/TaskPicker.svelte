<script lang="ts">
	/**
	 * TaskPicker — the units of an app study (spec 10.2 step 2): task versions, multi-select. Each row shows
	 * the pinned version (`PinnedRefBadge`, "latest is vN" when older), feature and surface chips and the
	 * size bucket. Prose variants are a factor (`prose_variant`), never part of a unit.
	 *
	 * Props: `value` (pinned task refs), `onchange(refs)`, `disabled`.
	 */
	import type { LibraryItemOut, TaskDerived, TaskSpecContent } from '$lib/api/v3/types';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { PinnedRefBadge, libraryStore } from '$lib/components/library';
	import { parseRef } from '$lib/api/v3/types';
	import { toast } from 'svelte-sonner';
	import Highlight from '$lib/components/Highlight.svelte';
	import { matchesAll, searchTokens } from '$lib/utils/search';
	import ListChecks from '@lucide/svelte/icons/list-checks';
	import Search from '@lucide/svelte/icons/search';

	interface Props {
		value: string[];
		onchange: (refs: string[]) => void;
		disabled?: boolean;
	}

	let { value, onchange, disabled = false }: Props = $props();

	const list = $derived(libraryStore.list('task'));
	let query = $state('');
	let showAll = $state(false);
	let category = $state('');
	let size = $state('');
	let onlyChosen = $state(false);
	let allCategories = $state(false);
	/** Categories shown before "+N more" (the most common first; the active one always stays visible). */
	const CATEGORY_CHIPS = 8;
	const tokens = $derived(searchTokens(query));

	const chosenSlugs = $derived(new Map(value.map((ref) => [parseRef(ref)?.slug ?? ref, ref])));
	const categories = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const item of list.items) {
			const c = (item.content as TaskSpecContent).category;
			if (c) counts.set(c, (counts.get(c) ?? 0) + 1);
		}
		return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
	});
	const visibleCategories = $derived(
		allCategories || categories.length <= CATEGORY_CHIPS + 1
			? categories
			: categories.filter(([name], i) => i < CATEGORY_CHIPS || name === category),
	);
	const sizes = $derived(
		(['S', 'M', 'L', 'XL'] as const).filter((b) => list.items.some((item) => derivedOf(item)?.size_bucket === b)),
	);
	// The list order is fixed (by name): ticking a card never moves it under the pointer.
	const items = $derived(
		list.items
			.filter((item) => {
				const content = item.content as TaskSpecContent;
				if (onlyChosen && !chosenSlugs.has(item.slug)) return false;
				if (category && content.category !== category) return false;
				if (size && derivedOf(item)?.size_bucket !== size) return false;
				return matchesAll([item.slug, item.name, item.description, content.category, ...(content.features ?? []), ...(content.surfaces ?? [])], tokens);
			})
			.sort((a, b) => a.name.localeCompare(b.name)),
	);
	const filtered = $derived(tokens.length > 0 || !!category || !!size || onlyChosen);

	function clearFilters() {
		query = '';
		category = '';
		size = '';
		onlyChosen = false;
	}

	function searchKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && query) {
			e.preventDefault();
			e.stopPropagation();
			query = '';
		}
	}
	const shown = $derived(showAll || filtered ? items : items.slice(0, 12));
	const shownUnchosen = $derived(shown.filter((item) => !chosenSlugs.has(item.slug)));

	function toggle(item: LibraryItemOut, on: boolean) {
		const next = value.filter((ref) => (parseRef(ref)?.slug ?? ref) !== item.slug);
		if (on) next.push(item.pinned_ref);
		onchange(next);
	}

	// Bulk clear is undoable (a toast with Undo) rather than confirmed up front.
	function clearAll() {
		const before = [...value];
		onchange([]);
		toast(`Cleared ${before.length} task${before.length === 1 ? '' : 's'}`, {
			action: { label: 'Undo', onClick: () => onchange(before) },
		});
	}

	function selectShown() {
		onchange([...value, ...shownUnchosen.map((item) => item.pinned_ref)]);
	}

	const derivedOf = (item: LibraryItemOut) => (item.derived ?? null) as Partial<TaskDerived> | null;
</script>

<div class="flex flex-col gap-2.5">
	<div class="flex flex-wrap items-center gap-2">
		<div class="relative w-full max-w-xs">
			<Search class="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
			<Input class="h-8 pl-8 text-sm" type="search" placeholder="Search tasks, features, surfaces" aria-label="Search tasks" bind:value={query} onkeydown={searchKeydown} />
		</div>
		<label class="flex items-center gap-2 text-xs">
			<Checkbox bind:checked={onlyChosen} />
			Chosen only
		</label>
		<span class="ml-auto flex items-center gap-1">
			{#if !disabled && shownUnchosen.length > 0 && filtered}
				<Button variant="outline" size="sm" class="h-7 text-xs" onclick={selectShown}>
					<ListChecks />Choose {shownUnchosen.length} shown
				</Button>
			{/if}
			{#if value.length > 0 && !disabled}
				<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={clearAll}>Clear {value.length}</Button>
			{/if}
		</span>
	</div>
	{#if categories.length > 1}
		<div class="flex flex-wrap gap-1" role="group" aria-label="Filter by category and size">
			<button type="button" class="tp-filter" aria-pressed={category === ''} onclick={() => (category = '')}>
				All <span class="tp-filter-n">{list.items.length}</span>
			</button>
			{#each visibleCategories as [name, n] (name)}
				<button type="button" class="tp-filter" aria-pressed={category === name} onclick={() => (category = category === name ? '' : name)}>
					{name} <span class="tp-filter-n">{n}</span>
				</button>
			{/each}
			{#if categories.length > CATEGORY_CHIPS + 1}
				<button type="button" class="tp-filter tp-more" aria-expanded={allCategories} onclick={() => (allCategories = !allCategories)}>
					{allCategories ? 'Fewer' : `+${categories.length - visibleCategories.length} more`}
				</button>
			{/if}
			{#if sizes.length > 1}
				<span class="tp-sep" aria-hidden="true"></span>
				<span class="self-center font-mono text-[11px] tracking-wide text-muted-foreground uppercase" aria-hidden="true">size</span>
				{#each sizes as b (b)}
					<button type="button" class="tp-filter" aria-pressed={size === b} title="Size bucket {b}" onclick={() => (size = size === b ? '' : b)}>
						<span class="sr-only">Size </span>{b}
					</button>
				{/each}
			{/if}
		</div>
	{/if}
	{#if list.error}
		<ErrorAlert message={list.error} onretry={() => list.revalidate()} />
	{:else if list.loading && list.items.length === 0}
		<LoadingState label="loading tasks" />
	{:else if list.items.length === 0}
		<EmptyState compact title="No tasks in the library" description="Seed or create tasks in the Library first." />
	{:else}
		<!-- Card grid: the whole card toggles; surfaces (CWE / security surfaces) fold into a count. -->
		<ul class="tp-grid" aria-label="Tasks">
			{#each shown as item (item.slug)}
				{@const chosen = chosenSlugs.get(item.slug)}
				{@const content = item.content as TaskSpecContent}
				{@const d = derivedOf(item)}
				{@const surfaces = content.surfaces ?? []}
				<li>
					<label class="tp-card" data-on={!!chosen} data-disabled={disabled}>
						<Checkbox
							class="mt-0.5"
							checked={!!chosen}
							{disabled}
							aria-label="Use {item.name}"
							onCheckedChange={(v) => toggle(item, v === true)}
						/>
						<span class="tp-body">
							<span class="tp-title">
								<span class="tp-name"><Highlight text={item.name} {tokens} /></span>
								<PinnedRefBadge ref={chosen ?? item.pinned_ref} kind="task" latestVersion={item.version} />
								{#if d?.size_bucket}<span class="tp-size" title="Size bucket">{d.size_bucket}</span>{/if}
							</span>
							{#if item.description}<span class="tp-desc" title={item.description}>{item.description}</span>{/if}
							<span class="tp-tags">
								{#if content.category}<span class="tp-cat">{content.category}</span>{/if}
								{#each (content.features ?? []).slice(0, 4) as feature (feature)}<span class="tp-feat">{feature}</span>{/each}
								{#if (content.features ?? []).length > 4}<span class="tp-feat" title={(content.features ?? []).slice(4).join(', ')}>+{(content.features ?? []).length - 4}</span>{/if}
								{#if surfaces.length}<span class="tp-surf" title={'Security surfaces: ' + surfaces.join(', ')}>{surfaces.length} surfaces</span>{/if}
							</span>
						</span>
					</label>
				</li>
			{/each}
		</ul>
		{#if items.length === 0}
			<p class="py-4 text-center text-xs text-muted-foreground">
				No task matches.
				<button type="button" class="inline-flex min-h-6 cursor-pointer items-center text-primary underline underline-offset-2" onclick={clearFilters}>Clear the filters</button>
			</p>
		{:else if !showAll && !filtered && items.length > shown.length}
			<Button variant="outline" size="sm" class="self-center" onclick={() => (showAll = true)}>Show all {items.length} tasks</Button>
		{/if}
	{/if}
</div>

<style>
	.tp-filter {
		display: inline-flex;
		height: 1.75rem;
		cursor: pointer;
		align-items: center;
		gap: 0.375rem;
		border: 1px solid var(--border);
		border-radius: 9999px;
		padding-inline: 0.625rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
		transition:
			border-color 150ms,
			background-color 150ms,
			color 150ms;
	}
	.tp-filter:hover {
		color: var(--foreground);
		border-color: color-mix(in oklab, var(--primary) 40%, var(--border));
	}
	.tp-filter[aria-pressed='true'] {
		border-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 10%, transparent);
		color: var(--foreground);
	}
	.tp-filter:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.tp-more {
		border-style: dashed;
		color: var(--primary);
	}
	.tp-sep {
		width: 1px;
		align-self: stretch;
		margin-inline: 0.25rem;
		background: var(--border);
	}
	.tp-filter-n {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		font-variant-numeric: tabular-nums;
		opacity: 0.75;
	}
	.tp-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(19rem, 1fr));
		gap: 0.5rem;
	}
	.tp-card {
		display: flex;
		height: 100%;
		cursor: pointer;
		align-items: flex-start;
		gap: 0.625rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		padding: 0.625rem 0.75rem;
		transition: border-color 150ms, background-color 150ms;
	}
	.tp-card:hover {
		border-color: color-mix(in oklab, var(--primary) 40%, var(--border));
	}
	.tp-card[data-on='true'] {
		border-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 7%, var(--card));
	}
	.tp-card[data-disabled='true'] {
		cursor: default;
	}
	.tp-body {
		display: flex;
		min-width: 0;
		flex: 1;
		flex-direction: column;
		gap: 0.3rem;
	}
	.tp-title {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.375rem;
	}
	.tp-name {
		font-size: 0.875rem;
		font-weight: 600;
	}
	.tp-size {
		border-radius: 0.25rem;
		border: 1px solid var(--border);
		padding: 0 0.3rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
	}
	.tp-desc {
		display: -webkit-box;
		overflow: hidden;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		font-size: 0.75rem;
		line-height: 1.4;
		color: var(--muted-foreground);
	}
	.tp-tags {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
	}
	.tp-cat,
	.tp-feat,
	.tp-surf {
		border-radius: 0.25rem;
		padding: 0 0.3125rem;
		font-size: 0.6875rem;
		line-height: 1.0625rem;
	}
	.tp-cat {
		color: var(--accent-foreground);
		background: var(--accent);
	}
	.tp-feat {
		background: var(--muted);
		color: var(--muted-foreground);
	}
	/* Body-colour text on an amber tint + border: ≥ 4.5:1 in both themes; the word carries the meaning. */
	.tp-surf {
		font-family: var(--font-mono);
		color: var(--foreground);
		background: color-mix(in oklab, var(--warning) 14%, transparent);
		box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--warning) 45%, transparent);
	}
</style>
