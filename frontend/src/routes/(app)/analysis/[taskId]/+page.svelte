<script lang="ts">
import { page } from '$app/stores';
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import Download from '@lucide/svelte/icons/download';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import Ban from '@lucide/svelte/icons/ban';
import {
	getAnalysisTask,
	getAnalysisResults,
	cancelAnalysisTask,
	type AnalysisTask,
	type AnalysisResult,
} from '$lib/api/analysis';
import { subscribe } from '$lib/api/sse';
import { downloadExport } from '$lib/api/export';
import { statusColors, severityColors } from '$lib/constants/analysis';
import { formatDuration, formatDate } from '$lib/utils/analysis';
import UnifiedFindingsTable from '$lib/components/analysis/UnifiedFindingsTable.svelte';

const taskId = $derived($page.params.taskId ?? '');

let task = $state<AnalysisTask | null>(null);
let results = $state<AnalysisResult[]>([]);
let loading = $state(true);
let error = $state('');
let cancelling = $state(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

let activeAnalyzerFilter = $state('');

const availableAnalyzers = $derived(results.map((r) => r.analyzer_name));

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
	<div class="flex items-center justify-center py-20">
		<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
	</div>
{:else if error}
	<div class="space-y-4">
		<Button variant="ghost" size="sm" href="/analysis" class="gap-1.5 px-2">
			<ArrowLeft class="h-3.5 w-3.5" /> Analysis Hub
		</Button>
		<Card.Root class="border-red-500/20">
			<Card.Content class="p-8 text-center">
				<AlertTriangle class="mx-auto mb-4 h-10 w-10 text-red-400" />
				<h2 class="mb-2 text-lg font-semibold text-red-400">Task Not Found</h2>
				<p class="text-sm text-muted-foreground">{error}</p>
				<Button variant="outline" size="sm" href="/analysis" class="mt-4">Back to Analysis Hub</Button>
			</Card.Content>
		</Card.Root>
	</div>
{:else if task}
	<!-- Sticky top bar -->
	<div class="sticky top-12 z-20 -mx-3 mb-4 border-b bg-background/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/60 sm:-mx-4 sm:px-4 md:-mx-5 md:px-5">
		<div class="flex items-center gap-2 py-2.5">
			<Button variant="ghost" size="sm" href="/analysis" class="h-7 shrink-0 gap-1 px-2">
				<ArrowLeft class="h-3.5 w-3.5" />
			</Button>

			<div class="min-w-0 flex-1">
				<div class="flex flex-wrap items-center gap-1.5">
					<h1 class="max-w-[200px] truncate text-sm font-semibold sm:max-w-xs">{task.name || 'Analysis Task'}</h1>
					<Badge
						variant="outline"
						class="{statusColors[task.status] ?? ''} text-xs {task.status === 'running' ? 'animate-pulse' : ''}"
					>
						{task.status}
					</Badge>
					{#if task.threshold_status === 'exceeded'}
						<Badge variant="outline" class="border-red-500/30 bg-red-500/15 text-xs text-red-400">
							THRESHOLD EXCEEDED
						</Badge>
					{:else if task.threshold_status === 'passed'}
						<Badge variant="outline" class="border-emerald-500/30 bg-emerald-500/15 text-xs text-emerald-500">
							PASS
						</Badge>
					{/if}
					<span class="hidden text-xs text-muted-foreground sm:inline">
						{task.findings_count} finding{task.findings_count !== 1 ? 's' : ''} · {results.length} analyzer{results.length !== 1 ? 's' : ''}
						{#if task.duration_seconds != null} · {formatDuration(task.duration_seconds)}{/if}
					</span>
				</div>
			</div>

			<div class="flex shrink-0 items-center gap-1">
				{#if task.status === 'running' || task.status === 'pending'}
					<Button
						variant="outline"
						size="sm"
						onclick={handleCancel}
						disabled={cancelling}
						class="h-7 border-amber-500/30 px-2 text-xs text-amber-400 hover:bg-amber-500/10"
					>
						<Ban class="mr-1 h-3 w-3" />
						{cancelling ? 'Cancelling…' : 'Cancel'}
					</Button>
				{/if}
				<Button variant="outline" size="icon" class="h-7 w-7" onclick={fetchData} title="Refresh">
					<RefreshCw class="h-3 w-3" />
				</Button>
				<details class="relative">
					<summary class="list-none">
						<Button variant="outline" size="sm" tag="span" class="h-7 cursor-pointer px-2 text-xs">
							<Download class="mr-1 h-3 w-3" /> Export
						</Button>
					</summary>
					<div class="absolute right-0 z-50 mt-1 w-44 rounded-md border bg-popover p-1 shadow-md">
						<button
							class="w-full rounded px-3 py-1.5 text-left text-xs hover:bg-accent"
							onclick={() => downloadExport(`findings.csv?task_id=${taskId}`)}
						>Findings CSV</button>
						<button
							class="w-full rounded px-3 py-1.5 text-left text-xs hover:bg-accent"
							onclick={() => downloadExport(`findings.json?task_id=${taskId}`)}
						>Findings JSON</button>
						<button
							class="w-full rounded px-3 py-1.5 text-left text-xs hover:bg-accent"
							onclick={() => downloadExport(`findings.sarif?task_id=${taskId}`)}
						>Findings SARIF</button>
					</div>
				</details>
			</div>
		</div>
	</div>

	{#if task.error_message}
		<div class="mb-4 rounded-md bg-red-500/10 p-3 text-sm text-red-400">
			<strong>Error:</strong> {task.error_message}
		</div>
	{/if}

	{#if task.container_instance_id}
		<Card.Root class="mb-4 border-blue-500/30 bg-blue-500/5">
			<Card.Content class="flex flex-wrap items-center gap-4 p-3 text-sm">
				<div class="flex items-center gap-1.5">
					<div class="h-2 w-2 rounded-full {task.status === 'running' ? 'animate-pulse bg-blue-400' : 'bg-blue-400/50'}"></div>
					<span class="text-xs font-medium text-blue-400">Live Container</span>
				</div>
				{#if task.target_url}
					<div class="flex items-center gap-1.5 text-xs">
						<span class="text-muted-foreground">Target:</span>
						<code class="rounded bg-muted px-1.5 py-0.5 text-xs">{task.target_url}</code>
					</div>
				{/if}
				<div class="flex items-center gap-1.5 text-xs">
					<span class="text-muted-foreground">Container:</span>
					<a
						href="/runtime/{task.container_instance_id}"
						class="text-blue-400 underline-offset-2 hover:underline"
					>{task.container_instance_id.slice(0, 8)}…</a>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}

	<!-- Two-panel layout -->
	<div class="flex gap-4">
		<!-- Sidebar -->
		<aside class="hidden w-52 shrink-0 md:block">
			<div class="sticky top-[6.5rem] space-y-0.5">
				<p class="mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
					Analyzers
				</p>

				<button
					class="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-sm transition-colors {activeAnalyzerFilter === '' ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'}"
					onclick={() => { activeAnalyzerFilter = ''; }}
				>
					<span>All</span>
					<span class="font-mono text-xs">{task.findings_count}</span>
				</button>

				{#each results as r (r.id)}
					<button
						class="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors {activeAnalyzerFilter === r.analyzer_name ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'}"
						onclick={() => { activeAnalyzerFilter = r.analyzer_name; }}
					>
						<span class="min-w-0 flex-1 truncate text-left text-xs">{r.analyzer_name}</span>
						<Badge
							variant="outline"
							class="h-4 shrink-0 px-1 py-0 text-[10px] {statusColors[r.status] ?? ''}"
						>{r.status.slice(0, 4)}</Badge>
						<span class="shrink-0 font-mono text-xs">{r.findings_count}</span>
					</button>
					{#if r.duration_seconds != null}
						<p class="-mt-0.5 px-2 pb-0.5 text-[10px] text-muted-foreground/50">
							{formatDuration(r.duration_seconds)}
						</p>
					{/if}
				{/each}

				{#if task.results_summary?.by_severity && Object.keys(task.results_summary.by_severity).length > 0}
					<div class="mt-3 border-t border-border pt-3">
						<p class="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
							By Severity
						</p>
						{#each Object.entries(task.results_summary.by_severity) as [sev, count]}
							{#if (count as number) > 0}
								<div class="flex items-center justify-between px-2 py-0.5 text-xs">
									<span class="capitalize text-muted-foreground">{sev}</span>
									<Badge variant="outline" class="h-4 px-1 py-0 text-[10px] {severityColors[sev] ?? ''}">
										{count as number}
									</Badge>
								</div>
							{/if}
						{/each}
					</div>
				{/if}

				{#if task.results_summary?.suppressed_count}
					<div class="mt-2 px-2 text-[10px] text-muted-foreground/60">
						{task.results_summary.suppressed_count} suppressed
					</div>
				{/if}

				<div class="mt-3 border-t border-border pt-3 px-2 space-y-1 text-[10px] text-muted-foreground">
					{#if task.started_at}
						<div>Started: <span class="font-mono">{formatDate(task.started_at)}</span></div>
					{/if}
					{#if task.completed_at}
						<div>Completed: <span class="font-mono">{formatDate(task.completed_at)}</span></div>
					{/if}
					{#if task.profile_id}
						<div>Profile ID: <span class="font-mono">{task.profile_id}</span></div>
					{/if}
				</div>
			</div>
		</aside>

		<!-- Main findings area -->
		<div class="min-w-0 flex-1">
			{#if task.status === 'pending' || (task.status === 'running' && results.length === 0)}
				<div class="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border py-20 text-muted-foreground">
					<LoaderCircle class="h-6 w-6 animate-spin" />
					<p class="text-sm">Analysis in progress…</p>
				</div>
			{:else}
				<UnifiedFindingsTable
					{taskId}
					{availableAnalyzers}
					initialAnalyzerFilter={activeAnalyzerFilter}
				/>
			{/if}
		</div>
	</div>
{/if}
