<script lang="ts">
	/**
	 * RunFindings — the findings of one run with server-side filters, search
	 * and severity-rank ordering; static and AI findings are separate views
	 * (AI rows are per-sample opinions). Filters live in the URL (see
	 * `./url.ts`), rows open the {@link FindingDrawer}.
	 *
	 * Props: `run`, `revision` (changes when the run progressed: silent
	 * reload), `onfile(path)` (open a file in the Files tab).
	 */
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import type {
		AnalysisRunDetail,
		Finding,
		FindingFacets,
		FindingFilters,
		FindingOrdering,
		FindingPage,
		Severity
	} from '$lib/api/analysis';
	import {
		FINDING_ORDERINGS,
		GROUNDINGS,
		isRunActive,
		isToolOk,
		listRunFindings,
		SEVERITIES
	} from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import { groundingTones, severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import FindingDrawer from '../FindingDrawer.svelte';
	import Hint from '../Hint.svelte';
	import {
		categoryLabel,
		fmtLocation,
		problemLabel,
		toolProblems,
		uncountedReason
	} from '../run-utils';
	import { csvParam, FINDING_PARAMS, intParam, replaceParams } from './url';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleMinus from '@lucide/svelte/icons/circle-minus';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import SearchX from '@lucide/svelte/icons/search-x';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Hourglass from '@lucide/svelte/icons/hourglass';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		run: AnalysisRunDetail;
		revision: string;
		onfile?: (path: string) => void;
	}

	let { run, revision, onfile }: Props = $props();

	const PER_PAGE = 50;

	const ORDER_LABELS: Record<FindingOrdering, string> = {
		severity: 'Severity (critical first)',
		'-severity': 'Severity (info first)',
		file: 'File (A–Z)',
		'-file': 'File (Z–A)',
		tool: 'Tool',
		rule: 'Rule',
		category: 'Category',
		'-created': 'Newest first',
		created: 'Oldest first'
	};

	// ── URL state ──────────────────────────────────────────────────────
	const sp = $derived(page.url.searchParams);
	const kind = $derived<'static' | 'ai'>(sp.get('kind') === 'ai' ? 'ai' : 'static');
	const severities = $derived(
		csvParam(sp.get('sev')).filter((s): s is Severity =>
			(SEVERITIES as readonly string[]).includes(s)
		)
	);
	const tool = $derived(sp.get('tool') ?? '');
	const category = $derived(sp.get('cat') ?? '');
	const origin = $derived(sp.get('origin') ?? '');
	const counted = $derived(sp.get('counted') ?? '');
	const consensus = $derived(sp.get('consensus') ?? '');
	const grounding = $derived(sp.get('grounding') ?? '');
	const sample = $derived(sp.get('sample') ?? '');
	const file = $derived(sp.get('file') ?? '');
	const rule = $derived(sp.get('rule') ?? '');
	const issue = $derived(sp.get('issue') ?? '');
	const q = $derived(sp.get('q') ?? '');
	const order = $derived<FindingOrdering>(
		(FINDING_ORDERINGS as readonly string[]).includes(sp.get('order') ?? '')
			? (sp.get('order') as FindingOrdering)
			: 'severity'
	);
	const pageNo = $derived(intParam(sp.get('fpage'), 1));

	/** Filters outside the four faceted dimensions. */
	const baseFilters = $derived<FindingFilters>({
		is_ai: kind === 'ai',
		counted: counted === 'yes' ? true : counted === 'no' ? false : undefined,
		consensus: kind === 'ai' && consensus ? consensus === 'yes' : undefined,
		grounding: kind === 'ai' && grounding ? (grounding as FindingFilters['grounding']) : undefined,
		sample: kind === 'ai' && sample !== '' ? Number(sample) : undefined,
		file: file || undefined,
		rule: rule || undefined,
		issue_key: issue || undefined,
		q: q || undefined
	});

	const filters = $derived<FindingFilters>({
		...baseFilters,
		severity: severities.length ? severities : undefined,
		tool: kind === 'static' && tool ? tool : undefined,
		category: category ? (category as FindingFilters['category']) : undefined,
		origin: origin ? (origin as FindingFilters['origin']) : undefined,
		ordering: order,
		page: pageNo,
		per_page: PER_PAGE
	});

	const filterKey = $derived(JSON.stringify(filters));

	function update(updates: Record<string, string | number | null>, resetPage = true) {
		void replaceParams(page.url, resetPage ? { ...updates, fpage: null } : updates);
	}

	// ── Data ───────────────────────────────────────────────────────────
	let data = $state<FindingPage | null>(null);
	let facets = $state<Partial<FindingFacets>>({});
	let loading = $state(true);
	let refreshing = $state(false);
	let error = $state('');
	let controller: AbortController | null = null;

	/**
	 * One request: the page plus its facets. The server counts each facet
	 * (severity, tool, category, origin) over every filter except that
	 * facet's own dimension, so a selected value never hides its siblings.
	 */
	async function load(silent: boolean) {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		if (!silent) refreshing = true;
		try {
			const pageData = await listRunFindings(
				run.id,
				{ ...filters, facets: true },
				{ signal: mine.signal }
			);
			data = pageData;
			facets = pageData.facets ?? {};
			error = '';
			applyPendingSelection();
		} catch (e) {
			if (isAbortError(e)) return;
			if (!(silent && data)) error = formatApiError(e, 'Could not load the findings.');
		} finally {
			if (controller === mine) {
				loading = false;
				refreshing = false;
			}
		}
	}

	let lastKey = '';
	$effect(() => {
		const key = filterKey;
		void revision;
		untrack(() => {
			const silent = key === lastKey;
			lastKey = key;
			void load(silent);
		});
		return () => controller?.abort();
	});

	// ── Search (debounced into the URL) ────────────────────────────────
	let searchText = $state(untrack(() => q));
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearch(value: string) {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => update({ q: value.trim() || null }), 300);
	}

	// ── Drawer ─────────────────────────────────────────────────────────
	let drawerOpen = $state(false);
	let selected = $state(-1);
	let pendingSelect: 'first' | 'last' | null = null;
	const items = $derived(data?.items ?? []);
	const current = $derived<Finding | null>(selected >= 0 ? (items[selected] ?? null) : null);
	const position = $derived(
		data && selected >= 0
			? { index: (data.page - 1) * data.per_page + selected, total: data.total }
			: null
	);

	function openFinding(index: number) {
		selected = index;
		drawerOpen = true;
	}

	function applyPendingSelection() {
		if (!pendingSelect || !data) return;
		selected = pendingSelect === 'first' ? 0 : data.items.length - 1;
		pendingSelect = null;
	}

	function step(delta: 1 | -1) {
		if (!data) return;
		const next = selected + delta;
		if (next >= 0 && next < items.length) {
			selected = next;
			return;
		}
		const target = data.page + delta;
		if (target < 1 || target > data.pages) return;
		pendingSelect = delta > 0 ? 'first' : 'last';
		update({ fpage: target }, false);
	}

	function showIssue(f: Finding) {
		drawerOpen = false;
		void replaceParams(
			page.url,
			{ kind: f.is_ai ? 'ai' : 'static', issue: f.issue_key },
			FINDING_PARAMS
		);
	}

	// ── Display helpers ────────────────────────────────────────────────
	const staticResults = $derived(run.results.filter((r) => r.tool_kind !== 'ai'));
	const aiResult = $derived(run.results.find((r) => r.tool_kind === 'ai') ?? null);
	const staticTotal = $derived(staticResults.reduce((n, r) => n + (r.findings?.total ?? 0), 0));
	const staticProblems = $derived(toolProblems(run.results).filter((p) => !p.isAi));

	const kindItems = $derived([
		{
			value: 'static',
			label: 'Static',
			icon: ScanSearch,
			// A lower bound while the run is active or a static tool has no data.
			badge: `${staticProblems.length > 0 || isRunActive(run.status) ? '≥ ' : ''}${staticTotal}`
		},
		{
			value: 'ai',
			label: 'AI (per sample)',
			icon: Sparkles,
			badge: !aiResult
				? undefined
				: toolProblems([aiResult]).length > 0
					? '—'
					: `${isRunActive(run.status) ? '≥ ' : ''}${aiResult.findings?.total ?? 0}`,
			disabled: !aiResult,
			title: aiResult ? 'Every finding of every review sample' : 'No AI reviewer in this plan'
		}
	]);

	const countLabel = (n: number | undefined) => (n == null ? '' : ` (${n})`);

	const toolItems = $derived(
		Object.entries(facets.tool ?? {}).map(([slug, n]) => ({
			value: slug,
			label: `${slug}${countLabel(n)}`
		}))
	);
	const categoryItems = $derived(
		Object.entries(facets.category ?? {}).map(([c, n]) => ({
			value: c,
			label: `${categoryLabel(c)}${countLabel(n)}`
		}))
	);
	const originItems = $derived(
		Object.entries(facets.origin ?? {}).map(([o, n]) => ({
			value: o,
			label: `${o}${countLabel(n)}`
		}))
	);
	const sampleItems = $derived(
		[...run.review_samples]
			.sort((a, b) => a.index - b.index)
			.map((s) => ({
				value: String(s.index),
				label: `Sample #${s.index} · ${s.status.replace(/_/g, ' ')}`
			}))
	);
	const orderItems = FINDING_ORDERINGS.map((o) => ({
		value: o,
		label: ORDER_LABELS[o]
	}));

	function toggleSeverity(s: Severity) {
		const next = severities.includes(s) ? severities.filter((x) => x !== s) : [...severities, s];
		update({
			sev: next.length ? SEVERITIES.filter((x) => next.includes(x)).join(',') : null
		});
	}

	const activeTags = $derived.by((): FilterTag[] => {
		const tags: FilterTag[] = [];
		if (severities.length)
			tags.push({
				key: 'sev',
				label: `Severity: ${severities.join(', ')}`,
				onRemove: () => update({ sev: null })
			});
		if (kind === 'static' && tool)
			tags.push({
				key: 'tool',
				label: `Tool: ${tool}`,
				onRemove: () => update({ tool: null })
			});
		if (category)
			tags.push({
				key: 'cat',
				label: `Category: ${categoryLabel(category)}`,
				onRemove: () => update({ cat: null })
			});
		if (origin)
			tags.push({
				key: 'origin',
				label: `Origin: ${origin}`,
				onRemove: () => update({ origin: null })
			});
		if (counted)
			tags.push({
				key: 'counted',
				label: counted === 'yes' ? 'Counted only' : 'Not counted only',
				onRemove: () => update({ counted: null })
			});
		if (kind === 'ai' && consensus)
			tags.push({
				key: 'consensus',
				label: consensus === 'yes' ? 'Consensus only' : 'No consensus only',
				onRemove: () => update({ consensus: null })
			});
		if (kind === 'ai' && grounding)
			tags.push({
				key: 'grounding',
				label: `Grounding: ${grounding.replace(/_/g, ' ')}`,
				onRemove: () => update({ grounding: null })
			});
		if (kind === 'ai' && sample !== '')
			tags.push({
				key: 'sample',
				label: `Sample #${sample}`,
				onRemove: () => update({ sample: null })
			});
		if (file)
			tags.push({
				key: 'file',
				label: `File: ${file}`,
				onRemove: () => update({ file: null })
			});
		if (rule)
			tags.push({
				key: 'rule',
				label: `Rule: ${rule}`,
				onRemove: () => update({ rule: null })
			});
		if (issue)
			tags.push({
				key: 'issue',
				label: `Issue: ${issue.slice(0, 12)}…`,
				onRemove: () => update({ issue: null })
			});
		if (q)
			tags.push({
				key: 'q',
				label: `Search: ${q}`,
				onRemove: () => {
					searchText = '';
					update({ q: null });
				}
			});
		return tags;
	});

	function clearAll() {
		searchText = '';
		void replaceParams(page.url, { kind: kind === 'ai' ? 'ai' : null }, FINDING_PARAMS);
	}

	const resultsText = $derived(
		data
			? `${data.total.toLocaleString()} ${kind === 'ai' ? 'AI sample' : 'static'} finding${data.total === 1 ? '' : 's'}`
			: ''
	);

	const filtered = $derived(activeTags.length > 0);
	const active = $derived(isRunActive(run.status));
