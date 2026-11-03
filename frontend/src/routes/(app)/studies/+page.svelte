<script lang="ts">
	// Every study of the user, newest first (spec 10.1). Filters and page live in the URL
	// (`?q&status&mode&kind&page`); while any visible row is active the list refetches every 15 s.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import { formatApiError } from '$lib/api/core';
	import { listStudies } from '$lib/api/v3/studies';
	import type { Page, StudyKind, StudyListOut, StudyMode, StudyStatus } from '$lib/api/v3/types';
	import { KIND_LABELS, MODE_LABELS, STUDY_STATUS_TONES, formatPct, formatUsd, isActive, spendFraction } from '$lib/components/studies/format';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Lock from '@lucide/svelte/icons/lock';
	import Plus from '@lucide/svelte/icons/plus';

	const PAGE_SIZE = 25;
	const POLL_MS = 15_000;

	const params = $derived(page.url.searchParams);
	const q = $derived(params.get('q') ?? '');
	const status = $derived(params.get('status') ?? '');
	const mode = $derived(params.get('mode') ?? '');
	const kind = $derived(params.get('kind') ?? '');
	const pageNum = $derived(Math.max(1, Math.floor(Number(params.get('page'))) || 1));

	let data = $state<Page<StudyListOut> | null>(null);
	let loading = $state(true);
	let error = $state('');
	let searchInput = $state(untrack(() => q));
	let seq = 0;

	const items = $derived(data?.items ?? []);
	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);

	async function load(silent = false) {
		const mine = ++seq;
		try {
			const res = await listStudies({
				q: q || undefined,
				status: (status || undefined) as StudyStatus | undefined,
				mode: (mode || undefined) as StudyMode | undefined,
				kind: (kind || undefined) as StudyKind | undefined,
				page: pageNum,
				page_size: PAGE_SIZE,
			});
			if (mine !== seq) return;
			data = res;
			error = '';
		} catch (e) {
			if (mine !== seq) return;
			if (!silent || !data) error = formatApiError(e, 'Could not load the studies.');
		} finally {
			if (mine === seq) loading = false;
		}
	}

	$effect(() => {
		void q;
		void status;
		void mode;
		void kind;
		void pageNum;
		untrack(() => load());
	});

	$effect(() => {
		if (!items.some((s) => isActive(s.status))) return;
		const timer = setInterval(() => {
			if (!document.hidden) void load(true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	function setParam(key: string, value: string) {
		const next = new URL(page.url);
		if (value) next.searchParams.set(key, value);
		else next.searchParams.delete(key);
		if (key !== 'page') next.searchParams.delete('page');
		void goto(next, { replaceState: true, keepFocus: true, noScroll: true });
	}

	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	function onSearch(value: string) {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => setParam('q', value.trim()), 300);
	}

	const STATUS_ITEMS = [
		{ value: '', label: 'Any status' },
		...(['draft', 'planned', 'piloting', 'running', 'paused', 'completed', 'stopped', 'cancelled'] as StudyStatus[]).map((s) => ({
			value: s,
			label: s.charAt(0).toUpperCase() + s.slice(1),
		})),
	];
	const MODE_ITEMS = [
		{ value: '', label: 'Any mode' },
		{ value: 'app', label: MODE_LABELS.app },
		{ value: 'slot', label: MODE_LABELS.slot },
	];
	const KIND_ITEMS = [
		{ value: '', label: 'Any kind' },
		{ value: 'exploratory', label: KIND_LABELS.exploratory },
		{ value: 'confirmatory', label: KIND_LABELS.confirmatory },
	];

	const tags = $derived<FilterTag[]>(
		[
			status && { key: 'status', label: `Status: ${status}`, onRemove: () => setParam('status', '') },
			mode && { key: 'mode', label: `Mode: ${MODE_LABELS[mode as StudyMode] ?? mode}`, onRemove: () => setParam('mode', '') },
			kind && { key: 'kind', label: `Kind: ${KIND_LABELS[kind as StudyKind] ?? kind}`, onRemove: () => setParam('kind', '') },
		].filter(Boolean) as FilterTag[],
	);

	const href = (s: StudyListOut) => (s.status === 'draft' ? `/studies/new?draft=${s.id}` : `/studies/${s.id}`);
</script>

<div class="space-y-4">
	<PageHeader title="Studies" description="Designed comparisons of models, harnesses and prompts: agentic whole apps or regenerated parts.">
		{#snippet actions()}
			<Button href="/slots/new" size="sm" variant="outline">Slot study…</Button>
			<Button href="/studies/new" size="sm"><Plus />New study</Button>
		{/snippet}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => load()} />{/if}

	<Card.Root class="gap-0 overflow-clip py-0">
		<FilterBar
			embedded
			searchPlaceholder="Search name, description, hypothesis"
			bind:searchValue={searchInput}
			onSearchInput={onSearch}
			activeTags={tags}
			onClearAll={() => goto('/studies', { replaceState: true })}
		>
			{#snippet filters()}
				<div class="flex flex-wrap gap-3">
					<div class="space-y-1">
						<Label for="filter-status" class="text-xs">Status</Label>
						<Select.Simple id="filter-status" size="sm" value={status} items={STATUS_ITEMS} onValueChange={(v) => setParam('status', v)} />
					</div>
					<div class="space-y-1">
						<Label for="filter-mode" class="text-xs">Mode</Label>
						<Select.Simple id="filter-mode" size="sm" value={mode} items={MODE_ITEMS} onValueChange={(v) => setParam('mode', v)} />
					</div>
					<div class="space-y-1">
						<Label for="filter-kind" class="text-xs">Kind</Label>
						<Select.Simple id="filter-kind" size="sm" value={kind} items={KIND_ITEMS} onValueChange={(v) => setParam('kind', v)} />
					</div>
				</div>
			{/snippet}
		</FilterBar>
		{#if loading && !data}
			<LoadingState label="loading studies" />
		{:else if items.length === 0}
			<EmptyState
				icon={FlaskConical}
				title={tags.length || q ? 'No study matches' : 'No studies yet'}
				description={tags.length || q ? 'Change or clear the filters.' : 'Design a study: pick tasks, models and harnesses, preview every prompt, then start it within a budget.'}
			>
				{#snippet action()}<Button href="/studies/new" size="sm"><Plus />New study</Button>{/snippet}
			</EmptyState>
		{:else}
			<TableToolbar>
				{#snippet summary()}{data?.total ?? 0} studies{/snippet}
			</TableToolbar>
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th class="col-sticky">Name</th>
							<th>Mode</th>
							<th>Kind</th>
							<th>Status</th>
							<th class="num">Progress</th>
							<th class="num">Spend / cap</th>
							<th class="num" title="Excluded and infra-pending cells over all cells">Exclusion</th>
						</tr>
					</thead>
					<tbody>
						{#each items as s (s.id)}
							{@const fraction = spendFraction(s.spent_usd, s.cap_usd)}
							<tr class="row-link" onclick={rowLink(href(s))}>
								<td class="col-sticky">
									<a href={href(s)} class="font-medium text-foreground hover:underline">{s.name}</a>
									{#if s.locked}<Lock class="ml-1 inline size-3 text-muted-foreground" aria-label="Locked (pre-registered)" />{/if}
									<span class="cell-sub block text-xs text-muted-foreground">{s.created_at ? new Date(s.created_at).toLocaleDateString() : ''}</span>
								</td>
								<td>{MODE_LABELS[s.mode]}</td>
								<td>{KIND_LABELS[s.kind]}</td>
								<td><StatusBadge status={s.status} tone={STUDY_STATUS_TONES[s.status]} /></td>
								<td class="num">{s.cells_total ? `${s.cells_done} / ${s.cells_total}` : '—'}</td>
								<td class="num">
									{formatUsd(s.spent_usd)} / {s.cap_usd === null ? '—' : formatUsd(s.cap_usd)}
									{#if fraction !== null}<span class="cell-sub block text-xs text-muted-foreground">{formatPct(fraction)}</span>{/if}
								</td>
								<td class="num">{formatPct(s.exclusion_rate, 1)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<PaginationBar class="border-t border-border" page={pageNum} {pages} onGoToPage={(p) => setParam('page', String(p))} resultsText="{data?.total ?? 0} studies" />
		{/if}
	</Card.Root>
</div>
