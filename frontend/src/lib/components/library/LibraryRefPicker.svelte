<script lang="ts">
	/**
	 * LibraryRefPicker — pick one or several library entities of a kind as **pinned** refs
	 * (`slug@v#hash12`, the form studies store).
	 *
	 *   <LibraryRefPicker kind="style" value={[styleRef]} onchange={([r]) => (styleRef = r ?? '')} />
	 *   <LibraryRefPicker kind="task" multiple value={taskRefs} onchange={(r) => (taskRefs = r)} />
	 *
	 * Props: `kind`, `multiple` (checklist with search; else a select), `value` (pinned refs), `filter`
	 * (hide rows), `onchange(refs)`, `placeholder`, `disabled`, `id`, `aria-label`.
	 * Rows come from `libraryStore` (latest version per slug, archived hidden). A value pinned to an older
	 * version stays selected and shows "latest is vN · Use latest"; a value whose slug is not visible is
	 * kept and flagged, never dropped silently.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import X from '@lucide/svelte/icons/x';
	import type { LibraryItemOut, LibraryKind } from '$lib/api/v3/types';
	import { parseRef } from '$lib/api/v3/types';
	import { itemSubline, kindInfo } from './kinds';
	import { libraryStore } from './library.svelte';
	import PinnedRefBadge from './PinnedRefBadge.svelte';
	import Highlight from '$lib/components/Highlight.svelte';
	import { matchesAll, searchTokens } from '$lib/utils/search';
	import { cn } from '$lib/utils';

	interface Props {
		kind: LibraryKind;
		multiple?: boolean;
		value: string[];
		filter?: (i: LibraryItemOut) => boolean;
		onchange: (refs: string[]) => void;
		placeholder?: string;
		disabled?: boolean;
		id?: string;
		'aria-label'?: string;
	}

	let { kind, multiple = false, value, filter, onchange, placeholder, disabled = false, id, 'aria-label': ariaLabel }: Props = $props();

	const list = $derived(libraryStore.list(kind));
	const rows = $derived(list.items.filter((i) => !filter || filter(i)));
	const info = $derived(kindInfo(kind));

	let search = $state('');
	const tokens = $derived(searchTokens(search));
	// Every word must match somewhere (name, slug, tags or the subline: harness, version, mode…).
	const shown = $derived(rows.filter((r) => matchesAll([r.name, r.slug, ...r.tags, itemSubline(r)], tokens)));

	function searchKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && search) {
			e.preventDefault();
			e.stopPropagation();
			search = '';
		}
	}

	const slugOf = (ref: string) => parseRef(ref)?.slug ?? ref;
	const selectedSlugs = $derived(new Set(value.map(slugOf)));

	interface Selected {
		ref: string;
		row: LibraryItemOut | undefined;
		stale: boolean;
		missing: boolean;
	}
	const selected = $derived<Selected[]>(
		value.map((ref) => {
			const row = list.bySlug(slugOf(ref));
			const version = parseRef(ref)?.version;
			return { ref, row, stale: !!row && version !== undefined && row.version > version, missing: list.loaded && !row };
		}),
	);

	function toggle(row: LibraryItemOut, on: boolean) {
		const rest = value.filter((r) => slugOf(r) !== row.slug);
		onchange(on ? [...rest, row.pinned_ref] : rest);
	}

	function useLatest(ref: string) {
		const row = list.bySlug(slugOf(ref));
		if (row) onchange(value.map((r) => (r === ref ? row.pinned_ref : r)));
	}

	const selectItems = $derived(rows.map((r) => ({ value: r.pinned_ref, label: r.name || r.slug })));
	const single = $derived(value[0] ?? '');
	const singleIsListed = $derived(rows.some((r) => r.pinned_ref === single));
</script>

{#if list.error}
	<ErrorAlert message={list.error} onretry={() => list.revalidate()} />
{:else if multiple}
	<div class="space-y-2" {id} role="group" aria-label={ariaLabel ?? info.plural}>
		{#if selected.length}
			<ul class="flex flex-wrap gap-1.5" aria-label="Selected">
				{#each selected as s (s.ref)}
					<li class="inline-flex items-center gap-1 rounded-md border border-border bg-card py-0.5 pl-1 pr-0.5">
						<PinnedRefBadge ref={s.ref} {kind} class="border-0" />
						{#if s.stale}
							<button type="button" class="inline-flex min-h-6 cursor-pointer items-center px-1 text-[11px] text-primary hover:underline" onclick={() => useLatest(s.ref)} {disabled}>Use latest</button>
						{/if}
						{#if s.missing}<span class="text-[11px] text-danger-fg">not visible</span>{/if}
						<button
							type="button"
							class="relative cursor-pointer rounded p-0.5 text-muted-foreground after:absolute after:-inset-1 after:content-[''] hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
							aria-label="Remove {s.ref}"
							onclick={() => onchange(value.filter((r) => r !== s.ref))}
							{disabled}
						>
							<X class="size-3.5" aria-hidden="true" />
						</button>
					</li>
				{/each}
			</ul>
		{/if}
		<div class="rounded-md border border-border">
			<div class="border-b border-border p-1.5">
				<Input
					type="search"
					bind:value={search}
					placeholder="Search {info.plural.toLowerCase()}…"
					class="h-8"
					aria-label="Search {info.plural}"
					onkeydown={searchKeydown}
					{disabled}
				/>
			</div>
			{#if list.loading}
				<p class="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground"><LoaderCircle class="size-4 animate-spin" />Loading</p>
			{:else if shown.length === 0}
				<p class="flex flex-wrap items-center gap-x-2 px-3 py-2 text-sm text-muted-foreground">
					No {info.plural.toLowerCase()} match{search ? ` “${search.trim()}”` : ''}.
					{#if search}
						<button type="button" class="inline-flex min-h-6 cursor-pointer items-center text-primary underline underline-offset-2" onclick={() => (search = '')}>Clear the search</button>
					{/if}
				</p>
			{:else}
				<ul class="max-h-64 overflow-y-auto py-1">
					{#each shown as row (row.slug)}
						{@const on = selectedSlugs.has(row.slug)}
						<li>
							<label
								class={cn('flex cursor-pointer items-start gap-2 px-3 py-1.5 transition-colors duration-150 hover:bg-muted/60', on && 'bg-primary/[0.06]')}
								title="{row.name || row.slug} — {row.slug}@{row.version} · {itemSubline(row)}"
							>
								<Checkbox checked={on} onCheckedChange={(c) => toggle(row, c === true)} {disabled} class="mt-0.5" />
								<span class="min-w-0 flex-1">
									<span class={cn('block truncate text-sm', on && 'font-medium')}><Highlight text={row.name || row.slug} {tokens} /></span>
									<span class="block truncate font-mono text-[11px] text-muted-foreground"
										><Highlight text="{row.slug}@{row.version}" {tokens} /> · {itemSubline(row)}</span
									>
								</span>
							</label>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
		<div class="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
			<span role="status" aria-atomic="true">{selected.length} of {rows.length} chosen{tokens.length ? ` · ${shown.length} match` : ''}</span>
			{#if selected.length}
				<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={() => onchange([])} {disabled}>Clear selection</Button>
			{/if}
		</div>
	</div>
{:else}
	<div class="space-y-1">
		<Select.Root
			type="single"
			value={single}
			onValueChange={(v) => onchange(v ? [v] : [])}
			items={selectItems}
			{disabled}
		>
			<Select.Trigger {id} aria-label={ariaLabel ?? info.label} class="min-h-9 w-full py-1.5 data-[size=default]:h-auto">
				{#if single}
					{@const row = list.bySlug(slugOf(single))}
					<span class="flex min-w-0 flex-col items-start text-left">
						<span class="truncate text-sm">{row?.name || slugOf(single)}</span>
						<span class="truncate font-mono text-[11px] text-muted-foreground">{single}</span>
					</span>
				{:else}
					<span class="text-muted-foreground">{list.loading ? 'Loading…' : (placeholder ?? `Choose a ${info.label.toLowerCase()}`)}</span>
				{/if}
			</Select.Trigger>
			<Select.Content>
				{#if single && !singleIsListed}
					<Select.Item value={single} label={single}>
						<span class="flex flex-col">
							<span class="font-mono text-xs">{single}</span>
							<span class="text-[11px] text-muted-foreground">pinned version</span>
						</span>
					</Select.Item>
					<Select.Separator />
				{/if}
				{#each rows as row (row.pinned_ref)}
					<Select.Item value={row.pinned_ref} label={row.name || row.slug}>
						<span class="flex min-w-0 flex-col">
							<span class="truncate text-sm">{row.name || row.slug}</span>
							<span class="truncate font-mono text-[11px] text-muted-foreground">{row.slug}@{row.version} · {itemSubline(row)}</span>
						</span>
					</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
		{#each selected.filter((s) => s.stale || s.missing) as s (s.ref)}
			<p class="text-[11px] text-muted-foreground">
				{#if s.stale}
					v{parseRef(s.ref)?.version} · latest is v{s.row?.version} ·
					<button type="button" class="cursor-pointer text-primary hover:underline" onclick={() => useLatest(s.ref)} {disabled}>Use latest</button>
				{:else}
					<span class="text-danger-fg">{s.ref} is not visible to you (archived or private).</span>
				{/if}
			</p>
		{/each}
	</div>
{/if}
