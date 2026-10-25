<script lang="ts">
	import { formatDateTime as fmt } from '$lib/utils/formatters';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import * as Dialog from '$lib/components/ui/dialog';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Eye from '@lucide/svelte/icons/eye';
	import Edit from '@lucide/svelte/icons/pencil';
	import Copy from '@lucide/svelte/icons/copy';
	import Play from '@lucide/svelte/icons/play';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import History from '@lucide/svelte/icons/history';
	import Workflow from '@lucide/svelte/icons/workflow';
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

	function duration(a: string | null, b: string | null) {
		if (!a || !b) return null;
		const ms = new Date(b).getTime() - new Date(a).getTime();
		if (ms < 1000) return `${ms}ms`;
		if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
		return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
	}

	onMount(load);
</script>

<div class="space-y-6">
	<PageHeader
		title={pipeline?.name ?? 'Workflow'}
		description={pipeline?.description || undefined}
		back={{ href: '/automation?type=workflow', label: 'Automation' }}
	>
		{#snippet meta()}
			{#if pipeline}<StatusBadge status={pipeline.status} />{/if}
		{/snippet}
		{#snippet actions()}
			{#if pipeline}
				<Button variant="outline" size="sm" onclick={load}>
					<RefreshCw class="h-4 w-4" />Refresh
				</Button>
				<Button variant="outline" size="sm" href="/automation/{id}/edit">
					<Edit class="h-4 w-4" />Edit
				</Button>
				<Button variant="outline" size="sm" onclick={clone}>
					<Copy class="h-4 w-4" />Clone
				</Button>
				<Button variant="destructive" size="sm" onclick={remove}>
					<Trash2 class="h-4 w-4" />Delete
				</Button>
				<Button size="sm" onclick={() => { showTrigger = true; }}>
					<Play class="h-4 w-4" />Run
				</Button>
			{/if}
		{/snippet}
		<p class="font-mono text-xs text-muted-foreground">{id}</p>
	</PageHeader>

	<!-- Trigger Run Dialog -->
	<Dialog.Root bind:open={showTrigger} onOpenChange={(o) => { if (!o) triggerError = ''; }}>
		<Dialog.Content class="max-w-md space-y-4">
			<Dialog.Header>
				<Dialog.Title>Trigger workflow run</Dialog.Title>
				<Dialog.Description class="text-sm text-muted-foreground">Optional run parameters, passed to every step.</Dialog.Description>
			</Dialog.Header>
			<div class="space-y-1">
				<Label for="run-params">Parameters (JSON)</Label>
				<textarea
					id="run-params"
					bind:value={paramsText}
					rows={5}
					aria-invalid={triggerError ? 'true' : undefined}
					class="w-full rounded-md border bg-background p-3 font-mono text-sm resize-y focus:outline-none focus:ring-2 focus:ring-ring"
				></textarea>
				{#if triggerError}<p class="text-sm text-destructive" role="alert">{triggerError}</p>{/if}
			</div>
			<Dialog.Footer>
				<Button variant="outline" onclick={() => { showTrigger = false; triggerError = ''; }}>Cancel</Button>
				<Button onclick={triggerRun} disabled={triggering}>
					<Play class="h-4 w-4" />{triggering ? 'Starting…' : 'Start run'}
				</Button>
			</Dialog.Footer>
		</Dialog.Content>
	</Dialog.Root>

	{#if loading}
		<LoadingState label="loading workflow" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
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
						<EmptyState compact icon={Workflow} title="No steps yet" description="Open the editor to add steps to this workflow.">
							{#snippet action()}
								<Button variant="outline" size="sm" href="/automation/{id}/edit"><Edit class="h-4 w-4" />Edit workflow</Button>
							{/snippet}
						</EmptyState>
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
				<Card.Title>Run history</Card.Title>
				<Card.Description>{runsTotal} run{runsTotal === 1 ? '' : 's'}</Card.Description>
			</Card.Header>
			<Card.Content class={runs.length === 0 ? '' : 'p-0'}>
				{#if runs.length === 0}
					<EmptyState compact icon={History} title="No runs yet" description="Run the workflow to see its history here.">
						{#snippet action()}
							<Button size="sm" onclick={() => { showTrigger = true; }}><Play class="h-4 w-4" />Run workflow</Button>
						{/snippet}
					</EmptyState>
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
									{#each runs as r (r.id)}
										<tr class="border-b transition-colors hover:bg-muted/50">
											<td class="px-3 py-2 align-top font-mono text-xs">{r.id.slice(0, 8)}…</td>
											<td class="px-3 py-2 align-top"><StatusBadge status={r.status} /></td>
											<td class="px-3 py-2 align-top text-sm">{fmt(r.started_at)}</td>
											<td class="px-3 py-2 align-top text-sm text-muted-foreground">{duration(r.started_at, r.completed_at) ?? '—'}</td>
											<td class="px-3 py-2">
												<div class="flex items-center justify-end gap-1">
													<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="View run" aria-label="View run {r.id.slice(0, 8)}" href="/automation/runs/{r.id}">
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
							<div class="rounded-md border p-3">
								<div class="flex items-start justify-between gap-2 mb-2">
									<a class="font-mono text-xs hover:underline" href="/automation/runs/{r.id}">{r.id.slice(0, 8)}…</a>
									<StatusBadge status={r.status} />
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
								<ChevronLeft class="h-4 w-4" /><span class="sr-only">Previous page</span>
							</Button>
							<span class="px-3 py-2 text-sm">Page {runsPage} / {runsPages}</span>
							<Button variant="outline" size="sm" disabled={runsPage >= runsPages} onclick={async () => { runsPage++; const r = await listPipelineRuns(id, runsPage); runs = r.items; runsTotal = r.total; runsPages = r.pages; }}>
								<ChevronRight class="h-4 w-4" /><span class="sr-only">Next page</span>
							</Button>
						</div>
					{/if}
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
