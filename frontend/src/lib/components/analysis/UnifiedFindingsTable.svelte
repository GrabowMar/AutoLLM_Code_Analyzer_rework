<script lang="ts">
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
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
	<div class="space-y-2">
		<!-- Row 1: severity pills -->
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="text-xs text-muted-foreground shrink-0">Severity</span>
			<button
				class="rounded-full px-2.5 py-0.5 text-xs font-medium cursor-pointer border transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none {filterSeverity === '' ? 'bg-primary text-primary-foreground border-transparent' : 'border-border hover:bg-muted'}"
				onclick={() => { filterSeverity = ''; }}
			>All</button>
			{#each severities as s}
				{@const colors: Record<string, string> = {
					critical: 'bg-red-500/15 text-red-400 border-red-500/30',
					high: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
					medium: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
					low: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
					info: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
				}}
				<button
					class="rounded-full px-2.5 py-0.5 text-xs font-medium cursor-pointer border transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none {filterSeverity === s ? colors[s] + ' border-current' : 'border-border hover:bg-muted'}"
					onclick={() => { filterSeverity = s; }}
				>{s}</button>
			{/each}
		</div>

		<!-- Row 2: secondary filters -->
		<div class="flex flex-wrap gap-2">
			<select
				class="h-8 rounded-md border border-input bg-background px-2 text-xs"
				bind:value={filterAnalyzer}
			>
				<option value="">All analyzers</option>
				{#each availableAnalyzers as name}
					<option value={name}>{name}</option>
				{/each}
			</select>

			<select
				class="h-8 rounded-md border border-input bg-background px-2 text-xs"
				bind:value={filterCategory}
			>
				<option value="">All categories</option>
				{#each categories as c}
					<option value={c}>{c.replace('_', ' ')}</option>
				{/each}
			</select>

			<input
				type="text"
				class="h-8 w-40 rounded-md border border-input bg-background px-2 text-xs"
				placeholder="Filter by file path…"
				value={filterFilePath}
				oninput={onFilePathInput}
			/>

			<label class="flex h-8 items-center gap-1.5 rounded-md border border-input bg-background px-2 text-xs cursor-pointer">
				<input type="checkbox" class="rounded" bind:checked={includeSuppressed} />
				Show suppressed
			</label>

			<!-- Group by pills -->
			<div class="flex items-center gap-1 ml-auto">
				<span class="text-xs text-muted-foreground">Group</span>
				{#each (['none', 'severity', 'analyzer', 'file'] as const) as g}
					<button
						class="rounded-full px-2 py-0.5 text-xs font-medium cursor-pointer border transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none {groupBy === g ? 'bg-primary text-primary-foreground border-transparent' : 'border-border hover:bg-muted'}"
						onclick={() => { groupBy = g; }}
					>{g === 'none' ? '—' : g}</button>
				{/each}
			</div>

			<span class="flex h-8 items-center text-xs text-muted-foreground">
				{total} finding{total !== 1 ? 's' : ''}
			</span>
		</div>
	</div>

	<!-- Table -->
	{#if loading}
		<div class="flex items-center justify-center py-10 text-muted-foreground">
			<Loader class="mr-2 h-4 w-4 animate-spin" /> Loading findings…
		</div>
	{:else if findings.length === 0}
		<div class="rounded-lg border border-dashed border-border py-10 text-center text-sm text-muted-foreground">
			No findings match your filters.
		</div>
	{:else}
		<div class="overflow-x-auto rounded-lg border border-border">
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
