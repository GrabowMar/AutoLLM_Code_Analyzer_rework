<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { TableToolbar } from '$lib/components/table';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import CoverageFunnel from '$lib/components/insights/CoverageFunnel.svelte';
	import CoverageNotice from '$lib/components/insights/CoverageNotice.svelte';
	import SensitivityTable from '$lib/components/insights/SensitivityTable.svelte';
	import { funnelGapCount, funnelGapLines, funnelTotal } from '$lib/components/insights/coverage';
	import { fmtScore, shortHash } from '$lib/components/insights/format';
	import RankingsTable from './RankingsTable.svelte';
	import Trophy from '@lucide/svelte/icons/trophy';
	import Download from '@lucide/svelte/icons/download';
	import Info from '@lucide/svelte/icons/info';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Scale from '@lucide/svelte/icons/scale';
	import X from '@lucide/svelte/icons/x';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getProviders } from '$lib/api/models';
	import { listExperiments, type ExperimentListItem } from '$lib/api/experiments';
	import {
		exportRankingsUrl,
		getRankings,
		getRankingsSensitivity,
		type RankingSortKey,
		type RankingsFilters,
		type RankingsResponse,
		type SensitivityResponse
	} from '$lib/api/rankings';

	type SortDir = 'asc' | 'desc';

	const SORT_ITEMS: { value: RankingSortKey; label: string }[] = [
		{ value: 'empirical', label: 'Empirical (measured)' },
		{ value: 'n_trials', label: 'Analysed trials' },
		{ value: 'composite', label: 'Composite' },
		{ value: 'mss', label: 'MSS (metadata)' },
		{ value: 'benchmark', label: 'Benchmarks' },
		{ value: 'cost_efficiency', label: 'Cost efficiency' },
		{ value: 'accessibility', label: 'Accessibility' },
		{ value: 'adoption', label: 'Adoption' },
		{ value: 'apps', label: 'Apps generated' },
		{ value: 'name', label: 'Name' }
	];
	const PER_PAGE_OPTIONS = [25, 50, 100];
	const MAX_COMPARE = 10;

	// ── State (initialised from the URL; written back on every change) ──
	const initial = page.url.searchParams;
	const param = (key: string) => initial.get(key) ?? '';
	const isSortKey = (v: string): v is RankingSortKey => SORT_ITEMS.some((s) => s.value === v);

	let searchInput = $state(param('q'));
	let search = $state(param('q'));
	let provider = $state(param('provider'));
	let paidOnly = $state(param('paid') === '1');
	let benchOnly = $state(param('bench') === '1');
	let sortBy = $state<RankingSortKey>(
		isSortKey(param('sort')) ? (param('sort') as RankingSortKey) : 'empirical'
	);
	let sortDir = $state<SortDir>(param('dir') === 'asc' ? 'asc' : 'desc');
	let pageNo = $state(Math.max(1, Number(param('page')) || 1));
	let perPage = $state(PER_PAGE_OPTIONS.includes(Number(param('per'))) ? Number(param('per')) : 25);
	let experimentId = $state(param('experiment'));
	let planHash = $state(param('plan'));
	let promptHash = $state(param('prompt_hash'));
	let bundleKey = $state(param('bundle_key'));

	let data = $state<RankingsResponse | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selected = $state<Set<string>>(new Set());

	let experiments = $state<ExperimentListItem[]>([]);
	let providers = $state<string[]>([]);

	let showMethodology = $state(false);
	let sensitivity = $state<SensitivityResponse | null>(null);
	let sensitivityError = $state<string | null>(null);
	let sensitivityLoading = $state(false);

	const filters = $derived<RankingsFilters>({
		search: search || undefined,
		provider: provider || undefined,
		include_free: paidOnly ? false : undefined,
		has_benchmarks: benchOnly || undefined,
		experiment_id: experimentId || undefined,
		plan_hash: planHash || undefined,
		prompt_hash: promptHash || undefined,
		bundle_key: bundleKey || undefined
	});
	const filtersKey = $derived(JSON.stringify(filters));

	// ── URL sync ──
	$effect(() => {
		const usp = new URLSearchParams();
		if (search) usp.set('q', search);
		if (provider) usp.set('provider', provider);
		if (paidOnly) usp.set('paid', '1');
		if (benchOnly) usp.set('bench', '1');
		if (sortBy !== 'empirical') usp.set('sort', sortBy);
		if (sortDir !== 'desc') usp.set('dir', sortDir);
		if (pageNo > 1) usp.set('page', String(pageNo));
		if (perPage !== 25) usp.set('per', String(perPage));
		if (experimentId) usp.set('experiment', experimentId);
		if (planHash) usp.set('plan', planHash);
		if (promptHash) usp.set('prompt_hash', promptHash);
		if (bundleKey) usp.set('bundle_key', bundleKey);
		const next = usp.toString();
		untrack(() => {
			if (next === page.url.searchParams.toString()) return;
			const url = new URL(page.url);
			url.search = next;
			goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		});
	});

	// ── Loading: one request per state change, superseded ones aborted ──
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const c = new AbortController();
		controller = c;
		loading = true;
		error = null;
		try {
			const res = await getRankings(
				{ ...filters, page: pageNo, per_page: perPage, sort_by: sortBy, sort_dir: sortDir },
				{ signal: c.signal }
			);
			if (c.signal.aborted) return;
			data = res;
			// A filter change can leave the page past the end: go to the last page.
			if (res.pagination.total > 0 && pageNo > res.pagination.total_pages) {
				pageNo = res.pagination.total_pages;
			}
		} catch (e) {
			if (c.signal.aborted || isAbortError(e)) return;
			error = formatApiError(e, 'Could not load the rankings.');
		} finally {
			if (controller === c) loading = false;
		}
	}

	$effect(() => {
		void [filtersKey, pageNo, perPage, sortBy, sortDir];
		untrack(load);
	});

	// Sensitivity follows the same filters, fetched only while the panel is open.
	let sensitivityController: AbortController | null = null;
	async function loadSensitivity() {
		sensitivityController?.abort();
		const c = new AbortController();
		sensitivityController = c;
		sensitivityLoading = true;
		sensitivityError = null;
		try {
			const res = await getRankingsSensitivity(filters, { signal: c.signal });
			if (!c.signal.aborted) sensitivity = res;
		} catch (e) {
			if (c.signal.aborted || isAbortError(e)) return;
			sensitivityError = formatApiError(e, 'Could not load the sensitivity analysis.');
		} finally {
			if (sensitivityController === c) sensitivityLoading = false;
		}
	}

	$effect(() => {
		void filtersKey;
		if (showMethodology) untrack(loadSensitivity);
	});

	onMount(() => {
		listExperiments({ per_page: 100, ordering: '-updated_at' })
			.then((res) => (experiments = res.items.filter((e) => e.status !== 'draft')))
			.catch(() => (experiments = []));
		getProviders()
			.then((list) => (providers = [...list].sort((a, b) => a.localeCompare(b))))
			.catch(() => (providers = []));
		return () => {
			clearTimeout(searchTimer);
			controller?.abort();
			sensitivityController?.abort();
		};
	});

	// ── Interactions ──
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearchInput(value: string) {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			if (value.trim() === search) return;
			search = value.trim();
			pageNo = 1;
		}, 300);
	}

	function onSort(key: RankingSortKey) {
		if (sortBy === key) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
		else {
			sortBy = key;
			sortDir = key === 'name' ? 'asc' : 'desc';
		}
		pageNo = 1;
	}

	function toggleSelect(id: string) {
		const next = new Set(selected);
		if (next.has(id)) next.delete(id);
		else if (next.size < MAX_COMPARE) next.add(id);
		selected = next;
	}

	function clearAllFilters() {
		clearTimeout(searchTimer);
		searchInput = '';
		search = '';
		provider = '';
		paidOnly = false;
		benchOnly = false;
		pageNo = 1;
	}

	function clearScope() {
		experimentId = '';
		planHash = '';
		promptHash = '';
		bundleKey = '';
		pageNo = 1;
	}

	function setPlan(hash: string) {
		planHash = hash;
		pageNo = 1;
	}

	const activeTags = $derived.by((): FilterTag[] => {
		const tags: FilterTag[] = [];
		if (search) {
			tags.push({
				key: 'search',
				label: `“${search}”`,
				onRemove: () => {
					searchInput = '';
					search = '';
					pageNo = 1;
				}
			});
		}
		if (provider) {
			tags.push({
				key: 'provider',
				label: `Provider: ${provider}`,
				onRemove: () => {
					provider = '';
					pageNo = 1;
				}
			});
		}
		if (paidOnly) {
			tags.push({
				key: 'paid',
				label: 'Paid only',
				onRemove: () => {
					paidOnly = false;
					pageNo = 1;
				}
			});
		}
		if (benchOnly) {
			tags.push({
				key: 'bench',
				label: 'With benchmarks',
				onRemove: () => {
					benchOnly = false;
					pageNo = 1;
				}
			});
		}
		return tags;
	});

	// ── Derived view data ──
	const comparability = $derived(data?.comparability ?? null);
	const gapCount = $derived(comparability ? funnelGapCount(comparability) : 0);
	const experimentItems = $derived(
		experiments.map((e) => ({ value: e.id, label: e.name, description: e.status }))
	);
	const selectedExperiment = $derived(experiments.find((e) => e.id === experimentId) ?? null);
	const planItems = $derived.by(() => {
		const plans = Object.entries(comparability?.plans ?? {}).sort((a, b) => b[1] - a[1]);
		const items = plans.map(([hash, n]) => ({
			value: hash,
			label: `${shortHash(hash, 12)} · ${n} job${n === 1 ? '' : 's'}`
		}));
		if (planHash && !items.some((i) => i.value === planHash)) {
			items.unshift({ value: planHash, label: `${shortHash(planHash, 12)} · no jobs in scope` });
		}
		return items;
	});
	const providerItems = $derived(providers.map((p) => ({ value: p, label: p })));
	const exportHref = $derived(
		exportRankingsUrl({ ...filters, sort_by: sortBy, sort_dir: sortDir })
	);
	const names = $derived(
		Object.fromEntries((data?.rankings ?? []).map((r) => [r.model_id, r.model_name]))
	);
	const scopeActive = $derived(!!(experimentId || planHash || promptHash || bundleKey));
	const sortLabel = $derived(SORT_ITEMS.find((s) => s.value === sortBy)?.label ?? sortBy);
	const startRank = $derived(((data?.pagination.page ?? pageNo) - 1) * perPage + 1);
	const weightsText = $derived(
		comparability
			? Object.entries(comparability.severity_weights)
					.map(([k, v]) => `${k} ${v}`)
					.join(', ')
			: ''
	);
