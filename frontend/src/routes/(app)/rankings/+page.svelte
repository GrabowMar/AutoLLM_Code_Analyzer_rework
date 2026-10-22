<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import Trophy from '@lucide/svelte/icons/trophy';
	import Medal from '@lucide/svelte/icons/medal';
	import Download from '@lucide/svelte/icons/download';
	import Info from '@lucide/svelte/icons/info';
	import ArrowUpDown from '@lucide/svelte/icons/arrow-up-down';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import {
		getRankings,
		getRankingsSensitivity,
		exportRankingsUrl,
		type RankingRow,
		type RankingsResponse,
		type SensitivityResponse,
	} from '$lib/api/client';

	type SortKey = 'mss' | 'empirical' | 'composite' | 'benchmark' | 'cost_efficiency' | 'accessibility' | 'adoption';

	let searchQuery = $state('');
	let providerFilter = $state('all');
	let sortBy = $state<SortKey>('mss');
	let sortAsc = $state(false);
	let showMethodology = $state(false);
	let selectedModels = $state<Set<string>>(new Set());
	let includeFree = $state(true);
	let hasBenchmarksOnly = $state(false);
	let page = $state(1);
	let perPage = $state(25);

	let loading = $state(true);
	let error = $state<string | null>(null);
	let data = $state<RankingsResponse | null>(null);
	let sensitivity = $state<SensitivityResponse | null>(null);
	let sensitivityError = $state(false);

	function toggleMethodology() {
		showMethodology = !showMethodology;
		if (showMethodology && !sensitivity && !sensitivityError) {
			getRankingsSensitivity()
				.then((s) => (sensitivity = s))
				.catch(() => (sensitivityError = true));
		}
	}

	let providers = $derived(() => {
		const set = new Set<string>();
		for (const r of data?.rankings ?? []) if (r.provider) set.add(r.provider);
		return [...set].sort();
	});

	async function load() {
		loading = true;
		error = null;
		try {
			data = await getRankings({
				page,
				per_page: perPage,
				sort_by: sortBy,
				sort_dir: sortAsc ? 'asc' : 'desc',
				search: searchQuery || undefined,
				provider: providerFilter === 'all' ? undefined : providerFilter,
				include_free: includeFree,
				has_benchmarks: hasBenchmarksOnly || undefined,
			});
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load rankings';
		} finally {
			loading = false;
		}
	}

	onMount(load);

	$effect(() => {
		// Re-load when filters/sort/pagination change.
		void [searchQuery, providerFilter, sortBy, sortAsc, includeFree, hasBenchmarksOnly, page, perPage];
		load();
	});

	function toggleSort(col: SortKey) {
		if (sortBy === col) sortAsc = !sortAsc;
		else { sortBy = col; sortAsc = false; }
		page = 1;
	}

	function toggleSelect(slug: string) {
		const next = new Set(selectedModels);
		if (next.has(slug)) next.delete(slug);
		else if (next.size < 10) next.add(slug);
		selectedModels = next;
	}

	function scoreColor01(v: number): string {
		if (v >= 0.8) return 'text-success-fg';
		if (v >= 0.4) return 'text-warning-fg';
		return 'text-danger-fg';
	}

	function pct(v: number | null | undefined): string {
		if (v == null) return '—';
		return `${(v * 100).toFixed(1)}`;
	}

	function fmtBench(row: RankingRow, key: string): string {
		const v = row[key];
		if (typeof v !== 'number') return '—';
		return v.toFixed(1);
	}

	function empiricalTitle(model: RankingRow): string {
		const v = model.variance;
		const parts = [`${model.n_trials} completed trial${model.n_trials === 1 ? 's — single runs are anecdotes' : 's'}`];
		if (v?.density_per_kloc_mean != null) {
			parts.push(`weighted findings density ${v.density_per_kloc_mean.toFixed(1)}/KLOC` +
				(v.density_per_kloc_stdev != null ? ` (±${v.density_per_kloc_stdev.toFixed(1)} between trials)` : ''));
		}
		if (model.functional_pass_rate != null) {
			parts.push(`smoke pass rate ${(model.functional_pass_rate * 100).toFixed(0)}%` +
				(v?.smoke_pass_rate_stdev != null ? ` (±${(v.smoke_pass_rate_stdev * 100).toFixed(0)}pp)` : ''));
		}
		return parts.join(' · ');
	}

	function indexOnPage(i: number): number {
		return (page - 1) * perPage + i + 1;
	}

	const activeFilters = $derived.by((): FilterTag[] => {
		const tags: FilterTag[] = [];
		if (providerFilter !== 'all') {
			tags.push({ key: 'provider', label: `Provider: ${providerFilter}`, onRemove: () => { providerFilter = 'all'; page = 1; } });
		}
		if (!includeFree) {
			tags.push({ key: 'nofree', label: 'Paid only', onRemove: () => { includeFree = true; page = 1; } });
		}
		if (hasBenchmarksOnly) {
			tags.push({ key: 'bench', label: 'With benchmarks', onRemove: () => { hasBenchmarksOnly = false; page = 1; } });
		}
		if (searchQuery) {
			tags.push({ key: 'search', label: `"${searchQuery}"`, onRemove: () => { searchQuery = ''; page = 1; } });
		}
		return tags;
	});

	function clearAllFilters() {
		searchQuery = '';
		providerFilter = 'all';
		includeFree = true;
		hasBenchmarksOnly = false;
		page = 1;
	}
