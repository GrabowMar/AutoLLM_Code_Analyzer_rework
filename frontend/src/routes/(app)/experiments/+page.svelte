<script lang="ts">
	// Every experiment (API: study) of the user, newest first (spec 10.1). Filters and page live in the URL
	// (`?q&status&mode&kind&quick&page`); while any visible row is active the list refetches every 15 s.
	// Quick generations (the "Generate app" page) are hidden unless `?quick=1`; they carry a "Quick" badge.
	//
	// Same toolbar as Applications / Models: search, then one menu per facet with counts. The counts and the
	// header figures come from one wider list call (up to 200 experiments matching the search and the quick
	// toggle, facets ignored), counted client-side; beyond 200 they say so.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import FilterMenu, { type FilterMenuOption } from '$lib/components/FilterMenu.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import { formatApiError } from '$lib/api/core';
	import { listStudies } from '$lib/api/v3/studies';
	import type { Page, StudyKind, StudyListOut, StudyMode, StudyStatus } from '$lib/api/v3/types';
	import { KIND_LABELS, MODE_LABELS, STUDY_STATUS_TONES, formatPct, formatUsd, isActive, spendFraction } from '$lib/components/studies/format';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Lock from '@lucide/svelte/icons/lock';
	import Plus from '@lucide/svelte/icons/plus';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Flag from '@lucide/svelte/icons/flag';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Scissors from '@lucide/svelte/icons/scissors';
	import Scale from '@lucide/svelte/icons/scale';
	import Zap from '@lucide/svelte/icons/zap';

	const PAGE_SIZE = 25;
	const POLL_MS = 15_000;
	const COUNT_LIMIT = 200;

	const params = $derived(page.url.searchParams);
	const q = $derived(params.get('q') ?? '');
	const status = $derived(params.get('status') ?? '');
	const mode = $derived(params.get('mode') ?? '');
	const kind = $derived(params.get('kind') ?? '');
	const showQuick = $derived(params.get('quick') === '1');
	const pageNum = $derived(Math.max(1, Math.floor(Number(params.get('page'))) || 1));

	let data = $state<Page<StudyListOut> | null>(null);
	let all = $state<Page<StudyListOut> | null>(null);
	let loading = $state(true);
	let error = $state('');
	let searchInput = $state(untrack(() => q));
	let seq = 0;

	const items = $derived(data?.items ?? []);
	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);

	async function load(silent = false) {
		const mine = ++seq;
		try {
			const [res, wide] = await Promise.all([
				listStudies({
					q: q || undefined,
					status: (status || undefined) as StudyStatus | undefined,
					mode: (mode || undefined) as StudyMode | undefined,
					kind: (kind || undefined) as StudyKind | undefined,
					include_quick: showQuick || undefined,
					page: pageNum,
					page_size: PAGE_SIZE,
				}),
				listStudies({ q: q || undefined, include_quick: showQuick || undefined, page: 1, page_size: COUNT_LIMIT }).catch(() => null),
			]);
			if (mine !== seq) return;
			data = res;
			if (wide) all = wide;
			error = '';
		} catch (e) {
			if (mine !== seq) return;
			if (!silent || !data) error = formatApiError(e, 'Could not load the experiments.');
		} finally {
			if (mine === seq) loading = false;
		}
	}

	$effect(() => {
		void q;
		void status;
		void mode;
		void kind;
		void showQuick;
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
		searchInput = value;
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => setParam('q', value.trim()), 300);
	}

	// ── Counts (from the wide list) ──────────────────────────────────────────────
	const pool = $derived(all?.items ?? []);
	const capped = $derived((all?.total ?? 0) > COUNT_LIMIT);
	function countBy<K extends keyof StudyListOut>(key: K): Map<string, number> {
		const m = new Map<string, number>();
		for (const s of pool) m.set(String(s[key]), (m.get(String(s[key])) ?? 0) + 1);
		return m;
	}
	const STATUSES: StudyStatus[] = ['draft', 'planned', 'piloting', 'running', 'paused', 'completed', 'stopped', 'cancelled'];
	const statusOptions = $derived.by<FilterMenuOption[]>(() => {
		const c = countBy('status');
		return STATUSES.filter((s) => c.has(s) || s === status).map((s) => ({
			value: s,
			label: s.charAt(0).toUpperCase() + s.slice(1),
			hint: String(c.get(s) ?? 0),
		}));
	});
	const modeOptions = $derived.by<FilterMenuOption[]>(() => {
		const c = countBy('mode');
		return (['app', 'slot'] as StudyMode[]).map((m) => ({ value: m, label: MODE_LABELS[m], hint: String(c.get(m) ?? 0) }));
	});
	const kindOptions = $derived.by<FilterMenuOption[]>(() => {
		const c = countBy('kind');
		return (['exploratory', 'confirmatory'] as StudyKind[]).map((k) => ({ value: k, label: KIND_LABELS[k], hint: String(c.get(k) ?? 0) }));
	});
	const stats = $derived({
		total: all?.total ?? 0,
		active: pool.filter((s) => isActive(s.status)).length,
		completed: pool.filter((s) => s.status === 'completed').length,
		spent: pool.reduce((a, s) => a + (s.spent_usd ?? 0), 0),
		cells: pool.reduce((a, s) => a + (s.cells_done ?? 0), 0),
	});
	const activeCount = $derived([status, mode, kind].filter(Boolean).length);
	const filtered = $derived(activeCount > 0 || !!q);

	const href = (s: StudyListOut) => (s.status === 'draft' ? `/experiments/new?draft=${s.id}` : `/experiments/${s.id}`);

	function relDate(iso: string | null): string {
		if (!iso) return '';
		const d = new Date(iso);
		const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
		if (days <= 0) return 'today';
		if (days === 1) return 'yesterday';
		if (days < 7) return `${days} days ago`;
		return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
	}