</script>

<div class="space-y-6">
	<PageHeader
		title="Model Rankings"
		description="Quality measured by this platform's analyses, next to the metadata-based MSS decision aid. Every empirical number comes from one analysis plan."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load} disabled={loading}>
				<RefreshCw class="h-3.5 w-3.5 {loading ? 'animate-spin motion-reduce:animate-none' : ''}" />
				Refresh
			</Button>
			<Button variant="outline" size="sm" href={exportHref} download>
				<Download class="h-3.5 w-3.5" />
				Export CSV
			</Button>
		{/snippet}
	</PageHeader>

	<!-- Scope: which jobs and which analysis plan the empirical columns describe -->
	<Card.Root>
		<Card.Header class="pb-3">
			<div class="flex flex-wrap items-start justify-between gap-2">
				<div class="min-w-0">
					<Card.Title class="flex items-center gap-2 text-base">
						<Scale class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
						Scope & comparability
					</Card.Title>
					<Card.Description>
						Runs are comparable only under one frozen analysis plan. Jobs outside it are counted
						here, never as zero findings.
					</Card.Description>
				</div>
				{#if scopeActive}
					<Button variant="ghost" size="sm" onclick={clearScope}>
						<X class="h-3.5 w-3.5" />Reset scope
					</Button>
				{/if}
			</div>
		</Card.Header>
		<Card.Content class="space-y-4">
			<div class="grid gap-3 sm:grid-cols-2">
				<div class="min-w-0 space-y-1.5">
					<label for="rk-experiment" class="text-xs font-medium">Experiment</label>
					<Select.Simple
						id="rk-experiment"
						value={experimentId}
						items={experimentItems}
						placeholder="All my generation jobs"
						clearLabel="All my generation jobs"
						onValueChange={(v) => {
							experimentId = v;
							pageNo = 1;
						}}
					/>
				</div>
				<div class="min-w-0 space-y-1.5">
					<label for="rk-plan" class="text-xs font-medium">Analysis plan</label>
					<Select.Simple
						id="rk-plan"
						value={planHash}
						items={planItems}
						placeholder="Auto: the plan that measured most jobs"
						clearLabel="Auto: the plan that measured most jobs"
						onValueChange={setPlan}
					/>
				</div>
			</div>
			{#if promptHash || bundleKey}
				<div class="flex flex-wrap items-center gap-2 text-xs">
					<span class="text-muted-foreground">Also limited to</span>
					{#if promptHash}
						<span class="fb-tag">
							prompt {shortHash(promptHash, 12)}
							<button
								type="button"
								class="cursor-pointer rounded p-0.5 hover:bg-background"
								aria-label="Remove the prompt filter"
								onclick={() => {
									promptHash = '';
									pageNo = 1;
								}}><X class="h-3 w-3" /></button
							>
						</span>
					{/if}
					{#if bundleKey}
						<span class="fb-tag">
							bundle {bundleKey}
							<button
								type="button"
								class="cursor-pointer rounded p-0.5 hover:bg-background"
								aria-label="Remove the bundle filter"
								onclick={() => {
									bundleKey = '';
									pageNo = 1;
								}}><X class="h-3 w-3" /></button
							>
						</span>
					{/if}
				</div>
			{/if}

			{#if comparability}
				<CoverageFunnel coverage={comparability} onSelectPlan={setPlan} />
				<p class="text-[11px] text-muted-foreground">
					Scoring <span class="mono-tag">{comparability.scoring_version}</span> · {comparability.density_basis}
					· severity weights {weightsText} · density cap {comparability.density_cap}/KLOC
					(provisional for this scoring version)
				</p>
			{:else if loading && !data}
				<div class="skeleton h-16" aria-hidden="true"></div>
			{/if}
		</Card.Content>
	</Card.Root>

	{#if comparability && gapCount > 0}
		<CoverageNotice
			title="{gapCount} of {funnelTotal(
				comparability
			)} completed jobs are not in the empirical columns{selectedExperiment
				? ` of ${selectedExperiment.name}`
				: ''}"
			items={funnelGapLines(comparability)}
		/>
	{/if}

	{#if data}
		<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
			<div class="kpi-card">
				<div class="kpi-label">Models</div>
				<div class="kpi-value">{data.statistics.total}</div>
				<div class="text-xs text-muted-foreground">
					{data.statistics.unique_providers} providers
				</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">With empirical score</div>
				<div class="kpi-value">{data.statistics.measured_models ?? '—'}</div>
				<div class="text-xs text-muted-foreground">usable static measurement</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Measured jobs</div>
				<div class="kpi-value">{comparability?.measured ?? '—'}</div>
				<div class="text-xs text-muted-foreground">
					{#if comparability}of {funnelTotal(comparability)} completed{:else}no coverage data{/if}
				</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Avg MSS (decision aid)</div>
				<div class="kpi-value">{fmtScore(data.statistics.avg_mss)}</div>
				<div class="text-xs text-muted-foreground">
					{data.statistics.with_benchmarks} with benchmarks
				</div>
			</div>
		</div>
	{/if}

	<FilterBar
		searchPlaceholder="Search models…"
		bind:searchValue={searchInput}
		{onSearchInput}
		{activeTags}
		resultsText={data
			? `${data.statistics.total} models · ${data.statistics.measured_models ?? 0} measured · ${data.statistics.with_benchmarks} with benchmarks`
			: ''}
		page={data?.pagination.page}
		pages={data?.pagination.total_pages}
		onGoToPage={(p) => (pageNo = p)}
		onClearAll={clearAllFilters}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Provider</span>
				<Select.Simple
					size="sm"
					class="w-48"
					aria-label="Filter by provider"
					value={provider}
					items={providerItems}
					placeholder="All providers"
					clearLabel="All providers"
					onValueChange={(v) => {
						provider = v;
						pageNo = 1;
					}}
				/>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Options</span>
				<label class="flex cursor-pointer items-center gap-2 text-xs">
					<Checkbox
						checked={paidOnly}
						onCheckedChange={(v) => {
							paidOnly = v === true;
							pageNo = 1;
						}}
					/>
					Paid models only
				</label>
				<label class="ml-3 flex cursor-pointer items-center gap-2 text-xs">
					<Checkbox
						checked={benchOnly}
						onCheckedChange={(v) => {
							benchOnly = v === true;
							pageNo = 1;
						}}
					/>
					With public benchmarks
				</label>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Sort</span>
				<Select.Simple
					size="sm"
					class="w-52"
					aria-label="Sort by"
					value={sortBy}
					items={SORT_ITEMS}
					onValueChange={(v) => {
						if (isSortKey(v)) {
							sortBy = v;
							pageNo = 1;
						}
					}}
				/>
				<Button
					variant="outline"
					size="sm"
					onclick={() => {
						sortDir = sortDir === 'desc' ? 'asc' : 'desc';
						pageNo = 1;
					}}
					aria-label="Sort direction: {sortDir === 'desc' ? 'descending' : 'ascending'}"
				>
					{sortDir === 'desc' ? 'High → low' : 'Low → high'}
				</Button>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Per page</span>
				<Select.Simple
					size="sm"
					class="w-24"
					aria-label="Rows per page"
					value={String(perPage)}
					items={PER_PAGE_OPTIONS.map((n) => ({ value: String(n), label: String(n) }))}
					onValueChange={(v) => {
						perPage = Number(v);
						pageNo = 1;
					}}
				/>
			</div>
		{/snippet}
	</FilterBar>

	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Content class="p-0 {loading && data ? 'opacity-70 transition-opacity' : ''}">
			{#if error}
				<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
			{:else if loading && !data}
				<LoadingState label="loading rankings" />
			{:else if data && data.rankings.length === 0}
				<EmptyState
					compact
					icon={Trophy}
					title="No models match"
					description="No models match the current filters."
				>
					{#snippet action()}
						<Button variant="outline" size="sm" onclick={clearAllFilters}>Clear filters</Button>
					{/snippet}
				</EmptyState>
			{:else if data}
				<TableToolbar
					selectedCount={selected.size}
					noun="model"
					onClearSelection={() => (selected = new Set())}
				>
					{#snippet summary()}
						<span class="inline-flex items-center gap-1.5">
							<Trophy class="size-3.5" aria-hidden="true" />
							<span
								title="Analysed trials = complete, eligible jobs measured under the reference plan / completed jobs. A warning sign marks models with jobs missing from the numbers; hover it for the reasons."
								>Ranked by <span class="font-medium text-foreground">{sortLabel}</span>{sortDir ===
								'asc'
									? ' (lowest first)'
									: ''}</span
							>
						</span>
					{/snippet}
					{#snippet bulk()}
						<Button
							size="sm"
							class="h-7 px-2.5 text-xs"
							href={selected.size >= 2
								? `/models/compare?models=${[...selected].map(encodeURIComponent).join(',')}`
								: undefined}
							disabled={selected.size < 2}
							title={selected.size < 2 ? 'Pick at least two models' : undefined}
						>
							<GitCompareArrows class="size-3.5" />Compare
						</Button>
						<span class="text-[11px]">up to {MAX_COMPARE}</span>
					{/snippet}
				</TableToolbar>
				<RankingsTable
					rows={data.rankings}
					{startRank}
					{sortBy}
					{sortDir}
					{onSort}
					{selected}
					maxSelected={MAX_COMPARE}
					onToggle={toggleSelect}
				/>
				<PaginationBar
					resultsText="Showing {startRank}–{Math.min(
						startRank + perPage - 1,
						data.pagination.total
					)} of {data.pagination.total} models"
					page={data.pagination.page}
					pages={data.pagination.total_pages}
					onGoToPage={(p) => (pageNo = p)}
					class="border-t border-border"
					scrollToCard
				/>
			{/if}
		</Card.Content>
	</Card.Root>

	<!-- Methodology + sensitivity -->
	<Card.Root>
		<button
			type="button"
			class="flex w-full cursor-pointer items-center gap-3 p-4 text-left text-sm"
			onclick={() => (showMethodology = !showMethodology)}
			aria-expanded={showMethodology}
			aria-controls="rk-methodology"
		>
			<Info class="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
			<span class="font-medium">Methodology & rank sensitivity</span>
			<ChevronDown
				class="ml-auto h-4 w-4 text-muted-foreground transition-transform duration-200 {showMethodology
					? 'rotate-180'
					: ''}"
				aria-hidden="true"
			/>
		</button>
		{#if showMethodology}
			<Card.Content id="rk-methodology" class="space-y-5 pt-0 text-sm text-muted-foreground">
				<section class="space-y-2">
					<h2 class="section-label text-foreground">Measurement — empirical quality</h2>
					<p>
						For each completed job the platform takes its <strong>canonical complete run</strong>
						under the reference plan and reads the stored measurement. The score is the
						<strong>severity-weighted density of counted static issues</strong> (findings
						deduplicated across tools, model-written lines only) per KLOC of measured SLOC, squashed
						to 0–100 and blended 60/40 with the <strong>functional pass rate</strong> of the same jobs
						when it exists. A failed, timed-out or skipped tool makes a job's static numbers missing
						— never "no issues". AI-reviewer findings are opinions: shown in their own column, never
						scored.
					</p>
					<p>
						<strong>Analysed trials</strong> counts only those measured jobs; jobs that are excluded,
						incomplete, failed, still running, unanalysed or analysed under another plan are listed in
						the coverage panel. Treat n=1 as an anecdote.
					</p>
				</section>
				<section class="space-y-2">
					<h2 class="section-label text-foreground">Decision aid — MSS</h2>
					<p>
						The <strong>Model Scoring System</strong> is an opinionated composite of model metadata,
						not a measurement: adoption 35%, public benchmarks 30%, cost efficiency 20%,
						accessibility 15%. <strong>Composite</strong> blends MSS (60%) with empirical quality (40%)
						when a model is measured; unmeasured models keep composite = MSS.
					</p>
				</section>
				<section class="space-y-2">
					<h2 class="section-label text-foreground">
						Sensitivity — do the severity weights matter?
					</h2>
					<p class="text-xs">
						The empirical ranking is recomputed under alternative severity weights (same scope and
						plan as the table). Kendall's τ near 1 means the order barely depends on the weights;
						listed pairs swap places.
					</p>
					{#if sensitivityError}
						<ErrorAlert message={sensitivityError} onretry={loadSensitivity} />
					{:else if sensitivity}
						<div class={sensitivityLoading ? 'opacity-70 transition-opacity' : ''}>
							<SensitivityTable {sensitivity} {names} />
						</div>
					{:else}
						<LoadingState label="loading sensitivity" class="py-6" />
					{/if}
				</section>
				<div class="flex flex-wrap gap-3 pt-1">
					<a
						href="https://www.swebench.com"
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-1 text-xs text-primary hover:underline"
					>
						SWE-bench <ExternalLink class="h-3 w-3" aria-hidden="true" />
					</a>
					<a
						href="https://evalplus.github.io/leaderboard.html"
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-1 text-xs text-primary hover:underline"
					>
						EvalPlus (HumanEval/MBPP+) <ExternalLink class="h-3 w-3" aria-hidden="true" />
					</a>
					<a
						href="https://livebench.ai/"
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-1 text-xs text-primary hover:underline"
					>
						LiveBench <ExternalLink class="h-3 w-3" aria-hidden="true" />
					</a>
				</div>
			</Card.Content>
		{/if}
	</Card.Root>
</div>
