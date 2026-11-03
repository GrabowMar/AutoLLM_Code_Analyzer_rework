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
	import { cn } from '$lib/utils';

	interface Props {
		value: string[];
		onchange: (refs: string[]) => void;
		disabled?: boolean;
	}

	let { value, onchange, disabled = false }: Props = $props();

	const list = $derived(libraryStore.list('task'));
	let query = $state('');
	let showAll = $state(false);

	const chosenSlugs = $derived(new Map(value.map((ref) => [parseRef(ref)?.slug ?? ref, ref])));
	const items = $derived(
		list.items
			.filter((item) => {
				const q = query.trim().toLowerCase();
				if (!q) return true;
				const content = item.content as TaskSpecContent;
				return [item.slug, item.name, content.category, ...(content.features ?? []), ...(content.surfaces ?? [])]
					.join(' ')
					.toLowerCase()
					.includes(q);
			})
			.sort((a, b) => Number(chosenSlugs.has(b.slug)) - Number(chosenSlugs.has(a.slug)) || a.name.localeCompare(b.name)),
	);
	const shown = $derived(showAll || query ? items : items.slice(0, 12));

	function toggle(item: LibraryItemOut, on: boolean) {
		const next = value.filter((ref) => (parseRef(ref)?.slug ?? ref) !== item.slug);
		if (on) next.push(item.pinned_ref);
		onchange(next);
	}

	const derivedOf = (item: LibraryItemOut) => (item.derived ?? null) as Partial<TaskDerived> | null;
</script>

<div class="space-y-2">
	<div class="flex flex-wrap items-center gap-2">
		<Input class="h-8 max-w-xs text-sm" placeholder="Search tasks, features, surfaces" aria-label="Search tasks" bind:value={query} />
		<span class="text-xs text-muted-foreground">{value.length} selected</span>
		{#if value.length > 0 && !disabled}
			<Button variant="ghost" size="sm" onclick={() => onchange([])}>Clear</Button>
		{/if}
	</div>
	{#if list.error}
		<ErrorAlert message={list.error} onretry={() => list.revalidate()} />
	{:else if list.loading && list.items.length === 0}
		<LoadingState label="loading tasks" />
	{:else if list.items.length === 0}
		<EmptyState compact title="No tasks in the library" description="Seed or create tasks in the Library first." />
	{:else}
		<ul class="divide-y divide-border rounded-md border border-border" aria-label="Tasks">
			{#each shown as item (item.slug)}
				{@const chosen = chosenSlugs.get(item.slug)}
				{@const content = item.content as TaskSpecContent}
				{@const d = derivedOf(item)}
				<li class={cn('flex items-start gap-3 px-3 py-2', chosen && 'bg-primary/[0.04]')}>
					<Checkbox
						class="mt-0.5"
						checked={!!chosen}
						{disabled}
						aria-label="Use {item.name}"
						onCheckedChange={(v) => toggle(item, v === true)}
					/>
					<div class="min-w-0 flex-1 space-y-1">
						<div class="flex flex-wrap items-center gap-2">
							<span class="text-sm font-medium">{item.name}</span>
							<PinnedRefBadge ref={chosen ?? item.pinned_ref} kind="task" latestVersion={item.version} />
							{#if d?.size_bucket}
								<span class="rounded border border-border px-1.5 font-mono text-[11px]" title="Size bucket">{d.size_bucket}</span>
							{/if}
							{#if content.category}<span class="text-xs text-muted-foreground">{content.category}</span>{/if}
						</div>
						<div class="flex flex-wrap gap-1">
							{#each content.features ?? [] as feature (feature)}
								<span class="rounded bg-muted px-1.5 text-[11px] text-muted-foreground">{feature}</span>
							{/each}
							{#each content.surfaces ?? [] as surface (surface)}
								<span class="rounded border border-border px-1.5 font-mono text-[11px] text-muted-foreground" title="Security surface">{surface}</span>
							{/each}
						</div>
					</div>
				</li>
			{/each}
		</ul>
		{#if !showAll && !query && items.length > shown.length}
			<Button variant="ghost" size="sm" onclick={() => (showAll = true)}>Show all {items.length} tasks</Button>
		{/if}
	{/if}
</div>
