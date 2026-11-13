<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page as appPage } from '$app/state';
	import * as Card from '$lib/components/ui/card';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Button } from '$lib/components/ui/button';
	import FilterMenu, { type FilterMenuOption } from '$lib/components/FilterMenu.svelte';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Building2 from '@lucide/svelte/icons/building-2';
	import Rows3 from '@lucide/svelte/icons/rows-3';
	import CircleDollarSign from '@lucide/svelte/icons/circle-dollar-sign';
	import BadgeCheck from '@lucide/svelte/icons/badge-check';
	import Gauge from '@lucide/svelte/icons/gauge';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { getModelFacets } from '$lib/api/models';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import Trophy from '@lucide/svelte/icons/trophy';
	import Download from '@lucide/svelte/icons/download';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { SortableTh, TableToolbar, ScoreCell, rowLink, tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';
	import { modelPathSegment } from '$lib/api/models';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { formatApiError } from '$lib/api/core';
	import {
		getRankings,
		getRankingsSensitivity,
		getRankingSources,
		exportRankingsUrl,
		MSS_TIER_WEIGHTS,
		MSS_WEIGHTS,
		type PriceTier,
		type RankingRow,
		type RankingsResponse,
		type RankingSourcesResponse,
		type SensitivityResponse,
	} from '$lib/api/rankings';
	import FlowList from '$lib/components/rankings/FlowList.svelte';
	import MssBreakdown from '$lib/components/rankings/MssBreakdown.svelte';
	import SourcesTable from '$lib/components/rankings/SourcesTable.svelte';
	import StudySelectionPanel from '$lib/components/rankings/StudySelectionPanel.svelte';
	import {
		COMPONENTS,
		NO_DATA,
		SORT_KEYS,
		backendSort,
		exclusionLabel,
		firstSortDir,
		fmtDate,
		fmtPct,
		fmtUsd,
		tierBand,
		tierLabel,
		weightLabel,
		type RankingSortKey,
	} from '$lib/components/rankings/mss';

	// ── Tabs (?tab=selection | method) ───────────────────────────────────────
	type PageTab = 'leaderboard' | 'selection' | 'method';
	const TABS: { value: PageTab; label: string }[] = [
		{ value: 'leaderboard', label: 'Leaderboard' },
		{ value: 'selection', label: 'Study selection' },
		{ value: 'method', label: 'Method & sources' },
	];
	const tab = $derived.by<PageTab>(() => {
		const t = appPage.url.searchParams.get('tab');
		return t === 'selection' || t === 'method' ? t : 'leaderboard';
	});
	function setTab(next: string) {
		const url = new URL(appPage.url);
		if (next === 'leaderboard') url.searchParams.delete('tab');
		else url.searchParams.set('tab', next);
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// ── Leaderboard ──────────────────────────────────────────────────────────
	let searchQuery = $state('');
	let providerFilter = $state('all');
	let sortBy = $state<RankingSortKey>('mss');
	let sortAsc = $state(false);
	let selectedModels = $state<Set<string>>(new Set());
	let eligibleOnly = $state(true);
	let includeFree = $state(true);
	let capabilityOnly = $state(false);
	let page = $state(1);
	let perPage = $state(25);
	let expanded = $state<Set<string>>(new Set());

	let loading = $state(true);
	let error = $state<string | null>(null);
	let data = $state<RankingsResponse | null>(null);

	let providerList = $state<string[]>([]);
	const providerOptions = $derived<FilterMenuOption[]>(
		[...new Set([...providerList, ...(data?.rankings ?? []).map((r) => r.provider).filter(Boolean)])]
			.sort()
			.map((p) => ({ value: p, label: p })),
	);

	// Search box: local value, applied after a short pause (each change is a ranking request).
	let searchInput = $state('');
	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	function onSearch(value: string) {
		searchInput = value;
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			searchQuery = value.trim();
			page = 1;
		}, 300);
	}

	async function load() {
		loading = true;
		error = null;
		try {
			data = await getRankings({
				page,
				per_page: perPage,
				...backendSort(sortBy, sortAsc ? 'asc' : 'desc'),
				search: searchQuery || undefined,
				provider: providerFilter === 'all' ? undefined : providerFilter,
				eligible_only: eligibleOnly,
				include_free: includeFree,
				has_capability: capabilityOnly || undefined,
			});
		} catch (e) {
			error = formatApiError(e, 'Failed to load rankings');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		getModelFacets()
			.then((f) => (providerList = f.providers.map((p) => p.value)))
			.catch(() => {});
	});

	$effect(() => {
		// Re-load when filters/sort/pagination change.
		void [searchQuery, providerFilter, sortBy, sortAsc, eligibleOnly, includeFree, capabilityOnly, page, perPage];
		load();
	});

	function toggleSort(col: RankingSortKey) {
		if (sortBy === col) sortAsc = !sortAsc;
		else {
			sortBy = col;
			sortAsc = firstSortDir(col) === 'asc';
		}
		page = 1;
	}

	function toggleSelect(slug: string) {
		const next = new Set(selectedModels);
		if (next.has(slug)) next.delete(slug);
		else if (next.size < 10) next.add(slug);
		selectedModels = next;
	}

	function toggleExpand(id: string) {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expanded = next;
	}

	/** The MSS columns: the score, its four components (weights in the header), coverage. */
	const mssCols: { key: RankingSortKey; field: keyof RankingRow; label: string; help: string }[] = [
		...COMPONENTS.map((c) => ({
			key: c.key as RankingSortKey,
			field: c.field,
			label: c.label,
			help: `${weightLabel(MSS_WEIGHTS[c.key])} of MSS. ${c.help}`,
		})),
	];
	// Measured columns with no value anywhere on this page are hidden (named in the toolbar).
	const showEmpirical = $derived((data?.rankings ?? []).some((r) => r.empirical_quality_score != null));
	const showApps = $derived((data?.rankings ?? []).some((r) => r.apps > 0));
	const hiddenCols = $derived([!showEmpirical && 'Empirical', !showApps && 'Apps'].filter(Boolean).join(', '));
	const COLSPAN = $derived(9 + Number(showEmpirical) + Number(showApps));
	const TIERS_CHEAP_FIRST: PriceTier[] = ['budget', 'mid', 'frontier'];

	const colMax = $derived.by(() => {
		const rows = data?.rankings ?? [];
		const max = (f: (r: RankingRow) => number | null | undefined) =>
			rows.reduce<number | null>((m, r) => {
				const v = f(r);
				return v != null && v > 0 && (m == null || v > m) ? v : m;
			}, null);
		// A "best" shared by most of the page (ties at 100.0) isn't a signal — only mark it when ≤ 2 rows hold it.
		const lead = (f: (r: RankingRow) => number | null | undefined) => {
			const m = max(f);
			return m != null && rows.filter((r) => f(r) === m).length <= 2 ? m : null;
		};
		const out: Record<string, number | null> = {
			empirical: lead((r) => r.empirical_quality_score),
			mss: lead((r) => r.mss_score),
			composite: lead((r) => r.composite_score),
		};
		for (const c of mssCols) out[c.key] = lead((r) => r[c.field] as number | null);
		return out;
	});
	const sortLabel = $derived(SORT_KEYS[sortBy]?.label ?? sortBy);
	const sortDir = $derived<'asc' | 'desc'>(sortAsc ? 'asc' : 'desc');

	function pct(v: number | null | undefined): string {
		if (v == null) return '—';
		return `${(v * 100).toFixed(1)}`;
	}

	function empiricalTitle(model: RankingRow): string {
		const v = model.variance;
		const parts = [`${model.n_trials} completed trial${model.n_trials === 1 ? 's — single runs are anecdotes' : 's'}`];
		if (v?.density_per_kloc_mean != null) {
			const spread = v.n_apps != null ? `between apps, n = ${v.n_apps}` : 'between trials';
			parts.push(`weighted code-issue density ${v.density_per_kloc_mean.toFixed(1)}/KLOC` +
				(v.density_per_kloc_stdev != null ? ` (±${v.density_per_kloc_stdev.toFixed(1)} ${spread})` : ''));
		}
		if (model.functional_pass_rate != null) {
			parts.push(`smoke pass rate ${(model.functional_pass_rate * 100).toFixed(0)}%` +
				(v?.smoke_pass_rate_stdev != null ? ` (±${(v.smoke_pass_rate_stdev * 100).toFixed(0)}pp)` : ''));
		}
		return parts.join(' · ');
	}

	/** "Excluded at E6: older than 12 months" for the MSS cell and badge tooltips. */
	function exclusionTitle(model: RankingRow): string {
		if (!model.exclusion) return '';
		const ex = exclusionLabel(model.exclusion.code);
		return `Excluded at ${model.exclusion.step}: ${ex.label}${ex.target ? ` ${ex.target}` : ''}${model.exclusion.detail ? ` (${model.exclusion.detail})` : ''}`;
	}

	/** The MSS cell's text when there is no MSS: excluded models have none by design; eligible ones lack data. */
	function mssMissing(model: RankingRow): string {
		return model.exclusion ? 'excluded' : NO_DATA;
	}

	function indexOnPage(i: number): number {
		return (page - 1) * perPage + i + 1;
	}

	const activeCount = $derived([providerFilter !== 'all', !eligibleOnly, !includeFree, capabilityOnly].filter(Boolean).length);
	const ROWS: FilterMenuOption[] = [
		{ value: '50', label: '50 per page' },
		{ value: '100', label: '100 per page' },
	];

	function clearFilters() {
		providerFilter = 'all';
		eligibleOnly = true;
		includeFree = true;
		capabilityOnly = false;
		page = 1;
	}

	function clearAllFilters() {
		searchInput = '';
		searchQuery = '';
		clearFilters();
	}

	// ── Method & sources (loaded on first open) ──────────────────────────────
	let sources = $state<RankingSourcesResponse | null>(null);
	let sourcesError = $state('');
	let sensitivity = $state<SensitivityResponse | null>(null);
	let sensitivityError = $state(false);
	let methodLoaded = false;

	function loadMethod() {
		methodLoaded = true;
		sourcesError = '';
		getRankingSources()
			.then((s) => (sources = s))
			.catch((e) => (sourcesError = formatApiError(e, 'Could not load the sources.')));
		getRankingsSensitivity()
			.then((s) => (sensitivity = s))
			.catch(() => (sensitivityError = true));
	}

	$effect(() => {
		if (tab === 'method' && !methodLoaded) loadMethod();
	});
