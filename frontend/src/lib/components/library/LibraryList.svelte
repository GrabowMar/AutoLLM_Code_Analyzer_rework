<script lang="ts">
	/**
	 * LibraryList — one kind's entities (latest version per slug) as a table: name and slug, pinned version,
	 * kind-specific details, tags, owner/visibility and date. Search, tag filter, "mine" and "show archived"
	 * are server-side (`GET /library/{kind}/?q=&tag=&mine=&archived=`), paginated by 50.
	 *
	 * Props: `kind` (API name), `reloadKey` (bump to reload after a write elsewhere on the page).
	 */
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { listLibrary } from '$lib/api/v3/library';
	import type { LibraryApiKind, LibraryItemOut, Page } from '$lib/api/v3/types';
	import { LIBRARY_API_KINDS } from '$lib/api/v3/types';
	import { itemSubline, kindInfo, libraryHref } from './kinds';
	import { libraryStore } from './library.svelte';

	interface Props {
		kind: LibraryApiKind;
		reloadKey?: number;
	}

	let { kind, reloadKey = 0 }: Props = $props();

	const PAGE_SIZE = 50;
	const info = $derived(kindInfo(kind));

	let q = $state('');
	let tag = $state('');
	let mine = $state(false);
	let archived = $state(false);
	let page = $state(1);
	let data = $state<Page<LibraryItemOut> | null>(null);
	let loading = $state(true);
	let error = $state('');
	let invalidations = $state(0);

	$effect(() => libraryStore.onInvalidate((k) => {
		if (!k || k === LIBRARY_API_KINDS[kind]) invalidations++;
	}));

	// Debounced, abortable load whenever a filter changes.
	$effect(() => {
		const params = { q: q.trim(), tag, mine, archived, page, page_size: PAGE_SIZE };
		void reloadKey;
		void invalidations;
		const controller = new AbortController();
		const timer = setTimeout(async () => {
			loading = data === null;
			error = '';
			try {
				data = await listLibrary(kind, params, { signal: controller.signal });
			} catch (e) {
				if (!isAbortError(e)) error = formatApiError(e, `Could not load ${info.plural.toLowerCase()}.`);
			} finally {
				if (!controller.signal.aborted) loading = false;
			}
		}, params.q ? 250 : 0);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1);
	const tags = $derived([...new Set((data?.items ?? []).flatMap((i) => i.tags))].sort());

	function setFilter(fn: () => void) {
		fn();
		page = 1;
	}

	const date = (iso: string) => new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' });
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<TableToolbar density={true}>
		{#snippet summary()}
			{#if data}<span><b class="font-mono tabular-nums">{data.total}</b> {data.total === 1 ? info.label.toLowerCase() : info.plural.toLowerCase()}</span>{/if}
		{/snippet}
		{#snippet controls()}
			<Input
				value={q}
				oninput={(e) => setFilter(() => (q = (e.currentTarget as HTMLInputElement).value))}
				placeholder="Search slug, name…"
				class="h-8 w-48"
				aria-label="Search {info.plural}"
			/>
			{#if tags.length}
				<Select.Simple
					value={tag}
					items={tags.map((t) => ({ value: t, label: t }))}
					placeholder="All tags"
					clearLabel="All tags"
					size="sm"
					class="w-40"
					aria-label="Filter by tag"
					onValueChange={(v) => setFilter(() => (tag = v))}
				/>
			{/if}
			<label class="flex items-center gap-1.5 text-xs">
				<Checkbox checked={mine} onCheckedChange={(c) => setFilter(() => (mine = c === true))} /> Mine
			</label>
			<label class="flex items-center gap-1.5 text-xs">
				<Checkbox checked={archived} onCheckedChange={(c) => setFilter(() => (archived = c === true))} /> Archived
			</label>
		{/snippet}
	</TableToolbar>

	{#if error}
		<div class="p-3"><ErrorAlert message={error} onretry={() => invalidations++} /></div>
	{:else if loading}
		<LoadingState label="loading {info.plural.toLowerCase()}" />
	{:else if !data || data.items.length === 0}
		<EmptyState
			compact
			icon={info.icon}
			title={q || tag || mine ? `No ${info.plural.toLowerCase()} match` : `No ${info.plural.toLowerCase()} yet`}
			description={q || tag || mine ? 'Clear the filters to see everything.' : info.description}
		/>
	{:else}
		<div class="table-frame" {@attach tableFrame}>
			<table>
				<thead>
					<tr>
						<th class="col-sticky">Name</th>
						<th>Version</th>
						<th>Details</th>
						<th>Tags</th>
						<th>Owner</th>
						<th class="num">Updated</th>
					</tr>
				</thead>
				<tbody>
					{#each data.items as item (item.slug)}
						{@const href = libraryHref(kind, item.slug)}
						<tr class="row-link" onclick={rowLink(href)}>
							<td class="col-sticky">
								<a {href} class="font-medium hover:text-primary">{item.name || item.slug}</a>
								<div class="cell-sub font-mono text-[11px] text-muted-foreground">{item.slug}</div>
							</td>
							<td class="font-mono text-xs" title={item.content_hash}>
								v{item.version}<span class="text-muted-foreground"> #{item.content_hash.slice(0, 8)}</span>
							</td>
							<td class="text-xs text-muted-foreground">{itemSubline(item)}</td>
							<td>
								<span class="flex max-w-64 flex-wrap gap-1">
									{#each item.tags.slice(0, 5) as t (t)}
										<span class="rounded-sm border border-border px-1 font-mono text-[11px] text-muted-foreground">{t}</span>
									{/each}
									{#if item.tags.length > 5}<span class="text-[11px] text-muted-foreground">+{item.tags.length - 5}</span>{/if}
								</span>
							</td>
							<td class="text-xs">
								{#if item.is_system}<span class="text-muted-foreground">system</span>{:else}{item.created_by?.username ?? '—'}{/if}
								{#if item.visibility === 'shared'}<span class="text-muted-foreground"> · shared</span>{/if}
								{#if item.is_archived}<span class="text-muted-foreground"> · archived</span>{/if}
							</td>
							<td class="num text-xs">{date(item.created_at)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<PaginationBar page={page} {pages} onGoToPage={(p) => (page = p)} class="border-t border-border" scrollToCard />
	{/if}
</Card.Root>