</script>

<div class="space-y-6">
	<PageHeader title="Model Rankings" description="Empirical quality measured on this platform, alongside the metadata-based MSS decision aid.">
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
	</PageHeader>

	<!-- Stats summary -->
	{#if data}
		<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
			<div class="kpi-card">
				<div class="kpi-label">Total Models</div>
				<div class="kpi-value">{data.statistics.total}</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">With Benchmarks</div>
				<div class="kpi-value">{data.statistics.with_benchmarks}</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Free Models</div>
				<div class="kpi-value">{data.statistics.free_models}</div>
			</div>
			<div class="kpi-card">
				<div class="kpi-label">Avg MSS (decision aid)</div>
				<div class="kpi-value">{(data.statistics.avg_mss * 100).toFixed(1)}</div>
			</div>
		</div>
	{/if}

	<!-- Filters -->
	<FilterBar
		searchPlaceholder="Search models..."
		bind:searchValue={searchQuery}
		activeTags={activeFilters}
		resultsText={data
			? `${data.statistics.total} models · ${data.statistics.free_models} free · ${data.statistics.with_benchmarks} with benchmarks`
			: ''}
		page={data?.pagination.page}
		pages={data?.pagination.total_pages}
		onGoToPage={(p) => { page = p; }}
		onClearAll={clearAllFilters}
	>
		{#snippet filters()}
			<!-- Row 1: Provider -->
			<div class="fb-group">
				<span class="fb-group-label">Provider</span>
				<select
					class="fb-select"
					aria-label="Filter by provider"
					bind:value={providerFilter}
					onchange={() => { page = 1; }}
				>
					<option value="all">All Providers</option>
					{#each providers() as p}
						<option value={p}>{p}</option>
					{/each}
				</select>
			</div>

			<!-- Row 2: Options -->
			<div class="fb-group">
				<span class="fb-group-label">Options</span>
				<button
					class="fb-chip {includeFree ? '' : 'fb-chip-on'}"
					onclick={() => { includeFree = !includeFree; page = 1; }}
					aria-pressed={!includeFree}
				>Paid only</button>
				<button
					class="fb-chip {hasBenchmarksOnly ? 'fb-chip-on' : ''}"
					onclick={() => { hasBenchmarksOnly = !hasBenchmarksOnly; page = 1; }}
					aria-pressed={hasBenchmarksOnly}
				>Has Benchmarks</button>
			</div>

			<!-- Row 3: Sort -->
			<div class="fb-group">
				<span class="fb-group-label">Sort</span>
				<button class="fb-chip {sortBy === 'mss' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'mss'} onclick={() => toggleSort('mss')}>MSS</button>
				<button class="fb-chip {sortBy === 'empirical' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'empirical'} onclick={() => toggleSort('empirical')}>Empirical</button>
				<button class="fb-chip {sortBy === 'composite' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'composite'} onclick={() => toggleSort('composite')}>Composite</button>
				<button class="fb-chip {sortBy === 'benchmark' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'benchmark'} onclick={() => toggleSort('benchmark')}>Benchmark</button>
				<button class="fb-chip {sortBy === 'cost_efficiency' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'cost_efficiency'} onclick={() => toggleSort('cost_efficiency')}>Cost</button>
				<button class="fb-chip {sortBy === 'accessibility' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'accessibility'} onclick={() => toggleSort('accessibility')}>Accessibility</button>
				<button class="fb-chip {sortBy === 'adoption' ? 'fb-chip-on' : ''}" aria-pressed={sortBy === 'adoption'} onclick={() => toggleSort('adoption')}>Adoption</button>
			</div>

			<!-- Row 4: Per page -->
			<div class="fb-group">
				<span class="fb-group-label">Per page</span>
				<button class="fb-chip {perPage === 25 ? 'fb-chip-on' : ''}" aria-pressed={perPage === 25} onclick={() => { perPage = 25; page = 1; }}>25</button>
				<button class="fb-chip {perPage === 50 ? 'fb-chip-on' : ''}" aria-pressed={perPage === 50} onclick={() => { perPage = 50; page = 1; }}>50</button>
				<button class="fb-chip {perPage === 100 ? 'fb-chip-on' : ''}" aria-pressed={perPage === 100} onclick={() => { perPage = 100; page = 1; }}>100</button>
			</div>
		{/snippet}

		{#snippet actions()}
			{#if selectedModels.size >= 2}
				<Button size="sm" href="/models/compare?models={[...selectedModels].join(',')}">
					<ArrowUpDown class="mr-1.5 h-3.5 w-3.5" />
					Compare {selectedModels.size}
				</Button>
			{/if}
		{/snippet}
	</FilterBar>

	<!-- Leaderboard -->
	<Card.Root>
		<Card.Header class="pb-0">
			<div class="flex items-center gap-2">
				<Trophy class="h-4 w-4 text-muted-foreground" />
				<Card.Title>Leaderboard</Card.Title>
			</div>
		</Card.Header>
		<Card.Content class="p-0 pt-4">
			{#if error}
				<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
			{:else if loading && !data}
				<LoadingState label="loading rankings" />
			{:else if data && data.rankings.length === 0}
				<EmptyState compact icon={Trophy} title="No ranked models" description="No models match the current filters.">
					{#snippet action()}
						<Button variant="outline" size="sm" onclick={clearAllFilters}>Clear all filters</Button>
					{/snippet}
				</EmptyState>
			{:else if data}
				<!-- Desktop table (768px+) -->
				<div class="hidden md:block overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b bg-muted/40 sticky top-0 z-10">
								<th class="w-10 px-3 py-2.5"></th>
								<th class="w-14 px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Rank</th>
								<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Model</th>
								{#each [
									{ key: 'empirical' as SortKey, label: 'Empirical (measured)' },
									{ key: 'mss' as SortKey, label: 'MSS (meta)' },
									{ key: 'composite' as SortKey, label: 'Composite' },
									{ key: 'adoption' as SortKey, label: 'Adoption' },
									{ key: 'benchmark' as SortKey, label: 'Bench' },
									{ key: 'cost_efficiency' as SortKey, label: 'Cost-Eff.' },
									{ key: 'accessibility' as SortKey, label: 'Access.' },
								] as col}
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">
										<button class="inline-flex items-center gap-1 hover:text-foreground" onclick={() => toggleSort(col.key)}>
											{col.label}
											<ArrowUpDown class="h-3 w-3 {sortBy === col.key ? 'text-foreground' : ''}" />
										</button>
									</th>
								{/each}
								<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">HumanEval</th>
								<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">MBPP</th>
								<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">SWE-bench</th>
								<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Apps</th>
							</tr>
						</thead>
						<tbody>
							{#each data.rankings as model, i}
								{@const rank = indexOnPage(i)}
								<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'} {selectedModels.has(model.model_id) ? 'ring-1 ring-inset ring-primary/40' : ''}">
									<td class="px-3 py-2 align-top">
										<input type="checkbox" checked={selectedModels.has(model.model_id)} onchange={() => toggleSelect(model.model_id)} class="rounded" aria-label="Select {model.model_name} for comparison" />
									</td>
									<td class="px-3 py-2 align-top">
										<div class="flex items-center gap-1.5">
											{#if rank <= 3}
												<Medal class="h-4 w-4 {rank === 1 ? 'text-warning-fg' : rank === 2 ? 'text-muted-foreground' : 'text-warning-fg/60'}" />
											{/if}
											<span class="text-xs font-semibold">#{rank}</span>
										</div>
									</td>
									<td class="px-3 py-2 align-top">
										<div>
											<a href="/models/{encodeURIComponent(model.model_id)}" class="font-medium hover:underline">{model.model_name}</a>
											<div class="text-[11px] text-muted-foreground">
												{model.provider}
												{#if model.is_free}<Badge variant="outline" class="ml-1 px-1 py-0 text-[11px]">Free</Badge>{/if}
											</div>
										</div>
									</td>
									<td class="px-3 py-2 text-right align-top" title={empiricalTitle(model)}>
										{#if model.empirical_quality_score != null}
											<div class="font-mono tabular-nums font-bold {scoreColor01(model.empirical_quality_score)}">{pct(model.empirical_quality_score)}</div>
											<div class="text-[11px] text-muted-foreground whitespace-nowrap">n={model.n_trials}{model.variance?.density_per_kloc_stdev != null ? ` · ±${model.variance.density_per_kloc_stdev.toFixed(1)}/KLOC` : ''}</div>
										{:else}
											<span class="text-xs text-muted-foreground">—</span>
										{/if}
									</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums {scoreColor01(model.mss_score)}">{pct(model.mss_score)}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor01(model.composite_score)}">{pct(model.composite_score)}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor01(model.adoption_score)}">{pct(model.adoption_score)}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor01(model.benchmark_score)}">{pct(model.benchmark_score)}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor01(model.cost_efficiency_score)}">{pct(model.cost_efficiency_score)}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs {scoreColor01(model.accessibility_score)}">{pct(model.accessibility_score)}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs">{fmtBench(model, 'humaneval')}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs">{fmtBench(model, 'mbpp')}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs">{fmtBench(model, 'swebench')}</td>
									<td class="px-3 py-2 text-right align-top font-mono tabular-nums text-xs text-muted-foreground">{model.apps}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>

				<!-- Mobile card view (below 768px) -->
				<div class="md:hidden space-y-3 px-4 pb-4">
					{#each data.rankings as model, i}
						{@const rank = indexOnPage(i)}
						<div class="bg-card border rounded-lg p-4 {selectedModels.has(model.model_id) ? 'ring-1 ring-inset ring-primary/40' : ''}">
							<div class="flex items-center gap-3">
								<input type="checkbox" checked={selectedModels.has(model.model_id)} onchange={() => toggleSelect(model.model_id)} class="rounded shrink-0" aria-label="Select {model.model_name} for comparison" />
								<div class="flex items-center gap-1.5 shrink-0">
									{#if rank <= 3}
										<Medal class="h-5 w-5 {rank === 1 ? 'text-warning-fg' : rank === 2 ? 'text-muted-foreground' : 'text-warning-fg/60'}" />
									{/if}
									<span class="text-xs font-bold text-muted-foreground">#{rank}</span>
								</div>
								<div class="min-w-0 flex-1">
									<a href="/models/{encodeURIComponent(model.model_id)}" class="font-medium text-sm hover:underline truncate block">{model.model_name}</a>
									<Badge variant="outline" class="mt-0.5 text-[11px]">{model.provider}</Badge>
								</div>
								<div class="text-right shrink-0" title={empiricalTitle(model)}>
									{#if model.empirical_quality_score != null}
										<div class="text-[11px] font-medium text-muted-foreground">Empirical (n={model.n_trials})</div>
										<div class="text-lg font-bold font-mono {scoreColor01(model.empirical_quality_score)}">{pct(model.empirical_quality_score)}</div>
									{:else}
										<div class="text-[11px] font-medium text-muted-foreground">MSS (meta)</div>
										<div class="text-lg font-bold font-mono {scoreColor01(model.mss_score)}">{pct(model.mss_score)}</div>
									{/if}
								</div>
							</div>

							<div class="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">MSS (meta)</span>
									<span class="text-xs font-mono font-medium {scoreColor01(model.mss_score)}">{pct(model.mss_score)}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">Composite</span>
									<span class="text-xs font-mono font-medium {scoreColor01(model.composite_score)}">{pct(model.composite_score)}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">Adoption</span>
									<span class="text-xs font-mono font-medium {scoreColor01(model.adoption_score)}">{pct(model.adoption_score)}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">Benchmarks</span>
									<span class="text-xs font-mono font-medium {scoreColor01(model.benchmark_score)}">{pct(model.benchmark_score)}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">Cost-Eff.</span>
									<span class="text-xs font-mono font-medium {scoreColor01(model.cost_efficiency_score)}">{pct(model.cost_efficiency_score)}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">Accessibility</span>
									<span class="text-xs font-mono font-medium {scoreColor01(model.accessibility_score)}">{pct(model.accessibility_score)}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">HumanEval</span>
									<span class="text-xs font-mono font-medium">{fmtBench(model, 'humaneval')}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">MBPP</span>
									<span class="text-xs font-mono font-medium">{fmtBench(model, 'mbpp')}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">SWE-bench</span>
									<span class="text-xs font-mono font-medium">{fmtBench(model, 'swebench')}</span>
								</div>
								<div class="flex items-center justify-between">
									<span class="text-[11px] text-muted-foreground">Apps</span>
									<span class="text-xs font-mono font-medium text-muted-foreground">{model.apps}</span>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</Card.Content>
	</Card.Root>

	<PaginationBar
		resultsText={data
			? `Showing ${(page - 1) * perPage + 1}–${Math.min(page * perPage, data.pagination.total)} of ${data.pagination.total} models`
			: ''}
		page={data?.pagination.page}
		pages={data?.pagination.total_pages}
		onGoToPage={(p) => { page = p; }}
		class="rounded-md border border-border"
	/>

	<!-- Methodology Panel -->
	<Card.Root>
		<button class="flex w-full cursor-pointer items-center gap-3 p-4 text-left text-sm" onclick={toggleMethodology} aria-expanded={showMethodology}>
			<Info class="h-4 w-4 text-muted-foreground shrink-0" />
			<span class="font-medium">Methodology: what is measured vs. what is opinion</span>
			<Badge variant="outline" class="ml-auto text-[11px]">{showMethodology ? 'Hide' : 'Show'}</Badge>
		</button>
		{#if showMethodology}
			<Card.Content class="pt-0 text-sm text-muted-foreground space-y-4">
				<div class="space-y-2">
					<p class="section-label text-foreground">Measurement — Empirical quality</p>
					<p>
						Measured on this platform: severity-weighted findings from the <strong>deterministic analysis
						tools only</strong> (AI-reviewer findings are tracked separately and never enter this score),
						normalized per KLOC of generated code, blended 60/40 with the <strong>smoke pass rate</strong>.
						The smoke test probes <code>/api/health</code> plus the template's declared GET endpoints —
						it shows the app runs, not that it is functionally correct. Scores come with the number of
						trials (n) and between-trial spread; treat n=1 as an anecdote, not a result.
					</p>
				</div>
				<div class="space-y-2">
					<p class="section-label text-foreground">Decision aid — MSS</p>
					<p>The <strong>Model Scoring System (MSS)</strong> is an opinionated composite of model metadata (0–1), not a measurement. Its four dimensions and weights are asserted, not derived:</p>
					<div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
						{#each [
							{ label: 'Adoption (35%)', desc: 'OpenRouter rank and local app generation count' },
							{ label: 'Benchmarks (30%)', desc: 'Public coding benchmarks: HumanEval, MBPP, SWE-bench, BFCL, WebDev Elo, LiveBench, etc.' },
							{ label: 'Cost Efficiency (20%)', desc: 'Performance per dollar + context window bonus' },
							{ label: 'Accessibility (15%)', desc: 'License, API stability, documentation quality' },
						] as dim}
							<div class="rounded-md border p-2.5">
								<span class="text-xs font-medium text-foreground">{dim.label}</span>
								<p class="text-[11px] text-muted-foreground">{dim.desc}</p>
							</div>
						{/each}
					</div>
					<p class="text-[11px]"><strong>Composite</strong> blends MSS (60%) with empirical quality (40%) when local measurements exist; models never exercised here keep composite = MSS.</p>
				</div>
				<div class="space-y-2">
					<p class="section-label text-foreground">Sensitivity — do the severity weights matter?</p>
					<p class="text-[11px]">
						The empirical ranking is recomputed under alternative severity weightings. Kendall's tau near 1.0
						means the order barely depends on the chosen weights; listed pairs swap places under that scheme.
					</p>
					{#if sensitivity && sensitivity.models_evaluated >= 2}
						<div class="overflow-x-auto">
							<table class="w-full text-xs">
								<thead>
									<tr class="border-b text-left text-muted-foreground">
										<th class="py-1.5 pr-3 font-medium">Scheme</th>
										<th class="py-1.5 pr-3 font-medium">Weights (crit/high/med/low/info)</th>
										<th class="py-1.5 pr-3 text-right font-medium">Kendall's τ</th>
										<th class="py-1.5 font-medium">Rank swaps vs. baseline</th>
									</tr>
								</thead>
								<tbody>
									{#each sensitivity.schemes as s}
										<tr class="border-b border-border/50">
											<td class="py-1.5 pr-3 font-mono">{s.scheme}</td>
											<td class="py-1.5 pr-3 font-mono tabular-nums">{s.weights.critical}/{s.weights.high}/{s.weights.medium}/{s.weights.low}/{s.weights.info}</td>
											<td class="py-1.5 pr-3 text-right font-mono tabular-nums {s.kendall_tau >= 0.9 ? 'text-success-fg' : s.kendall_tau >= 0.7 ? 'text-warning-fg' : 'text-danger-fg'}">{s.kendall_tau.toFixed(2)}</td>
											<td class="py-1.5">
												{#if s.adjacent_swaps.length === 0}
													<span class="text-success-fg">none — stable</span>
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
						<p class="text-[11px] italic">Loading sensitivity analysis…</p>
					{/if}
				</div>
				<div class="flex flex-wrap gap-2 pt-1">
					<a href="https://www.swebench.com" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-xs text-primary hover:underline">
						SWE-bench <ExternalLink class="h-3 w-3" />
					</a>
					<a href="https://evalplus.github.io/leaderboard.html" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-xs text-primary hover:underline">
						EvalPlus (HumanEval/MBPP+) <ExternalLink class="h-3 w-3" />
					</a>
					<a href="https://livebench.ai/" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-xs text-primary hover:underline">
						LiveBench <ExternalLink class="h-3 w-3" />
					</a>
				</div>
			</Card.Content>
		{/if}
	</Card.Root>
</div>