</script>

<div class="space-y-4">
	<PageHeader title="Experiments" description="Designed comparisons of models, harnesses and prompts: agentic whole apps or regenerated parts.">
		{#snippet actions()}
			<Button href="/slots/new" size="sm" variant="outline"><Scissors />Slot experiment…</Button>
			<Button href="/experiments/new" size="sm"><Plus />New experiment</Button>
		{/snippet}
		<div class="header-stats">
			{#if all}
				<span class="header-stat"><b>{stats.total.toLocaleString()}</b> experiments</span>
				<span class="header-stat"><b>{stats.active}</b> active</span>
				<span class="header-stat"><b>{stats.completed}</b> completed</span>
				<span class="header-stat"><b>{stats.cells.toLocaleString()}</b> cells run</span>
				<span class="header-stat"><b>{formatUsd(stats.spent)}</b> spent</span>
				{#if capped}<span class="header-stat text-muted-foreground">(counts over the newest {COUNT_LIMIT})</span>{/if}
			{:else}
				<span class="header-stat text-muted-foreground">loading…</span>
			{/if}
		</div>
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => load()} />{/if}

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="tb-bar" role="search" aria-label="Search and filter experiments">
			<div class="tb-search">
				<Search class="tb-search-icon" aria-hidden="true" />
				<input
					value={searchInput}
					oninput={(e) => onSearch((e.target as HTMLInputElement).value)}
					type="search"
					placeholder="Search name, description, hypothesis…"
					aria-label="Search experiments"
					autocomplete="off"
				/>
				{#if searchInput}<button class="tb-search-clear" onclick={() => onSearch('')} aria-label="Clear search"><X class="size-3" /></button>{/if}
			</div>
			<div class="tb-menus">
				<FilterMenu label="Status" icon={Flag} value={status} options={statusOptions} onChange={(v) => setParam('status', v)} anyLabel="Any status" />
				<FilterMenu label="Mode" icon={AppWindow} value={mode} options={modeOptions} onChange={(v) => setParam('mode', v)} anyLabel="Any mode" />
				<FilterMenu label="Kind" icon={Scale} value={kind} options={kindOptions} onChange={(v) => setParam('kind', v)} anyLabel="Any kind" />
				<button
					type="button"
					class="fm-trigger {showQuick ? 'fm-trigger-on' : ''}"
					aria-pressed={showQuick}
					title="Single apps launched from Generate app"
					onclick={() => setParam('quick', showQuick ? '' : '1')}
				>
					<Zap class="size-3.5" aria-hidden="true" />Quick generations
				</button>
				{#if activeCount > 0}
					<button class="tb-clear" onclick={() => goto(`/experiments${showQuick ? '?quick=1' : ''}`, { replaceState: true })}>
						<X class="size-3" aria-hidden="true" />Clear{activeCount > 1 ? ` ${activeCount}` : ''}
					</button>
				{/if}
			</div>
		</div>

		{#if loading && !data}
			<LoadingState label="loading experiments" />
		{:else if items.length === 0}
			<EmptyState
				icon={FlaskConical}
				title={filtered ? 'No experiment matches' : 'No experiments yet'}
				description={filtered ? 'Change or clear the filters.' : 'Design an experiment: pick tasks, models and harnesses, preview every prompt, then start it within a budget.'}
			>
				{#snippet action()}<Button href="/experiments/new" size="sm"><Plus />New experiment</Button>{/snippet}
			</EmptyState>
		{:else}
			<TableToolbar>
				{#snippet summary()}{data?.total ?? 0} experiment{data?.total === 1 ? '' : 's'}{/snippet}
			</TableToolbar>
			<div class="table-frame" {@attach tableFrame}>
				<table class="xp-table">
					<thead>
						<tr>
							<th class="col-sticky">Experiment</th>
							<th>Mode</th>
							<th>Status</th>
							<th>Progress</th>
							<th>Spend / cap</th>
							<th class="num" title="Excluded and infra-pending cells over all cells">Exclusion</th>
						</tr>
					</thead>
					<tbody>
						{#each items as s (s.id)}
							{@const fraction = spendFraction(s.spent_usd, s.cap_usd)}
							{@const progress = s.cells_total ? s.cells_done / s.cells_total : null}
							<tr class="row-link" onclick={rowLink(href(s))}>
								<td class="col-sticky max-w-0 w-[44%] min-w-[13rem]">
									<div class="flex min-w-0 items-center gap-1.5">
										<a href={href(s)} class="truncate font-medium text-foreground hover:text-primary" title={s.name}>{s.name}</a>
										{#if s.kind === 'confirmatory'}<span class="xp-tag xp-tag-conf" title="Confirmatory: the analysis is pre-registered">Confirmatory</span>{/if}
										{#if s.locked}<Lock class="size-3 shrink-0 text-muted-foreground" aria-label="Locked (pre-registered)" />{/if}
										{#if s.is_quick}<span class="xp-tag" title="A single app launched from Generate app">Quick</span>{/if}
									</div>
									<span class="cell-sub block text-xs text-muted-foreground" title={s.created_at ?? ''}>{relDate(s.created_at)}</span>
								</td>
								<td class="whitespace-nowrap">
									<span class="xp-mode">
										{#if s.mode === 'slot'}<Scissors class="size-3.5" aria-hidden="true" />{:else}<AppWindow class="size-3.5" aria-hidden="true" />{/if}
										{s.mode === 'slot' ? 'Slot' : 'App'}
									</span>
								</td>
								<td class="whitespace-nowrap"><StatusBadge status={s.status} tone={STUDY_STATUS_TONES[s.status]} /></td>
								<td>
									{#if progress !== null}
										<span class="xp-meter" title="{s.cells_done} of {s.cells_total} cells done">
											<span class="xp-bar"><span style:width="{Math.max(2, progress * 100)}%" data-full={progress >= 1}></span></span>
											<span class="xp-val">{s.cells_done}/{s.cells_total}</span>
										</span>
									{:else}<span class="text-xs text-muted-foreground">no cells yet</span>{/if}
								</td>
								<td>
									<span class="xp-meter" title={fraction === null ? undefined : `${formatPct(fraction)} of the cap`}>
										<span class="xp-bar xp-bar-spend"><span style:width="{fraction === null ? 0 : Math.max(fraction > 0 ? 2 : 0, Math.min(1, fraction) * 100)}%" data-warn={fraction !== null && fraction >= 0.8}></span></span>
										<span class="xp-val">{formatUsd(s.spent_usd)}<span class="text-muted-foreground">{` / ${s.cap_usd === null ? '—' : formatUsd(s.cap_usd)}`}</span></span>
									</span>
								</td>
								<td class="num">
									{#if s.exclusion_rate === null}<span class="text-muted-foreground">—</span>
									{:else}<span class={s.exclusion_rate >= 0.2 ? 'text-warning-fg' : ''}>{formatPct(s.exclusion_rate, 1)}</span>{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<PaginationBar class="border-t border-border" page={pageNum} {pages} onGoToPage={(p) => setParam('page', String(p))} resultsText="{data?.total ?? 0} experiments" />
		{/if}
	</Card.Root>
</div>

<style>

	.xp-table :global(td) {
		vertical-align: middle;
	}
	.xp-tag {
		flex-shrink: 0;
		border-radius: 9999px;
		border: 1px solid var(--border);
		padding: 0 0.4375rem;
		font-size: 0.625rem;
		font-weight: 500;
		line-height: 1rem;
		color: var(--muted-foreground);
	}
	.xp-tag-conf {
		border-color: color-mix(in oklab, var(--primary) 40%, var(--border));
		color: var(--accent-foreground);
		background: var(--accent);
	}
	.xp-mode {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.8125rem;
		color: var(--muted-foreground);
	}
	.xp-meter {
		display: inline-flex;
		align-items: center;
		gap: 0.625rem;
	}
	.xp-bar {
		display: block;
		width: 4.5rem;
		height: 5px;
		overflow: hidden;
		border-radius: 9999px;
		background: var(--muted);
	}
	.xp-bar > span {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: var(--primary);
	}
	.xp-bar > span[data-full='true'] {
		background: var(--success);
	}
	.xp-bar-spend > span {
		background: color-mix(in oklab, var(--muted-foreground) 65%, transparent);
	}
	.xp-bar-spend > span[data-warn='true'] {
		background: var(--warning);
	}
	.xp-val {
		font-family: var(--font-mono);
		font-size: 0.8125rem;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
</style>
