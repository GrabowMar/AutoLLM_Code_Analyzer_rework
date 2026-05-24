<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Eye from '@lucide/svelte/icons/eye';
	import Edit from '@lucide/svelte/icons/pencil';
	import Copy from '@lucide/svelte/icons/copy';
	import Play from '@lucide/svelte/icons/play';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import {
		getPipeline,
		deletePipeline,
		clonePipeline,
		triggerPipelineRun,
		listPipelineRuns,
		type PipelineDetail,
		type PipelineRunListItem
	} from '$lib/api/client';
	import type { Edge } from '@xyflow/svelte';
	import WorkflowCanvas from '$lib/components/workflow/WorkflowCanvas.svelte';
	import SchedulesPanel from '$lib/components/workflow/SchedulesPanel.svelte';
	import BatchesPanel from '$lib/components/workflow/BatchesPanel.svelte';
	import { pipelineToFlow, type PipelineConfig, type WorkflowNode } from '$lib/utils/pipeline-flow';

	const id = page.params.id as string;

	let pipeline = $state<PipelineDetail | null>(null);
	let runs = $state<PipelineRunListItem[]>([]);
	let runsTotal = $state(0);
	let runsPage = $state(1);
	let runsPages = $state(1);
	let loading = $state(true);
	let error = $state('');

	let nodes = $state<WorkflowNode[]>([]);
	let edges = $state<Edge[]>([]);
	let selectedNodeId = $state<string | null>(null);

	// Trigger run dialog
	let showTrigger = $state(false);
	let paramsText = $state('{}');
	let triggerError = $state('');
	let triggering = $state(false);

	const statusColors: Record<string, string> = {
		pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
		succeeded: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		cancelled: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
		draft: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		active: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		archived: 'bg-orange-500/15 text-orange-400 border-orange-500/30'
	};

	async function load() {
		loading = true;
		error = '';
		try {
			pipeline = await getPipeline(id);
			const cfg = (pipeline.config as unknown as PipelineConfig | undefined) ?? { steps: [] };
			const flow = pipelineToFlow(cfg);
			nodes = flow.nodes;
			edges = flow.edges;
			const r = await listPipelineRuns(id, runsPage);
			runs = r.items;
			runsTotal = r.total;
			runsPages = r.pages;
		} catch {
			error = 'Failed to load workflow';
		} finally {
			loading = false;
		}
	}

	async function remove() {
		if (!confirm('Delete this workflow?')) return;
		await deletePipeline(id);
		goto('/automation');
	}

	async function clone() {
		const newName = prompt('New name:', `${pipeline?.name} (copy)`);
		if (!newName) return;
		const p = await clonePipeline(id, newName);
		goto(`/automation/${p.id}`);
	}

	async function triggerRun() {
		triggerError = '';
		let params: Record<string, unknown> = {};
		try {
			params = JSON.parse(paramsText);
		} catch {
			triggerError = 'Invalid JSON params';
			return;
		}
		triggering = true;
		try {
			const r = await triggerPipelineRun(id, params);
			goto(`/automation/runs/${r.id}`);
		} catch (e: unknown) {
			triggerError = (e as { detail?: string })?.detail ?? 'Failed to trigger run';
		} finally {
			triggering = false;
		}
	}

	function fmt(s: string | null) {
		return s ? new Date(s).toLocaleString() : '—';
	}

	function duration(a: string | null, b: string | null) {
		if (!a || !b) return null;
		const ms = new Date(b).getTime() - new Date(a).getTime();
		if (ms < 1000) return `${ms}ms`;
		if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
		return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
	}

	onMount(load);
</script>

<svelte:head><title>{pipeline?.name ?? 'Workflow'} — LLM Eval Lab</title></svelte:head>