</script>

<div class="space-y-4">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<Segmented
			aria-label="Finding source"
			value={kind}
			items={kindItems}
			onValueChange={(v) => {
				searchText = '';
				void replaceParams(page.url, { kind: v === 'ai' ? 'ai' : null }, FINDING_PARAMS);
			}}
		/>
		<p class="max-w-xl text-xs text-muted-foreground">
			{#if kind === 'static'}
				Every static finding; <span class="font-medium text-foreground">counted</span> ones are in the
				measured scope and feed the totals.
			{:else}
				Each review sample's findings. Only consensus clusters with grounded evidence are counted
				(see AI review).
			{/if}
		</p>
	</div>

	{#if kind === 'static' && staticProblems.length > 0}
		<p class="flex items-start gap-1.5 text-xs text-warning-fg">
			<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
			Findings of {staticProblems.map(problemLabel).join(', ')} are missing: these tools did not succeed.
		</p>
	{/if}

	<FilterBar
		searchPlaceholder="Search title, description, rule or path…"
		bind:searchValue={searchText}
		onSearchInput={onSearch}
		{activeTags}
		onClearAll={clearAll}
		{resultsText}
		page={data?.page}
		pages={data?.pages}
		onGoToPage={(p) => update({ fpage: p }, false)}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Severity</span>
				{#each SEVERITIES as s (s)}
					<button
						type="button"
						class={cn('fb-chip', severities.includes(s) && 'fb-chip-on')}
						aria-pressed={severities.includes(s)}
						onclick={() => toggleSeverity(s)}
					>
						<span class="capitalize">{s}</span>
						{#if facets.severity}<span class="tabular-nums opacity-70"
								>{facets.severity[s] ?? 0}</span
							>{/if}
					</button>
				{/each}
			</div>
			<div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
				{#if kind === 'static'}
					<Select.Simple
						size="sm"
						aria-label="Tool"
						value={tool}
						items={toolItems}
						placeholder="All tools"
						clearLabel="All tools"
						onValueChange={(v) => update({ tool: v || null })}
					/>
				{:else}
					<Select.Simple
						size="sm"
						aria-label="Review sample"
						value={sample}
						items={sampleItems}
						placeholder="All samples"
						clearLabel="All samples"
						onValueChange={(v) => update({ sample: v === '' ? null : v })}
					/>
				{/if}
				<Select.Simple
					size="sm"
					aria-label="Category"
					value={category}
					items={categoryItems}
					placeholder="All categories"
					clearLabel="All categories"
					onValueChange={(v) => update({ cat: v || null })}
				/>
				<Select.Simple
					size="sm"
					aria-label="File origin"
					value={origin}
					items={originItems}
					placeholder="All origins"
					clearLabel="All origins"
					onValueChange={(v) => update({ origin: v || null })}
				/>
				<Select.Simple
					size="sm"
					aria-label="Order"
					value={order}
					items={orderItems}
					onValueChange={(v) => update({ order: v === 'severity' ? null : v })}
				/>
			</div>
			<div class="flex flex-wrap items-center gap-3">
				<Segmented
					size="sm"
					aria-label="Counted"
					value={counted || 'all'}
					items={[
						{ value: 'all', label: 'All' },
						{ value: 'yes', label: 'Counted' },
						{ value: 'no', label: 'Not counted' }
					]}
					onValueChange={(v) => update({ counted: v === 'all' ? null : v })}
				/>
				{#if kind === 'ai'}
					<Segmented
						size="sm"
						aria-label="Consensus"
						value={consensus || 'all'}
						items={[
							{ value: 'all', label: 'All' },
							{ value: 'yes', label: 'Consensus' },
							{ value: 'no', label: 'No consensus' }
						]}
						onValueChange={(v) => update({ consensus: v === 'all' ? null : v })}
					/>
					<Select.Simple
						size="sm"
						class="w-48"
						aria-label="Grounding"
						value={grounding}
						items={GROUNDINGS.map((g) => ({
							value: g,
							label: g.replace(/_/g, ' ')
						}))}
						placeholder="Any grounding"
						clearLabel="Any grounding"
						onValueChange={(v) => update({ grounding: v || null })}
					/>
				{/if}
			</div>
		{/snippet}
	</FilterBar>

	{#if error}
		<ErrorAlert message={error} onretry={() => load(false)} />
	{/if}

	{#if loading && !data}
		<LoadingState label="loading findings" />
	{:else if data && items.length === 0}
		{#if filtered}
			<EmptyState
				icon={SearchX}
				title="No findings match these filters"
				description="Remove a filter or clear them all."
			>
				{#snippet action()}
					<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>
				{/snippet}
			</EmptyState>
		{:else if active}
			<EmptyState
				icon={Hourglass}
				title="No findings yet"
				description="Tools are still running; findings appear as each tool finishes."
			/>
		{:else if kind === 'ai' && !aiResult}
			<EmptyState icon={Sparkles} title="No AI reviewer in this plan" />
		{:else if kind === 'ai' && aiResult && !isToolOk(aiResult.status)}
			<EmptyState
				icon={TriangleAlert}
				title="The AI review did not succeed"
				description={aiResult.error_message || 'No review sample produced findings.'}
			/>
		{:else if kind === 'static' && staticProblems.length > 0}
			<EmptyState
				icon={TriangleAlert}
				title="No static findings from the tools that succeeded"
				description="{staticProblems.length} tool{staticProblems.length === 1
					? ''
					: 's'} did not succeed, so this is not a clean result."
			/>
		{:else}
			<EmptyState
				icon={CircleCheck}
				title={kind === 'ai' ? 'The reviewers reported no findings' : 'No static findings'}
				description={kind === 'ai'
					? 'Every review sample finished without a finding.'
					: 'Every static tool succeeded or was not applicable and reported nothing.'}
			/>
		{/if}
	{:else if data}
		<Card.Root class="py-0">
			<Card.Content class="p-0">
				<div
					class={cn('table-scroll-wrapper transition-opacity', refreshing && 'opacity-60')}
					aria-busy={refreshing}
				>
					<table class="table-card-mobile">
						<thead>
							<tr>
								<th>Severity</th>
								<th>Finding</th>
								<th>Location</th>
								<th>{kind === 'ai' ? 'Sample' : 'Tool'}</th>
								<th class="hidden lg:table-cell">Category</th>
								<th>Counted</th>
							</tr>
						</thead>
						<tbody>
							{#each items as f, i (f.id)}
								<tr class="relative cursor-pointer">
									<td data-label="Severity">
										<Badge class={severityColors[f.severity]}>{f.severity}</Badge>
									</td>
									<td data-label="Finding" class="max-w-md">
										<button
											type="button"
											class="block w-full cursor-pointer text-left after:absolute after:inset-0 after:content-[''] focus-visible:outline-none focus-visible:after:rounded focus-visible:after:ring-2 focus-visible:after:ring-ring/50"
											onclick={() => openFinding(i)}
										>
											<span class="line-clamp-2 break-words text-sm font-medium"
												>{f.title || f.rule_id || 'Finding'}</span
											>
											{#if f.rule_id && f.rule_id !== f.title && !(f.is_ai && f.rule_id === f.category)}
												<span class="block truncate font-mono text-[11px] text-muted-foreground"
													>{f.rule_id}</span
												>
											{/if}
										</button>
									</td>
									<td data-label="Location" class="max-w-xs">
										<div class="min-w-0 flex-1">
											<span
												class="block truncate font-mono text-xs"
												title={fmtLocation(f.file_path, f.line_number, f.end_line)}
												>{fmtLocation(f.file_path, f.line_number, f.end_line)}</span
											>
											{#if f.file_origin !== 'authored'}
												<span class="text-[11px] text-muted-foreground">{f.file_origin}</span>
											{/if}
										</div>
									</td>
									<td data-label={kind === 'ai' ? 'Sample' : 'Tool'} class="whitespace-nowrap">
										{#if f.is_ai}
											<div class="flex flex-col items-start gap-0.5">
												<span class="font-mono text-xs">#{f.review_sample_index ?? '—'}</span>
												{#if f.grounding}
													<StatusBadge status={f.grounding} tones={groundingTones} />
												{/if}
											</div>
										{:else}
											<span class="font-mono text-xs">{f.tool_slug}</span>
										{/if}
									</td>
									<td data-label="Category" class="hidden text-xs lg:table-cell">
										{categoryLabel(f.category)}
										{#if f.cwe.length}<span
												class="block font-mono text-[11px] text-muted-foreground"
												>{f.cwe.join(', ')}</span
											>{/if}
									</td>
									<td data-label="Counted" class="relative z-[1]">
										{#if f.counted}
											<span class="inline-flex items-center gap-1 text-xs text-success-fg">
												<CircleCheck class="size-3.5" aria-hidden="true" />Counted
											</span>
										{:else}
											<Hint text={uncountedReason(f)} underline={false}>
												<span class="inline-flex items-center gap-1 text-xs text-muted-foreground">
													<CircleMinus class="size-3.5" aria-hidden="true" />No
												</span>
											</Hint>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</div>

<FindingDrawer
	bind:open={drawerOpen}
	finding={current}
	runId={run.id}
	{position}
	onprev={() => step(-1)}
	onnext={() => step(1)}
	onissue={showIssue}
	onfile={onfile
		? (path) => {
				drawerOpen = false;
				onfile(path);
			}
		: undefined}
/>
