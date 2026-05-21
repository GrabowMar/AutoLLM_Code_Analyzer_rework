<script lang="ts">
import { page } from '$app/stores';
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import { Separator } from '$lib/components/ui/separator';
import { downloadExport } from '$lib/api/export';
import Download from '@lucide/svelte/icons/download';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import Microscope from '@lucide/svelte/icons/microscope';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import Ban from '@lucide/svelte/icons/ban';
import {
getAnalysisTask,
getAnalysisResults,
getAnalysisFindings,
cancelAnalysisTask,
type AnalysisTask,
type AnalysisResult,
type AnalysisFinding,
type PaginatedFindings,
} from '$lib/api/client';
import { subscribe } from '$lib/api/sse';
import { statusColors, severityColors } from '$lib/constants/analysis';
import { formatDuration, formatDate } from '$lib/utils/analysis';
import AnalyzerSummary from '$lib/components/analysis/AnalyzerSummary.svelte';

const taskId = $derived($page.params.taskId ?? '');

let task = $state<AnalysisTask | null>(null);
let results = $state<AnalysisResult[]>([]);
let loading = $state(true);
let error = $state('');
let cancelling = $state(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

let findingsMap = $state<Record<number, AnalysisFinding[]>>({});
let findingsPagination = $state<Record<number, { page: number; pages: number; total: number }>>({});
let findingsLoading = $state<Record<number, boolean>>({});
let expandedFindings = $state<Record<number, boolean>>({});

let activeSection = $state('summary');

const sections = $derived([
{ id: 'summary', label: 'Summary' },
...results.map((r) => ({ id: `result-${r.id}`, label: r.analyzer_name })),
]);

function scrollToSection(id: string) {
activeSection = id;
document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function fetchData() {
try {
const [t, r] = await Promise.all([getAnalysisTask(taskId), getAnalysisResults(taskId)]);
task = t;
results = r;
error = '';
} catch (e: any) {
error = e?.detail || e?.message || 'Failed to load analysis task';
} finally {
loading = false;
}
}

function startPolling() {
stopPolling();
pollTimer = setInterval(async () => {
if (task && (task.status === 'running' || task.status === 'pending')) {
try {
const [t, r] = await Promise.all([getAnalysisTask(taskId), getAnalysisResults(taskId)]);
task = t;
results = r;
} catch {
// ignore poll errors
}
} else {
stopPolling();
}
}, 3000);
}

function stopPolling() {
if (pollTimer) {
clearInterval(pollTimer);
pollTimer = null;
}
}

async function handleCancel() {
if (!task || cancelling) return;
cancelling = true;
try {
await cancelAnalysisTask(taskId);
await fetchData();
} catch {
// ignore
} finally {
cancelling = false;
}
}

async function loadFindings(resultObj: AnalysisResult, pageNum: number = 1) {
const rid = resultObj.id;
findingsLoading[rid] = true;
try {
const data: PaginatedFindings = await getAnalysisFindings(taskId, {
analyzer: resultObj.analyzer_name,
page: pageNum,
per_page: 25,
});
if (pageNum === 1) {
findingsMap[rid] = data.items;
} else {
findingsMap[rid] = [...(findingsMap[rid] ?? []), ...data.items];
}
findingsPagination[rid] = { page: data.page, pages: data.pages, total: data.total };
} catch {
// ignore
} finally {
findingsLoading[rid] = false;
}
}

function toggleFindingExpand(findingId: number) {
expandedFindings[findingId] = !expandedFindings[findingId];
}

$effect(() => {
if (task && (task.status === 'running' || task.status === 'pending')) {
startPolling();
}
return () => stopPolling();
});

onMount(() => {
fetchData();

const cleanupSse = subscribe([`analysis:${taskId}`], async () => {
try {
const [t, r] = await Promise.all([getAnalysisTask(taskId), getAnalysisResults(taskId)]);
task = t;
results = r;
if (pollTimer) stopPolling();
} catch {
// refetch failed — polling will cover this
}
});

return () => {
stopPolling();
cleanupSse();
};
});
</script>

<svelte:head>
<title>{task?.name || `Analysis ${taskId}`} - LLM Lab</title>
</svelte:head>

{#if loading}
<Card.Root>
<Card.Content class="flex items-center justify-center py-20">
<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
</Card.Content>
</Card.Root>
{:else if error}
<div class="space-y-6">
<div class="flex items-center gap-2 text-sm text-muted-foreground">
<Button variant="ghost" size="sm" href="/analysis" class="gap-1.5 px-2">
<ArrowLeft class="h-3.5 w-3.5" />
Analysis Hub
</Button>
</div>
<Card.Root class="border-red-500/20">
<Card.Content class="p-8 text-center">
<AlertTriangle class="mx-auto h-10 w-10 text-red-400 mb-4" />
<h2 class="text-lg font-semibold text-red-400 mb-2">Task Not Found</h2>
<p class="text-sm text-muted-foreground">{error}</p>
<Button variant="outline" size="sm" href="/analysis" class="mt-4">Back to Analysis Hub</Button>
</Card.Content>
</Card.Root>
</div>
{:else if task}
<div class="space-y-6">
<!-- Breadcrumb -->
<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
	<a href="/analysis" class="hover:text-foreground transition-colors flex items-center gap-1">
		<ArrowLeft class="h-3.5 w-3.5" />
		<span class="font-medium text-foreground">Analysis Hub</span>
	</a>
	<span>/</span>
	<span class="text-muted-foreground truncate max-w-[300px]">{task.name || taskId}</span>
</nav>

<!-- Header Card -->
<Card.Root class="border-border/60">
<Card.Content class="p-5">
<div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
	<div class="flex items-start gap-4">
		<div class="flex h-12 w-12 items-center justify-center rounded-lg bg-muted">
			<Microscope class="h-6 w-6 text-muted-foreground" />
		</div>
		<div>
			<h1 class="text-xl font-semibold">{task.name || 'Analysis Task'}</h1>
			<div class="flex flex-wrap items-center gap-2 mt-1.5">
				<Badge variant="outline" class="{statusColors[task.status] ?? ''} {task.status === 'running' ? 'animate-pulse' : ''}">{task.status}</Badge>
			</div>
			<p class="text-xs text-muted-foreground mt-1 font-mono truncate max-w-full">
				{taskId}{#if task.duration_seconds != null} · {formatDuration(task.duration_seconds)}{/if} · {task.findings_count} findings
			</p>
		</div>
	</div>
	<div class="hidden sm:flex items-center gap-3 text-sm text-muted-foreground">
		<div class="text-center">
			<div class="text-2xl font-bold text-foreground">{task.findings_count}</div>
			<div class="text-xs">Findings</div>
		</div>
		<Separator orientation="vertical" class="h-8" />
		<div class="text-center">
			<div class="text-2xl font-bold text-foreground">{results.length}</div>
			<div class="text-xs">Analyzers</div>
		</div>
	</div>
</div>
<div class="mt-4 flex flex-wrap items-center gap-2">
	{#if task.status === 'running' || task.status === 'pending'}
		<Button variant="outline" size="sm" onclick={handleCancel} disabled={cancelling} class="border-amber-500/30 text-amber-400 hover:bg-amber-500/10">
			<Ban class="mr-1.5 h-3.5 w-3.5" />
			{cancelling ? 'Cancelling…' : 'Cancel'}
		</Button>
	{/if}
	<Button variant="outline" size="sm" onclick={fetchData}>
		<RefreshCw class="mr-1.5 h-3.5 w-3.5" /> Refresh
	</Button>
	<details class="relative">
		<summary class="list-none">
			<Button variant="outline" size="sm" tag="span">
				<Download class="mr-1.5 h-3.5 w-3.5" /> Export
			</Button>
		</summary>
		<div class="absolute left-0 z-50 mt-1 w-52 rounded-md border bg-popover p-1 shadow-md">
			<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport(`findings.csv?task_id=${taskId}`)}>Findings CSV</button>
			<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport(`findings.json?task_id=${taskId}`)}>Findings JSON</button>
			<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport(`findings.sarif?task_id=${taskId}`)}>Findings SARIF</button>
		</div>
	</details>
</div>
{#if task.error_message}
<div class="mt-3 rounded-md bg-red-500/10 p-3 text-sm text-red-400">
<strong>Error:</strong> {task.error_message}
</div>
{/if}
</Card.Content>
</Card.Root>

{#if task.container_instance_id}
<Card.Root class="border-blue-500/30 bg-blue-500/5">
<Card.Header class="pb-2">
<div class="flex items-center gap-2">
<div class="h-2 w-2 rounded-full {task.status === 'running' ? 'animate-pulse bg-blue-400' : 'bg-blue-400/50'}"></div>
<Card.Title class="text-sm">Live Container</Card.Title>
</div>
</Card.Header>
<Card.Content class="pt-0">
<div class="space-y-1 text-sm">
{#if task.target_url}
<div class="flex items-center gap-2">
<span class="text-muted-foreground">Target URL:</span>
<code class="rounded bg-muted px-1.5 py-0.5 text-xs">{task.target_url}</code>
</div>
{/if}
<div class="flex items-center gap-2">
<span class="text-muted-foreground">Container:</span>
<a
href="/runtime/{task.container_instance_id}"
class="text-xs text-blue-400 underline-offset-2 hover:underline"
>{task.container_instance_id.slice(0, 8)}…</a>
</div>
</div>
</Card.Content>
</Card.Root>
{/if}

<div class="sticky top-0 z-40 -mx-4 bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60">
<nav class="flex gap-1 overflow-x-auto flex-nowrap border-b py-2">
{#each sections as section}
<button
class="rounded-md px-3 py-1.5 text-sm transition-colors whitespace-nowrap {activeSection === section.id ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground'}"
onclick={() => scrollToSection(section.id)}
>
{section.label}
</button>
{/each}
</nav>
</div>

<div id="summary" class="scroll-mt-16 space-y-4">
<h2 class="text-lg font-semibold">Summary</h2>

<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 lg:grid-cols-4">
{#each results as r}
<Card.Root>
<Card.Content class="p-4">
<div class="flex items-center gap-2 mb-2">
<span class="text-sm font-medium">{r.analyzer_name}</span>
<Badge variant="outline" class="ml-auto text-[10px] {statusColors[r.status] ?? ''}">{r.status}</Badge>
</div>
<div class="text-xl font-bold">{r.findings_count} findings</div>
{#if r.duration_seconds != null}
<div class="text-xs text-muted-foreground mt-1">{formatDuration(r.duration_seconds)}</div>
{/if}
</Card.Content>
</Card.Root>
{/each}
</div>

<Card.Root>
<Card.Content class="p-4">
<div class="flex flex-wrap items-center gap-3 sm:gap-6 text-sm">
<div><span class="text-muted-foreground">Duration:</span> <span class="font-mono font-medium">{formatDuration(task.duration_seconds)}</span></div>
<div><span class="text-muted-foreground">Started:</span> <span class="font-mono">{formatDate(task.started_at)}</span></div>
<div><span class="text-muted-foreground">Completed:</span> <span class="font-mono">{formatDate(task.completed_at)}</span></div>
<div><span class="text-muted-foreground">Created:</span> <span class="font-mono">{formatDate(task.created_at)}</span></div>
</div>
</Card.Content>
</Card.Root>

{#if task.results_summary?.by_severity}
<div class="flex flex-wrap gap-2">
{#each Object.entries(task.results_summary.by_severity) as [sev, count]}
<Badge variant="outline" class="{severityColors[sev] ?? ''} text-xs">
{count} {sev}
</Badge>
{/each}
</div>
{/if}

<div class="grid gap-4 md:grid-cols-2">
<Card.Root>
<Card.Header><Card.Title class="text-sm">Task Information</Card.Title></Card.Header>
<Card.Content>
<dl class="grid grid-cols-2 gap-y-2 text-sm">
<dt class="text-muted-foreground">Task ID</dt><dd class="font-mono text-xs truncate">{task.id}</dd>
<dt class="text-muted-foreground">Status</dt><dd><Badge variant="outline" class="{statusColors[task.status] ?? ''} text-xs">{task.status}</Badge></dd>
<dt class="text-muted-foreground">Total Findings</dt><dd class="font-semibold">{task.findings_count}</dd>
<dt class="text-muted-foreground">Analyzers Run</dt><dd>{task.results_count}</dd>
{#if task.generation_job_name}
<dt class="text-muted-foreground">Generation Job</dt><dd class="font-mono text-xs truncate">{task.generation_job_name}</dd>
{/if}
</dl>
</Card.Content>
</Card.Root>
<Card.Root>
<Card.Header><Card.Title class="text-sm">Configuration</Card.Title></Card.Header>
<Card.Content>
{#if task.configuration && Object.keys(task.configuration).length > 0}
<dl class="grid grid-cols-2 gap-y-2 text-sm">
{#if task.configuration.analyzers}
<dt class="text-muted-foreground">Analyzers</dt>
<dd>
<div class="flex flex-wrap gap-1">
{#each task.configuration.analyzers as a}
<Badge variant="secondary" class="text-xs">{a}</Badge>
{/each}
</div>
</dd>
{/if}
{#each Object.entries(task.configuration.settings ?? {}) as [key, val]}
<dt class="text-muted-foreground">{key}</dt><dd class="font-mono text-xs">{JSON.stringify(val)}</dd>
{/each}
</dl>
{:else}
<p class="text-sm text-muted-foreground">No configuration data</p>
{/if}
</Card.Content>
</Card.Root>
</div>
</div>

{#each results as r (r.id)}
<AnalyzerSummary
result={r}
findings={findingsMap[r.id]}
pagination={findingsPagination[r.id]}
findingsLoading={findingsLoading[r.id]}
{expandedFindings}
onLoadFindings={loadFindings}
onToggleExpand={toggleFindingExpand}
/>
{/each}
</div>
{/if}