<div class="space-y-6">
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/automation" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Automation</span>
		</a>
		<span>/</span>
		<span class="truncate max-w-[300px]">{pipeline?.name ?? 'Loading...'}</span>
	</nav>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
		<div class="page-header">
			<h1>
				{pipeline?.name ?? 'Loading...'}
				{#if pipeline}
					<Badge class={statusColors[pipeline.status] ?? ''} variant="outline">{pipeline.status}</Badge>
				{/if}
			</h1>
			<p class="font-mono text-xs">{id}</p>
		</div>
		{#if pipeline}
			<div class="flex items-center gap-2 flex-wrap">
				<Button variant="outline" size="sm" onclick={load}>
					<RefreshCw class="mr-2 h-4 w-4" />Refresh
				</Button>
				<Button variant="outline" size="sm" onclick={() => goto(`/automation/${id}/edit`)}>
					<Edit class="mr-2 h-4 w-4" />Edit
				</Button>
				<Button variant="outline" size="sm" onclick={clone}>
					<Copy class="mr-2 h-4 w-4" />Clone
				</Button>
				<Button size="sm" onclick={() => { showTrigger = true; }}>
					<Play class="mr-2 h-4 w-4" />Run
				</Button>
				<Button variant="destructive" size="sm" onclick={remove}>
					<Trash2 class="mr-2 h-4 w-4" />Delete
				</Button>
			</div>
		{/if}
	</div>

	<!-- Trigger Run Dialog -->
	{#if showTrigger}
		<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" role="dialog" tabindex="-1" onclick={(e) => { if (e.target === e.currentTarget) showTrigger = false; }} onkeydown={(e) => { if (e.key === 'Escape') showTrigger = false; }}>
			<div class="w-full max-w-md rounded-lg border bg-card shadow-lg p-6 space-y-4">
				<h2 class="text-lg font-semibold">Trigger Workflow Run</h2>
				<div class="space-y-1">
					<Label>Parameters (JSON)</Label>
					<textarea
						bind:value={paramsText}
						rows={5}
						class="w-full rounded-md border bg-background p-3 font-mono text-sm resize-y focus:outline-none focus:ring-2 focus:ring-ring"
					></textarea>
					{#if triggerError}<p class="text-sm text-destructive">{triggerError}</p>{/if}
				</div>
				<div class="flex justify-end gap-2">
					<Button variant="outline" onclick={() => { showTrigger = false; triggerError = ''; }}>Cancel</Button>
					<Button onclick={triggerRun} disabled={triggering}>
						<Play class="mr-2 h-4 w-4" />{triggering ? 'Starting...' : 'Start Run'}
					</Button>
				</div>
			</div>
		</div>
	{/if}

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<p class="text-destructive">{error}</p>
	{:else if pipeline}
		<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
			<Card.Root class="md:col-span-1">
				<Card.Header><Card.Title>Details</Card.Title></Card.Header>
				<Card.Content class="space-y-2 text-sm">
					<div class="flex justify-between"><span class="text-muted-foreground">Version</span><span>v{pipeline.version}</span></div>
					<div class="flex justify-between"><span class="text-muted-foreground">Created</span><span>{fmt(pipeline.created_at)}</span></div>
					<div class="flex justify-between"><span class="text-muted-foreground">Updated</span><span>{fmt(pipeline.updated_at)}</span></div>
					<div class="flex justify-between"><span class="text-muted-foreground">Nodes</span><span>{nodes.length}</span></div>
					<div class="flex justify-between"><span class="text-muted-foreground">Edges</span><span>{edges.length}</span></div>
					{#if pipeline.description}
						<p class="text-muted-foreground pt-2 border-t">{pipeline.description}</p>
					{/if}
					{#if pipeline.tags.length > 0}
						<div class="flex flex-wrap gap-1 pt-2 border-t">
							{#each pipeline.tags as tag (tag)}<Badge variant="secondary" class="text-xs">{tag}</Badge>{/each}
						</div>
					{/if}
				</Card.Content>
			</Card.Root>

			<!-- Workflow canvas (read-only) -->
			<Card.Root class="md:col-span-2">
				<Card.Header><Card.Title>Workflow</Card.Title></Card.Header>
				<Card.Content class="p-0">
					{#if nodes.length === 0}
						<p class="text-sm text-muted-foreground p-6 text-center">No nodes defined. Click Edit to build the workflow.</p>
					{:else}
						<div class="h-[400px] border-t">
							<WorkflowCanvas bind:nodes bind:edges bind:selectedNodeId readOnly />
						</div>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Schedules + Batches (absorbed from dedicated pages) -->
		<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
			<SchedulesPanel pipelineId={id} />
			<BatchesPanel pipelineId={id} />
		</div>

		<!-- Runs history -->
		<Card.Root>
			<Card.Header>
				<Card.Title>Run History ({runsTotal})</Card.Title>
			</Card.Header>
			<Card.Content class={runs.length === 0 ? '' : 'p-0'}>
				{#if runs.length === 0}
					<p class="text-sm text-muted-foreground">No runs yet. Click <strong>Run</strong> to trigger the workflow.</p>
				{:else}
					<div class="hidden md:block">
						<div class="overflow-x-auto">
							<table class="w-full">
								<thead>
									<tr class="border-b bg-muted/40 sticky top-0 z-10">
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Run ID</th>
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Status</th>
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Started</th>
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Duration</th>
										<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Actions</th>
									</tr>
								</thead>
								<tbody>
									{#each runs as r, i (r.id)}
										<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'} {r.status === 'failed' ? 'bg-destructive/[0.03]' : ''}">
											<td class="px-3 py-2 align-top font-mono text-xs">{r.id.slice(0, 8)}…</td>
											<td class="px-3 py-2 align-top"><Badge variant="outline" class="text-[10px] {statusColors[r.status] ?? ''}">{r.status}</Badge></td>
											<td class="px-3 py-2 align-top text-sm">{fmt(r.started_at)}</td>
											<td class="px-3 py-2 align-top text-sm text-muted-foreground">{duration(r.started_at, r.completed_at) ?? '—'}</td>
											<td class="px-3 py-2">
												<div class="flex items-center justify-end gap-1">
													<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="View" onclick={() => goto(`/automation/runs/${r.id}`)}>
														<Eye class="h-3.5 w-3.5" />
													</Button>
												</div>
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</div>

					<div class="md:hidden space-y-3 p-3">
						{#each runs as r (r.id)}
							<div class="border rounded-lg p-3 bg-card">
								<div class="flex items-start justify-between gap-2 mb-2">
									<button class="font-mono text-xs hover:underline" onclick={() => goto(`/automation/runs/${r.id}`)}>{r.id.slice(0, 8)}…</button>
									<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[r.status] ?? ''}">{r.status}</Badge>
								</div>
								<div class="text-xs text-muted-foreground space-y-0.5">
									<div>Started: {fmt(r.started_at)}</div>
									<div>Duration: {duration(r.started_at, r.completed_at) ?? '—'}</div>
								</div>
							</div>
						{/each}
					</div>

					{#if runsPages > 1}
						<div class="flex justify-center gap-2 p-4 border-t">
							<Button variant="outline" size="sm" disabled={runsPage <= 1} onclick={async () => { runsPage--; const r = await listPipelineRuns(id, runsPage); runs = r.items; runsTotal = r.total; runsPages = r.pages; }}>
								<ChevronLeft class="h-4 w-4" />
							</Button>
							<span class="px-3 py-2 text-sm">Page {runsPage} / {runsPages}</span>
							<Button variant="outline" size="sm" disabled={runsPage >= runsPages} onclick={async () => { runsPage++; const r = await listPipelineRuns(id, runsPage); runs = r.items; runsTotal = r.total; runsPages = r.pages; }}>
								<ChevronRight class="h-4 w-4" />
							</Button>
						</div>
					{/if}
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
