<script lang="ts">
import { page } from '$app/stores';
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import SearchX from '@lucide/svelte/icons/search-x';
import CircleCheck from '@lucide/svelte/icons/circle-check';
import Ban from '@lucide/svelte/icons/ban';
import { toast } from 'svelte-sonner';
import {
	getRun,
	getRunFindings,
	cancelRun,
	type AnalysisRunDetail,
	type RunFinding,
} from '$lib/api/runs';
import { severityColors, severityOrder } from '$lib/constants/analysis';
import PageHeader from '$lib/components/PageHeader.svelte';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';
import PaginationBar from '$lib/components/PaginationBar.svelte';
import { tableFrame } from '$lib/components/table';
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

// Scalar metrics per tool (skip lists/objects except rank distributions,
// which render as a compact "A:3 B:1" row).
const metricResults = $derived(
	(run?.results ?? []).filter((r) => Object.keys(r.metrics ?? {}).length > 0),
);

function scalarMetrics(metrics: Record<string, any>): [string, number][] {
	return Object.entries(metrics ?? {}).filter(
		([, v]) => typeof v === 'number',
	) as [string, number][];
}

function distributionRow(metrics: Record<string, any>): string {
	const dist = Object.entries(metrics ?? {}).find(
		([, v]) => v && typeof v === 'object' && !Array.isArray(v) && Object.values(v).every((n) => typeof n === 'number'),
	);
	if (!dist) return '';
	return Object.entries(dist[1] as Record<string, number>)
		.filter(([, n]) => n > 0)
		.map(([k, n]) => `${k}:${n}`)
		.join(' ');
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

<div class="space-y-6">
	<PageHeader
		title={run?.name || 'Analysis Run'}
		documentTitle={run?.name || `Analysis ${runId.slice(0, 8)}`}
		back={{ href: '/analysis', label: 'Analysis' }}
	>
		{#snippet meta()}
			{#if run}<StatusBadge status={run.status} label={statusLabel(run.status)} />{/if}
		{/snippet}
		{#snippet actions()}
			{#if run && isActive}
				<Button variant="outline" size="sm" onclick={handleCancel} disabled={cancelling}>
					{#if cancelling}
						<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
					{:else}
						<Ban class="mr-1.5 h-3.5 w-3.5" />
					{/if}
					{cancelling ? 'Cancelling…' : 'Cancel run'}
				</Button>
			{/if}
			<Button variant="outline" size="sm" onclick={loadAll} disabled={loading} aria-label="Refresh run">
				<RefreshCw class="mr-1.5 h-3.5 w-3.5" />
				Refresh
			</Button>
		{/snippet}
		{#if run}
			<p class="text-xs text-muted-foreground">
				{totalFindings} finding{totalFindings !== 1 ? 's' : ''} · {run.results.length} tool{run.results.length !== 1 ? 's' : ''}
			</p>
		{/if}
	</PageHeader>

{#if loading}
	<LoadingState label="loading analysis run" />
{:else if error}
	<ErrorAlert title="Run not found" message={error} onretry={loadAll} />
{:else if run}
	{#if run.error_message}
		<ErrorAlert title="Run error" message={run.error_message} />
	{/if}

	<!-- Two-panel layout -->
	<div class="flex gap-4">
		<!-- Sidebar -->
		<aside class="hidden w-56 shrink-0 md:block">
			<div class="sticky top-16 space-y-0.5">
				<p class="section-label mb-2 px-2">Tools</p>

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
						<StatusBadge status={r.status} />
						<span class="shrink-0 font-mono text-xs">{toolFindingCount(r)}</span>
					</button>
				{/each}

				{#if Object.values(severityCounts).some((c) => (c as number) > 0)}
					<div class="mt-3 border-t border-border pt-3">
						<p class="section-label mb-1.5 px-2">By Severity</p>
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
									<Badge variant="outline" class="h-4 px-1 py-0 text-[11px] {severityColors[sev] ?? ''}">{severityCounts[sev]}</Badge>
								</button>
							{/if}
						{/each}
					</div>
				{/if}

				{#if metricResults.length > 0}
					<div class="mt-3 border-t border-border pt-3">
						<p class="section-label mb-1.5 px-2">Metrics</p>
						{#each metricResults as r (r.id)}
							<div class="px-2 pb-2">
								<p class="text-xs font-medium">{r.tool_slug}</p>
								{#each scalarMetrics(r.metrics) as [key, value] (key)}
									<div class="flex items-center justify-between text-[11px] text-muted-foreground">
										<span>{key.replaceAll('_', ' ')}</span>
										<span class="font-mono">{value}</span>
									</div>
								{/each}
								{#if distributionRow(r.metrics)}
									<div class="text-[11px] text-muted-foreground">
										<span class="font-mono">{distributionRow(r.metrics)}</span>
									</div>
								{/if}
							</div>
						{/each}
					</div>
				{/if}

				<div class="mt-3 border-t border-border px-2 pt-3 space-y-1 text-[11px] text-muted-foreground">
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
				<LoadingState label="analysis in progress" />
			{:else if findings.length === 0}
				{#if toolFilter || severityFilter}
					<EmptyState
						icon={SearchX}
						title="No findings{toolFilter ? ` for ${toolFilter}` : ''}{severityFilter ? ` at ${severityFilter} severity` : ''}"
					>
						{#snippet action()}
							<Button variant="outline" size="sm" onclick={() => { toolFilter = ''; severityFilter = ''; findingsPage = 1; fetchFindings(); }}>
								Clear filters
							</Button>
						{/snippet}
					</EmptyState>
				{:else if totalFindings === 0 && !isActive}
					<EmptyState icon={CircleCheck} title="No findings" description="This run completed with no issues." />
				{:else}
					<EmptyState title="No findings" />
				{/if}
			{:else}
				<Card.Root class="gap-0 overflow-clip py-0">
					<Card.Content class="p-0">
						<div class="table-frame" {@attach tableFrame}>
							<table>
								<thead>
									<tr>
										<th scope="col">Severity</th>
										<th scope="col">Title</th>
										<th scope="col">Location</th>
										<th scope="col">Rule</th>
										<th scope="col">Tool</th>
									</tr>
								</thead>
								<tbody>
									{#each findings as f (f.id)}
										<tr class="align-top">
											<td class="whitespace-nowrap">
												<Badge variant="outline" class="text-[11px] capitalize {severityColors[f.severity] ?? ''}">{f.severity}</Badge>
											</td>
											<td>
												<div class="font-medium">{f.title}</div>
												{#if f.description && f.description !== f.title}
													<div class="cell-sub mt-0.5 line-clamp-2 text-xs text-muted-foreground">{f.description}</div>
												{/if}
												{#if f.code_snippet}
													<code class="cell-sub mt-1 block max-w-md truncate rounded bg-muted px-1.5 py-0.5 text-[11px]">{f.code_snippet}</code>
												{/if}
											</td>
											<td class="text-xs text-muted-foreground">
												{#if f.file_path}
													<span class="font-mono">{f.file_path}{f.line_number != null ? `:${f.line_number}` : ''}</span>
												{:else}
													—
												{/if}
											</td>
											<td class="text-xs"><span class="font-mono text-muted-foreground">{f.rule_id || '—'}</span></td>
											<td class="whitespace-nowrap"><Badge variant="secondary" class="text-[11px]">{f.tool_slug}</Badge></td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</Card.Content>
				</Card.Root>

				{#if findingsPages > 1}
					<PaginationBar
						resultsText="Showing {(findingsPage - 1) * perPage + 1}–{Math.min(findingsPage * perPage, findingsTotal)} of {findingsTotal}"
						page={findingsPage}
						pages={findingsPages}
						onGoToPage={goToPage}
						class="mt-3 rounded-md border border-border"
					/>
				{/if}
			{/if}
		</div>
	</div>
{/if}
</div>
