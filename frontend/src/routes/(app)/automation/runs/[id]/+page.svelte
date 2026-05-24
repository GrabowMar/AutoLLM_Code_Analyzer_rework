<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import XCircle from '@lucide/svelte/icons/x-circle';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import CheckCircle from '@lucide/svelte/icons/check-circle';
	import AlertCircle from '@lucide/svelte/icons/alert-circle';
	import Clock from '@lucide/svelte/icons/clock';
	import { getRun, getPipeline, cancelRun, retryRun, getRunLogs, type PipelineRunDetail, type PipelineDetail } from '$lib/api/client';
	import { subscribe } from '$lib/api/sse';
	import type { Edge } from '@xyflow/svelte';
	import WorkflowCanvas from '$lib/components/workflow/WorkflowCanvas.svelte';
	import { pipelineToFlow, type PipelineConfig, type WorkflowNode } from '$lib/utils/pipeline-flow';

	const id = page.params.id as string;

	let run = $state<PipelineRunDetail | null>(null);
	let pipeline = $state<PipelineDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let activeTab = $state<'canvas' | 'timeline' | 'logs'>('canvas');
	let logs = $state('');
	let logsLoading = $state(false);
	let logsError = $state('');
	let retrying = $state(false);

	let nodes = $state<WorkflowNode[]>([]);
	let edges = $state<Edge[]>([]);
	let selectedNodeId = $state<string | null>(null);

	const statusColors: Record<string, string> = {
		pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
		succeeded: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		cancelled: 'bg-orange-500/15 text-orange-400 border-orange-500/30'
	};

	const statusBg: Record<string, string> = {
		pending: 'border-l-slate-400',
		running: 'border-l-blue-400',
		succeeded: 'border-l-emerald-500',
		failed: 'border-l-red-400',
		cancelled: 'border-l-orange-400'
	};

	const stepIdToName = $derived.by(() => {
		const map = new Map<string, string>();
		pipeline?.steps?.forEach((s) => map.set(s.id, s.name));
		return map;
	});

	const runStatusMap = $derived.by(() => {
		const map: Record<string, string> = {};
		run?.step_runs?.forEach((sr) => {
			if (sr.step_id) {
				const name = stepIdToName.get(sr.step_id);
				if (name) map[name] = sr.status;
			}
		});
		return map;
	});

	async function load() {
		loading = true;
		try {
			run = await getRun(id);
			error = '';
			if (run && (!pipeline || pipeline.id !== run.pipeline_id)) {
				try {
					pipeline = await getPipeline(run.pipeline_id);
					const cfg = (pipeline.config as unknown as PipelineConfig | undefined) ?? { steps: [] };
					const flow = pipelineToFlow(cfg);
					nodes = flow.nodes;
					edges = flow.edges;
				} catch {
					/* pipeline may be deleted; canvas just shows nothing */
				}
			}
		} catch {
			error = 'Failed to load run';
		} finally {
			loading = false;
		}
	}

	async function cancel() {
		if (!confirm('Cancel this run?')) return;
		run = await cancelRun(id);
	}

	async function retry() {
		retrying = true;
		try {
			const newRun = await retryRun(id);
			goto(`/automation/runs/${newRun.id}`);
		} catch (e: unknown) {
			const body = e as { status?: number; detail?: string };
			if (body?.status === 404) {
				alert('Retry endpoint not yet available (7b not deployed)');
			} else {
				alert(body?.detail ?? 'Failed to retry run');
			}
		} finally {
			retrying = false;
		}
	}

	async function loadLogs() {
		logsLoading = true;
		logsError = '';
		try {
			const data = await getRunLogs(id);
			logs = data.logs ?? '';
		} catch {
			if (run?.step_runs?.length) {
				logs = run.step_runs.map((sr) => {
					const name = sr.step_id ? stepIdToName.get(sr.step_id) ?? sr.step_id.slice(0, 8) : 'unknown';
					const parts = [`=== Step ${name} [${sr.status}] ===`];
					if (sr.error) parts.push(`Error: ${sr.error}`);
					if (Object.keys(sr.output).length > 0) parts.push(`Output: ${JSON.stringify(sr.output, null, 2)}`);
					return parts.join('\n');
				}).join('\n\n');
				logsError = 'Live log endpoint not available — showing step output data.';
			} else {
				logsError = 'Logs not available yet.';
			}
		} finally {
			logsLoading = false;
		}
	}

	function fmt(s: string | null) {
		return s ? new Date(s).toLocaleString() : '—';
	}

	function duration(a: string | null, b: string | null): string | null {
		if (!a) return null;
		const end = b ? new Date(b).getTime() : Date.now();
		const ms = end - new Date(a).getTime();
		if (ms < 1000) return `${ms}ms`;
		if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
		return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
	}

	let unsub: (() => void) | null = null;

	onMount(async () => {
		await load();
		unsub = subscribe([`automation:${id}`], async () => {
			try {
				run = await getRun(id);
			} catch {
				/* ignore transient */
			}
		});
	});

	onDestroy(() => { unsub?.(); });

	$effect(() => {
		if (activeTab === 'logs') loadLogs();
	});
