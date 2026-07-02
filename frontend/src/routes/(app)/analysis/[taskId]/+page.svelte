<script lang="ts">
import { page } from '$app/stores';
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import AlertTriangle from '@lucide/svelte/icons/triangle-alert';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import Ban from '@lucide/svelte/icons/ban';
import Check from '@lucide/svelte/icons/check';
import { toast } from 'svelte-sonner';
import {
	getRun,
	getRunFindings,
	cancelRun,
	type AnalysisRunDetail,
	type RunFinding,
} from '$lib/api/runs';
import { statusColors, severityColors, severityOrder } from '$lib/constants/analysis';
import { formatDate, statusLabel } from '$lib/utils/formatters';

// Severities ordered critical→info for the summary bar / sidebar.
const SEVERITIES = Object.keys(severityOrder) as string[];

const runId = $derived($page.params.taskId ?? '');

let run = $state<AnalysisRunDetail | null>(null);
let findings = $state<RunFinding[]>([]);
let findingsTotal = $state(0);
let findingsPages = $state(1);
let findingsPage = $state(1);
let perPage = $state(50);
let loading = $state(true);
let error = $state('');
let cancelling = $state(false);
let toolFilter = $state('');
let severityFilter = $state('');
let pollTimer: ReturnType<typeof setInterval> | null = null;

const isActive = $derived(run?.status === 'running' || run?.status === 'pending');
const totalFindings = $derived((run?.summary?.total_findings as number) ?? 0);
const severityCounts = $derived(
	(run?.summary?.severity_counts as Record<string, number>) ?? {},
);

function toolFindingCount(r: { summary: Record<string, any> }): number {
	const counts = (r.summary?.severity_counts ?? {}) as Record<string, number>;
	return Object.values(counts).reduce((a, b) => a + (b ?? 0), 0);
}

async function fetchRun() {
	try {
		run = await getRun(runId);
		error = '';
	} catch (e: any) {
		error = e?.detail || e?.message || 'Failed to load analysis run';
	}
}

async function fetchFindings() {
	try {
		const data = await getRunFindings(runId, {
			page: findingsPage,
			per_page: perPage,
			tool_slug: toolFilter || undefined,
			severity: severityFilter || undefined,
		});
		findings = data.items;
		findingsTotal = data.total;
		findingsPages = data.pages;
		findingsPage = data.page;
	} catch {
		// non-fatal
	}
}

async function loadAll() {
	loading = true;
	await fetchRun();
	await fetchFindings();
	loading = false;
}

function selectTool(slug: string) {
	toolFilter = slug;
	findingsPage = 1;
	fetchFindings();
}

function selectSeverity(sev: string) {
	severityFilter = sev;
	findingsPage = 1;
	fetchFindings();
}

function goToPage(p: number) {
	if (p < 1 || p > findingsPages) return;
	findingsPage = p;
	fetchFindings();
}

async function handleCancel() {
	if (!run || cancelling) return;
	cancelling = true;
	try {
		await cancelRun(runId);
		toast.success('Run cancelled');
		await fetchRun();
	} catch {
		toast.error('Failed to cancel run');
	} finally {
		cancelling = false;
	}
}

$effect(() => {
	if (isActive && !pollTimer) {
		pollTimer = setInterval(async () => {
			await fetchRun();
			await fetchFindings();
			if (!isActive && pollTimer) {
				clearInterval(pollTimer);
				pollTimer = null;
			}
		}, 3000);
	}
	return () => {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	};
});

onMount(() => {
	loadAll();
	return () => {
		if (pollTimer) clearInterval(pollTimer);
	};
});
</script>

<svelte:head>
	<title>{run?.name || `Analysis ${runId}`} - LLM Lab</title>
</svelte:head>

