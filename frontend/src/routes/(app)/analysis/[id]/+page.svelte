<script lang="ts">
	/**
	 * One analysis run: live progress while it runs (per-tool pipeline, phase,
	 * elapsed clocks; SSE with a polling fallback), then its results — Overview
	 * (coverage, attention items, levels as reported, top files/rules, metrics),
	 * Findings (faceted explorer with full per-finding detail and export) and
	 * Tools (state, time, version, errors, raw output of every tool).
	 *
	 * URL: `?tab=overview|findings|tools` and the findings filters
	 * (`tool`, `level`, `cat`, `file`, `rule`, `q`, `sort`, `merge=0`), so views can be linked.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import { Popover } from 'bits-ui';
	import { toast } from 'svelte-sonner';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import Ban from '@lucide/svelte/icons/ban';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Copy from '@lucide/svelte/icons/copy';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatApiError } from '$lib/api/core';
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import {
		cancelRun,
		deleteRun,
		getFindingFacets,
		getRun,
		rerunRun,
		type AnalysisRunDetail,
		type FindingFacets,
		type RunToolResult,
	} from '$lib/api/runs';
	import type { SseEvent } from '$lib/api/sse';
	import FindingsExplorer, { EMPTY_FILTERS, type FindingFilters } from '$lib/components/analysis/FindingsExplorer.svelte';
	import RunOverview from '$lib/components/analysis/RunOverview.svelte';
	import ToolPipeline from '$lib/components/analysis/ToolPipeline.svelte';
	import ToolOutputDialog from '$lib/components/analysis/ToolOutputDialog.svelte';
	import RunLiveStrip from '$lib/components/analysis/RunLiveStrip.svelte';
	import { RunWatcher } from '$lib/components/analysis/live.svelte';
	import { loadTools, toolIndex } from '$lib/components/analysis/tools';
	import {
		RUN_STATUS_HELP,
		elapsedSince,
		formatClock,
		formatSpan,
		isActive,
		resultState,
		runDisplayName,
		runStatusLabel,
		targetLines,
	} from '$lib/components/analysis/format';
	import { formatDate } from '$lib/utils/formatters';
	import type { RunStatus } from '$lib/api/runs';

	const runId = $derived(page.params.id ?? '');

	let run = $state<AnalysisRunDetail | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let tools = $state.raw<Map<string, AnalyzerTool>>(new Map());
	let facets = $state<FindingFacets | null>(null);
	/** Bumped when findings may have changed (a tool finished): explorer and facets refetch. */
	let findingsKey = $state(0);

	// ── URL state ─────────────────────────────────────────────────────
	type Tab = 'overview' | 'findings' | 'tools';
	const TABS: Tab[] = ['overview', 'findings', 'tools'];

	function filtersFromUrl(url: URL): FindingFilters {
		const p = url.searchParams;
		const sort = p.get('sort');
		return {
			tool: p.get('tool') ?? '',
			level: p.get('level') ?? '',
			category: p.get('cat') ?? '',
			file: p.get('file') ?? '',
			rule: p.get('rule') ?? '',
			search: p.get('q') ?? '',
			sort: sort === 'file' || sort === 'tool' || sort === 'rule' ? sort : 'severity',
			merge: p.get('merge') !== '0',
		};
	}

	const urlTab = page.url.searchParams.get('tab');
	let tab = $state<Tab>(TABS.includes(urlTab as Tab) ? (urlTab as Tab) : 'overview');
	let tabChosen = TABS.includes(urlTab as Tab);
	let filters = $state<FindingFilters>(filtersFromUrl(page.url));

	function writeUrl() {
		const url = new URL(page.url);
		const p = url.searchParams;
		const set = (key: string, value: string, fallback = '') => (value && value !== fallback ? p.set(key, value) : p.delete(key));
		set('tab', tab, 'overview');
		set('tool', filters.tool);
		set('level', filters.level);
		set('cat', filters.category);
		set('file', filters.file);
		set('rule', filters.rule);
		set('q', filters.search);
		set('sort', filters.sort, 'severity');
		set('merge', filters.merge ? '' : '0');
		if (url.search === page.url.search) return;
		try {
			replaceState(url, page.state);
		} catch {
			// Before the router is ready (first render): the next change writes it.
		}
	}

	$effect(() => {
		void tab;
		void JSON.stringify(filters);
		untrack(writeUrl);
	});

	function setTab(next: string) {
		tab = (TABS.includes(next as Tab) ? next : 'overview') as Tab;
		tabChosen = true;
	}

	function showFindings(partial: Partial<FindingFilters>) {
		filters = { ...EMPTY_FILTERS, sort: filters.sort, merge: filters.merge, ...partial };
		setTab('findings');
		window.scrollTo({ top: 0 });
	}

	// ── data ──────────────────────────────────────────────────────────
	const results = $derived(new Map((run?.results ?? []).map((r) => [r.tool_slug, r])));
	const active = $derived(isActive(run?.status));

	async function refresh() {
		try {
			const next = await getRun(runId);
			const wasActive = isActive(run?.status);
			const finishedCount = (r: AnalysisRunDetail | null) =>
				(r?.results ?? []).filter((x) => x.status === 'completed' || x.status === 'failed').length;
			const grew = finishedCount(next) !== finishedCount(run) || (wasActive && !isActive(next.status));
			run = next;
			loadError = '';
			if (!tabChosen && isActive(next.status)) tab = 'tools';
			if (grew) findingsKey++;
		} catch (e) {
			if (!run) loadError = formatApiError(e, 'Could not load this analysis run.');
		} finally {
			loading = false;
			watcher.sync();
		}
	}

	async function refreshFacets() {
		try {
			facets = await getFindingFacets(runId);
		} catch {
			// The overview still renders from the run summary.
		}
	}

	$effect(() => {
		void findingsKey;
		if (run) untrack(() => void refreshFacets());
	});

	let refreshTimer: ReturnType<typeof setTimeout> | undefined;
	function refreshSoon(delay = 600) {
		clearTimeout(refreshTimer);
		refreshTimer = setTimeout(() => void watcher.pollNow(), delay);
	}

	function onEvent(e: SseEvent) {
		if (!run) return;
		if (e.type === 'result') {
			const r = e.data.result as RunToolResult | undefined;
			if (r?.tool_slug) {
				const others = run.results.filter((x) => x.tool_slug !== r.tool_slug);
				run.results = [...others, r];
				if (r.status === 'completed' || r.status === 'failed') findingsKey++;
			}
			refreshSoon();
		} else if (e.type === 'progress') {
			run.phase = (e.data.phase as AnalysisRunDetail['phase']) ?? '';
		} else if (e.type === 'status') {
			const status = e.data.status as RunStatus | undefined;
			if (status) run.status = status;
			refreshSoon(isActive(status) ? 600 : 0);
		}
	}

	const watcher = new RunWatcher({
		channels: () => [`analysis:${runId}`],
		active: () => isActive(run?.status),
		onEvent,
		poll: refresh,
	});

	// The page component is reused when navigating between runs (re-run): reload on a new id.
	let loadedId = '';
	$effect(() => {
		const id = runId;
		untrack(() => {
			if (!id || id === loadedId) return;
			loadedId = id;
			run = null;
			facets = null;
			loading = true;
			loadError = '';
			tabChosen = TABS.includes(page.url.searchParams.get('tab') as Tab);
			tab = tabChosen ? (page.url.searchParams.get('tab') as Tab) : 'overview';
			filters = filtersFromUrl(page.url);
			outputOpen = false;
			outputResult = null;
			void refresh();
		});
	});

	onMount(() => {
		watcher.start();
		loadTools()
			.then((list) => (tools = toolIndex(list)))
			.catch(() => {
				// Names fall back to slugs.
			});
	});
	onDestroy(() => {
		watcher.destroy();
		clearTimeout(refreshTimer);
	});

	// ── actions ───────────────────────────────────────────────────────
	let busy = $state<'cancel' | 'rerun' | 'delete' | null>(null);
	let rerunOpen = $state(false);
	let confirmDelete = $state(false);
	let outputOpen = $state(false);
	let outputResult = $state<RunToolResult | null>(null);

	const unmeasuredCount = $derived(
		(run?.tool_slugs ?? []).filter((slug) => {
			const r = results.get(slug);
			const st = resultState(r);
			return r && !st.measured && !st.live;
		}).length
	);

	async function handleCancel() {
		busy = 'cancel';
		try {
			run = await cancelRun(runId);
			toast.success('Run cancelled');
			watcher.sync();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not cancel the run.'));
		} finally {
			busy = null;
		}
	}

	async function handleRerun(onlyUnmeasured: boolean) {
		rerunOpen = false;
		busy = 'rerun';
		try {
			const next = await rerunRun(runId, { only_unmeasured: onlyUnmeasured });
			toast.success(onlyUnmeasured ? `Re-running ${next.tool_slugs.length} tool${next.tool_slugs.length === 1 ? '' : 's'}` : 'Analysis started again');
			await goto(`/analysis/${next.id}?tab=tools`);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not start the re-run.'));
		} finally {
			busy = null;
		}
	}

	async function handleDelete() {
		busy = 'delete';
		try {
			await deleteRun(runId);
			toast.success('Run deleted');
			confirmDelete = false;
			await goto('/analysis');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not delete the run.'));
		} finally {
			busy = null;
		}
	}

	async function copyId() {
		try {
			await navigator.clipboard.writeText(runId);
			toast.success('Run ID copied');
		} catch {
			toast.error('Copy failed');
		}
	}

	const target = $derived(targetLines(run?.target));
	const findingsTotal = $derived(
		active ? (run?.progress?.findings ?? 0) : ((run?.summary?.total_findings as number | undefined) ?? facets?.total ?? 0)
	);
	const toolsDone = $derived((run?.results ?? []).filter((r) => r.status !== 'running' && r.status !== 'pending').length);