</script>

<svelte:head><title>Run Detail — LLM Eval Lab</title></svelte:head>

<div class="space-y-6">
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/automation" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Automation</span>
		</a>
		<span>/</span>
		{#if run}
			<a href="/automation/{run.pipeline_id}" class="hover:text-foreground transition-colors">{pipeline?.name ?? 'Workflow'}</a>
			<span>/</span>
		{/if}
		<span class="truncate max-w-[300px]">Run</span>
	</nav>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
		<div class="page-header">
			<h1>
				Run Detail
				{#if run}
					<Badge class={statusColors[run.status] ?? ''} variant="outline">{run.status}</Badge>
					{#if run.status === 'running'}
						<LoaderCircle class="h-4 w-4 animate-spin text-blue-400 inline" />
					{/if}
				{/if}
			</h1>
			<p class="font-mono text-xs">{id}</p>
		</div>
		<div class="flex items-center gap-2 flex-wrap">
			<Button variant="outline" size="sm" onclick={load}><RefreshCw class="mr-2 h-4 w-4" />Refresh</Button>
			{#if run && (run.status === 'failed' || run.status === 'cancelled')}
				<Button variant="outline" size="sm" onclick={retry} disabled={retrying}>
					<RotateCcw class="mr-2 h-4 w-4" />{retrying ? 'Retrying...' : 'Retry'}
				</Button>
			{/if}
			{#if run && (run.status === 'pending' || run.status === 'running')}
				<Button variant="destructive" size="sm" onclick={cancel}><XCircle class="mr-2 h-4 w-4" />Cancel</Button>
			{/if}
		</div>
	</div>

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<Card.Root>
			<Card.Content class="pt-6 text-center text-destructive">{error}</Card.Content>
		</Card.Root>
	{:else if run}
		<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
			<Card.Root>
				<Card.Header><Card.Title>Run Info</Card.Title></Card.Header>
				<Card.Content class="space-y-2 text-sm">
					<div class="flex justify-between">
						<span class="text-muted-foreground">Workflow</span>
						<button class="hover:underline font-mono text-xs" onclick={() => goto(`/automation/${run!.pipeline_id}`)}>{run.pipeline_id.slice(0, 8)}…</button>
					</div>
					<div class="flex justify-between"><span class="text-muted-foreground">Started</span><span>{fmt(run.started_at)}</span></div>
					<div class="flex justify-between"><span class="text-muted-foreground">Completed</span><span>{fmt(run.completed_at)}</span></div>
					<div class="flex justify-between">
						<span class="text-muted-foreground">Duration</span>
						<span>{duration(run.started_at, run.completed_at) ?? '—'}</span>
					</div>
					{#if run.error}
						<div class="pt-2">
							<p class="text-muted-foreground text-xs font-medium uppercase">Error</p>
							<p class="text-destructive text-xs mt-1 font-mono">{run.error}</p>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header><Card.Title>Parameters</Card.Title></Card.Header>
				<Card.Content>
					<pre class="text-xs font-mono bg-muted/50 rounded p-2 overflow-auto max-h-40">{JSON.stringify(run.params, null, 2)}</pre>
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Tabs: Canvas | Timeline | Logs -->
		<div class="space-y-4">
			<div class="flex gap-1 border-b">
				<button
					class="px-4 py-2 text-sm font-medium transition-colors {activeTab === 'canvas' ? 'border-b-2 border-primary text-foreground' : 'text-muted-foreground hover:text-foreground'}"
					onclick={() => activeTab = 'canvas'}
				>Workflow Canvas</button>
				<button
					class="px-4 py-2 text-sm font-medium transition-colors {activeTab === 'timeline' ? 'border-b-2 border-primary text-foreground' : 'text-muted-foreground hover:text-foreground'}"
					onclick={() => activeTab = 'timeline'}
				>Step Timeline</button>
				<button
					class="px-4 py-2 text-sm font-medium transition-colors {activeTab === 'logs' ? 'border-b-2 border-primary text-foreground' : 'text-muted-foreground hover:text-foreground'}"
					onclick={() => activeTab = 'logs'}
				>Logs</button>
			</div>

			{#if activeTab === 'canvas'}
				<Card.Root>
					<Card.Header><Card.Title>Workflow Status</Card.Title></Card.Header>
					<Card.Content class="p-0">
						{#if nodes.length === 0}
							<p class="text-sm text-muted-foreground p-6 text-center">Workflow not available (parent may have been deleted).</p>
						{:else}
							<div class="h-[500px] border-t">
								<WorkflowCanvas bind:nodes bind:edges bind:selectedNodeId readOnly runStatusMap={runStatusMap} />
							</div>
							<div class="px-4 py-2 border-t flex items-center gap-3 text-[11px] text-muted-foreground flex-wrap">
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-slate-500/40"></span>pending</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-blue-400 animate-pulse"></span>running</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-emerald-500"></span>succeeded</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-red-500"></span>failed</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-orange-400"></span>cancelled</span>
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
			{:else if activeTab === 'timeline'}
				<Card.Root>
					<Card.Header><Card.Title>Step Runs ({run.step_runs.length})</Card.Title></Card.Header>
					<Card.Content>
						{#if run.step_runs.length === 0}
							<p class="text-sm text-muted-foreground">No step runs recorded yet.</p>
						{:else}
							<div class="space-y-0">
								{#each run.step_runs as sr, idx}
									{@const dur = duration(sr.started_at, sr.completed_at)}
									{@const stepName = sr.step_id ? stepIdToName.get(sr.step_id) ?? sr.step_id.slice(0, 8) : 'unknown'}
									<div class="flex items-start gap-3">
										<div class="flex flex-col items-center shrink-0 pt-1">
											{#if sr.status === 'succeeded'}
												<CheckCircle class="h-5 w-5 text-emerald-500 shrink-0" />
											{:else if sr.status === 'failed'}
												<AlertCircle class="h-5 w-5 text-red-400 shrink-0" />
											{:else if sr.status === 'running'}
												<LoaderCircle class="h-5 w-5 text-blue-400 animate-spin shrink-0" />
											{:else if sr.status === 'cancelled'}
												<XCircle class="h-5 w-5 text-orange-400 shrink-0" />
											{:else}
												<Clock class="h-5 w-5 text-slate-400 shrink-0" />
											{/if}
											{#if idx < run.step_runs.length - 1}
												<div class="w-px flex-1 bg-border my-1 min-h-[1.5rem]"></div>
											{/if}
										</div>
										<div class="flex-1 rounded-md border border-l-4 {statusBg[sr.status] ?? 'border-l-border'} bg-card p-3 mb-2">
											<div class="flex items-start justify-between gap-2">
												<div class="min-w-0">
													<p class="font-medium text-sm font-mono">{stepName}</p>
													<p class="text-xs text-muted-foreground">Attempt {sr.attempt} · {fmt(sr.started_at)}</p>
												</div>
												<div class="flex items-center gap-2 shrink-0">
													{#if dur}<span class="text-xs text-muted-foreground">{dur}</span>{/if}
													<Badge class="{statusColors[sr.status] ?? ''} text-xs" variant="outline">{sr.status}</Badge>
												</div>
											</div>
											{#if sr.error}
												<p class="text-xs text-destructive mt-2 font-mono">{sr.error}</p>
											{/if}
											{#if Object.keys(sr.output).length > 0}
												<details class="mt-2">
													<summary class="text-xs text-muted-foreground cursor-pointer hover:text-foreground">Output</summary>
													<pre class="text-xs font-mono bg-muted/50 rounded p-2 mt-1 overflow-auto max-h-32">{JSON.stringify(sr.output, null, 2)}</pre>
												</details>
											{/if}
										</div>
									</div>
								{/each}
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
			{:else}
				<Card.Root>
					<Card.Header><Card.Title>Run Logs</Card.Title></Card.Header>
					<Card.Content>
						{#if logsLoading}
							<div class="flex justify-center py-8"><LoaderCircle class="h-6 w-6 animate-spin text-muted-foreground" /></div>
						{:else}
							{#if logsError}
								<p class="text-xs text-muted-foreground mb-2">{logsError}</p>
							{/if}
							{#if logs}
								<pre class="text-xs font-mono bg-muted/50 rounded p-3 overflow-auto max-h-[60vh] whitespace-pre-wrap">{logs}</pre>
							{:else}
								<p class="text-sm text-muted-foreground">No logs available.</p>
							{/if}
						{/if}
					</Card.Content>
				</Card.Root>
			{/if}
		</div>
	{/if}
</div>
