<script lang="ts">
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
import ChevronDown from '@lucide/svelte/icons/chevron-down';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import EyeOff from '@lucide/svelte/icons/eye-off';
import Eye from '@lucide/svelte/icons/eye';
import Loader from '@lucide/svelte/icons/loader-circle';
import { getAnalysisFindings, suppressFinding, unsuppressFinding, type AnalysisFinding } from '$lib/api/analysis';
import { severityColors } from '$lib/constants/analysis';

interface Props {
	taskId: string;
	availableAnalyzers: string[];
	initialAnalyzerFilter?: string;
}

let { taskId, availableAnalyzers, initialAnalyzerFilter = '' }: Props = $props();

// State
let findings = $state<AnalysisFinding[]>([]);
let total = $state(0);
let currentPage = $state(1);
let loading = $state(false);
let loadingMore = $state(false);
let expandedIds = $state(new Set<number>());
let suppressingId = $state<number | null>(null);
let suppressReasonInputId = $state<number | null>(null);
let suppressReason = $state('');

// Filters
let filterSeverity = $state('');
let filterCategory = $state('');
let filterAnalyzer = $state(initialAnalyzerFilter);
let filterFilePath = $state('');
let includeSuppressed = $state(false);
let groupBy = $state<'none' | 'analyzer' | 'severity' | 'file'>('none');

const PER_PAGE = 25;

const severities = ['critical', 'high', 'medium', 'low', 'info'];
const categories = ['security', 'quality', 'performance', 'style', 'best_practice', 'accessibility', 'seo'];

let filePathDebounce: ReturnType<typeof setTimeout> | null = null;

async function fetchFindings(page = 1, append = false) {
	if (append) {
		loadingMore = true;
	} else {
		loading = true;
		if (!append) findings = [];
	}
	try {
		const data = await getAnalysisFindings(taskId, {
			page,
			per_page: PER_PAGE,
			severity: filterSeverity || undefined,
			category: filterCategory || undefined,
			analyzer: filterAnalyzer || undefined,
			file_path: filterFilePath || undefined,
			include_suppressed: includeSuppressed || undefined,
		});
		if (append) {
			findings = [...findings, ...data.items];
		} else {
			findings = data.items;
		}
		total = data.total;
		currentPage = page;
	} finally {
		loading = false;
		loadingMore = false;
	}
}

$effect(() => {
	// Re-fetch when filters change (except file_path which is debounced)
	void filterSeverity;
	void filterCategory;
	void filterAnalyzer;
	void includeSuppressed;
	fetchFindings(1);
});

// Expose a way to re-fetch when the analyzer filter changes from outside
$effect(() => {
	filterAnalyzer = initialAnalyzerFilter;
});

const activeFilters = $derived.by(() => {
	const tags: FilterTag[] = [];
	if (filterSeverity) tags.push({ key: 'sev', label: `Severity: ${filterSeverity}`, onRemove: () => { filterSeverity = ''; } });
	if (filterCategory) tags.push({ key: 'cat', label: `Category: ${filterCategory.replace('_', ' ')}`, onRemove: () => { filterCategory = ''; } });
	if (filterAnalyzer) tags.push({ key: 'anlz', label: `Analyzer: ${filterAnalyzer}`, onRemove: () => { filterAnalyzer = ''; } });
	if (filterFilePath) tags.push({ key: 'path', label: `Path: ${filterFilePath}`, onRemove: () => { filterFilePath = ''; } });
	if (includeSuppressed) tags.push({ key: 'supp', label: 'Show suppressed', onRemove: () => { includeSuppressed = false; } });
	return tags;
});

const resultsText = $derived(total > 0 ? `${total} finding${total !== 1 ? 's' : ''}` : '');

function onFilePathInput(e: Event) {
	filterFilePath = (e.target as HTMLInputElement).value;
	if (filePathDebounce) clearTimeout(filePathDebounce);
	filePathDebounce = setTimeout(() => fetchFindings(1), 350);
}

function toggleExpand(id: number) {
	if (expandedIds.has(id)) {
		expandedIds.delete(id);
	} else {
		expandedIds.add(id);
	}
	expandedIds = new Set(expandedIds);
}

async function handleSuppress(finding: AnalysisFinding) {
	suppressingId = finding.id;
	try {
		await suppressFinding(taskId, finding.id, suppressReason);
		suppressReasonInputId = null;
		suppressReason = '';
		await fetchFindings(1);
	} finally {
		suppressingId = null;
	}
}

async function handleUnsuppress(finding: AnalysisFinding) {
	suppressingId = finding.id;
	try {
		await unsuppressFinding(taskId, finding.id);
		await fetchFindings(1);
	} finally {
		suppressingId = null;
	}
}

const remaining = $derived(total - findings.length);

