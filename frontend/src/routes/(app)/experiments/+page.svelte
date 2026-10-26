<script lang="ts">
	// Every experiment of the user, most recently updated first, with a live
	// capacity strip. Filters and page live in the URL (`?q&status&mode&endpoint&page`).
	// Live: one SSE connection (capacity + up to 10 visible active experiments,
	// whose `tick` patches progress) and a 15 s list refetch while any row is
	// active; the capacity strip also re-polls every 30 s as a fallback.
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import { formatApiError } from '$lib/api/core';
	import { subscribe } from '$lib/api/sse';
	import { getCapacity, type Capacity } from '$lib/api/execution';
	import { listEndpoints, type EndpointSummary } from '$lib/api/inference';
	import {
		archiveExperiment,
		cancelExperiment,
		listExperiments,
		pauseExperiment,
		resumeExperiment,
		type ExperimentListItem,
		type ExperimentListQuery,
		type PaginatedExperiments,
	} from '$lib/api/experiments';
	import type { CapacityEvent, TickEvent } from '$lib/api/types/events';
	import CapacityStrip from '$lib/components/experiments/CapacityStrip.svelte';
	import ExperimentRow, { type RowAction } from '$lib/components/experiments/ExperimentRow.svelte';
	import { isActive } from '$lib/components/experiments/format';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Plus from '@lucide/svelte/icons/plus';
	import Server from '@lucide/svelte/icons/server';

	const PER_PAGE = 25;
	const LIST_POLL_MS = 15_000;
	const CAPACITY_POLL_MS = 30_000;
	const SEARCH_DEBOUNCE_MS = 300;
	const MAX_LIVE_ROWS = 10;

	const STATUS_ITEMS = [
		{ value: 'active', label: 'Active', description: 'Running or paused' },
		{ value: 'draft', label: 'Drafts' },
		{ value: 'finished', label: 'Finished', description: 'Completed or cancelled' },
		{ value: 'archived', label: 'Archived' },
		{ value: 'all', label: 'All' },
	];
	const MODE_ITEMS = [
		{ value: 'all', label: 'All methods' },
		{ value: 'agent', label: 'Agent' },
		{ value: 'scaffolding', label: 'Scaffolding' },
	];

	const params = $derived(page.url.searchParams);
	const q = $derived(params.get('q') ?? '');
	const statusFilter = $derived(params.get('status') ?? 'all');
	const modeFilter = $derived(params.get('mode') ?? 'all');
	const endpointFilter = $derived(params.get('endpoint') ?? 'all');
	const pageNum = $derived(Math.max(1, Math.floor(Number(params.get('page'))) || 1));

	let data = $state<PaginatedExperiments | null>(null);
	let loading = $state(true);
	let error = $state('');
	let refreshError = $state('');
	let seq = 0;

	let capacity = $state<Capacity | null>(null);
	let endpoints = $state<EndpointSummary[] | null>(null);
	let searchInput = $state(untrack(() => q));

	const items = $derived(data?.items ?? []);
	const anyActive = $derived(items.some((e) => isActive(e.status)));
	const filtered = $derived(!!q || statusFilter !== 'all' || modeFilter !== 'all' || endpointFilter !== 'all');

	async function load(silent = false) {
		const mine = ++seq;
		const query: ExperimentListQuery = {
			page: pageNum,
			per_page: PER_PAGE,
			search: q || undefined,
			status: statusFilter === 'all' ? undefined : (statusFilter as ExperimentListQuery['status']),
			mode: modeFilter === 'all' ? undefined : (modeFilter as ExperimentListQuery['mode']),
			endpoint: endpointFilter === 'all' ? undefined : endpointFilter,
			ordering: '-updated_at',
		};
		try {
			const res = await listExperiments(query);
			if (mine !== seq) return;
			data = res;
			error = '';
			refreshError = '';
		} catch (e) {
			if (mine !== seq) return;
			const message = formatApiError(e, 'Could not load experiments.');
			// A failed background refresh keeps the last good list on screen.
			if (silent && data) refreshError = message;
			else error = message;
		} finally {
			if (mine === seq) loading = false;
		}
	}

	// URL → data.
	$effect(() => {
		void q;
		void statusFilter;
		void modeFilter;
		void endpointFilter;
		void pageNum;
		untrack(() => load());
	});

	$effect(() => {
		if (!anyActive) return;
		const timer = setInterval(() => {
			if (!document.hidden) void load(true);
		}, LIST_POLL_MS);
		return () => clearInterval(timer);
	});

	async function loadCapacity() {
		try {
			capacity = await getCapacity();
		} catch {
			// The strip is informational; keep the last value.
		}
	}

	onMount(() => {
		void loadCapacity();
		listEndpoints()
			.then((list) => (endpoints = list))
			.catch(() => (endpoints = []));
		const timer = setInterval(() => {
			if (!document.hidden) void loadCapacity();
		}, CAPACITY_POLL_MS);
		return () => clearInterval(timer);
	});

	// One SSE connection: capacity plus the visible active experiments.
	const liveIds = $derived(
		items
			.filter((e) => isActive(e.status))
			.slice(0, MAX_LIVE_ROWS)
			.map((e) => e.id)
	);
	const channelsKey = $derived(['capacity', ...liveIds.map((id) => `experiment:${id}`)].join(','));

	$effect(() => {
		const channels = channelsKey.split(',');
		return subscribe(channels, (event) => {
			const d = event.data as Record<string, unknown>;
			if (d.type === 'capacity' && capacity) {
				const c = d as unknown as CapacityEvent;
				capacity = {
					...capacity,
					lanes: c.lanes,
					executor: { ...capacity.executor, ...c.executor },
					limits: { ...capacity.limits, warnings: c.warnings },
				};
			} else if (d.type === 'tick' && typeof d.channel === 'string' && data) {
				const id = d.channel.slice('experiment:'.length);
				const tick = d as unknown as TickEvent;
				data = {
					...data,
					items: data.items.map((e) =>
						e.id === id
							? {
									...e,
									progress: tick.counts ? { ...e.progress, ...tick.counts } : e.progress,
									eta_seconds: tick.eta_seconds !== undefined ? tick.eta_seconds : e.eta_seconds,
								}
							: e,
					),
				};
			} else if (d.type === 'state') {
				void load(true);
			}
		});
	});

	function setParams(updates: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [key, value] of Object.entries(updates)) {
			if (value && value !== 'all') url.searchParams.set(key, value);
			else url.searchParams.delete(key);
		}
		if (!('page' in updates)) url.searchParams.delete('page');
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	function onSearch(value: string) {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => setParams({ q: value.trim() || null }), SEARCH_DEBOUNCE_MS);
	}

	const endpointItems = $derived([
		{ value: 'all', label: 'All endpoints' },
		{ value: 'openrouter', label: 'OpenRouter' },
		...(endpoints ?? []).filter((e) => !e.is_system).map((e) => ({ value: e.slug, label: e.name, description: e.slug })),
	]);

	const activeTags = $derived.by((): FilterTag[] => {
		const tags: FilterTag[] = [];
		if (statusFilter !== 'all') {
			tags.push({
				key: 'status',
				label: STATUS_ITEMS.find((i) => i.value === statusFilter)?.label ?? statusFilter,
				onRemove: () => setParams({ status: null }),
			});
		}
		if (modeFilter !== 'all') {
			tags.push({ key: 'mode', label: MODE_ITEMS.find((i) => i.value === modeFilter)?.label ?? modeFilter, onRemove: () => setParams({ mode: null }) });
		}
		if (endpointFilter !== 'all') tags.push({ key: 'endpoint', label: `Endpoint: ${endpointFilter}`, onRemove: () => setParams({ endpoint: null }) });
		return tags;
	});

	const resultsText = $derived(
		data && data.total > 0
			? `Showing ${(data.page - 1) * PER_PAGE + 1}–${Math.min(data.page * PER_PAGE, data.total)} of ${data.total} experiments`
			: ''
	);

	// ── row actions ─────────────────────────────────────────────────
	let busyId = $state<string | null>(null);
	let cancelTarget = $state<ExperimentListItem | null>(null);
	let cancelOpen = $state(false);

	async function act(exp: ExperimentListItem, action: RowAction) {
		if (action === 'cancel') {
			cancelTarget = exp;
			cancelOpen = true;
			return;
		}
		busyId = exp.id;
		try {
			if (action === 'pause') await pauseExperiment(exp.id);
			else if (action === 'resume') await resumeExperiment(exp.id);
			else if (action === 'archive') await archiveExperiment(exp.id);
			toast.success(
				action === 'pause' ? 'Experiment paused' : action === 'resume' ? 'Experiment resumed' : 'Experiment archived'
			);
			await load(true);
		} catch (e) {
			toast.error(formatApiError(e, `Could not ${action} the experiment.`));
		} finally {
			busyId = null;
		}
	}

	async function confirmCancel() {
		const exp = cancelTarget;
		if (!exp) return;
		busyId = exp.id;
		try {
			const result = await cancelExperiment(exp.id, { scope: 'all' });
			toast.success(
				`Cancelled ${result.cancelled_pending} queued run${result.cancelled_pending === 1 ? '' : 's'}` +
					(result.finishing_evaluations ? `; ${result.finishing_evaluations} evaluation(s) finish first` : '')
			);
			cancelOpen = false;
			await load(true);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not cancel the experiment.'));
		} finally {
			busyId = null;
		}
	}