</script>

{#snippet nav()}
	<Tabs.List aria-label="Run sections" class="w-max min-w-full overflow-x-visible">
		<Tabs.Trigger value="overview">Overview</Tabs.Trigger>
		<Tabs.Trigger value="findings" class="gap-1.5">
			Findings<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{findingsTotal}</span>
		</Tabs.Trigger>
		<Tabs.Trigger value="tools" class="gap-1.5">
			Tools<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{toolsDone}/{run?.tool_slugs.length ?? 0}</span>
		</Tabs.Trigger>
	</Tabs.List>
{/snippet}

<Tabs.Root value={tab} onValueChange={setTab} class="space-y-4">
	<PageHeader
		title={run ? runDisplayName(run) : 'Analysis run'}
		documentTitle={run ? runDisplayName(run) : `Analysis ${runId.slice(0, 8)}`}
		back={{ href: '/analysis', label: 'Analysis' }}
		nav={run ? nav : undefined}
	>
		{#snippet meta()}
			{#if run}
				<span title={RUN_STATUS_HELP[run.status]}><StatusBadge status={run.status} label={runStatusLabel(run.status)} /></span>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if run}
				{#if active}
					<Button variant="outline" size="sm" onclick={handleCancel} disabled={busy !== null}>
						{#if busy === 'cancel'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Ban />{/if}
						Cancel run
					</Button>
				{:else}
					<Button variant="outline" size="sm" onclick={() => (confirmDelete = true)} disabled={busy !== null} aria-label="Delete run" title="Delete run">
						<Trash2 />
					</Button>
					<Popover.Root bind:open={rerunOpen}>
						<Popover.Trigger disabled={busy !== null}>
							{#snippet child({ props })}
								<Button {...props} size="sm" disabled={busy !== null}>
									{#if busy === 'rerun'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<RotateCcw />{/if}
									Re-run<ChevronDown class="opacity-70" />
								</Button>
							{/snippet}
						</Popover.Trigger>
						<Popover.Portal>
							<Popover.Content align="end" sideOffset={6} class="z-50 w-72 rounded-md border border-border bg-popover p-1 text-sm text-popover-foreground shadow-md">
								<button type="button" class="w-full cursor-pointer rounded px-3 py-2 text-left hover:bg-muted" onclick={() => handleRerun(false)}>
									<span class="block font-medium">All {run.tool_slugs.length} tools</span>
									<span class="block text-xs text-muted-foreground">A fresh run on the same code.</span>
								</button>
								<button
									type="button"
									class="w-full cursor-pointer rounded px-3 py-2 text-left hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
									disabled={unmeasuredCount === 0}
									onclick={() => handleRerun(true)}
								>
									<span class="block font-medium">Only tools that measured nothing ({unmeasuredCount})</span>
									<span class="block text-xs text-muted-foreground">Failed, cancelled or skipped because the app was down; results of the others stand.</span>
								</button>
							</Popover.Content>
						</Popover.Portal>
					</Popover.Root>
				{/if}
			{/if}
		{/snippet}
		{#if run}
			<div class="header-stats">
				{#if run.target.kind === 'job' && run.target.job_id}
					<a href="/applications/{run.target.job_id}" class="header-stat min-w-0 hover:text-foreground" title="Open the app">
						<b class="truncate font-sans">{target.title}</b>{#if target.sub}<span class="truncate">{target.sub}</span>{/if}
					</a>
					<a href="/analysis?job={run.target.job_id}" class="hover:text-foreground hover:underline">all analyses of this app</a>
				{:else}
					<span class="header-stat"><b class="font-sans">{target.title}</b></span>
				{/if}
				{#if run.batch_id}<a href="/analysis?batch={run.batch_id}" class="hover:text-foreground hover:underline">batch</a>{/if}
				<span>created <span class="font-mono">{formatDate(run.created_at)}</span></span>
				{#if active && run.started_at}
					<span>elapsed <span class="font-mono tabular-nums">{formatClock(elapsedSince(run.started_at, watcher.now))}</span></span>
				{:else if run.duration_seconds != null}
					<span>took <span class="font-mono">{formatSpan(run.duration_seconds)}</span></span>
				{/if}
				<button type="button" class="inline-flex cursor-pointer items-center gap-1 font-mono hover:text-foreground" onclick={copyId} title="Copy run ID">
					{runId.slice(0, 8)}<Copy class="size-3" />
				</button>
			</div>
		{/if}
	</PageHeader>

	{#if loading && !run}
		<LoadingState label="loading analysis run" />
	{:else if loadError}
		<ErrorAlert title="Run not found" message={loadError} onretry={refresh} />
	{:else if run}
		{#if active}
			<RunLiveStrip {run} {results} {tools} now={watcher.now} connection={watcher.connection} />
		{/if}
		{#if run.error_message}
			<ErrorAlert title={run.status === 'failed' ? 'The run failed' : 'Note'} message={run.error_message} />
		{/if}

		<Tabs.Content value="overview" class="pt-0">
			<RunOverview
				{run}
				{tools}
				{results}
				{facets}
				onfilter={showFindings}
				ontab={setTab}
				onrerununmeasured={() => handleRerun(true)}
				rerunning={busy === 'rerun'}
			/>
		</Tabs.Content>

		<Tabs.Content value="findings" class="pt-0">
			<FindingsExplorer runId={run.id} {tools} bind:filters refreshKey={findingsKey} live={active} />
		</Tabs.Content>

		<Tabs.Content value="tools" class="pt-0">
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-base">Tools</Card.Title>
					<Card.Description class="text-xs">
						In execution order. A skipped tool measured nothing unless it had no file to read (“Not applicable”).
					</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<ToolPipeline
						{tools}
						slugs={run.tool_slugs}
						{results}
						now={watcher.now}
						onfindings={(slug) => showFindings({ tool: slug })}
						onoutput={(r) => {
							outputResult = r;
							outputOpen = true;
						}}
					/>
				</Card.Content>
			</Card.Root>
		</Tabs.Content>
	{/if}
</Tabs.Root>

<ToolOutputDialog bind:open={outputOpen} {runId} result={outputResult} />

<ConfirmDialog
	bind:open={confirmDelete}
	title="Delete this analysis run?"
	description="Its tool results and findings are removed permanently. Study tables and reports that counted them will change."
	confirmLabel="Delete"
	destructive
	busy={busy === 'delete'}
	onConfirm={handleDelete}
/>
