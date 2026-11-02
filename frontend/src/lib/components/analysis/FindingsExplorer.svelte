<script lang="ts" module>
	export interface FindingFilters {
		tool: string;
		/** A tool-reported level (case-folded) or "unrated". */
		level: string;
		/**
		 * code | dependencies (known-vulnerability advisories) | runtime (dynamic tools) |
		 * source / tests (code findings outside / inside test files, counted apart as in the study); "" = all.
		 */
		scope: '' | 'code' | 'dependencies' | 'runtime' | 'source' | 'tests';
		category: string;
		file: string;
		rule: string;
		search: string;
		sort: 'severity' | 'file' | 'tool' | 'rule';
		/** Collapse findings of several tools that report one issue into one row. */
		merge: boolean;
	}

	export const EMPTY_FILTERS: FindingFilters = {
		tool: '',
		level: '',
		scope: '',
		category: '',
		file: '',
		rule: '',
		search: '',
		sort: 'severity',
		merge: true,
	};
</script>

<script lang="ts">
	/**
	 * FindingsExplorer — a run's findings: facet rail (scope — code, dependency
	 * advisories, runtime —, tool, level as the tool reported it, category,
	 * file, rule — with counts), search, sort, a table
	 * whose rows expand into `FindingDetail`, pagination and CSV/JSON export of
	 * the current filter. With `filters.merge` one row is one issue: findings of
	 * several tools that report the same thing (the study's cross-tool dedup)
	 * collapse into it and are listed under "Also reported by". Facets and
	 * exports still count findings.
	 *
	 * Props: `runId`, `tools` (slug → catalog tool, for names/icons), `filters`
	 * (bindable; the page keeps it in the URL), `refreshKey` (bump to refetch, e.g.
	 * when a tool finishes during a live run), `live` (a run still adding findings),
	 * `prevalence` / `scopeLabel` (the run context's rule prevalence and its corpus,
	 * shown in each finding's detail).
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import * as Select from '$lib/components/ui/select';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Download from '@lucide/svelte/icons/download';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import SearchX from '@lucide/svelte/icons/search-x';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';
	import { TableToolbar, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import {
		getAllRunFindings,
		getFindingFacets,
		getRunFindings,
		type FindingFacets,
		type FindingsParams,
		type RulePrevalence,
		type RunFinding,
	} from '$lib/api/runs';
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import { cn } from '$lib/utils';
	import LevelBadge from './LevelBadge.svelte';
	import FindingDetail from './FindingDetail.svelte';
	import { locationText } from './finding-data';
	import { downloadText, issueClassLabel, prevalenceIndex, prevalenceKey, sortLevels, toCsv } from './format';

	interface Props {
		runId: string;
		tools: Map<string, AnalyzerTool>;
		filters: FindingFilters;
		refreshKey?: number;
		live?: boolean;
		prevalence?: RulePrevalence[];
		scopeLabel?: string;
	}

	let { runId, tools, filters = $bindable(), refreshKey = 0, live = false, prevalence = [], scopeLabel = '' }: Props = $props();

	const prevalenceByToolRule = $derived(prevalenceIndex(prevalence).byToolRule);
	function rulePrevalence(f: RunFinding): RulePrevalence | null {
		return f.rule_id ? (prevalenceByToolRule.get(prevalenceKey(f.tool_slug, f.rule_id)) ?? null) : null;
	}

	const PER_PAGE = 50;
	let page = $state(1);
	let findings = $state<RunFinding[]>([]);
	let total = $state(0);
	let findingsTotal = $state<number | null>(null);
	let pages = $state(1);
	let loading = $state(true);
	let error = $state('');
	let facets = $state<FindingFacets | null>(null);
	let expanded = $state(new Set<string>());
	let searchText = $state(untrack(() => filters.search));
	let exporting = $state(false);
	let showFacetsMobile = $state(false);

	let findingsCtl: AbortController | null = null;
	let facetsCtl: AbortController | null = null;

	function toolName(slug: string): string {
		return tools.get(slug)?.name ?? slug;
	}

	function params(): FindingsParams {
		return {
			tool_slug: filters.tool,
			tool_severity: filters.level,
			category: filters.category,
			file_path: filters.file,
			rule_id: filters.rule,
			search: filters.search,
			sort: filters.sort,
			scope: filters.scope,
		};
	}

	const SCOPES: { value: 'code' | 'dependencies' | 'runtime'; label: string; title: string }[] = [
		{ value: 'code', label: 'Code', title: 'Findings about the code itself' },
		{ value: 'dependencies', label: 'Dependency advisories', title: 'Known vulnerabilities in package versions (pip-audit, npm-audit, osv-scanner, trivy, retire): not code issues' },
		{ value: 'runtime', label: 'Runtime (dynamic tools)', title: 'What dynamic scanners saw in the running app' },
	];
	// The code scope's two parts, as the study counts them (test code apart).
	const CODE_PARTS: { value: 'source' | 'tests'; label: string; title: string }[] = [
		{ value: 'source', label: 'App source', title: "Code findings outside test files: the app's own code issues" },
		{ value: 'tests', label: 'Test files', title: 'Code findings in tests/, *.test.*, *.spec.*, conftest.py and smoke scripts: counted apart, as in the study' },
	];
	/** Code findings outside / inside test files (null when code is unmeasured). */
	const codeParts = $derived(facets?.code_parts ?? null);
	const SCOPE_LABELS: Record<string, string> = {
		...Object.fromEntries(SCOPES.map((sc) => [sc.value, sc.label])),
		source: 'Code · app source',
		tests: 'Code · test files',
	};

	/** Distinct tools of a row: its own, then those of its duplicates. */
	function rowTools(f: RunFinding): string[] {
		return [...new Set([f.tool_slug, ...(f.duplicates ?? []).map((d) => d.tool_slug)])];
	}

	async function loadFindings(quiet = false) {
		findingsCtl?.abort();
		const ctl = new AbortController();
		findingsCtl = ctl;
		if (!quiet) loading = true;
		try {
			const data = await getRunFindings(runId, { ...params(), group: filters.merge, page, per_page: PER_PAGE }, { signal: ctl.signal });
			findings = data.items;
			total = data.total;
			findingsTotal = data.findings_total ?? null;
			pages = data.pages;
			page = data.page;
			error = '';
		} catch (e) {
			if (isAbortError(e) && ctl.signal.aborted) return;
			error = formatApiError(e, 'Could not load findings.');
		} finally {
			if (findingsCtl === ctl) loading = false;
		}
	}

	async function loadFacets() {
		facetsCtl?.abort();
		const ctl = new AbortController();
		facetsCtl = ctl;
		try {
			facets = await getFindingFacets(runId, { tool_slug: filters.tool, scope: filters.scope }, { signal: ctl.signal });
		} catch (e) {
			if (isAbortError(e) && ctl.signal.aborted) return;
			// Facets are a convenience: the table still works without them.
		}
	}

	// Filters (and the page) drive the table; the tool filter and refreshKey drive the facets.
	const filterKey = $derived(JSON.stringify({ ...params(), merge: filters.merge }));
	let lastFilterKey = '';
	$effect(() => {
		const key = filterKey;
		void runId;
		untrack(() => {
			if (key !== lastFilterKey) {
				lastFilterKey = key;
				page = 1;
				expanded = new Set();
			}
			void loadFindings();
		});
	});
	$effect(() => {
		void filters.tool;
		void filters.scope;
		void runId;
		untrack(() => void loadFacets());
	});
	// Live runs: refresh quietly (keep the current page and expanded rows).
	let lastRefresh = untrack(() => refreshKey);
	$effect(() => {
		const key = refreshKey;
		untrack(() => {
			if (key === lastRefresh) return;
			lastRefresh = key;
			void loadFindings(true);
			void loadFacets();
		});
	});

	// Debounced search box → filters.search.
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearchInput(value: string) {
		searchText = value;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			if (filters.search !== value.trim()) filters = { ...filters, search: value.trim() };
		}, 300);
	}
	$effect(() => {
		// External changes (Overview links, clear all) update the box.
		const s = filters.search;
		untrack(() => {
			if (searchText.trim() !== s) searchText = s;
		});
	});

	function setFilter<K extends keyof FindingFilters>(key: K, value: FindingFilters[K]) {
		const next = { ...filters, [key]: filters[key] === value ? '' : value };
		// Levels, files and rules are counted within the selected scope and tool.
		if (key === 'tool' || key === 'scope') {
			next.level = '';
			next.file = '';
			next.rule = '';
			next.category = '';
		}
		filters = next;
	}

	function clearAll() {
		filters = { ...EMPTY_FILTERS, sort: filters.sort, merge: filters.merge };
	}

	function goToPage(p: number) {
		if (p < 1 || p > pages) return;
		page = p;
		expanded = new Set();
		void loadFindings();
	}

	function toggle(id: string) {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expanded = next;
	}

	const activeChips = $derived(
		[
			filters.scope && { key: 'scope' as const, label: SCOPE_LABELS[filters.scope] ?? filters.scope },
			filters.tool && { key: 'tool' as const, label: `Tool: ${toolName(filters.tool)}` },
			filters.level && {
				key: 'level' as const,
				label: filters.level === 'unrated' ? 'Not rated by tool' : `Level: ${filters.level}`,
			},
			filters.category && { key: 'category' as const, label: `Category: ${filters.category}` },
			filters.file && { key: 'file' as const, label: `File: ${filters.file}` },
			filters.rule && { key: 'rule' as const, label: `Rule: ${filters.rule}` },
			filters.search && { key: 'search' as const, label: `“${filters.search}”` },
		].filter(Boolean) as { key: keyof FindingFilters; label: string }[]
	);

	const resultsText = $derived.by(() => {
		if (total === 0) return '';
		const range = `${(page - 1) * PER_PAGE + 1}–${Math.min(page * PER_PAGE, total)} of ${total}`;
		if (findingsTotal === null) return `${range} findings`;
		const merged = findingsTotal - total;
		return `${range} issues · ${findingsTotal} findings${merged > 0 ? `, ${merged} merged as duplicates` : ''}`;
	});

	const sortItems = [
		{ value: 'severity', label: 'Level (as reported)' },
		{ value: 'file', label: 'File' },
		{ value: 'tool', label: 'Tool' },
		{ value: 'rule', label: 'Rule' },
	];

	async function exportAs(format: 'csv' | 'json') {
		exporting = true;
		try {
			const rows = await getAllRunFindings(runId, params());
			const stamp = runId.slice(0, 8);
			if (format === 'json') {
				// The normalized severity is a heuristic for unrated tools: export what the tools said.
				const data = rows.map(({ severity: _severity, ...rest }) => rest);
				downloadText(`findings-${stamp}.json`, JSON.stringify(data, null, 2), 'application/json');
			} else {
				const header = ['tool', 'level_reported', 'rule', 'title', 'file', 'line', 'column', 'in_tests', 'category', 'description', 'suggestion', 'cwe', 'package', 'installed_version', 'fixed_versions', 'docs_url'];
				const body = rows.map((f) => {
					const d = f.tool_specific_data ?? {};
					const fixed = d.fixed_versions ?? d.fix_versions ?? d.fixed_version ?? '';
					return [
						f.tool_slug,
						f.tool_severity || 'unrated',
						f.rule_id,
						f.title,
						f.file_path,
						f.line_number,
						f.column_number,
						f.in_tests ? 'yes' : 'no',
						f.category,
						f.description,
						f.suggestion,
						Array.isArray(d.cwe) ? d.cwe.join(' ') : (d.cwe ?? ''),
						d.package ?? '',
						d.installed_version ?? d.version ?? '',
						Array.isArray(fixed) ? fixed.join(' ') : fixed,
						d.docs_url ?? '',
					];
				});
				downloadText(`findings-${stamp}.csv`, toCsv([header, ...body]), 'text/csv');
			}
			toast.success(`Exported ${rows.length} finding${rows.length === 1 ? '' : 's'}`);
		} catch (e) {
			toast.error(formatApiError(e, 'Export failed.'));
		} finally {
			exporting = false;
		}
	}