</script>

<div class="space-y-6">
	<PageHeader title="Experiments" description="Designed model comparisons, run in parallel across your endpoints.">
		{#snippet actions()}
			<Button variant="outline" size="sm" href="/endpoints"><Server />Endpoints</Button>
			<Button size="sm" href="/experiments/new"><Plus />New experiment</Button>
		{/snippet}
	</PageHeader>

	{#if capacity}
		<CapacityStrip {capacity} />
	{/if}

	<FilterBar
		searchPlaceholder="Search experiments…"
		bind:searchValue={searchInput}
		onSearchInput={onSearch}
		{activeTags}
		onClearAll={() => {
			searchInput = '';
			setParams({ q: null, status: null, mode: null, endpoint: null });
		}}
		{resultsText}
		page={data?.page}
		pages={data?.pages}
		onGoToPage={(p) => setParams({ page: String(p) })}
	>
		{#snippet filters()}
			<div class="grid gap-3 sm:grid-cols-3">
				<div class="space-y-1">
					<Label class="text-xs" for="exp-filter-status">Status</Label>
					<Select.Simple id="exp-filter-status" size="sm" value={statusFilter} items={STATUS_ITEMS} onValueChange={(v) => setParams({ status: v })} />
				</div>
				<div class="space-y-1">
					<Label class="text-xs" for="exp-filter-mode">Method</Label>
					<Select.Simple id="exp-filter-mode" size="sm" value={modeFilter} items={MODE_ITEMS} onValueChange={(v) => setParams({ mode: v })} />
				</div>
				<div class="space-y-1">
					<Label class="text-xs" for="exp-filter-endpoint">Endpoint</Label>
					<Select.Simple id="exp-filter-endpoint" size="sm" value={endpointFilter} items={endpointItems} onValueChange={(v) => setParams({ endpoint: v })} />
				</div>
			</div>
		{/snippet}
	</FilterBar>

	{#if error}
		<ErrorAlert message={error} onretry={() => load()} />
	{:else if refreshError}
		<ErrorAlert message={`Showing the last list: ${refreshError}`} onretry={() => load()} />
	{/if}

	{#if loading && !data}
		<LoadingState label="loading experiments" />
	{:else if data && items.length === 0}
		{#if filtered}
			<EmptyState icon={FlaskConical} title="No experiments match" description="Try another search or clear the filters." />
		{:else}
			<EmptyState
				icon={FlaskConical}
				title="Create your first experiment"
				description="Pick models, harness presets or prompt profiles and apps; every condition runs on every app, several times, with shared seeds."
			>
				{#snippet action()}
					<Button size="sm" href="/experiments/new"><Plus />New experiment</Button>
					{#if endpoints && endpoints.filter((e) => !e.is_system).length === 0}
						<Button variant="outline" size="sm" href="/endpoints/new"><Server />Add a local endpoint</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{/if}
	{:else if data}
		<ul class="data-list">
			{#each items as exp, i (exp.id)}
				<li class="rise-in" style="--stagger-i: {Math.min(i, 8)}">
					<ExperimentRow experiment={exp} busy={busyId === exp.id} onaction={(action) => act(exp, action)} />
				</li>
			{/each}
		</ul>
	{/if}
</div>

<ConfirmDialog
	bind:open={cancelOpen}
	title="Cancel “{cancelTarget?.name ?? ''}”?"
	description="Queued runs are dropped and running ones are stopped. Runs already being evaluated finish and are graded. You can top up failed and cancelled cells later."
	confirmLabel="Cancel experiment"
	cancelLabel="Keep running"
	destructive
	busy={busyId === cancelTarget?.id}
	onConfirm={confirmCancel}
/>