</script>

{#snippet nav()}
	<Tabs.List aria-label="Rankings sections" class="w-max min-w-full overflow-x-visible">
		{#each TABS as t (t.value)}
			<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
		{/each}
	</Tabs.List>
{/snippet}

{#snippet help()}
	<div class="space-y-2 text-xs">
		<p>
			<b>MSS</b> (Model Selection Score) ranks models for the study on four components scored 0–100 against fixed bounds:
			{COMPONENTS.map((c) => `${c.label} ${weightLabel(MSS_WEIGHTS[c.key])}`).join(', ')}. A missing component is left out and the
			weights renormalised; it is shown as "no data", never 0.
		</p>
		<p><b>Empirical</b> is measured on this platform; <b>Composite</b> = 60% MSS + 40% empirical where measured.</p>
		<p><b>Study selection</b> picks the study models: stratified by price tier, ranked within each tier.</p>
	</div>
{/snippet}

<Tabs.Root value={tab} onValueChange={(v) => setTab(v)} class="space-y-4">
	<PageHeader
		title="Model Rankings"
		description="The Model Selection Score that picks the study models, next to quality measured on this platform."
		{nav}
		{help}
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load} disabled={loading}>
				<RefreshCw class="mr-1.5 h-3.5 w-3.5 {loading ? 'animate-spin' : ''}" />
				Refresh
			</Button>
			<Button variant="outline" size="sm" href={exportRankingsUrl()} target="_blank" rel="noopener noreferrer">
				<Download class="mr-1.5 h-3.5 w-3.5" />
				Export CSV
			</Button>
		{/snippet}
		{#if !data}
			<div class="header-stats"><span class="header-stat text-muted-foreground">loading…</span></div>
		{:else}
			<div class="header-stats">
				<span class="header-stat"><b>{data.statistics.total}</b> models</span>
				<span class="header-stat" title="Pass every eligibility filter and have popularity and price data"><b>{data.statistics.eligible}</b> eligible</span>
				<span class="header-stat"><b>{data.statistics.with_popularity}</b> with popularity</span>
				<span class="header-stat"><b>{data.statistics.with_capability}</b> with capability</span>
				<span class="header-stat"><b>{data.statistics.unique_vendors}</b> vendors</span>
				<span class="header-stat" title="Mean MSS over the listed models that have one"><b>{data.statistics.avg_mss != null ? (data.statistics.avg_mss * 100).toFixed(1) : '—'}</b> avg MSS</span>
			</div>
		{/if}
	</PageHeader>

	<Tabs.Content value="leaderboard" class="pt-0">
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Content class="p-0">
				<div class="tb-bar" role="search" aria-label="Search and filter rankings">
					<div class="tb-search">
						<Search class="tb-search-icon" aria-hidden="true" />
						<input type="search" value={searchInput} oninput={(e) => onSearch((e.target as HTMLInputElement).value)} placeholder="Search models…" aria-label="Search models" autocomplete="off" />
					</div>
					<div class="tb-menus">
						<FilterMenu
							label="Provider"
							icon={Building2}
							value={providerFilter === 'all' ? '' : providerFilter}
							options={providerOptions}
							onChange={(v) => { providerFilter = v || 'all'; page = 1; }}
							searchable
							anyLabel="All providers"
						/>
						<button type="button" class="fm-trigger {eligibleOnly ? 'fm-trigger-on' : ''}" aria-pressed={eligibleOnly} title="Hide models that fail an eligibility filter (they have no MSS)" onclick={() => { eligibleOnly = !eligibleOnly; page = 1; }}>
							<BadgeCheck class="size-3.5" aria-hidden="true" />Eligible only
						</button>
						<button type="button" class="fm-trigger {capabilityOnly ? 'fm-trigger-on' : ''}" aria-pressed={capabilityOnly} title="Only models with an LMArena WebDev rating" onclick={() => { capabilityOnly = !capabilityOnly; page = 1; }}>
							<Gauge class="size-3.5" aria-hidden="true" />With capability
						</button>
						<button type="button" class="fm-trigger {includeFree ? '' : 'fm-trigger-on'}" aria-pressed={!includeFree} onclick={() => { includeFree = !includeFree; page = 1; }}>
							<CircleDollarSign class="size-3.5" aria-hidden="true" />Paid only
						</button>
						<FilterMenu
							label="Rows"
							icon={Rows3}
							value={perPage === 25 ? '' : String(perPage)}
							options={ROWS}
							onChange={(v) => { perPage = v ? Number(v) : 25; page = 1; }}
							anyLabel="25 per page"
						/>
						{#if activeCount > 0}
							<button class="inline-flex h-9 items-center gap-1 px-2 text-xs text-muted-foreground hover:text-foreground" onclick={clearFilters}>
								<X class="size-3" aria-hidden="true" />Reset{activeCount > 1 ? ` ${activeCount}` : ''}
							</button>
						{/if}
					</div>
				</div>
				{#if error}
					<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
				{:else if loading && !data}
					<div class="divide-y divide-border/60" aria-busy="true" aria-label="Loading rankings">
						{#each Array(8) as _, i (i)}
							<div class="flex items-center gap-4 px-4 py-3">
								<span class="skel h-4 w-4"></span><span class="skel h-4 w-7"></span><span class="skel h-3.5 w-56"></span>
								{#each Array(5) as __, j (j)}<span class="skel ml-auto h-3 w-12"></span>{/each}
							</div>
						{/each}
					</div>
				{:else if data && data.rankings.length === 0}
					<EmptyState
						compact
						icon={Trophy}
						title={eligibleOnly && activeCount <= 1 && !searchQuery ? 'No eligible models yet' : 'No ranked models'}
						description={eligibleOnly && activeCount <= 1 && !searchQuery
							? 'No model has both popularity and price data yet (run manage.py refresh_mss_inputs), or every model fails a filter. Turn off "Eligible only" to see why each was excluded.'
							: 'No models match the current filters.'}
					>
						{#snippet action()}
							{#if eligibleOnly}
								<Button variant="outline" size="sm" onclick={() => { eligibleOnly = false; page = 1; }}>Show excluded models</Button>
							{:else}
								<Button variant="outline" size="sm" onclick={clearAllFilters}>Clear all filters</Button>
							{/if}
						{/snippet}
					</EmptyState>
				{:else if data}
					<TableToolbar selectedCount={selectedModels.size} noun="model" onClearSelection={() => (selectedModels = new Set())}>
						{#snippet summary()}
							<span class="inline-flex items-center gap-1.5">
								<Trophy class="size-3.5" aria-hidden="true" />
								Ranked by <span class="font-medium text-foreground">{sortLabel}</span>{sortAsc ? (sortBy === 'run_cost' ? ' (cheapest first)' : ' (lowest first)') : ''}
								<span class="opacity-50">·</span> missing inputs say "no data", never 0
								{#if hiddenCols}<span class="opacity-50">·</span> <span title="Nothing measured on this platform for the models on this page">{hiddenCols} hidden (nothing measured)</span>{/if}
							</span>
						{/snippet}
						{#snippet bulk()}
							<Button
								size="sm"
								class="h-7 px-2.5 text-xs"
								href={selectedModels.size >= 2 ? `/models/compare?models=${[...selectedModels].join(',')}` : undefined}
								disabled={selectedModels.size < 2}
								title={selectedModels.size < 2 ? 'Pick at least two models' : undefined}
							>
								<GitCompareArrows class="size-3.5" />Compare
							</Button>
							<span class="text-[11px]">up to 10</span>
						{/snippet}
					</TableToolbar>

					<!-- Desktop table (768px+); dimmed while the next sort / filter / page loads. -->
					<div class="table-frame mss-frame hidden transition-opacity duration-150 md:block {loading ? 'opacity-60' : ''}" aria-busy={loading} {@attach tableFrame}>
						<table>
							<thead>
								<tr class="[&>th]:pb-1 [&>th]:pt-2">
									<th class="col-sticky" rowspan="2">
										<div class="flex items-center gap-3">
											<span class="w-4"></span>
											<span class="w-8">Rank</span>
											<span>Model</span>
										</div>
									</th>
									{#if showEmpirical}<th class="border-l border-border text-center" title="Measured on this platform">Measured</th>{/if}
									<th class="border-l border-border text-center" colspan={mssCols.length + 3} title="Model Selection Score v2: a pre-registered weighted sum on fixed bounds, used to pick the study models">Model Selection Score <span class="normal-case tracking-normal text-muted-foreground">· {mssCols.map((c) => `${c.label.toLowerCase()} ${weightLabel(MSS_WEIGHTS[c.key as keyof typeof MSS_WEIGHTS])}`).join(' · ')}</span></th>
									<th class="border-l border-border text-center" colspan={showApps ? 2 : 1}>Blend</th>
								</tr>
								<tr>
									{#if showEmpirical}<SortableTh label="Empirical" field="empirical" sortBy={sortBy} {sortDir} onSort={(f) => toggleSort(f as RankingSortKey)} align="right" help="Severity-weighted code issues per KLOC (no dependency advisories, DAST or test code) blended with the smoke pass rate" class="border-l border-border" />{/if}
									<SortableTh label="MSS" field="mss" sortBy={sortBy} {sortDir} onSort={(f) => toggleSort(f as RankingSortKey)} align="right" help="Weighted mean of the components present (weights renormalised over them); excluded models have none" class="border-l border-border" />
									{#each mssCols as col (col.key)}
										<SortableTh label={col.label} field={col.key} sortBy={sortBy} {sortDir} onSort={(f) => toggleSort(f as RankingSortKey)} align="right" help={col.help} />
									{/each}
									<SortableTh label="Cov." field="coverage" sortBy={sortBy} {sortDir} onSort={(f) => toggleSort(f as RankingSortKey)} align="right" help="Coverage: share of the MSS weight whose input is present" />
									<SortableTh label="$/run" field="run_cost" sortBy={sortBy} {sortDir} onSort={(f) => toggleSort(f as RankingSortKey)} align="right" help="Cost of one reference OpenCode run at first-party list prices; the price tier is under it" />
									<SortableTh label="Composite" field="composite" sortBy={sortBy} {sortDir} onSort={(f) => toggleSort(f as RankingSortKey)} align="right" help="MSS 60% + empirical 40% where measured; otherwise equal to MSS; none without an MSS" class="border-l border-border" />
									{#if showApps}<SortableTh label="Apps" field="apps" sortBy={sortBy} {sortDir} onSort={(f) => toggleSort(f as RankingSortKey)} align="right" help="Applications generated with this model here" />{/if}
								</tr>
							</thead>
							<tbody>
								{#each data.rankings as model, i (model.model_id)}
									{@const rank = indexOnPage(i)}
									{@const selected = selectedModels.has(model.model_id)}
									{@const open = expanded.has(model.model_id)}
									{@const href = `/models/${modelPathSegment(model.model_id)}`}
									<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
									<tr class="row-link" data-selected={selected} onclick={rowLink(href)}>
										<td class="col-sticky">
											<div class="flex items-center gap-3">
												<Checkbox
													checked={selected}
													disabled={!selected && selectedModels.size >= 10}
													onCheckedChange={() => toggleSelect(model.model_id)}
													aria-label="Select {model.model_name} for comparison"
												/>
												<span
													class="inline-flex h-6 w-8 items-center justify-center rounded-sm font-mono text-xs tabular-nums {rank <= 3 && !sortAsc ? 'bg-primary/12 font-semibold text-primary' : 'text-muted-foreground'}"
												>{rank}</span>
												<button
													type="button"
													class="-ml-1 inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"
													aria-expanded={open}
													aria-controls="mss-bd-{i}"
													aria-label="{open ? 'Hide' : 'Show'} the MSS inputs of {model.model_name}"
													onclick={() => toggleExpand(model.model_id)}
												>
													<ChevronRight class="size-3.5 transition-transform duration-150 {open ? 'rotate-90' : ''}" />
												</button>
												<div class="min-w-0 max-w-[220px]">
													<a {href} class="block truncate font-medium hover:text-primary" title={model.model_name}>{shortModelName(model.model_name)}</a>
													<div class="cell-sub flex items-center gap-1.5 text-[11px] text-muted-foreground">
														<span class="truncate">{model.vendor ?? (modelNamePrefix(model.model_name) || model.provider)}</span>
														{#if model.price_tier}<span class="shrink-0 rounded-sm border border-border px-1">{tierLabel(model.price_tier)}</span>{/if}
														{#if model.exclusion}
															<span class="shrink-0 rounded-sm bg-muted px-1" title={exclusionTitle(model)}>{model.exclusion.step} · {exclusionLabel(model.exclusion.code).label}</span>
														{/if}
														{#if model.mss_inherited_from}<span class="shrink-0" title="Route copy: shows the scores of {model.mss_inherited_from}">↳ copy</span>{/if}
														{#if model.is_free}<span class="rounded-sm bg-success/12 px-1 font-medium text-success-fg">Free</span>{/if}
													</div>
												</div>
											</div>
										</td>
										{#if showEmpirical}
											<ScoreCell
												value={model.empirical_quality_score}
												best={model.empirical_quality_score != null && model.empirical_quality_score === colMax.empirical}
												title={empiricalTitle(model)}
												sub={model.empirical_quality_score != null ? `n=${model.n_trials}` : undefined}
												class="border-l border-border/60"
											/>
										{/if}
										<ScoreCell
											value={model.mss_score}
											strong
											best={model.mss_score != null && model.mss_score === colMax.mss}
											missing={mssMissing(model)}
											title={model.mss_score == null ? exclusionTitle(model) || 'Popularity or price missing' : undefined}
											class="border-l border-border/60"
										/>
										{#each mssCols as col (col.key)}
											{@const v = model[col.field] as number | null}
											<ScoreCell value={v} best={v != null && v === colMax[col.key]} missing={NO_DATA} />
										{/each}
										<td class="num text-xs {model.mss_coverage == null ? 'text-muted-foreground' : ''}">{fmtPct(model.mss_coverage)}</td>
										<td class="num text-xs {model.run_cost_usd == null ? 'text-muted-foreground' : ''}">
											{model.run_cost_usd != null ? fmtUsd(model.run_cost_usd) : NO_DATA}
											{#if model.planning_cost_usd != null}<span class="cell-sub block text-[11px] text-muted-foreground" title="30 runs at the planning basket, retries included">π {fmtUsd(model.planning_cost_usd, 2)}</span>{/if}
										</td>
										<ScoreCell value={model.composite_score} best={model.composite_score != null && model.composite_score === colMax.composite} class="border-l border-border/60" />
										{#if showApps}<td class="num text-xs {model.apps ? '' : 'text-muted-foreground'}">{model.apps || '—'}</td>{/if}
									</tr>
									{#if open}
										<tr class="bd-row" id="mss-bd-{i}">
											<td colspan={COLSPAN} class="p-0"><div class="bd-pin"><MssBreakdown row={model} /></div></td>
										</tr>
									{/if}
								{/each}
							</tbody>
						</table>
					</div>

					<!-- Mobile list (below 768px) -->
					<ul class="md:hidden">
						{#each data.rankings as model, i (model.model_id)}
							{@const rank = indexOnPage(i)}
							{@const selected = selectedModels.has(model.model_id)}
							{@const open = expanded.has(model.model_id)}
							{@const headline = model.empirical_quality_score != null ? { label: `Empirical · n=${model.n_trials}`, v: model.empirical_quality_score } : { label: 'MSS', v: model.mss_score }}
							<li class="data-list-row px-3" data-active={selected}>
								<div class="flex items-start gap-3">
									<Checkbox
										class="mt-1.5"
										checked={selected}
										disabled={!selected && selectedModels.size >= 10}
										onCheckedChange={() => toggleSelect(model.model_id)}
										aria-label="Select {model.model_name} for comparison"
									/>
									<span class="inline-flex h-6 w-8 shrink-0 items-center justify-center rounded-sm font-mono text-xs tabular-nums {rank <= 3 && !sortAsc ? 'bg-primary/12 font-semibold text-primary' : 'text-muted-foreground'}">{rank}</span>
									<div class="min-w-0 flex-1">
										<div class="flex items-start justify-between gap-2">
											<div class="min-w-0">
												<a href="/models/{modelPathSegment(model.model_id)}" class="block truncate text-sm font-medium hover:text-primary" title={model.model_name}>{shortModelName(model.model_name)}</a>
												<p class="flex items-center gap-1.5 text-[11px] text-muted-foreground">
													<span class="truncate">{model.vendor ?? (modelNamePrefix(model.model_name) || model.provider)}</span>
													{#if model.price_tier}<span class="shrink-0 rounded-sm border border-border px-1">{tierLabel(model.price_tier)}</span>{/if}
													{#if model.exclusion}<span class="shrink-0 rounded-sm bg-muted px-1" title={exclusionTitle(model)}>{exclusionLabel(model.exclusion.code).label}</span>{/if}
												</p>
											</div>
											<div class="shrink-0 text-right" title={empiricalTitle(model)}>
												<div class="font-mono text-lg font-medium leading-none tabular-nums {headline.v == null ? 'text-sm text-muted-foreground' : ''}">{headline.v != null ? pct(headline.v) : mssMissing(model)}</div>
												<div class="mt-1 text-[11px] text-muted-foreground">{headline.label}</div>
											</div>
										</div>
										<dl class="mt-2.5 grid grid-cols-3 gap-x-3 gap-y-2 text-[11px]">
											{#each mssCols as col (col.key)}
												{@const v = model[col.field] as number | null}
												<div>
													<dt class="text-muted-foreground">{col.label}</dt>
													<dd class="flex items-center gap-1.5">
														{#if v == null}
															<span class="text-muted-foreground">{NO_DATA}</span>
														{:else}
															<span class="w-9 font-mono text-xs tabular-nums">{pct(v)}</span>
															<span class="h-[3px] flex-1 overflow-hidden rounded-full bg-muted" aria-hidden="true"><span class="block h-full rounded-full bg-muted-foreground/45" style:width="{Math.max(0, Math.min(1, v)) * 100}%"></span></span>
														{/if}
													</dd>
												</div>
											{/each}
											<div>
												<dt class="text-muted-foreground">$/run</dt>
												<dd class="font-mono text-xs tabular-nums {model.run_cost_usd == null ? 'text-muted-foreground' : ''}">{model.run_cost_usd != null ? fmtUsd(model.run_cost_usd) : NO_DATA}</dd>
											</div>
											<div>
												<dt class="text-muted-foreground">Coverage</dt>
												<dd class="font-mono text-xs tabular-nums">{fmtPct(model.mss_coverage)}</dd>
											</div>
										</dl>
										<button type="button" class="mt-2 inline-flex min-h-9 cursor-pointer items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground" aria-expanded={open} onclick={() => toggleExpand(model.model_id)}>
											<ChevronRight class="size-3 transition-transform duration-150 {open ? 'rotate-90' : ''}" />{open ? 'Hide' : 'Show'} inputs{model.apps ? ` · ${model.apps} app${model.apps === 1 ? '' : 's'}` : ''}
										</button>
										{#if open}<div class="-mx-1 mt-1 rounded-md border border-border"><MssBreakdown row={model} /></div>{/if}
									</div>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
				<PaginationBar
					resultsText={data
						? `Showing ${data.pagination.total ? (page - 1) * perPage + 1 : 0}–${Math.min(page * perPage, data.pagination.total)} of ${data.pagination.total} models`
						: ''}
					page={data?.pagination.page}
					pages={data?.pagination.total_pages}
					onGoToPage={(p) => { page = p; }}
					class="border-t border-border"
					scrollToCard
				/>
			</Card.Content>
		</Card.Root>
	</Tabs.Content>

	<Tabs.Content value="selection" class="pt-0">
		{#if tab === 'selection'}<StudySelectionPanel />{/if}
	</Tabs.Content>

	<Tabs.Content value="method" class="pt-0">
		{#if tab === 'method'}
			<div class="space-y-4">
				<div class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
					<Card.Root>
						<Card.Header>
							<Card.Title class="text-sm font-medium">Model Selection Score (MSS v2)</Card.Title>
							<Card.Description class="text-xs">A pre-registered weighted sum (OECD/JRC 2008) that decides which models the study runs — a decision aid, not a measurement.</Card.Description>
						</Card.Header>
						<Card.Content class="space-y-3 text-xs text-muted-foreground">
							<p class="rounded-md border border-border bg-muted/40 px-3 py-2 font-mono text-foreground">
								MSS = Σ<sub>present</sub> w<sub>k</sub>·s<sub>k</sub> / Σ<sub>present</sub> w<sub>k</sub> · coverage = Σ<sub>present</sub> w<sub>k</sub>
							</p>
							<div class="overflow-x-auto">
								<table class="w-full">
									<thead>
										<tr><th>Component</th><th class="num">MSS</th><th class="num" title="Within-tier score used for the study selection (Amendment A1)">MSS<sub>tier</sub></th><th>Source and transform</th></tr>
									</thead>
									<tbody>
										{#each COMPONENTS as c (c.key)}
											<tr>
												<td class="text-xs text-foreground"><span class="font-mono text-muted-foreground">{c.letter}</span> {c.label}</td>
												<td class="num text-xs">{weightLabel(MSS_WEIGHTS[c.key])}</td>
												<td class="num text-xs">{weightLabel(MSS_TIER_WEIGHTS[c.key])}</td>
												<td class="text-[11px] leading-snug whitespace-normal">{c.help}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
							<p>
								Each component is scored 0–1 against <b class="text-foreground">fixed bounds</b> (goalposts), so adding or removing a model never reorders the others.
								A <b class="text-foreground">missing component</b> is left out and the weights renormalised — never scored 0; coverage says how much weight was present.
								Popularity and price are required: a model without either is excluded as insufficient evidence, not ranked low.
							</p>
							<p><b class="text-foreground">Composite</b> blends MSS (60%) with empirical quality (40%) where local measurements exist; a model without an MSS has no composite.</p>
						</Card.Content>
					</Card.Root>

					<Card.Root>
						<Card.Header>
							<Card.Title class="text-sm font-medium">Study selection</Card.Title>
							<Card.Description class="text-xs">Stratified purposive quota sampling by price tier, with scoring within each stratum (Amendment A1).</Card.Description>
						</Card.Header>
						<Card.Content class="space-y-2 text-xs text-muted-foreground">
							<p><b class="text-foreground">Tiers</b>: price thirds of the reference run cost over the eligible frame; exactly 3 frontier, 3 mid and 4 budget models at N = 10.</p>
							<p><b class="text-foreground">MSS<sub>tier</sub></b> ranks models inside their tier (price re-normalised inside the tier band); the global MSS stays for this leaderboard.</p>
							<p><b class="text-foreground">Quality floor</b>: capability at or above the tier median; no capability evidence only with top-quartile popularity; relaxed stepwise to the 40th, then 33rd percentile, each relaxation reported.</p>
							<p><b class="text-foreground">Quotas</b>: at most 2 per vendor, at least 6 vendors, 3 open- and 3 closed-weight, at least 1 Claude model.</p>
							<p>
								<b class="text-foreground">Hard budgets</b> with a 15% contingency: Claude on the Anthropic key; otherwise OpenCode Go when served there within its per-model monthly limit;
								otherwise the shared OpenRouter credit; otherwise not selectable.
							</p>
							<p>
								<b class="text-foreground">Algorithm</b>: frontier → mid → budget, each in MSS<sub>tier</sub> order, admitting a model only if a feasible completion still exists (exact look-ahead).
								<b class="text-foreground">Sensitivity</b> reruns the whole procedure under Dirichlet and uniform weight draws, specification draws, one-at-a-time changes, tier cuts ±10% and floor variants.
							</p>
						</Card.Content>
					</Card.Root>
				</div>

				<Card.Root class="gap-0 overflow-clip py-0">
					<Card.Header class="border-b border-border py-3">
						<Card.Title class="text-sm font-medium">Sources</Card.Title>
						<Card.Description class="text-xs">{sources ? `As of ${fmtDate(sources.as_of)}. ` : ''}Each input is stored with its source, license, as_of, fetched_at and window.</Card.Description>
					</Card.Header>
					{#if sourcesError}
						<div class="p-4"><ErrorAlert message={sourcesError} onretry={loadMethod} /></div>
					{:else if !sources}
						<div class="p-4"><LoadingState label="loading sources" /></div>
					{:else}
						<dl class="fig-strip rounded-none border-0 border-b shadow-none">
							<div class="fig-cell"><dt class="fig-label">Canonical models</dt><dd class="fig-value">{sources.coverage.canonical_models}</dd><dd class="fig-sub">{sources.coverage.passing_filters_e1_e7} pass E1–E7</dd></div>
							<div class="fig-cell"><dt class="fig-label">Eligible</dt><dd class="fig-value">{sources.coverage.eligible}</dd><dd class="fig-sub">popularity and price present</dd></div>
							<div class="fig-cell"><dt class="fig-label">Popularity</dt><dd class="fig-value">{sources.coverage.with_popularity}</dd><dd class="fig-sub">{sources.coverage.popularity_proxy} via LMArena proxy</dd></div>
							<div class="fig-cell"><dt class="fig-label">Price</dt><dd class="fig-value">{sources.coverage.with_price}</dd><dd class="fig-sub">of {sources.coverage.passing_filters_e1_e7}</dd></div>
							<div class="fig-cell"><dt class="fig-label">Capability</dt><dd class="fig-value">{sources.coverage.with_capability}</dd><dd class="fig-sub">of {sources.coverage.passing_filters_e1_e7}</dd></div>
							<div class="fig-cell"><dt class="fig-label">Recency</dt><dd class="fig-value">{sources.coverage.with_recency}</dd><dd class="fig-sub">of {sources.coverage.passing_filters_e1_e7}</dd></div>
						</dl>
						<SourcesTable sources={sources.sources} />
						{#if sources.tiers?.rule}
							<p class="border-t border-border px-4 py-3 text-xs text-muted-foreground">
								<b class="text-foreground">Price tiers</b> ({sources.tiers.rule}):
								{#each TIERS_CHEAP_FIRST as t (t)}
									<span class="mr-3 font-mono">{tierLabel(t)} {tierBand(sources.tiers, t)} · {sources.tiers.counts?.[t] ?? 0}</span>
								{/each}
							</p>
						{/if}
					{/if}
				</Card.Root>

				{#if sources}
					<Card.Root>
						<Card.Header>
							<Card.Title class="text-sm font-medium">Frame flow</Card.Title>
							<Card.Description class="text-xs">From catalog rows to canonical models (K) to the eligible frame (E), with every exclusion and its reason.</Card.Description>
						</Card.Header>
						<Card.Content><FlowList flow={sources.flow} class="max-w-3xl" /></Card.Content>
					</Card.Root>
				{/if}

				<Card.Root>
					<Card.Header>
						<Card.Title class="text-sm font-medium">Measurement: empirical quality</Card.Title>
						<Card.Description class="text-xs">Measured on this platform; it never enters the MSS or the study selection.</Card.Description>
					</Card.Header>
					<Card.Content class="space-y-4 text-sm text-muted-foreground">
						<p>
							Severity-weighted <strong>code issues</strong> from the deterministic analysis tools that measured every compared app (AI-reviewer
							findings are tracked separately and never enter this score). Dependency advisories, dynamic-scanner (DAST) findings and issues in
							test code are left out; the density is per KLOC of non-test source code, each app's repeats are averaged before models are compared,
							and it is blended 60/40 with the <strong>smoke pass rate</strong>. The smoke test probes <code>/api/health</code> plus the template's
							declared GET endpoints — it shows the app runs, not that it is functionally correct. Scores come with the number of trials (n) and
							between-trial spread; treat n=1 as an anecdote, not a result.
						</p>
						<div class="space-y-2">
							<p class="section-label text-foreground">Sensitivity — do the severity weights matter?</p>
							<p class="text-[11px]">
								The empirical ranking is recomputed under alternative severity weightings. Kendall's tau near 1.0 means the order barely depends on
								the chosen weights; listed pairs swap places under that scheme.
							</p>
							{#if sensitivity && sensitivity.models_evaluated >= 2}
								<div class="overflow-x-auto">
									<table class="w-full text-xs">
										<thead>
											<tr>
												<th>Scheme</th>
												<th>Weights (crit/high/med/low/info)</th>
												<th class="num">Kendall's τ</th>
												<th>Rank swaps vs. baseline</th>
											</tr>
										</thead>
										<tbody>
											{#each sensitivity.schemes as s (s.scheme)}
												<tr>
													<td class="font-mono">{s.scheme}</td>
													<td class="font-mono tabular-nums">{s.weights.critical}/{s.weights.high}/{s.weights.medium}/{s.weights.low}/{s.weights.info}</td>
													<td class="num">{s.kendall_tau.toFixed(2)}</td>
													<td>
														{#if s.adjacent_swaps.length === 0}
															none — stable
														{:else}
															{s.adjacent_swaps.map((p) => p.join(' ⇄ ')).join(', ')}
														{/if}
													</td>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
							{:else if sensitivity}
								<p class="text-[11px] italic">Fewer than two models have local measurements — run experiments on more models to compare rank stability.</p>
							{:else if sensitivityError}
								<p class="text-[11px] italic">Sensitivity data unavailable.</p>
							{:else}
								<LoadingState label="loading the sensitivity analysis" />
							{/if}
						</div>
					</Card.Content>
				</Card.Root>

				<div class="flex flex-wrap gap-x-4 gap-y-2">
					{#each [
						{ href: 'https://openrouter.ai/rankings', label: 'OpenRouter rankings' },
						{ href: 'https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset', label: 'LMArena leaderboard dataset' },
						{ href: 'https://models.dev', label: 'models.dev list prices' },
						{ href: 'https://opencode.ai/docs/go', label: 'OpenCode Go limits' },
						{ href: '/docs/design/MSS_V2', label: 'MSS v2 design' },
					] as link (link.href)}
						<a href={link.href} target={link.href.startsWith('http') ? '_blank' : undefined} rel={link.href.startsWith('http') ? 'noopener noreferrer' : undefined} class="inline-flex items-center gap-1 text-xs text-primary hover:underline">
							{link.label}{#if link.href.startsWith('http')}<ExternalLink class="h-3 w-3" />{/if}
						</a>
					{/each}
				</div>
			</div>
		{/if}
	</Tabs.Content>
</Tabs.Root>

<style>
	.skel {
		display: block;
		border-radius: 0.25rem;
		background: color-mix(in oklab, var(--muted-foreground) 18%, transparent);
		animation: skel-pulse 1.4s ease-in-out infinite;
	}
	@keyframes skel-pulse {
		50% {
			opacity: 0.45;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.skel {
			animation: none;
		}
	}
	.bd-row {
		background: color-mix(in oklab, var(--muted) 45%, transparent);
	}
	.bd-row:hover {
		background: color-mix(in oklab, var(--muted) 45%, transparent);
	}
	/* The breakdown row spans the whole (scrollable) table but stays in view at the frame's width. */
	.mss-frame {
		container-type: inline-size;
	}
	.bd-pin {
		position: sticky;
		left: 0;
		width: 100cqw;
	}
</style>
