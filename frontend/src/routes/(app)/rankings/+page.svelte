<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import Trophy from '@lucide/svelte/icons/trophy';
	import Download from '@lucide/svelte/icons/download';
	import Info from '@lucide/svelte/icons/info';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { SortableTh, TableToolbar, ScoreCell, rowLink, tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';
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

	const metaCols: { key: SortKey; field: keyof RankingRow; label: string; help: string }[] = [
		{ key: 'mss', field: 'mss_score', label: 'MSS', help: 'Model Scoring System: weighted blend of the four dimensions' },
		{ key: 'composite', field: 'composite_score', label: 'Composite', help: 'MSS 60% + empirical 40% where measured; otherwise equal to MSS' },
		{ key: 'adoption', field: 'adoption_score', label: 'Adoption', help: 'OpenRouter rank and local generation count (35%)' },
		{ key: 'benchmark', field: 'benchmark_score', label: 'Bench', help: 'Public coding benchmarks (30%)' },
		{ key: 'cost_efficiency', field: 'cost_efficiency_score', label: 'Cost', help: 'Performance per dollar + context bonus (20%)' },
		{ key: 'accessibility', field: 'accessibility_score', label: 'Access', help: 'License, API stability, documentation (15%)' },
	];
	const ALL_BENCH = [
		{ key: 'humaneval', label: 'HumanEval' },
		{ key: 'mbpp', label: 'MBPP' },
		{ key: 'swebench', label: 'SWE-bench' },
	];
	// Benchmark columns with no value anywhere on this page are hidden (named in the toolbar).
	const benchCols = $derived(ALL_BENCH.filter((c) => (data?.rankings ?? []).some((r) => typeof r[c.key] === 'number')));
	const hiddenBench = $derived(ALL_BENCH.filter((c) => !benchCols.includes(c)).map((c) => c.label));
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
		const out: Record<string, number | null> = { empirical: lead((r) => r.empirical_quality_score) };
		for (const c of metaCols) out[c.key] = lead((r) => r[c.field] as number);
		return out;
	});
	const sortLabel = $derived(
		sortBy === 'empirical' ? 'Empirical' : (metaCols.find((c) => c.key === sortBy)?.label ?? sortBy)
	);

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

	</FilterBar>

	<!-- Leaderboard -->
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Content class="p-0">
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
				<TableToolbar selectedCount={selectedModels.size} noun="model" onClearSelection={() => (selectedModels = new Set())}>
					{#snippet summary()}
						<span class="inline-flex items-center gap-1.5">
							<Trophy class="size-3.5" aria-hidden="true" />
							Ranked by <span class="font-medium text-foreground">{sortLabel}</span>{sortAsc ? ' (lowest first)' : ''}
							<span class="opacity-50">·</span> column leaders highlighted
							{#if hiddenBench.length}
								<span class="opacity-50">·</span>
								<span title="Columns without data on this page are hidden">{hiddenBench.join(', ')} hidden (no data)</span>
							{/if}
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

				<!-- Desktop table (768px+) -->
				<div class="table-frame hidden md:block" {@attach tableFrame}>
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
								<th class="border-l border-border text-center" title="Measured on this platform">Measured</th>
								<th class="border-l border-border text-center" colspan={metaCols.length} title="Model Scoring System — an opinionated composite of metadata, not a measurement">MSS decision aid</th>
								{#if benchCols.length}
									<th class="border-l border-border text-center" colspan={benchCols.length}>Public benchmarks</th>
								{/if}
								<th class="border-l border-border" rowspan="2"><span class="block text-right" title="Applications generated with this model here">Apps</span></th>
							</tr>
							<tr>
								<SortableTh label="Empirical" field="empirical" sortBy={sortBy} sortDir={sortAsc ? 'asc' : 'desc'} onSort={(f) => toggleSort(f as SortKey)} align="right" help="Severity-weighted findings per KLOC blended with the smoke pass rate" class="border-l border-border" />
								{#each metaCols as col, ci (col.key)}
									<SortableTh label={col.label} field={col.key} sortBy={sortBy} sortDir={sortAsc ? 'asc' : 'desc'} onSort={(f) => toggleSort(f as SortKey)} align="right" help={col.help} class={ci === 0 ? 'border-l border-border' : ''} />
								{/each}
								{#each benchCols as col, ci (col.key)}
									<th class="num {ci === 0 ? 'border-l border-border' : ''}">{col.label}</th>
								{/each}
							</tr>
						</thead>
						<tbody>
							{#each data.rankings as model, i (model.model_id)}
								{@const rank = indexOnPage(i)}
								{@const selected = selectedModels.has(model.model_id)}
								{@const href = `/models/${encodeURIComponent(model.model_id)}`}
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
											<div class="min-w-0 max-w-[280px]">
												<a {href} class="block truncate font-medium hover:text-primary" title={model.model_name}>{shortModelName(model.model_name)}</a>
												<div class="cell-sub flex items-center gap-1.5 text-[11px] text-muted-foreground">
													<span class="truncate">{modelNamePrefix(model.model_name) || model.provider}</span>
													{#if model.is_free}<span class="rounded-sm bg-success/12 px-1 font-medium text-success-fg">Free</span>{/if}
												</div>
											</div>
										</div>
									</td>
									<ScoreCell
										value={model.empirical_quality_score}
										strong
										best={model.empirical_quality_score != null && model.empirical_quality_score === colMax.empirical}
										title={empiricalTitle(model)}
										sub={model.empirical_quality_score != null ? `n=${model.n_trials}` : undefined}
										class="border-l border-border/60"
									/>
									{#each metaCols as col, ci (col.key)}
										<ScoreCell value={model[col.field] as number} strong={col.key === 'mss'} best={model[col.field] === colMax[col.key]} class={ci === 0 ? 'border-l border-border/60' : ''} />
									{/each}
									{#each benchCols as col, ci (col.key)}
										<td class="num text-xs {ci === 0 ? 'border-l border-border/60' : ''}">{fmtBench(model, col.key)}</td>
									{/each}
									<td class="num border-l border-border/60 text-xs {model.apps ? '' : 'text-muted-foreground'}">{model.apps || '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>

				<!-- Mobile list (below 768px) -->
				<ul class="md:hidden">
					{#each data.rankings as model, i (model.model_id)}
						{@const rank = indexOnPage(i)}
						{@const selected = selectedModels.has(model.model_id)}
						{@const headline = model.empirical_quality_score != null ? { label: `Empirical · n=${model.n_trials}`, v: model.empirical_quality_score } : { label: 'MSS (meta)', v: model.mss_score }}
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
											<a href="/models/{encodeURIComponent(model.model_id)}" class="block truncate text-sm font-medium hover:text-primary" title={model.model_name}>{shortModelName(model.model_name)}</a>
											<p class="flex items-center gap-1.5 text-[11px] text-muted-foreground">
												<span class="truncate">{modelNamePrefix(model.model_name) || model.provider}</span>
												{#if model.is_free}<span class="rounded-sm bg-success/12 px-1 font-medium text-success-fg">Free</span>{/if}
											</p>
										</div>
										<div class="shrink-0 text-right" title={empiricalTitle(model)}>
											<div class="font-mono text-lg font-medium leading-none tabular-nums">{pct(headline.v)}</div>
											<div class="mt-1 text-[11px] text-muted-foreground">{headline.label}</div>
										</div>
									</div>
									<dl class="mt-2.5 grid grid-cols-3 gap-x-3 gap-y-2 text-[11px]">
										{#each metaCols as col (col.key)}
											{@const v = model[col.field] as number}
											<div>
												<dt class="text-muted-foreground">{col.label}</dt>
												<dd class="flex items-center gap-1.5">
													<span class="w-9 font-mono text-xs tabular-nums {v ? '' : 'text-muted-foreground'}">{pct(v)}</span>
													<span class="h-[3px] flex-1 overflow-hidden rounded-full bg-muted" aria-hidden="true"><span class="block h-full rounded-full bg-muted-foreground/45" style:width="{Math.max(0, Math.min(1, v ?? 0)) * 100}%"></span></span>
												</dd>
											</div>
										{/each}
									</dl>
									{#if benchCols.length || model.apps}
										<p class="mt-2 font-mono text-[11px] text-muted-foreground">
											{#each benchCols as col (col.key)}{col.label} {fmtBench(model, col.key)} · {/each}{model.apps} app{model.apps === 1 ? '' : 's'}
										</p>
									{/if}
								</div>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
			<PaginationBar
				resultsText={data
					? `Showing ${(page - 1) * perPage + 1}–${Math.min(page * perPage, data.pagination.total)} of ${data.pagination.total} models`
					: ''}
				page={data?.pagination.page}
				pages={data?.pagination.total_pages}
				onGoToPage={(p) => { page = p; }}
				class="border-t border-border"
				scrollToCard
			/>
		</Card.Content>
	</Card.Root>

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
