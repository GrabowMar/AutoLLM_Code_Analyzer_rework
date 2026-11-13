<script lang="ts">
	// Applications: the agentic app samples (`GET /runs/?kind=app_agentic`, spec 9.5, 10.1). Each row opens
	// `/samples/[id]`; tested, usable rows can be used as slot parents. A pass rate that was not measured is
	// shown as "not measured", never 0.
	//
	// Layout follows the Models page: header stats from `GET /runs/facets/`, a filter toolbar (search, outcome,
	// status, model, task, harness, stack, experiment, usable) with counts, sortable columns, a Columns menu for the
	// setup factors, and every filter / sort in the URL so a view is a link.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { DropdownMenu } from 'bits-ui';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import FilterMenu, { type FilterMenuOption } from '$lib/components/FilterMenu.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { rowLink, tableFrame, TableToolbar, SortableTh } from '$lib/components/table';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Scissors from '@lucide/svelte/icons/scissors';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Flag from '@lucide/svelte/icons/flag';
	import Activity from '@lucide/svelte/icons/activity';
	import Cpu from '@lucide/svelte/icons/cpu';
	import ListChecks from '@lucide/svelte/icons/list-checks';
	import Bot from '@lucide/svelte/icons/bot';
	import Layers from '@lucide/svelte/icons/layers';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import BadgeCheck from '@lucide/svelte/icons/badge-check';
	import Columns3 from '@lucide/svelte/icons/columns-3';
	import Check from '@lucide/svelte/icons/check';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSampleFacets, listSamples } from '$lib/api/v3/samples';
	import type { Page, RunFacetsOut, RunListOut, RunListQuery } from '$lib/api/v3/types';
	import { fmtRate, fmtSeconds, fmtUsd, OUTCOME_TONES, outcomeLabel, shortSha, UseAsParentAction } from '$lib/components/runs';
	import { formatDate } from '$lib/utils/formatters';

	const PAGE_SIZE = 50;

	// ── URL state ───────────────────────────────────────────────────────────────
	const FILTER_KEYS = ['q', 'status', 'outcome', 'model', 'task', 'harness', 'stack', 'study', 'usable'] as const;
	type FilterKey = (typeof FILTER_KEYS)[number];
	const param = (key: string) => page.url.searchParams.get(key) ?? '';
	const filters = $derived(Object.fromEntries(FILTER_KEYS.map((k) => [k, param(k)])) as Record<FilterKey, string>);
	const sort = $derived(param('sort'));
	const currentPage = $derived(Math.max(1, Number(param('page') || '1') || 1));
	const activeCount = $derived(FILTER_KEYS.filter((k) => k !== 'q' && filters[k]).length);

	function setParams(patch: Record<string, string>, { keepPage = false } = {}) {
		const url = new URL(page.url);
		for (const [key, value] of Object.entries(patch)) {
			if (value) url.searchParams.set(key, value);
			else url.searchParams.delete(key);
		}
		if (!keepPage) url.searchParams.delete('page');
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// Search box: local value, pushed to the URL after a short pause.
	let searchInput = $state('');
	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		const q = filters.q;
		untrack(() => {
			if (q !== searchInput.trim()) searchInput = q;
		});
	});
	function onSearch(value: string) {
		searchInput = value;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => setParams({ q: value.trim() }), 300);
	}

	// ── Data ────────────────────────────────────────────────────────────────────
	let data = $state<Page<RunListOut> | null>(null);
	let facets = $state<RunFacetsOut | null>(null);
	let loading = $state(true);
	let error = $state('');
	let reloadKey = $state(0);

	const query = $derived<RunListQuery>({
		kind: 'app_agentic',
		q: filters.q || undefined,
		status: filters.status || undefined,
		outcome: filters.outcome || undefined,
		model: filters.model || undefined,
		task: filters.task || undefined,
		harness: filters.harness || undefined,
		stack: filters.stack || undefined,
		study: filters.study || undefined,
		usable: filters.usable === '' ? undefined : filters.usable === 'true',
	});

	$effect(() => {
		void reloadKey;
		const q = query;
		const controller = new AbortController();
		loading = untrack(() => data === null);
		listSamples({ ...q, sort: sort || undefined, page: currentPage, page_size: PAGE_SIZE }, { signal: controller.signal })
			.then((out) => {
				data = out;
				error = '';
			})
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the applications.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	$effect(() => {
		void reloadKey;
		const q = query;
		const controller = new AbortController();
		getSampleFacets(q, { signal: controller.signal })
			.then((out) => (facets = out))
			.catch(() => {});
		return () => controller.abort();
	});

	// While a visible row is still pending / running, refresh quietly so it moves on when it finishes.
	const inProgress = $derived(data?.items.some((r) => r.status === 'pending' || r.status === 'running') ?? false);
	$effect(() => {
		if (!inProgress) return;
		const timer = setInterval(() => {
			if (document.visibilityState === 'visible') reloadKey += 1;
		}, 10_000);
		return () => clearInterval(timer);
	});

	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);
	const filtered = $derived(activeCount > 0 || !!filters.q);

	// ── Filter options (with counts) ───────────────────────────────────────────────
	const opt = (rows: { value: string; label: string; count: number }[] | undefined, label?: (v: string, l: string) => string) =>
		(rows ?? []).map((r) => ({ value: r.value, label: label ? label(r.value, r.label) : r.label, hint: r.count.toLocaleString() }) satisfies FilterMenuOption);
	const shortModel = (m: string) => m.split('/').pop() ?? m;
	const statusOptions = $derived(opt(facets?.statuses));
	const runningCount = $derived(facets?.statuses.find((s) => s.value === 'running')?.count ?? 0);
	const outcomeOptions = $derived(opt(facets?.outcomes, (v) => outcomeLabel(v)));
	const modelOptions = $derived(opt(facets?.models, (v) => shortModel(v)));
	const taskOptions = $derived(opt(facets?.tasks));
	const harnessOptions = $derived(opt(facets?.harnesses));
	const stackOptions = $derived(opt(facets?.stacks));
	const studyOptions = $derived(opt(facets?.studies));
	const USABLE: FilterMenuOption[] = [
		{ value: 'true', label: 'Usable', hint: 'analysed as data' },
		{ value: 'false', label: 'Not usable', hint: 'infra / excluded' },
	];

	// ── Sorting (column headers) ───────────────────────────────────────────────
	const sortField = $derived(sort.replace(/^-/, '') || 'created');
	const sortDir = $derived<'asc' | 'desc'>(!sort || sort.startsWith('-') ? 'desc' : 'asc');
	function onSort(field: string) {
		// Numbers open high-first; clicking the active column flips it.
		const next = sortField === field ? (sortDir === 'desc' ? field : `-${field}`) : `-${field}`;
		setParams({ sort: next === '-created' ? '' : next });
	}

	// ── Columns (setup factors), remembered per browser ──────────────────────────
	const FACTOR_COLUMNS: { key: string; label: string }[] = [
		{ key: 'harness_preset', label: 'Harness' },
		{ key: 'stack_profile', label: 'Stack' },
		{ key: 'style', label: 'Style' },
		{ key: 'sampling_preset', label: 'Sampling' },
		{ key: 'prompt_set', label: 'Prompt set' },
		{ key: 'prose_variant', label: 'Prose variant' },
		{ key: 'starter_level', label: 'Starter' },
		{ key: 'guidance', label: 'Guidance' },
		{ key: 'auth_mode', label: 'Auth mode' },
		{ key: 'network_policy', label: 'Network' },
		{ key: 'resource_policy', label: 'Resource policy' },
	];
	const DEFAULT_FACTORS = ['harness_preset', 'stack_profile'];
	const COLUMNS_KEY = 'llmlab.apps.columns.v1';
	let factorCols = $state<string[]>([...DEFAULT_FACTORS]);
	$effect(() => {
		try {
			const saved = JSON.parse(localStorage.getItem(COLUMNS_KEY) ?? 'null');
			if (Array.isArray(saved)) factorCols = saved.filter((k) => FACTOR_COLUMNS.some((c) => c.key === k));
		} catch {
			/* storage unavailable: defaults */
		}
	});
	function toggleFactor(key: string) {
		factorCols = factorCols.includes(key) ? factorCols.filter((k) => k !== key) : [...factorCols, key];
		try {
			localStorage.setItem(COLUMNS_KEY, JSON.stringify(factorCols));
		} catch {
			/* not persisted */
		}
	}
	function resetColumns() {
		factorCols = [...DEFAULT_FACTORS];
		try {
			localStorage.removeItem(COLUMNS_KEY);
		} catch {
			/* nothing to clear */
		}
	}
	const shownFactors = $derived(FACTOR_COLUMNS.filter((c) => factorCols.includes(c.key)));
	const columnsCustom = $derived(factorCols.length !== DEFAULT_FACTORS.length || !DEFAULT_FACTORS.every((k) => factorCols.includes(k)));

	// ── Header summary ───────────────────────────────────────────────────────────
	const summary = $derived(facets?.summary);
	const menuItem =
		'flex min-w-0 cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1 text-[13px] outline-none data-[highlighted]:bg-surface-2 data-[disabled]:opacity-50';
</script>

<div class="space-y-4">
	<PageHeader
		title="Applications"
		description="Agentic app samples: open one to see its manifest, files, trajectory and evaluation, or use it as a slot parent."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => (reloadKey += 1)}>
				<RefreshCw class="mr-1.5 h-3.5 w-3.5" />Refresh
			</Button>
			<Button size="sm" href="/slots/new">
				<Scissors class="mr-1.5 h-3.5 w-3.5" />Regenerate a part…
			</Button>
		{/snippet}
		<div class="header-stats">
			{#if summary}
				<span class="header-stat"><b>{summary.total.toLocaleString()}</b> {filtered ? 'matching' : 'apps'}</span>
				<span class="header-stat"><b>{summary.tested.toLocaleString()}</b> tested</span>
				{#if runningCount > 0 || filters.status === 'running'}
					<button
						class="header-stat hover:text-foreground"
						title={filters.status === 'running' ? 'Show every status' : 'Show only running apps'}
						onclick={() => setParams({ status: filters.status === 'running' ? '' : 'running' })}
					>
						<span class="status-dot status-dot-live h-1.5 w-1.5 text-info" aria-hidden="true"></span>
						<b>{runningCount.toLocaleString()}</b> running
					</button>
				{/if}
				<span class="header-stat" title="Mean agent pass rate over apps whose evaluation was measured">
					<b>{summary.mean_pass_rate == null ? '—' : fmtRate(summary.mean_pass_rate)}</b> mean pass rate
					<span class="text-muted-foreground/80">n={summary.measured}</span>
				</span>
				<span class="header-stat"><b>{facets?.models.length ?? 0}</b> models</span>
				<span class="header-stat"><b>{facets?.tasks.length ?? 0}</b> tasks</span>
			{:else}
				<span class="header-stat text-muted-foreground">loading…</span>
			{/if}
		</div>
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => (reloadKey += 1)} />{/if}

	<Card.Root class="gap-0 overflow-clip py-0">
		<!-- Filter toolbar (same pattern as Models): search + one menu per facet, each with counts. -->
		<div class="tb-bar" role="search" aria-label="Search and filter applications">
			<div class="tb-search">
				<Search class="tb-search-icon" aria-hidden="true" />
				<input
					value={searchInput}
					oninput={(e) => onSearch((e.target as HTMLInputElement).value)}
					type="search"
					placeholder="Search task, model or run id…"
					aria-label="Search applications"
					autocomplete="off"
					spellcheck="false"
				/>
				{#if searchInput}
					<button class="tb-search-clear" onclick={() => onSearch('')} aria-label="Clear search"><X class="size-3" /></button>
				{/if}
			</div>
			<div class="tb-menus">
				<FilterMenu label="Status" icon={Activity} value={filters.status} options={statusOptions} onChange={(v) => setParams({ status: v })} anyLabel="Any status" />
				<FilterMenu label="Outcome" icon={Flag} value={filters.outcome} options={outcomeOptions} onChange={(v) => setParams({ outcome: v })} />
				<FilterMenu label="Model" icon={Cpu} value={filters.model} options={modelOptions} onChange={(v) => setParams({ model: v })} searchable anyLabel="All models" />
				<FilterMenu label="Task" icon={ListChecks} value={filters.task} options={taskOptions} onChange={(v) => setParams({ task: v })} anyLabel="All tasks" />
				<FilterMenu label="Harness" icon={Bot} value={filters.harness} options={harnessOptions} onChange={(v) => setParams({ harness: v })} anyLabel="All harnesses" />
				<FilterMenu label="Stack" icon={Layers} value={filters.stack} options={stackOptions} onChange={(v) => setParams({ stack: v })} anyLabel="All stacks" />
				<FilterMenu label="Experiment" icon={FlaskConical} value={filters.study} options={studyOptions} onChange={(v) => setParams({ study: v })} searchable anyLabel="All experiments" />
				<FilterMenu label="Usable" icon={BadgeCheck} value={filters.usable} options={USABLE} onChange={(v) => setParams({ usable: v })} anyLabel="Usable or not" />
				{#if activeCount > 0}
					<button class="tb-clear" onclick={() => setParams(Object.fromEntries(FILTER_KEYS.filter((k) => k !== 'q').map((k) => [k, ''])))}>
						<X class="size-3" aria-hidden="true" />Clear{activeCount > 1 ? ` ${activeCount}` : ''}
					</button>
				{/if}
			</div>
		</div>

		{#if loading}
			<LoadingState label="loading applications" />
		{:else if !data || data.items.length === 0}
			<EmptyState
				compact
				icon={AppWindow}
				title={filtered ? 'No application matches' : 'No applications yet'}
				description={filtered
					? 'Change or clear the filters.'
					: 'Agentic app runs appear here when an experiment (or a “Generate a parent app” run) creates them.'}
			>
				{#snippet action()}
					{#if !filtered}<Button size="sm" href="/experiments/new?mode=app">New experiment</Button>{/if}
				{/snippet}
			</EmptyState>
		{:else}
			<TableToolbar>
				{#snippet summary()}
					{data?.items.length ?? 0} of {(data?.total ?? 0).toLocaleString()}
				{/snippet}
				{#snippet controls()}
					<div class="hidden md:block">
					<DropdownMenu.Root>
						<DropdownMenu.Trigger class="fm-trigger h-7! text-xs" aria-label="Choose setup columns">
							<Columns3 class="size-3.5" aria-hidden="true" />Columns
							{#if columnsCustom}<span class="af-dot" title="Customised"></span>{/if}
						</DropdownMenu.Trigger>
						<DropdownMenu.Portal>
							<DropdownMenu.Content
								align="end"
								sideOffset={6}
								collisionPadding={12}
								class="z-50 w-72 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
							>
								<p class="px-2 pb-1 pt-1.5 text-[11px] text-muted-foreground">Setup factors shown as columns.</p>
								<div class="grid grid-cols-2">
									{#each FACTOR_COLUMNS as col (col.key)}
										<DropdownMenu.CheckboxItem
											class={menuItem}
											checked={factorCols.includes(col.key)}
											onCheckedChange={() => toggleFactor(col.key)}
											closeOnSelect={false}
										>
											{#snippet children({ checked })}
												<span class="af-box" data-on={checked}>{#if checked}<Check class="size-3" />{/if}</span>
												<span class="truncate">{col.label}</span>
											{/snippet}
										</DropdownMenu.CheckboxItem>
									{/each}
								</div>
								<DropdownMenu.Separator class="-mx-1 my-1 h-px bg-border" />
								<DropdownMenu.Item class={menuItem} disabled={!columnsCustom} onSelect={resetColumns} closeOnSelect={false}>
									<RotateCcw class="size-3.5 text-muted-foreground" aria-hidden="true" />Reset to default
								</DropdownMenu.Item>
							</DropdownMenu.Content>
						</DropdownMenu.Portal>
					</DropdownMenu.Root>
					</div>
				{/snippet}
			</TableToolbar>
			<div class="table-frame hidden md:block" {@attach tableFrame}>
				<table class="apps-table">
					<thead>
						<tr>
							<th>Application</th>
							<th>Model</th>
							{#each shownFactors as col (col.key)}<th>{col.label}</th>{/each}
							<th>Outcome</th>
							<SortableTh label="Pass rate" field="pass_rate" sortBy={sortField} {sortDir} {onSort} align="right" help="Agent checks only; infra and flaky checks excluded. Not-measured runs sort last." />
							<SortableTh label="Cost" field="cost" sortBy={sortField} {sortDir} {onSort} align="right" />
							<SortableTh label="Wall time" field="wall" sortBy={sortField} {sortDir} {onSort} align="right" />
							<SortableTh label="Created" field="created" sortBy={sortField} {sortDir} {onSort} />
							<th><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.items as row (row.id)}
							{@const title = row.task_title ?? row.labels?.task ?? row.task ?? 'App'}
							<tr class="row-link" onclick={rowLink(`/samples/${row.id}`)}>
								<td class="max-w-0 w-[22%]">
									<a class="block truncate font-medium hover:text-primary" href={`/samples/${row.id}`} title={row.task ?? title}>{title}</a>
									<span class="cell-sub block font-mono text-[11px] text-muted-foreground">{shortSha(row.id, 8)}</span>
								</td>
								<td class="max-w-[14rem]">
									{#if row.model}
										<span class="block truncate" title={row.model}>{shortModel(row.model)}</span>
										{#if row.model.includes('/')}<span class="cell-sub block truncate text-[11px] text-muted-foreground">{row.model.split('/').slice(0, -1).join('/')}</span>{/if}
									{:else}<span class="text-muted-foreground">—</span>{/if}
								</td>
								{#each shownFactors as col (col.key)}
									<td class="whitespace-nowrap">
										{#if row.labels?.[col.key]}<span class="af-chip">{row.labels[col.key]}</span>{:else}<span class="text-muted-foreground">—</span>{/if}
									</td>
								{/each}
								<td class="whitespace-nowrap">
									{#if row.outcome}
										<StatusBadge status={row.outcome} tones={OUTCOME_TONES} label={outcomeLabel(row.outcome)} />
									{:else}
										<StatusBadge status={row.status} />
									{/if}
									{#if row.phase && row.status === 'running'}<div class="cell-sub">{row.phase}</div>{/if}
								</td>
								<td class="num">
									{#if row.agent_pass_rate === null}
										<span class="text-xs text-muted-foreground">not measured</span>
									{:else}
										<span class="af-rate" data-tone={row.agent_pass_rate >= 0.9 ? 'good' : row.agent_pass_rate >= 0.5 ? 'mid' : 'low'}>
											<span class="af-rate-bar" aria-hidden="true"><span style:width="{Math.max(3, row.agent_pass_rate * 100)}%"></span></span>
											<span class="af-rate-val">{fmtRate(row.agent_pass_rate)}</span>
										</span>
									{/if}
								</td>
								<td class="num">{fmtUsd(row.cost_usd)}</td>
								<td class="num">{fmtSeconds(row.wall_s)}</td>
								<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(row.created_at)}</td>
								<td class="text-right">
									<UseAsParentAction runId={row.id} run={row} hideWhenIneligible />
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<ul class="md:hidden">
				{#each data.items as row (row.id)}
					{@const title = row.task_title ?? row.labels?.task ?? row.task ?? 'App'}
					<li class="data-list-row px-3">
						<a href={`/samples/${row.id}`} class="block min-w-0">
							<div class="flex items-center gap-2">
								<span class="truncate text-sm font-medium">{title}</span>
								<span class="cell-sub block font-mono text-[11px] text-muted-foreground">{shortSha(row.id, 8)}</span>
								<span class="ml-auto shrink-0">
									{#if row.outcome}<StatusBadge status={row.outcome} tones={OUTCOME_TONES} label={outcomeLabel(row.outcome)} />{:else}<StatusBadge status={row.status} />{/if}
								</span>
							</div>
							<p class="mt-0.5 truncate text-xs text-muted-foreground">
								{row.model ? shortModel(row.model) : '—'}{#if row.labels?.harness_preset}{` · ${row.labels.harness_preset}`}{/if}{#if row.labels?.stack_profile}{` · ${row.labels.stack_profile}`}{/if}
							</p>
							<div class="mt-1.5 flex items-center gap-4 text-xs">
								{#if row.agent_pass_rate === null}
									<span class="text-muted-foreground">not measured</span>
								{:else}
									<span class="af-rate" data-tone={row.agent_pass_rate >= 0.9 ? 'good' : row.agent_pass_rate >= 0.5 ? 'mid' : 'low'}>
										<span class="af-rate-bar" aria-hidden="true"><span style:width="{Math.max(3, row.agent_pass_rate * 100)}%"></span></span>
										<span class="af-rate-val font-mono">{fmtRate(row.agent_pass_rate)}</span>
									</span>
								{/if}
								<span class="font-mono text-muted-foreground">{fmtSeconds(row.wall_s)}</span>
								<span class="ml-auto text-muted-foreground">{formatDate(row.created_at)}</span>
							</div>
						</a>
					</li>
				{/each}
			</ul>
			<PaginationBar
				class="border-t border-border"
				resultsText={`${data.items.length} of ${data.total.toLocaleString()}`}
				page={currentPage}
				{pages}
				onGoToPage={(p) => setParams({ page: String(p) }, { keepPage: true })}
				scrollToCard
			/>
		{/if}
	</Card.Root>
</div>

<style>
	/* Filter toolbar — same look as the Models page */
	.af-dot {
		width: 0.375rem;
		height: 0.375rem;
		border-radius: 9999px;
		background: var(--primary);
	}
	.af-box {
		display: inline-flex;
		width: 0.875rem;
		height: 0.875rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 0.1875rem;
		border: 1px solid var(--input);
		color: var(--primary-foreground);
	}
	.af-box[data-on='true'] {
		border-color: var(--primary);
		background: var(--primary);
	}

	/* Rows */
	.apps-table :global(td) {
		vertical-align: middle;
	}
	.af-chip {
		border-radius: 0.25rem;
		border: 1px solid var(--border);
		padding: 0 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		line-height: 1.125rem;
	}
	.af-rate {
		display: inline-flex;
		align-items: center;
		justify-content: flex-end;
		gap: 0.5rem;
	}
	.af-rate-bar {
		display: block;
		width: 3rem;
		height: 4px;
		overflow: hidden;
		border-radius: 9999px;
		background: var(--muted);
	}
	.af-rate-bar > span {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: var(--muted-foreground);
	}
	.af-rate[data-tone='good'] .af-rate-bar > span {
		background: var(--success);
	}
	.af-rate[data-tone='mid'] .af-rate-bar > span {
		background: var(--warning);
	}
	.af-rate[data-tone='low'] .af-rate-bar > span {
		background: var(--destructive);
	}
	.af-rate-val {
		min-width: 3rem;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

</style>