// Grouped view helpers
type Group = { key: string; label: string; items: AnalysisFinding[] };
const grouped = $derived.by<Group[]>(() => {
	if (groupBy === 'none') return [];
	const map = new Map<string, AnalysisFinding[]>();
	for (const f of findings) {
		const key =
			groupBy === 'analyzer'
				? f.analyzer_name
				: groupBy === 'severity'
					? f.severity
					: f.file_path || '(no file)';
		if (!map.has(key)) map.set(key, []);
		map.get(key)!.push(f);
	}
	return Array.from(map.entries()).map(([key, items]) => ({ key, label: key, items }));
});
</script>

<div class="space-y-3">
	<!-- Filter bar -->
	<FilterBar
		searchPlaceholder="Filter by file path…"
		bind:searchValue={filterFilePath}
		onSearchInput={onFilePathInput}
		activeTags={activeFilters}
		resultsText={resultsText}
		onClearAll={() => { filterSeverity = ''; filterCategory = ''; filterAnalyzer = ''; filterFilePath = ''; includeSuppressed = false; }}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Severity</span>
				<button class="fb-chip {filterSeverity === '' ? 'fb-chip-on' : ''}" onclick={() => { filterSeverity = ''; }}>All</button>
				<button class="fb-chip {filterSeverity === 'critical' ? 'fb-chip-red' : ''}" onclick={() => { filterSeverity = 'critical'; }}>Critical</button>
				<button class="fb-chip {filterSeverity === 'high' ? 'fb-chip-orange' : ''}" onclick={() => { filterSeverity = 'high'; }}>High</button>
				<button class="fb-chip {filterSeverity === 'medium' ? 'fb-chip-amber' : ''}" onclick={() => { filterSeverity = 'medium'; }}>Medium</button>
				<button class="fb-chip {filterSeverity === 'low' ? 'fb-chip-blue' : ''}" onclick={() => { filterSeverity = 'low'; }}>Low</button>
				<button class="fb-chip {filterSeverity === 'info' ? 'fb-chip-slate' : ''}" onclick={() => { filterSeverity = 'info'; }}>Info</button>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Analyzer</span>
				<select class="fb-select" bind:value={filterAnalyzer}>
					<option value="">All analyzers</option>
					{#each availableAnalyzers as name}
						<option value={name}>{name}</option>
					{/each}
				</select>
				<span class="fb-group-label ml-2">Category</span>
				<select class="fb-select" bind:value={filterCategory}>
					<option value="">All categories</option>
					{#each categories as c}
						<option value={c}>{c.replace('_', ' ')}</option>
					{/each}
				</select>
				<button class="fb-chip ml-2 {includeSuppressed ? 'fb-chip-slate' : ''}" onclick={() => { includeSuppressed = !includeSuppressed; }}>Show suppressed</button>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Group by</span>
				<button class="fb-chip {groupBy === 'none' ? 'fb-chip-on' : ''}" onclick={() => { groupBy = 'none'; }}>None</button>
				<button class="fb-chip {groupBy === 'severity' ? 'fb-chip-on' : ''}" onclick={() => { groupBy = 'severity'; }}>Severity</button>
				<button class="fb-chip {groupBy === 'analyzer' ? 'fb-chip-on' : ''}" onclick={() => { groupBy = 'analyzer'; }}>Analyzer</button>
				<button class="fb-chip {groupBy === 'file' ? 'fb-chip-on' : ''}" onclick={() => { groupBy = 'file'; }}>File</button>
			</div>
		{/snippet}
	</FilterBar>

	<!-- Table -->
	{#if loading}
		<div class="flex items-center justify-center py-10 text-muted-foreground">

			<Loader class="mr-2 h-4 w-4 animate-spin" /> Loading findings…
		</div>
	{:else if findings.length === 0}
		<div class="rounded-md border border-dashed border-border py-10 text-center text-sm text-muted-foreground">
			No findings match your filters.
		</div>
	{:else}
		<div class="overflow-x-auto rounded-md border border-border">
			<table class="w-full text-sm">
				<thead>
					<tr class="border-b border-border bg-muted/30">
						<th class="w-6 px-2 py-2"></th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Severity</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Title</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Analyzer</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Location</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Rule</th>
						<th class="w-8 px-2 py-2"></th>
					</tr>
				</thead>
				<tbody>
					{#if groupBy !== 'none'}
						{#each grouped as group}
							<tr class="border-b border-border bg-muted/50">
								<td colspan="7" class="px-3 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wide">
									{group.label} — {group.items.length} finding{group.items.length !== 1 ? 's' : ''}
								</td>
							</tr>
							{#each group.items as finding}
								{@render findingRow(finding)}
							{/each}
						{/each}
					{:else}
						{#each findings as finding}
							{@render findingRow(finding)}
						{/each}
					{/if}
				</tbody>
			</table>
		</div>

		{#if remaining > 0}
			<div class="flex justify-center">
				<Button
					variant="outline"
					size="sm"
					disabled={loadingMore}
					onclick={() => fetchFindings(currentPage + 1, true)}
				>
					{#if loadingMore}
						<Loader class="mr-1.5 h-3 w-3 animate-spin" />
					{/if}
					Load more ({remaining} remaining)
				</Button>
			</div>
		{/if}
	{/if}
</div>

{#snippet findingRow(finding: AnalysisFinding)}
	<tr
		class={`border-b border-border transition-colors hover:bg-muted/30 cursor-pointer ${finding.suppressed ? 'opacity-50' : ''}`}
		onclick={() => toggleExpand(finding.id)}
	>
		<td class="px-2 py-2.5 text-muted-foreground">
			{#if expandedIds.has(finding.id)}
				<ChevronDown class="h-3.5 w-3.5" />
			{:else}
				<ChevronRight class="h-3.5 w-3.5" />
			{/if}
		</td>
		<td class="px-3 py-2.5">
			<Badge variant="outline" class={`text-xs ${severityColors[finding.severity] ?? ''}`}>
				{finding.severity}
			</Badge>
		</td>
		<td class="px-3 py-2.5 font-medium max-w-xs truncate">{finding.title}</td>
		<td class="px-3 py-2.5">
			<Badge variant="outline" class="text-xs">{finding.analyzer_name}</Badge>
		</td>
		<td class="px-3 py-2.5 text-xs text-muted-foreground font-mono">
			{#if finding.file_path}
				{finding.file_path}{finding.line_number != null ? `:${finding.line_number}` : ''}
			{:else}
				—
			{/if}
		</td>
		<td class="px-3 py-2.5 text-xs text-muted-foreground font-mono">
			{finding.rule_id || '—'}
		</td>
		<td class="px-2 py-2.5" onclick={(e) => e.stopPropagation()}>
			{#if finding.suppressed}
				<Button
					variant="ghost"
					size="icon"
					class="h-6 w-6"
					title="Unsuppress finding"
					disabled={suppressingId === finding.id}
					onclick={() => handleUnsuppress(finding)}
				>
					{#if suppressingId === finding.id}
						<Loader class="h-3 w-3 animate-spin" />
					{:else}
						<Eye class="h-3 w-3" />
					{/if}
				</Button>
			{:else}
				<Button
					variant="ghost"
					size="icon"
					class="h-6 w-6 text-muted-foreground hover:text-foreground"
					title="Mark as false positive"
					onclick={() => { suppressReasonInputId = finding.id; suppressReason = ''; }}
				>
					<EyeOff class="h-3 w-3" />
				</Button>
			{/if}
		</td>
	</tr>

	{#if expandedIds.has(finding.id)}
		<tr class="border-b border-border bg-muted/10">
			<td colspan="7" class="px-4 py-3">
				<div class="space-y-2 text-sm">
					{#if finding.description}
						<p class="text-foreground">{finding.description}</p>
					{/if}
					{#if finding.suggestion}
						<p class="text-emerald-400"><span class="font-medium">Fix: </span>{finding.suggestion}</p>
					{/if}
					{#if finding.code_snippet}
						<pre class="overflow-x-auto rounded bg-muted p-2 text-xs font-mono">{finding.code_snippet}</pre>
					{/if}
					<div class="flex flex-wrap gap-3 text-xs text-muted-foreground">
						<span>Category: {finding.category}</span>
						<span>Confidence: {finding.confidence}</span>
						{#if finding.suppression_reason}
							<span class="text-amber-400">Suppressed: {finding.suppression_reason}</span>
						{/if}
					</div>
				</div>
			</td>
		</tr>
	{/if}

	{#if suppressReasonInputId === finding.id}
		<tr class="border-b border-border bg-amber-500/5">
			<td colspan="7" class="px-4 py-3">
				<div class="flex items-center gap-2">
					<input
						type="text"
						class="h-8 flex-1 rounded-md border border-input bg-background px-3 text-sm"
						placeholder="Reason (optional)…"
						bind:value={suppressReason}
						onkeydown={(e) => { if (e.key === 'Enter') handleSuppress(finding); if (e.key === 'Escape') suppressReasonInputId = null; }}
					/>
					<Button size="sm" disabled={suppressingId === finding.id} onclick={() => handleSuppress(finding)}>
						{#if suppressingId === finding.id}
							<Loader class="mr-1 h-3 w-3 animate-spin" />
						{/if}
						Suppress
					</Button>
					<Button variant="ghost" size="sm" onclick={() => { suppressReasonInputId = null; }}>Cancel</Button>
				</div>
			</td>
		</tr>
	{/if}
{/snippet}