</script>

{#snippet facetButton(active: boolean, onclick: () => void, count: number, children: import('svelte').Snippet, title = '')}
	<button
		type="button"
		class={cn(
			'flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1 text-left text-xs transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none',
			active ? 'bg-primary/10 font-medium text-primary' : 'text-foreground/90 hover:bg-muted'
		)}
		aria-pressed={active}
		{title}
		{onclick}
	>
		<span class="min-w-0 flex-1">{@render children()}</span>
		<span class="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">{count}</span>
	</button>
{/snippet}

{#snippet facetRail()}
	{#if facets}
		<div class="space-y-4">
			{#if facets.scopes && ((facets.scopes.dependencies ?? 0) > 0 || (facets.scopes.runtime ?? 0) > 0 || (codeParts?.tests ?? 0) > 0 || (codeParts?.excluded ?? 0) > 0)}
				{@const scopes = facets.scopes}
				<div>
					<p class="section-label mb-1 px-2" title="Dependency advisories and runtime findings are not code issues; test code is counted apart">Scope</p>
					<!-- A scope no tool measured (null) has no findings to filter to. -->
					{#each SCOPES.filter((sc) => (scopes[sc.value] ?? 0) > 0) as sc (sc.value)}
						{#snippet scopeName()}<span>{sc.label}</span>{/snippet}
						{@render facetButton(filters.scope === sc.value, () => setFilter('scope', sc.value), scopes[sc.value] ?? 0, scopeName, sc.title)}
						{#if sc.value === 'code' && codeParts && ((codeParts.tests ?? 0) > 0 || (codeParts.excluded ?? 0) > 0)}
							<div class="ml-3 border-l border-border pl-1">
								{#each CODE_PARTS as part (part.value)}
									{#snippet partName()}<span>{part.label}</span>{/snippet}
									{@render facetButton(filters.scope === part.value, () => setFilter('scope', part.value), codeParts[part.value] ?? 0, partName, part.title)}
								{/each}
							</div>
						{/if}
					{/each}
				</div>
			{/if}
			<div>
				<p class="section-label mb-1 px-2">Tool</p>
				{#each facets.tools as t (t.value)}
					{#snippet label()}
						<span class="flex min-w-0 items-center gap-1.5">
							<ToolIcon tool={tools.get(t.value) ?? { slug: t.value }} size="xs" />
							<span class="truncate">{toolName(t.value)}</span>
						</span>
					{/snippet}
					{@render facetButton(filters.tool === t.value, () => setFilter('tool', t.value), t.count, label)}
				{/each}
			</div>

			{#if facets.levels.length > 0 || facets.unrated > 0}
				<div>
					<p class="section-label mb-1 px-2" title="Levels exactly as the tools printed them">Level (as reported)</p>
					{#each sortLevels(facets.levels) as l (l.value)}
						{#snippet label()}<LevelBadge label={l.value} />{/snippet}
						{@render facetButton(filters.level === l.value, () => setFilter('level', l.value), l.count, label)}
					{/each}
					{#if facets.unrated > 0}
						{#snippet unratedLabel()}<span class="text-muted-foreground">Not rated by tool</span>{/snippet}
						{@render facetButton(filters.level === 'unrated', () => setFilter('level', 'unrated'), facets.unrated, unratedLabel, 'Findings of tools that print no severity')}
					{/if}
				</div>
			{/if}

			{#if facets.categories.length > 1}
				<div>
					<p class="section-label mb-1 px-2">Category</p>
					{#each facets.categories as c (c.value)}
						{#snippet label()}<span class="truncate capitalize">{c.value.replaceAll('_', ' ')}</span>{/snippet}
						{@render facetButton(filters.category === c.value, () => setFilter('category', c.value), c.count, label)}
					{/each}
				</div>
			{/if}

			{#if facets.files.length > 0}
				<div>
					<p class="section-label mb-1 px-2">File</p>
					<div class="max-h-64 overflow-y-auto">
						{#each facets.files as file (file.value)}
							{#snippet label()}<span class="block truncate font-mono" dir="rtl" title={file.value}>&lrm;{file.value}</span>{/snippet}
							{@render facetButton(filters.file === file.value, () => setFilter('file', file.value), file.count, label, file.value)}
						{/each}
					</div>
					{#if facets.files_total > facets.files.length}
						<p class="px-2 pt-1 text-[11px] text-muted-foreground">+{facets.files_total - facets.files.length} more files — search to find them</p>
					{/if}
				</div>
			{/if}

			{#if facets.rules.length > 0}
				<div>
					<p class="section-label mb-1 px-2">Rule</p>
					<div class="max-h-64 overflow-y-auto">
						{#each facets.rules as r (r.value)}
							{#snippet label()}
								<span class="block truncate font-mono" title={r.label ? `${r.value} — ${r.label}` : r.value}>{r.value}</span>
							{/snippet}
							{@render facetButton(filters.rule === r.value, () => setFilter('rule', r.value), r.count, label)}
						{/each}
					</div>
					{#if facets.rules_total > facets.rules.length}
						<p class="px-2 pt-1 text-[11px] text-muted-foreground">+{facets.rules_total - facets.rules.length} more rules</p>
					{/if}
				</div>
			{/if}
		</div>
	{:else}
		<LoadingState label="loading filters" class="py-6" />
	{/if}
{/snippet}

<div class="grid items-start gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
	<aside class="hidden lg:sticky lg:top-[4.5rem] lg:block lg:max-h-[calc(100dvh-5.5rem)] lg:overflow-y-auto lg:overscroll-contain" aria-label="Filter findings">
		<Card.Root class="py-3">
			<Card.Content class="px-2">{@render facetRail()}</Card.Content>
		</Card.Root>
	</aside>

	<Card.Root class="min-w-0 gap-0 overflow-clip py-0">
		<div class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
			<div class="relative min-w-48 flex-1">
				<Search class="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
				<Input
					value={searchText}
					oninput={(e) => onSearchInput((e.currentTarget as HTMLInputElement).value)}
					placeholder="Search title, file, rule, code…"
					aria-label="Search findings"
					class="h-8 pl-8 text-sm"
				/>
			</div>
			<Button variant="outline" size="sm" class="lg:hidden" aria-expanded={showFacetsMobile} onclick={() => (showFacetsMobile = !showFacetsMobile)}>
				<SlidersHorizontal class="size-3.5" />Filters
			</Button>
			<label class="flex cursor-pointer items-center gap-2 text-xs whitespace-nowrap" title="One row per issue: findings of several tools that report the same thing are merged">
				<Checkbox bind:checked={() => filters.merge, (v) => (filters = { ...filters, merge: v })} />Merge duplicates
			</label>
			<div class="w-44">
				<Select.Simple
					bind:value={() => filters.sort, (v) => (filters = { ...filters, sort: (v || 'severity') as FindingFilters['sort'] })}
					items={sortItems}
					size="sm"
					aria-label="Sort findings"
				/>
			</div>
			<Button variant="outline" size="sm" disabled={exporting || total === 0} onclick={() => exportAs('csv')} title="Download the filtered findings as CSV">
				{#if exporting}<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />{:else}<Download class="size-3.5" />{/if}CSV
			</Button>
			<Button variant="outline" size="sm" disabled={exporting || total === 0} onclick={() => exportAs('json')} title="Download the filtered findings as JSON">JSON</Button>
		</div>

		{#if showFacetsMobile}
			<div class="border-b border-border p-2 lg:hidden">{@render facetRail()}</div>
		{/if}

		{#if activeChips.length > 0}
			<div class="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-1.5">
				{#each activeChips as chip (chip.key)}
					<button
						type="button"
						class="inline-flex max-w-full cursor-pointer items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary hover:bg-primary/15"
						onclick={() => (filters = { ...filters, [chip.key]: '' })}
						aria-label="Remove filter {chip.label}"
					>
						<span class="truncate">{chip.label}</span><X class="size-3 shrink-0" />
					</button>
				{/each}
				<button type="button" class="cursor-pointer text-xs text-muted-foreground hover:text-foreground" onclick={clearAll}>Clear all</button>
			</div>
		{/if}

		<TableToolbar>
			{#snippet summary()}
				{#if resultsText}{resultsText} · click a row for details{:else if !loading}No findings{/if}
				{#if live}<span class="text-info-fg"> · more arrive as tools finish</span>{/if}
			{/snippet}
		</TableToolbar>

		{#if error}
			<div class="p-3"><ErrorAlert message={error} onretry={() => loadFindings()} /></div>
		{:else if loading && findings.length === 0}
			<LoadingState label="loading findings" />
		{:else if findings.length === 0}
			{#if activeChips.length > 0}
				<EmptyState compact icon={SearchX} title="No findings match these filters">
					{#snippet action()}<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>{/snippet}
				</EmptyState>
			{:else if live}
				<EmptyState compact title="No findings yet" description="Findings appear here as each tool finishes." />
			{:else}
				<EmptyState compact icon={CircleCheck} title="No findings" description="The tools that ran reported nothing for this code. Check the Tools tab for tools that failed or were skipped." />
			{/if}
		{:else}
			<div class={cn('table-frame transition-opacity', loading && 'opacity-60')} {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th class="w-8"><span class="sr-only">Expand</span></th>
							<th>Finding</th>
							<th>Location</th>
							<th>Tool</th>
						</tr>
					</thead>
					<tbody>
						{#each findings as f (f.id)}
							{@const open = expanded.has(f.id)}
							{@const slugs = rowTools(f)}
							{@const cls = issueClassLabel(f.issue_class, f.tool_slug, f.rule_id)}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the expand button is the keyboard path) -->
							<tr class="row-link align-top" data-selected={open} onclick={() => toggle(f.id)}>
								<td class="w-8 pr-0">
									<button
										type="button"
										class="cursor-pointer rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
										aria-expanded={open}
										aria-controls="finding-{f.id}"
										aria-label="{open ? 'Hide' : 'Show'} details of {f.title}"
										onclick={(e) => { e.stopPropagation(); toggle(f.id); }}
									>
										<ChevronRight class={cn('size-4 transition-transform duration-150', open && 'rotate-90')} />
									</button>
								</td>
								<td class="max-w-0 w-[62%] min-w-[16rem]">
									<div class="flex items-start gap-2">
										<LevelBadge label={f.tool_severity} class="mt-0.5" />
										<div class="min-w-0">
											<p class="font-medium leading-5 [overflow-wrap:anywhere]">{f.title}</p>
											{#if f.rule_id || cls}
												<p class="cell-sub truncate font-mono text-[11px] text-muted-foreground">
													{f.rule_id}{#if cls}<span title="Issue class: the cross-tool unit the study deduplicates on">{`${f.rule_id ? ' · ' : ''}${cls}`}</span>{/if}
												</p>
											{/if}
										</div>
									</div>
								</td>
								<td class="max-w-0 w-[22%] font-mono text-xs text-muted-foreground">
									{#if f.file_path}<span class="block truncate" title={locationText(f)} dir="rtl">&lrm;{locationText(f)}</span>{:else}—{/if}
									{#if f.in_tests}<span class="cell-sub mt-0.5 block text-right"><span class="inline-block rounded border border-border px-1 font-sans text-[11px]" title="Test code: counted apart from the app's code issues, as in the study">test file</span></span>{/if}
								</td>
								<td class="whitespace-nowrap text-xs">
									<span class="inline-flex items-center gap-1.5" title={slugs.map(toolName).join(', ')}>
										{#each slugs as slug (slug)}
											<ToolIcon tool={tools.get(slug) ?? { slug }} size="xs" />
										{/each}
										{toolName(f.tool_slug)}
										{#if slugs.length > 1}<span class="text-muted-foreground">+{slugs.length - 1}</span>{/if}
									</span>
									{#if f.duplicates?.length}
										<p class="cell-sub text-[11px] text-muted-foreground">{f.duplicates.length + 1} findings</p>
									{/if}
								</td>
							</tr>
							{#if open}
								<tr class="bg-muted/20" id="finding-{f.id}">
									<td></td>
									<td colspan="3" class="py-3">
										<!-- width 0 / min-width 100%: long code lines scroll inside instead of widening the table -->
										<div class="@container w-0 min-w-full">
											<FindingDetail finding={f} toolName={toolName(f.tool_slug)} prevalence={rulePrevalence(f)} {scopeLabel} />
											{#if f.duplicates?.length}
												<div class="mt-4 border-t border-border pt-3">
													<p class="section-label mb-1.5">Also reported by</p>
													<ul class="space-y-1">
														{#each f.duplicates as d (d.id)}
															{@const dOpen = expanded.has(d.id)}
															<li>
																<button
																	type="button"
																	class="flex w-full cursor-pointer items-start gap-2 rounded px-1 py-1 text-left text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
																	aria-expanded={dOpen}
																	aria-controls="finding-{d.id}"
																	onclick={() => toggle(d.id)}
																>
																	<ChevronRight class={cn('mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform duration-150', dOpen && 'rotate-90')} />
																	<span class="inline-flex shrink-0 items-center gap-1.5 font-medium">
																		<ToolIcon tool={tools.get(d.tool_slug) ?? { slug: d.tool_slug }} size="xs" />
																		{toolName(d.tool_slug)}
																	</span>
																	<LevelBadge label={d.tool_severity} />
																	<span class="min-w-0 flex-1 [overflow-wrap:anywhere]">
																		{d.title}
																		{#if d.rule_id}<span class="font-mono text-muted-foreground"> · {d.rule_id}</span>{/if}
																		{#if d.file_path && locationText(d) !== locationText(f)}<span class="font-mono text-muted-foreground"> · {locationText(d)}</span>{/if}
																	</span>
																</button>
																{#if dOpen}
																	<div class="py-2 pl-6" id="finding-{d.id}">
																		<FindingDetail finding={d} toolName={toolName(d.tool_slug)} prevalence={rulePrevalence(d)} {scopeLabel} issueClass={f.issue_class ?? ''} />
																	</div>
																{/if}
															</li>
														{/each}
													</ul>
												</div>
											{/if}
										</div>
									</td>
								</tr>
							{/if}
						{/each}
					</tbody>
				</table>
			</div>
			<PaginationBar page={page} pages={pages} onGoToPage={goToPage} class="border-t border-border" scrollToCard />
		{/if}
	</Card.Root>
</div>
