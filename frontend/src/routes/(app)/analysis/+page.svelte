<script lang="ts">
	/**
	 * Analysis runs: every run of the user's analyzer workspace with live
	 * progress (SSE for the active runs on screen, polling fallback), status
	 * filter with counts, server-side search, sorting, batch / app / tool scopes
	 * from the URL (`?batch=`, `?job=`, `?tool=`), selection with bulk cancel and
	 * delete, and per-row re-run.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import Plus from '@lucide/svelte/icons/plus';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Ban from '@lucide/svelte/icons/ban';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { SortableTh, TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import {
		bulkRunAction,
		cancelRun,
		getRunStats,
		listRuns,
		rerunRun,
		type AnalysisRunListItem,
		type ListRunsParams,
		type RunStats,
	} from '$lib/api/runs';
	import type { SseEvent } from '$lib/api/sse';
	import RunStatusCell from '$lib/components/analysis/RunStatusCell.svelte';
	import RunFindingsCell from '$lib/components/analysis/RunFindingsCell.svelte';
	import { RunWatcher } from '$lib/components/analysis/live.svelte';
	import { loadTools, toolIndex } from '$lib/components/analysis/tools';
	import { formatSpan, isActive, runDisplayName, targetLines } from '$lib/components/analysis/format';
	import { formatDate } from '$lib/utils/formatters';

	// ── URL state ─────────────────────────────────────────────────────
	type SortField = 'created' | 'duration' | 'findings' | 'name';
	const SORTS: Record<string, NonNullable<ListRunsParams['sort']>> = {
		'created:desc': 'created_desc',
		'created:asc': 'created_asc',
		'duration:desc': 'duration_desc',
		'findings:desc': 'findings_desc',
		'name:asc': 'name_asc',
	};

	const q = page.url.searchParams;
	let status = $state(q.get('status') ?? '');
	let search = $state(q.get('q') ?? '');
	let searchText = $state(q.get('q') ?? '');
	let batch = $state(q.get('batch') ?? '');
	let job = $state(q.get('job') ?? '');
	let toolFilter = $state(q.get('tool') ?? '');
	let sortBy = $state<SortField>((q.get('sort')?.split(':')[0] as SortField) ?? 'created');
	let sortDir = $state<'asc' | 'desc'>(q.get('sort')?.endsWith(':asc') ? 'asc' : 'desc');
	let pageNo = $state(Number(q.get('page')) || 1);
	const PER_PAGE = 25;

	function writeUrl() {
		const url = new URL(page.url);
		const p = url.searchParams;
		const set = (k: string, v: string) => (v ? p.set(k, v) : p.delete(k));
		set('status', status);
		set('q', search);
		set('batch', batch);
		set('job', job);
		set('tool', toolFilter);
		set('sort', sortBy === 'created' && sortDir === 'desc' ? '' : `${sortBy}:${sortDir}`);
		set('page', pageNo > 1 ? String(pageNo) : '');
		if (url.search === page.url.search) return;
		try {
			replaceState(url, page.state);
		} catch {
			// Before the router is ready: the next change writes it.
		}
	}

	// ── data ──────────────────────────────────────────────────────────
	let runs = $state<AnalysisRunListItem[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let stats = $state<RunStats | null>(null);
	let loading = $state(true);
	let error = $state('');
	let tools = $state.raw<Map<string, AnalyzerTool>>(new Map());
	let ctl: AbortController | null = null;

	function params(): ListRunsParams {
		return {
			page: pageNo,
			per_page: PER_PAGE,
			status: status || undefined,
			search: search || undefined,
			batch_id: batch || undefined,
			generation_job_id: job || undefined,
			tool_slug: toolFilter || undefined,
			sort: SORTS[`${sortBy}:${sortDir}`] ?? 'created_desc',
		};
	}

	async function fetchRuns(quiet = false) {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		if (!quiet) loading = true;
		try {
			const [data, s] = await Promise.all([listRuns(params(), { signal: mine.signal }), getRunStats()]);
			runs = data.items;
			total = data.total;
			pages = data.pages;
			pageNo = data.page;
			stats = s;
			error = '';
			// Drop selections that left the page.
			const ids = new Set(runs.map((r) => r.id));
			selected = new Set([...selected].filter((id) => ids.has(id)));
		} catch (e) {
			if (isAbortError(e) && mine.signal.aborted) return;
			error = formatApiError(e, 'Could not load analysis runs.');
		} finally {
			if (ctl === mine) loading = false;
			watcher.sync();
		}
	}

	const queryKey = $derived(JSON.stringify(params()));
	$effect(() => {
		void queryKey;
		untrack(() => {
			writeUrl();
			void fetchRuns();
		});
	});

	// Debounced search.
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearch(value: string) {
		searchText = value;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			search = value.trim();
			pageNo = 1;
		}, 300);
	}

	// ── live ──────────────────────────────────────────────────────────
	const activeIds = $derived(runs.filter((r) => isActive(r.status)).map((r) => r.id));
	let eventTimer: ReturnType<typeof setTimeout> | undefined;
	function onEvent(_e: SseEvent) {
		clearTimeout(eventTimer);
		eventTimer = setTimeout(() => void watcher.pollNow(), 500);
	}
	const watcher = new RunWatcher({
		channels: () => activeIds.map((id) => `analysis:${id}`),
		active: () => activeIds.length > 0,
		onEvent,
		poll: () => fetchRuns(true),
		slowPollMs: 15_000,
		fastPollMs: 4_000,
	});

	onMount(() => {
		watcher.start();
		loadTools()
			.then((list) => (tools = toolIndex(list)))
			.catch(() => {});
	});
	onDestroy(() => {
		watcher.destroy();
		clearTimeout(eventTimer);
		clearTimeout(searchTimer);
		ctl?.abort();
	});

	// ── filters ───────────────────────────────────────────────────────
	function count(s: string): string | undefined {
		if (!stats) return undefined;
		const n = s === '' ? stats.total : s === 'active' ? stats.active : (stats.by_status[s as keyof RunStats['by_status']] ?? 0);
		return String(n);
	}

	const statusItems = $derived([
		{ value: '', label: 'All', badge: count('') },
		{ value: 'active', label: 'Active', badge: count('active') },
		{ value: 'completed', label: 'Completed', badge: count('completed') },
		{ value: 'partial', label: 'Partial', badge: count('partial') },
		{ value: 'failed', label: 'Failed', badge: count('failed') },
		{ value: 'cancelled', label: 'Cancelled', badge: count('cancelled') },
	]);

	function setStatus(v: string) {
		status = v;
		pageNo = 1;
	}

	function toggleSort(field: string) {
		const f = field as SortField;
		if (sortBy === f) {
			// Only "created" sorts both ways; the others are most-first only.
			if (f === 'created') sortDir = sortDir === 'desc' ? 'asc' : 'desc';
		} else {
			sortBy = f;
			sortDir = f === 'name' ? 'asc' : 'desc';
		}
		pageNo = 1;
	}

	const scopeTags = $derived(
		[
			batch && { key: 'batch', label: `Batch ${batch.slice(0, 8)}`, clear: () => (batch = '') },
			job && { key: 'job', label: `App ${job.slice(0, 8)}`, clear: () => (job = '') },
			toolFilter && { key: 'tool', label: `Tool: ${tools.get(toolFilter)?.name ?? toolFilter}`, clear: () => (toolFilter = '') },
		].filter(Boolean) as { key: string; label: string; clear: () => void }[]
	);
	const anyFilter = $derived(!!(status || search || scopeTags.length));

	function clearAll() {
		status = '';
		search = '';
		searchText = '';
		batch = '';
		job = '';
		toolFilter = '';
		pageNo = 1;
	}

	// ── selection & actions ───────────────────────────────────────────
	let selected = $state(new Set<string>());
	const allSelected = $derived(runs.length > 0 && runs.every((r) => selected.has(r.id)));
	const someSelected = $derived(selected.size > 0 && !allSelected);
	const selectedRuns = $derived(runs.filter((r) => selected.has(r.id)));
	const selectedActive = $derived(selectedRuns.filter((r) => isActive(r.status)).length);

	function toggleAll(on: boolean) {
		selected = on ? new Set(runs.map((r) => r.id)) : new Set();
	}

	function toggleOne(id: string, on: boolean) {
		const next = new Set(selected);
		if (on) next.add(id);
		else next.delete(id);
		selected = next;
	}

	let busyIds = $state(new Set<string>());
	let bulkBusy = $state(false);
	let confirmOpen = $state(false);

	function setBusy(id: string, on: boolean) {
		const next = new Set(busyIds);
		if (on) next.add(id);
		else next.delete(id);
		busyIds = next;
	}

	async function cancelOne(run: AnalysisRunListItem) {
		setBusy(run.id, true);
		try {
			await cancelRun(run.id);
			toast.success('Run cancelled');
			await fetchRuns(true);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not cancel the run.'));
		} finally {
			setBusy(run.id, false);
		}
	}

	async function rerunOne(run: AnalysisRunListItem) {
		setBusy(run.id, true);
		try {
			const next = await rerunRun(run.id);
			toast.success('Analysis started again', {
				action: { label: 'Open', onClick: () => goto(`/analysis/${next.id}`) },
			});
			await fetchRuns(true);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not re-run.'));
		} finally {
			setBusy(run.id, false);
		}
	}

	async function runBulk(action: 'cancel' | 'delete') {
		bulkBusy = true;
		try {
			const res = await bulkRunAction(action, [...selected]);
			const verb = action === 'cancel' ? 'Cancelled' : 'Deleted';
			toast.success(`${verb} ${res.affected} run${res.affected === 1 ? '' : 's'}${res.skipped ? ` · ${res.skipped} skipped (${action === 'delete' ? 'still active' : 'already finished'})` : ''}`);
			confirmOpen = false;
			selected = new Set();
			await fetchRuns(true);
		} catch (e) {
			toast.error(formatApiError(e, `Could not ${action} the runs.`));
		} finally {
			bulkBusy = false;
		}
	}

	function toolName(slug: string): string {
		return tools.get(slug)?.name ?? slug;
	}

	function toolsCell(run: AnalysisRunListItem): { text: string; sub: string } {
		const s = run.summary ?? {};
		if (isActive(run.status) || s.tools_run == null) return { text: String(run.tool_slugs.length), sub: '' };
		const failed = (s.tools_failed as number) ?? 0;
		return { text: `${(s.tools_completed as number) ?? 0}/${run.tool_slugs.length}`, sub: failed ? `${failed} failed` : '' };
	}

	const resultsText = $derived(
		total > 0 ? `${(pageNo - 1) * PER_PAGE + 1}–${Math.min(pageNo * PER_PAGE, total)} of ${total} runs` : ''
	);
</script>

<div class="space-y-4">
	<PageHeader title="Analysis" description="Static and dynamic analysis of generated apps with the tools installed in your analyzer workspace.">
		{#snippet actions()}
			<Button variant="outline" size="sm" href="/analyzers"><ScanSearch />Analyzers</Button>
			<Button size="sm" href="/analysis/create"><Plus />New analysis</Button>
		{/snippet}
		{#if stats}
			<div class="header-stats">
				<span class="header-stat"><b>{stats.total}</b>runs</span>
				{#if stats.active > 0}
					<button type="button" class="header-stat cursor-pointer text-info-fg hover:underline" onclick={() => setStatus('active')}>
						<span class="status-dot status-dot-live h-1.5 w-1.5" aria-hidden="true"></span><b>{stats.active}</b>active
					</button>
				{/if}
				<span class="header-stat"><b>{stats.by_status.completed ?? 0}</b>completed</span>
				{#if stats.by_status.partial}<span class="header-stat"><b>{stats.by_status.partial}</b>partial</span>{/if}
				{#if stats.by_status.failed}<span class="header-stat"><b>{stats.by_status.failed}</b>failed</span>{/if}
			</div>
		{/if}
	</PageHeader>

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="flex flex-wrap items-center gap-2 border-b border-border p-2" role="search" aria-label="Filter analysis runs">
			<div class="relative min-w-52 flex-1">
				<Search class="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
				<Input
					type="search"
					value={searchText}
					oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
					placeholder="Search name, app, model, stack or ID…"
					aria-label="Search analysis runs"
					class="h-9 pl-8"
				/>
			</div>
			<div class="max-w-full overflow-x-auto">
				<Segmented size="sm" aria-label="Status" value={status} onValueChange={setStatus} items={statusItems} />
			</div>
		</div>
		{#if scopeTags.length > 0 || anyFilter}
			<div class="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-1.5 text-xs">
				{#each scopeTags as tag (tag.key)}
					<button type="button" class="inline-flex cursor-pointer items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-primary hover:bg-primary/15" onclick={() => { tag.clear(); pageNo = 1; }} aria-label="Remove filter {tag.label}">
						{tag.label}<X class="size-3" />
					</button>
				{/each}
				{#if anyFilter}<button type="button" class="cursor-pointer text-muted-foreground hover:text-foreground" onclick={clearAll}>Clear all</button>{/if}
			</div>
		{/if}

		<TableToolbar selectedCount={selected.size} noun="run" onClearSelection={() => (selected = new Set())}>
			{#snippet summary()}
				{resultsText || (loading ? 'Loading…' : 'No runs')}{#if activeIds.length > 0}<span class="text-info-fg"> · {activeIds.length} live on this page</span>{/if}
			{/snippet}
			{#snippet bulk()}
				{#if selectedActive > 0}
					<Button variant="outline" size="sm" class="h-7 text-xs" disabled={bulkBusy} onclick={() => runBulk('cancel')}><Ban class="size-3.5" />Cancel {selectedActive}</Button>
				{/if}
				{#if selected.size - selectedActive > 0}
					<Button variant="outline" size="sm" class="h-7 text-xs text-danger-fg" disabled={bulkBusy} onclick={() => (confirmOpen = true)}><Trash2 class="size-3.5" />Delete {selected.size - selectedActive}</Button>
				{/if}
			{/snippet}
		</TableToolbar>

		{#if error}
			<div class="p-3"><ErrorAlert message={error} onretry={() => fetchRuns()} /></div>
		{:else if loading && runs.length === 0}
			<LoadingState label="loading analysis runs" />
		{:else if runs.length === 0}
			{#if anyFilter}
				<EmptyState compact icon={Microscope} title="No runs match these filters">
					{#snippet action()}<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>{/snippet}
				</EmptyState>
			{:else}
				<EmptyState icon={Microscope} title="No analyses yet" description="Pick generated apps and installed tools; findings, metrics and every tool's output land here.">
					{#snippet action()}<Button size="sm" href="/analysis/create"><Plus />New analysis</Button>{/snippet}
				</EmptyState>
			{/if}
		{:else}
			<div class="hidden md:block">
				<div class="table-frame transition-opacity {loading ? 'opacity-60' : ''}" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th class="w-8">
									<Checkbox checked={allSelected} indeterminate={someSelected} onCheckedChange={(v) => toggleAll(!!v)} aria-label="Select all runs on this page" />
								</th>
								<SortableTh label="Analysis" field="name" {sortBy} {sortDir} onSort={toggleSort} ascOnly />
								<th>Status</th>
								<SortableTh label="Findings" field="findings" {sortBy} {sortDir} onSort={toggleSort} align="right" help="Total findings; chips show levels exactly as the tools reported them" />
								<th class="num" title="Tools that produced a result / tools in the run">Tools</th>
								<SortableTh label="Duration" field="duration" {sortBy} {sortDir} onSort={toggleSort} align="right" />
								<SortableTh label="Created" field="created" {sortBy} {sortDir} onSort={toggleSort} />
								<th><span class="sr-only">Actions</span></th>
							</tr>
						</thead>
						<tbody>
							{#each runs as run (run.id)}
								{@const href = `/analysis/${run.id}`}
								{@const t = targetLines(run.target)}
								{@const tc = toolsCell(run)}
								<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
								<tr class="row-link align-top" data-selected={selected.has(run.id)} onclick={rowLink(href)}>
									<td class="w-8">
										<Checkbox checked={selected.has(run.id)} onCheckedChange={(v) => toggleOne(run.id, !!v)} aria-label="Select {runDisplayName(run)}" />
									</td>
									<td class="max-w-0 min-w-[16rem] w-[34%]">
										<a {href} class="block truncate font-medium hover:text-primary" title={runDisplayName(run)}>{runDisplayName(run)}</a>
										{#if run.name !== run.target.label}
											<span class="cell-sub block truncate text-xs text-muted-foreground" title={run.target.label}>{t.title}{t.sub ? ` · ${t.sub}` : ''}</span>
										{:else if t.sub}
											<span class="cell-sub block truncate text-xs text-muted-foreground">{run.target.stack_slug}</span>
										{/if}
									</td>
									<td><RunStatusCell {run} {toolName} /></td>
									<td class="num"><RunFindingsCell {run} /></td>
									<td class="num">
										{tc.text}
										{#if tc.sub}<span class="cell-sub block text-[11px] text-danger-fg">{tc.sub}</span>{/if}
									</td>
									<td class="num text-muted-foreground">{isActive(run.status) ? '—' : formatSpan(run.duration_seconds)}</td>
									<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(run.created_at)}</td>
									<td class="w-px whitespace-nowrap text-right">
										<div class="flex items-center justify-end gap-0.5">
											{#if isActive(run.status)}
												<Button variant="ghost" size="sm" class="size-8 p-0" disabled={busyIds.has(run.id)} onclick={() => cancelOne(run)} title="Cancel run" aria-label="Cancel {runDisplayName(run)}">
													{#if busyIds.has(run.id)}<LoaderCircle class="size-4 animate-spin motion-reduce:animate-none" />{:else}<Ban class="size-4 text-warning-fg" />{/if}
												</Button>
											{:else}
												<span class="row-actions-more">
													<Button variant="ghost" size="sm" class="size-8 p-0" disabled={busyIds.has(run.id) || run.target.kind === 'deleted'} onclick={() => rerunOne(run)} title="Re-run all tools" aria-label="Re-run {runDisplayName(run)}">
														{#if busyIds.has(run.id)}<LoaderCircle class="size-4 animate-spin motion-reduce:animate-none" />{:else}<RotateCcw class="size-4" />{/if}
													</Button>
												</span>
											{/if}
											<Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:text-foreground" {href} title="Open" aria-label="Open {runDisplayName(run)}">
												<ChevronRight class="size-4" />
											</Button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>

			<!-- Phones: one row per run -->
			<ul class="data-list rounded-none border-0 shadow-none md:hidden">
				{#each runs as run (run.id)}
					{@const t = targetLines(run.target)}
					<li>
						<div class="data-list-row flex items-start gap-3" data-active={selected.has(run.id)}>
							<Checkbox class="mt-1" checked={selected.has(run.id)} onCheckedChange={(v) => toggleOne(run.id, !!v)} aria-label="Select {runDisplayName(run)}" />
							<a href="/analysis/{run.id}" class="min-w-0 flex-1 space-y-1.5">
								<span class="block truncate text-sm font-medium">{runDisplayName(run)}</span>
								{#if run.name !== run.target.label}<span class="block truncate text-xs text-muted-foreground">{t.title}{t.sub ? ` · ${t.sub}` : ''}</span>{/if}
								<span class="flex flex-wrap items-start justify-between gap-2">
									<RunStatusCell {run} {toolName} />
									<RunFindingsCell {run} />
								</span>
								<span class="block text-xs text-muted-foreground">{formatDate(run.created_at)}{#if !isActive(run.status) && run.duration_seconds != null} · {formatSpan(run.duration_seconds)}{/if}</span>
							</a>
						</div>
					</li>
				{/each}
			</ul>
			<PaginationBar page={pageNo} pages={pages} onGoToPage={(p) => (pageNo = p)} class="border-t border-border" scrollToCard />
		{/if}
	</Card.Root>
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete {selected.size - selectedActive} analysis run{selected.size - selectedActive === 1 ? '' : 's'}?"
	description="Their tool results and findings are removed permanently; study tables and reports that counted them will change. Active runs in the selection are skipped."
	confirmLabel="Delete"
	destructive
	busy={bulkBusy}
	onConfirm={() => runBulk('delete')}
/>