{#if loading}
	<!-- Skeleton: header + sidebar + findings rows (reserves layout) -->
	<div class="animate-pulse motion-reduce:animate-none space-y-4">
		<div class="flex items-center gap-2 border-b py-2.5">
			<div class="h-7 w-7 rounded bg-muted"></div>
			<div class="h-4 w-40 rounded bg-muted"></div>
			<div class="h-5 w-16 rounded-full bg-muted"></div>
		</div>
		<div class="flex gap-4">
			<div class="hidden w-56 shrink-0 space-y-2 md:block">
				{#each Array(6) as _}<div class="h-7 rounded bg-muted"></div>{/each}
			</div>
			<div class="min-w-0 flex-1 space-y-2">
				{#each Array(8) as _}<div class="h-10 rounded bg-muted"></div>{/each}
			</div>
		</div>
	</div>
{:else if error}
	<div class="space-y-4">
		<Button variant="ghost" size="sm" href="/analysis" class="gap-1.5 px-2">
			<ArrowLeft class="h-3.5 w-3.5" /> Analysis
		</Button>
		<Card.Root class="border-red-500/20">
			<Card.Content class="p-8 text-center">
				<AlertTriangle class="mx-auto mb-4 h-10 w-10 text-red-400" />
				<h2 class="mb-2 text-lg font-semibold text-red-400">Run Not Found</h2>
				<p class="text-sm text-muted-foreground">{error}</p>
				<Button variant="outline" size="sm" href="/analysis" class="mt-4">Back to Analysis</Button>
			</Card.Content>
		</Card.Root>
	</div>
{:else if run}
	<!-- Sticky top bar -->
	<div class="sticky top-12 z-20 -mx-3 mb-4 border-b bg-background/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/60 sm:-mx-4 sm:px-4 md:-mx-5 md:px-5">
		<div class="flex items-center gap-2 py-2.5">
			<Button variant="ghost" size="sm" href="/analysis" class="h-7 shrink-0 gap-1 px-2">
				<ArrowLeft class="h-3.5 w-3.5" />
			</Button>
			<div class="min-w-0 flex-1">
				<div class="flex flex-wrap items-center gap-1.5">
					<h1 class="max-w-[200px] truncate text-sm font-semibold sm:max-w-xs">{run.name || 'Analysis Run'}</h1>
					<Badge variant="outline" class="{statusColors[run.status] ?? ''} text-xs {run.status === 'running' ? 'animate-pulse motion-reduce:animate-none' : ''}">
						{statusLabel(run.status)}
					</Badge>
					<span class="hidden text-xs text-muted-foreground sm:inline">
						{totalFindings} finding{totalFindings !== 1 ? 's' : ''} · {run.results.length} tool{run.results.length !== 1 ? 's' : ''}
					</span>
				</div>
			</div>
			<div class="flex shrink-0 items-center gap-1">
				{#if isActive}
					<Button variant="outline" size="sm" onclick={handleCancel} disabled={cancelling} class="h-7 border-amber-500/30 px-2 text-xs text-amber-400 hover:bg-amber-500/10">
						<Ban class="mr-1 h-3 w-3" />
						{cancelling ? 'Cancelling…' : 'Cancel'}
					</Button>
				{/if}
				<Button variant="outline" size="icon" class="h-7 w-7" onclick={loadAll} title="Refresh" aria-label="Refresh run">
					<RefreshCw class="h-3 w-3" />
				</Button>
			</div>
		</div>
	</div>

	{#if run.error_message}
		<div class="mb-4 rounded-md bg-red-500/10 p-3 text-sm text-red-400">
			<strong>Error:</strong> {run.error_message}
		</div>
	{/if}

	<!-- Two-panel layout -->
	<div class="flex gap-4">
		<!-- Sidebar -->
		<aside class="hidden w-56 shrink-0 md:block">
			<div class="sticky top-[6.5rem] space-y-0.5">
				<p class="mb-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Tools</p>

				<button
					class="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-sm transition-colors {toolFilter === '' ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'}"
					onclick={() => selectTool('')}
				>
					<span>All</span>
					<span class="font-mono text-xs">{totalFindings}</span>
				</button>

				{#each run.results as r (r.id)}
					<button
						class="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors {toolFilter === r.tool_slug ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'}"
						onclick={() => selectTool(r.tool_slug)}
						title={r.error_message}
					>
						<span class="min-w-0 flex-1 truncate text-left text-xs">{r.tool_slug}</span>
						<Badge variant="outline" class="h-4 shrink-0 px-1 py-0 text-[10px] {statusColors[r.status] ?? ''}">{r.status.slice(0, 4)}</Badge>
						<span class="shrink-0 font-mono text-xs">{toolFindingCount(r)}</span>
					</button>
				{/each}

				{#if Object.values(severityCounts).some((c) => (c as number) > 0)}
					<div class="mt-3 border-t border-border pt-3">
						<p class="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">By Severity</p>
						<button
							class="flex w-full items-center justify-between rounded-md px-2 py-0.5 text-xs transition-colors {severityFilter === '' ? 'font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/50'}"
							onclick={() => selectSeverity('')}
						>
							<span>All severities</span>
						</button>
						{#each ['critical', 'high', 'medium', 'low', 'info'] as sev}
							{#if (severityCounts[sev] ?? 0) > 0}
								<button
									class="flex w-full items-center justify-between rounded-md px-2 py-0.5 text-xs transition-colors {severityFilter === sev ? 'bg-primary/10 font-medium' : 'hover:bg-muted/50'}"
									onclick={() => selectSeverity(sev)}
								>
									<span class="capitalize text-muted-foreground">{sev}</span>
									<Badge variant="outline" class="h-4 px-1 py-0 text-[10px] {severityColors[sev] ?? ''}">{severityCounts[sev]}</Badge>
								</button>
							{/if}
						{/each}
					</div>
				{/if}

				<div class="mt-3 border-t border-border px-2 pt-3 space-y-1 text-[10px] text-muted-foreground">
					{#if run.started_at}<div>Started: <span class="font-mono">{formatDate(run.started_at)}</span></div>{/if}
					{#if run.completed_at}<div>Completed: <span class="font-mono">{formatDate(run.completed_at)}</span></div>{/if}
					{#if run.generation_job_id}
						<div>Job: <a href="/generation/{run.generation_job_id}" class="font-mono text-primary hover:underline">{run.generation_job_id.slice(0, 8)}…</a></div>
					{/if}
				</div>
			</div>
		</aside>

		<!-- Main findings area -->
		<div class="min-w-0 flex-1">
			<!-- Severity summary bar: at-a-glance triage + quick filter -->
			{#if totalFindings > 0}
				<div class="mb-3 flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter findings by severity">
					<button
						class="rounded-full border px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring {severityFilter === '' ? 'border-primary/40 bg-primary/10 font-medium text-primary' : 'border-input hover:bg-muted/50'}"
						aria-pressed={severityFilter === ''}
						onclick={() => selectSeverity('')}
					>
						All <span class="font-mono">{totalFindings}</span>
					</button>
					{#each SEVERITIES as sev}
						{#if (severityCounts[sev] ?? 0) > 0}
							<button
								class="rounded-full border px-2.5 py-1 text-xs capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring {severityFilter === sev ? `${severityColors[sev]} font-medium` : 'border-input hover:bg-muted/50'}"
								aria-pressed={severityFilter === sev}
								onclick={() => selectSeverity(sev)}
							>
								{sev} <span class="font-mono">{severityCounts[sev]}</span>
							</button>
						{/if}
					{/each}
				</div>
			{/if}

			{#if isActive && findings.length === 0}
				<div class="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border py-20 text-muted-foreground">
					<LoaderCircle class="h-6 w-6 animate-spin motion-reduce:animate-none" />
					<p class="text-sm">Analysis in progress…</p>
				</div>
			{:else if findings.length === 0}
				<div class="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border py-20 text-muted-foreground">
					<p class="text-sm">No findings{toolFilter ? ` for ${toolFilter}` : ''}{severityFilter ? ` at ${severityFilter} severity` : ''}.</p>
					{#if toolFilter || severityFilter}
						<Button variant="outline" size="sm" onclick={() => { toolFilter = ''; severityFilter = ''; findingsPage = 1; fetchFindings(); }}>
							Clear filters
						</Button>
					{:else if totalFindings === 0 && !isActive}
						<p class="flex items-center gap-1.5 text-xs text-emerald-500">
							<Check class="h-3.5 w-3.5" /> This run completed with no issues.
						</p>
					{/if}
				</div>
			{:else}
				<Card.Root>
					<Card.Content class="p-0">
						<div class="overflow-x-auto">
							<table class="w-full text-sm">
								<thead>
									<tr class="border-b bg-muted/40">
										<th scope="col" class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Severity</th>
										<th scope="col" class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Title</th>
										<th scope="col" class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Location</th>
										<th scope="col" class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Rule</th>
										<th scope="col" class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Tool</th>
									</tr>
								</thead>
								<tbody>
									{#each findings as f (f.id)}
										<tr class="border-b align-top hover:bg-muted/30">
											<td class="px-3 py-2">
												<Badge variant="outline" class="text-[10px] capitalize {severityColors[f.severity] ?? ''}">{f.severity}</Badge>
											</td>
											<td class="px-3 py-2">
												<div class="font-medium">{f.title}</div>
												{#if f.description && f.description !== f.title}
													<div class="mt-0.5 text-xs text-muted-foreground line-clamp-2">{f.description}</div>
												{/if}
												{#if f.code_snippet}
													<code class="mt-1 block max-w-md truncate rounded bg-muted px-1.5 py-0.5 text-[11px]">{f.code_snippet}</code>
												{/if}
											</td>
											<td class="px-3 py-2 text-xs text-muted-foreground">
												{#if f.file_path}
													<span class="font-mono">{f.file_path}{f.line_number != null ? `:${f.line_number}` : ''}</span>
												{:else}
													—
												{/if}
											</td>
											<td class="px-3 py-2 text-xs"><span class="font-mono text-muted-foreground">{f.rule_id || '—'}</span></td>
											<td class="px-3 py-2"><Badge variant="secondary" class="text-[10px]">{f.tool_slug}</Badge></td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</Card.Content>
				</Card.Root>

				{#if findingsPages > 1}
					<div class="mt-3 flex items-center justify-between text-sm">
						<span class="text-muted-foreground">
							Showing {(findingsPage - 1) * perPage + 1}–{Math.min(findingsPage * perPage, findingsTotal)} of {findingsTotal}
						</span>
						<div class="flex items-center gap-1">
							<Button variant="outline" size="sm" disabled={findingsPage <= 1} onclick={() => goToPage(findingsPage - 1)}>Prev</Button>
							<span class="px-2 text-xs text-muted-foreground">{findingsPage} / {findingsPages}</span>
							<Button variant="outline" size="sm" disabled={findingsPage >= findingsPages} onclick={() => goToPage(findingsPage + 1)}>Next</Button>
						</div>
					</div>
				{/if}
			{/if}
		</div>
	</div>
{/if}
